#!/usr/bin/env python3
"""Generate default/savedsearches.conf for the HL - detections. Edit here, run
`python3 build_detections.py`, commit both files.

Every detection is mapped to MITRE ATT&CK v20 (released 2026-10-13): technique,
tactic and, where MITRE has one for this kind of data, the detection strategy
(DET####) the logic follows. Each search ends in `hl_finding(...)` (macros.conf),
so every finding has the same fields. Firing alerts:
  - write one event per finding to index=security, sourcetype=homelab:detection
    (Log Event action), which is what the security agent reads. The agents post
    to Slack. The homelab_slack action (bin/homelab_slack.py) is installed but
    not attached to any search; add `action.homelab_slack = 1` to send directly.

Windows: most run every 15 minutes over [-20m@m, -5m@m), contiguous and late enough
for indexing lag. "First seen" detections search their own longer baseline. Each
detection runs at its own minute in the quarter hour (cron_for) so they don't all
queue at once.

Three "first seen" detections keep most of their baseline in a KV store lookup that
an "HL - Baseline: ..." search rebuilds once a day (baseline()), and read raw events
only from the end of that baseline (since_baseline()). Also writes collections.conf
and transforms.conf.
"""
import pathlib
import zlib

SEVERITY = {"low": 2, "medium": 3, "high": 4, "critical": 5}

# Common tail for Kubernetes audit events: one row per user/source/object.
K8S_TAIL = (
    '| eval user=\'user.username\', src=mvindex(\'sourceIPs{}\', 0), '
    'object=coalesce(\'objectRef.namespace\'."/", "").\'objectRef.resource\'.coalesce("/".\'objectRef.name\', "").coalesce("/".\'objectRef.subresource\', "") '
    '| stats count min(_time) as first_seen max(_time) as last_seen values(verb) as verbs values(userAgent) as agents values(responseStatus.code) as codes by user src object '
    '| eval host="k3s-01", detail="verbs=".mvjoin(verbs, ",")." codes=".mvjoin(codes, ",")." user_agent=".mvjoin(agents, ",") '
    '| convert ctime(first_seen) ctime(last_seen) '
)

D = []

# Every 15 minutes, at a minute offset taken from a hash of the name: stable when
# detections are added or reordered, and never a multiple of 5, when the Falco and
# honeypot alerts (*/5) and the 85_homelab_ops checks start. Windows are relative to
# the scheduled time, so [-20m@m, -5m@m) still tiles with no gap or overlap.
OFFSETS = [m for m in range(15) if m % 5]


def cron_for(name):
    m = OFFSETS[zlib.crc32(name.encode()) % len(OFFSETS)]
    return f'{",".join(str(m + q) for q in (0, 15, 30, 45))} * * * *'


# "First seen" baselines. Each lookup holds, per key, the last time the key was seen
# in [-span@d, @d) and baseline_end (that @d), rebuilt by a daily search; the detection
# then reads raw events only from baseline_end. A key counts as seen before if either
# part has it, which is the same answer as reading the whole span raw. If the lookup
# is empty (first deploy, KV store wiped), stale, or ends after the detection window
# starts, since_baseline falls back to the full span and the lookup is ignored: slower,
# never blind. Events indexed after the daily rebuild with an older _time are in
# neither part, so at worst they cause an extra alert, never a missed one.
B = []


def baseline(name, lookup, keys, search, span, cron):
    B.append(dict(name=name, lookup=lookup, keys=keys, search=search, span=span, cron=cron))


def since_baseline(lookup, span):
    return (f"[| inputlookup {lookup} | stats max(baseline_end) as e "
            "| appendpipe [| stats count | where count=0] | stats max(e) as e "
            "| eval e=if(e<=relative_time(now(), \"-20m@m\"), e, null()), "
            f"earliest=floor(max(coalesce(e, 0), relative_time(now(), \"{span}\"))) | return earliest] ")


# After `lookup ... OUTPUT last_seen as baseline_last_seen baseline_end`: in_baseline=1
# when the key was seen in the span before the window (nested if: no null comparisons).
def in_baseline(span):
    return ("| eval in_baseline=if(isnotnull(baseline_last_seen) AND isnotnull(baseline_end), "
            f"if(baseline_last_seen>=relative_time(now(), \"{span}\") "
            "AND baseline_end<=relative_time(now(), \"-20m@m\"), 1, 0), 0) ")


def det(name, technique, tactic, severity, strategy, search, test, suppress="user,src,object",
        period="4h", window=("-20m@m", "-5m@m"), cron=None, description=""):
    D.append(dict(name=name, technique=technique, tactic=tactic, severity=severity, strategy=strategy,
                  search=search, test=test, suppress=suppress, period=period, window=window, cron=cron,
                  description=description))


# ---- Kubernetes (k3s-01 API audit log) -------------------------------------------------
det("HL - Kubernetes exec, attach or port-forward into a pod",
    "T1609", "Execution", "high", "DET0065",
    "`hl_k8s_audit` objectRef.subresource IN (exec, attach, portforward) "
    "| where NOT `hl_k8s_platform_user` AND NOT match('user.username', \"^system:serviceaccount:arc-runners:\") "
    + K8S_TAIL,
    "kubectl -n <scratch> exec <pod> -- true",
    description="Someone ran a command in, attached to, or port-forwarded into a pod. ARC runner pods are excluded: in Kubernetes mode the runner execs job steps by design.")

det("HL - Kubernetes RBAC role or binding changed",
    "T1098.006", "Privilege Escalation", "high", "DET0572",
    "`hl_k8s_audit` objectRef.resource IN (roles, clusterroles, rolebindings, clusterrolebindings) "
    "verb IN (create, update, patch, delete) responseStatus.code<400 "
    "| where NOT `hl_k8s_platform_user` "
    + K8S_TAIL,
    "kubectl -n <scratch> create rolebinding hl-test --clusterrole=view --user=nobody, then delete it",
    description="A role or role binding was created, changed or deleted by something other than Argo CD, a platform operator or Kubernetes itself.")

det("HL - Kubernetes secrets read by a person or application",
    "T1552.007", "Credential Access", "medium", "DET0198",
    "`hl_k8s_audit` objectRef.resource=secrets verb IN (get, list, watch) responseStatus.code<400 "
    "| where NOT `hl_k8s_platform_user` "
    + K8S_TAIL,
    "kubectl -n <scratch> get secrets",
    description="Secrets were read by a person (system:admin is the root kubeconfig on k3s-01) or an application service account. Platform controllers' reads are not audited.")

det("HL - Kubernetes service account token minted",
    "T1528", "Credential Access", "medium", "DET0515",
    "`hl_k8s_audit` objectRef.resource=serviceaccounts objectRef.subresource=token verb=create responseStatus.code<400 "
    "| where NOT `hl_k8s_platform_user` "
    + K8S_TAIL,
    "kubectl -n <scratch> create token default",
    description="A service account token was requested by a person or an application. The kubelet's own pod tokens are not audited. Gateway client tokens (mint-client-token.sh) show up here, as intended. The homelab agents get kubelet-projected tokens, which are not audited, so nothing routine mints here.")

det("HL - Kubernetes workload created by hand",
    "T1610", "Execution", "medium", "DET0249",
    "`hl_k8s_audit` objectRef.resource IN (pods, deployments, daemonsets, statefulsets, jobs, cronjobs) verb=create responseStatus.code<400 "
    "| where NOT `hl_k8s_platform_user` AND NOT match('user.username', \"^system:serviceaccount:(arc-runners|toolhive-mcp):\") "
    # Trivy Operator runs each image scan as a Job in its own namespace.
    "| where NOT ('user.username'==\"system:serviceaccount:trivy-system:trivy-operator\" AND 'objectRef.namespace'==\"trivy-system\" AND 'objectRef.resource'==\"jobs\") "
    "| eval privileged=if(match(_raw, \"\\\"privileged\\\":true|\\\"hostNetwork\\\":true|\\\"hostPID\\\":true|\\\"hostPath\\\"\"), \"yes\", \"no\") "
    + K8S_TAIL.replace("values(verb) as verbs", "values(verb) as verbs values(privileged) as privileged").replace(
        'detail="verbs="', 'detail="privileged_or_host_access=".mvjoin(privileged, ",")." verbs="'),
    "kubectl -n <scratch> run hl-test --image=busybox --restart=Never -- true, then delete it",
    description="A pod or workload was created by a person or an application rather than by Argo CD or a controller. People's requests are audited with the body, so privileged, host-network, host-PID and hostPath pods are flagged in the detail.")

det("HL - Kubernetes anonymous or denied API requests",
    "T1613", "Discovery", "medium", "DET0490",
    "`hl_k8s_audit` (user.username=\"system:anonymous\" OR responseStatus.code IN (401, 403)) "
    "| eval user=coalesce('user.username', \"(unauthenticated)\"), src=mvindex('sourceIPs{}', 0), object=coalesce('objectRef.resource', 'requestURI') "
    "| stats count min(_time) as first_seen max(_time) as last_seen values(object) as objects values(responseStatus.code) as codes values(userAgent) as agents by user src "
    "| where count>=5 OR user=\"system:anonymous\" "
    "| eval host=\"k3s-01\", object=mvjoin(mvindex(objects, 0, 9), \",\"), detail=\"codes=\".mvjoin(codes, \",\").\" user_agent=\".mvjoin(agents, \",\") "
    "| convert ctime(first_seen) ctime(last_seen) ",
    "curl -sk https://k3s-01:6443/api/v1/namespaces/default/secrets (no token)",
    suppress="user,src",
    description="Anonymous API requests, or five or more rejected (401, which has no user: anonymous auth is off on k3s) or denied (403) requests from one identity and source in 15 minutes: API scanning or a misused credential.")

# ---- Vault --------------------------------------------------------------------------------
VAULT_TAIL = (
    "| eval user=coalesce('auth.display_name', \"(no token)\"), src='request.remote_address', object='request.path' "
    "| stats count min(_time) as first_seen max(_time) as last_seen values(request.operation) as ops values(error) as errors values(auth.policies{}) as policies by user src object "
    "| eval host=\"vault\", detail=\"operations=\".mvjoin(ops, \",\").\" policies=\".mvjoin(policies, \",\").if(isnotnull(errors), \" errors=\".mvjoin(errors, \";\"), \"\") "
    "| convert ctime(first_seen) ctime(last_seen) "
)
det("HL - Vault audit device changed",
    "T1685", "Defense Impairment", "critical", "DET0497",
    "`hl_vault_audit` request.path=\"sys/audit/*\" request.operation IN (create, update, delete) "
    + VAULT_TAIL,
    "vault audit enable -path=hl-test file file_path=discard, then vault audit disable hl-test",
    suppress="object",
    description="An audit device was enabled, changed or disabled. Disabling Vault's audit log hides everything after it.")

det("HL - Vault root token used",
    "T1078.001", "Privilege Escalation", "high", "DET0465",
    "`hl_vault_audit` auth.policies{}=root "
    + VAULT_TAIL.replace("by user src object", "by user src").replace("object='request.path'", "object=null()")
    .replace("values(error) as errors", "values(request.path) as paths values(error) as errors")
    .replace('detail="operations="', 'object=mvjoin(mvindex(paths, 0, 9), ","), detail="operations="'),
    "vault token lookup with a root token",
    suppress="user,src", period="1h",
    description="A token with the root policy was used. Root should only be used to unseal-time administration; every use is worth knowing about.")

det("HL - Vault auth method or policy changed",
    "T1556", "Persistence", "high", "DET0104",
    "`hl_vault_audit` request.operation IN (create, update, delete) "
    "(request.path=\"sys/auth/*\" OR request.path=\"sys/policy/*\" OR request.path=\"sys/policies/*\" OR request.path=\"auth/*/role/*\" OR request.path=\"auth/*/config*\") "
    + VAULT_TAIL,
    "vault policy write hl-test - <<<'path \"kv/data/hl-test\" { capabilities = [\"read\"] }', then vault policy delete hl-test",
    suppress="object",
    description="An auth method, auth role or policy was created, changed or deleted.")

det("HL - Vault permission denied burst",
    "T1555.006", "Credential Access", "medium", "DET0130",
    "`hl_vault_audit` error=\"*permission denied*\" "
    "| eval user=coalesce('auth.display_name', \"(no token)\"), src='request.remote_address' "
    "| stats count min(_time) as first_seen max(_time) as last_seen values(request.path) as paths by user src "
    "| where count>=5 "
    "| eval host=\"vault\", object=mvjoin(mvindex(paths, 0, 9), \",\"), detail=\"denied requests: \".count "
    "| convert ctime(first_seen) ctime(last_seen) ",
    "six reads of a secret with a token that lacks the policy",
    suppress="user,src",
    description="Five or more permission-denied responses for one identity and source in 15 minutes: a token probing for secrets it can't read.")

det("HL - Vault secret read by a new identity",
    "T1555.006", "Credential Access", "medium", "DET0130",
    "`hl_vault_audit` " + since_baseline("hl_baseline_vault_reader", "-14d@d")
    + "latest=-5m@m request.operation=read request.path=\"kv/data/*\" "
    "| eval user='auth.display_name', object='request.path', src='request.remote_address' "
    "| stats count min(_time) as first_seen max(_time) as last_seen latest(src) as src by user object "
    "| where first_seen>=relative_time(now(), \"-20m@m\") "
    "| lookup hl_baseline_vault_reader user object OUTPUT last_seen as baseline_last_seen baseline_end "
    + in_baseline("-14d@d") + "| where in_baseline=0 "
    "| eval host=\"vault\", detail=\"first read of this secret by this identity in 14 days\" "
    "| convert ctime(first_seen) ctime(last_seen) ",
    "read a kv secret with an identity that never read it before",
    suppress="user,object", period="24h",
    description="An identity read a KV secret it had not read in the previous 14 days (ESO and AppRole clients read the same paths on a loop, so a new pair stands out).")

baseline("HL - Baseline: Vault secret readers", "hl_baseline_vault_reader", ["user", "object"],
         "`hl_vault_audit` request.operation=read request.path=\"kv/data/*\" "
         "| eval user='auth.display_name', object='request.path' "
         "| stats max(_time) as last_seen by user object ",
         "-14d@d", "23 0 * * *")

# ---- Hosts: logins and commands --------------------------------------------------------
det("HL - SSH login from a new source or to a new host",
    "T1021.004", "Lateral Movement", "medium", "DET0596",
    "index=infra sourcetype=linux_secure eventtype=homelab_tailscale_ssh_login "
    + since_baseline("hl_baseline_ssh_login", "-30d@d") + "latest=-5m@m "
    "| eval who=coalesce(ts_user, user) "
    "| stats count min(_time) as first_seen max(_time) as last_seen by who dest src user "
    "| where first_seen>=relative_time(now(), \"-20m@m\") "
    "| lookup hl_baseline_ssh_login who dest src user OUTPUT last_seen as baseline_last_seen baseline_end "
    + in_baseline("-30d@d") + "| where in_baseline=0 "
    "| eval host=dest, object=\"local user \".user, user=who, detail=\"first Tailscale SSH login for this identity, host and source in 30 days\" "
    "| convert ctime(first_seen) ctime(last_seen) ",
    "Tailscale SSH to a host you haven't logged in to for 30 days (or from a new device)",
    suppress="user,host,src", period="24h",
    description="A Tailscale SSH login whose identity, target host and source device were not seen together in the previous 30 days.")

baseline("HL - Baseline: Tailscale SSH logins", "hl_baseline_ssh_login", ["who", "dest", "src", "user"],
         "index=infra sourcetype=linux_secure eventtype=homelab_tailscale_ssh_login "
         "| eval who=coalesce(ts_user, user) "
         "| stats max(_time) as last_seen by who dest src user ",
         "-30d@d", "26 0 * * *")

det("HL - Proxmox login failures",
    "T1110.001", "Credential Access", "high", "DET0551",
    "index=infra (pvedaemon OR pveproxy) \"authentication failure\" "
    "| rex \"rhost=(?<rhost>\\S+)\" | rex \"user=(?<pve_user>\\S+)\" "
    "| eval src=coalesce(rhost, src), user=coalesce(pve_user, user) "
    "| stats count min(_time) as first_seen max(_time) as last_seen values(host) as hosts by src user "
    "| eval host=mvjoin(hosts, \",\"), object=\"proxmox login\", detail=count.\" failed logins\" "
    "| convert ctime(first_seen) ctime(last_seen) ",
    "curl -sk https://proxmox3:8006/api2/json/access/ticket -d username=root@pam -d password=wrong",
    suppress="src,user", period="1h",
    description="Failed logins to the Proxmox web UI or API. Nothing logs in with a password routinely (Terraform and Packer use API tokens), so one failure is reported.")

SENSITIVE = (
    "(?i)(systemctl\\s+(stop|disable|mask)\\s+\\S*(falco|otelcol|tailscaled|auditd|cribl|splunk)"
    "|authorized_keys|crontab\\s+(-e|-r|\\S+\\.\\w+)|(sed\\s+-i|tee|>|vi|vim|nano|cp|mv)\\s*[^;|]*/etc/(pam\\.d|sudoers|ssh/sshd_config)"
    "|\\b(iptables|nft|ufw)\\s+(-F|flush|disable|-X)|history\\s+-c|journalctl\\s+--(vacuum|rotate)"
    "|(truncate|shred|rm)\\s+[^;|]*?/var/log|unset\\s+HISTFILE|useradd|usermod|userdel|passwd\\s|chpasswd"
    "|curl[^|;]*\\|\\s*(ba)?sh|wget[^|;]*\\|\\s*(ba)?sh|base64\\s+-d[^|;]*\\|\\s*(ba)?sh|nc\\s+-[a-z]*e\\s)"
)
det("HL - Sensitive command run over SSH or sudo",
    "T1685", "Defense Impairment", "medium", "DET0497",
    "index=infra sourcetype=linux_secure (eventtype=homelab_tailscale_ssh_command OR app=sudo) "
    "| eval cmd=coalesce(command, process) "
    f"| regex cmd=\"{SENSITIVE}\" "
    # A local sudo has no src, and stats drops rows whose by-fields are empty: until
    # 2026-11-04 this only ever fired for commands over Tailscale SSH (found by
    # infrastructure_tooling/art_validation).
    "| eval user=coalesce(ts_user, user), src=coalesce(src, \"local\") "
    "| stats count min(_time) as first_seen max(_time) as last_seen values(cmd) as cmds by host user src "
    "| eval object=\"command\", detail=mvjoin(mvindex(cmds, 0, 4), \" || \") "
    "| convert ctime(first_seen) ctime(last_seen) ",
    "ssh root@<test-vm> 'crontab -l; journalctl --rotate'",
    suppress="host,user", period="1h",
    description="A command that stops security tooling, touches authorized_keys or cron, writes to auth config (PAM, sudoers, sshd_config; reading them is not flagged), changes accounts, flushes the firewall, clears logs or history, or pipes a download into a shell. Covers T1685 (and T1685.006 log clearing), T1053.003, T1098.004, T1136.001, T1059.004. Your own admin work will match; tune by adding exclusions, not by widening the window.")

# ---- UniFi --------------------------------------------------------------------------------
det("HL - UniFi admin login from a new source",
    "T1078.001", "Initial Access", "medium", "DET0465",
    "index=audit sourcetype=ubiquiti:unifi:ocsf class_uid=3002 earliest=-30d@d latest=-5m@m "
    "| eval src=coalesce('src_endpoint.ip', src, \"unknown\"), user=coalesce('user.name', 'actor.user.name', user, \"unknown\") "
    # Only a failure inside the current window counts; over the whole baseline, one
    # failed login re-alerted every day for 30 days.
    "| eval recent_fail=if(_time>=relative_time(now(), \"-20m@m\") AND match(status, \"(?i)fail\"), 1, 0) "
    "| stats count min(_time) as first_seen max(_time) as last_seen values(status) as status max(recent_fail) as recent_fail by user src "
    "| where first_seen>=relative_time(now(), \"-20m@m\") OR recent_fail=1 "
    "| eval host=\"unifi\", object=\"UniFi Network\", detail=\"status=\".mvjoin(status, \",\").\" (first login from this source in 30 days, or a failure)\" "
    "| convert ctime(first_seen) ctime(last_seen) ",
    "log in to the UniFi console from a device that hasn't been used in 30 days, or with a wrong password",
    suppress="user,src", period="24h",
    description="A UniFi admin login from a source address not seen in 30 days, or any failed login.")

det("HL - UniFi configuration changed",
    "T1686", "Defense Impairment", "medium", "DET0306",
    "index=audit sourcetype=ubiquiti:unifi:ocsf class_uid=5019 "
    # The cc-unifi-ocsf pack puts a config change's source address in unmapped.src_ip.
    "| eval user=coalesce('actor.user.name', 'user.name', user, \"unknown\"), src=coalesce('unmapped.src_ip', 'src_endpoint.ip', src, \"unknown\"), object=coalesce('device.name', 'device.hostname', \"unifi\") "
    "| stats count min(_time) as first_seen max(_time) as last_seen values(message) as messages by user src object "
    "| eval host=\"unifi\", detail=mvjoin(mvindex(messages, 0, 4), \" || \") "
    "| convert ctime(first_seen) ctime(last_seen) ",
    "create, then delete, a disabled dummy firewall rule in UniFi",
    suppress="object", period="1h",
    description="A UniFi configuration change: firewall policies, logging/SIEM settings, CyberSecure, networks. About one a day normally.")

# Isolated networks are blocked from the other Internal networks by UniFi's built-in
# "Isolated Networks" policy, which can't log. The custom policy "Log isolated network
# block" (same match, new connections only, Syslog Logging on) sits ahead of it and logs
# each attempt as CEF 203 "Blocked by Firewall". Needs the Gateway and Firewall Default
# Policy categories in UniFi's Activity Logging.
det("HL - Isolated network device tried to reach a trusted network",
    "T1046", "Discovery", "medium", "",
    'index=network sourcetype=ubiquiti:unifi:ocsf class_uid=4001 disposition=Blocked firewall_rule.name="Log isolated network block" '
    '| eval target=\'dst_endpoint.ip\'.if(isnotnull(\'dst_endpoint.port\'), ":".\'dst_endpoint.port\', "") '
    "| stats count min(_time) as first_seen max(_time) as last_seen values(target) as targets "
    "values(src_endpoint.hostname) as hostnames values(src_endpoint.name) as names values(src_endpoint.mac) as macs by src_endpoint.ip "
    "| rename src_endpoint.ip as src "
    '| eval host=coalesce(mvindex(hostnames, 0), src), user="", object=mvjoin(mvindex(targets, 0, 4), ", "), '
    'detail="blocked ".count." connection attempt(s) to ".mvcount(targets)." target(s): ".mvjoin(mvindex(targets, 0, 9), ", ")'
    '.if(isnotnull(names), "; device ".mvjoin(names, ", "), "").if(isnotnull(macs), " (".mvjoin(macs, ", ").")", "") '
    "| convert ctime(first_seen) ctime(last_seen) ",
    "from a device on an isolated network, try to open a port on a host in another Internal network (nc -vz <host> <port>); it is blocked",
    suppress="src", period="1h",
    description="A device on an isolated network tried to open a connection to another Internal network and the firewall blocked it. Isolated devices have no reason to do this, so any attempt is worth a look; a scan or repeated tries suggests the device is compromised.")

# ---- Authentik and Splunk Web logins ------------------------------------------------------
AK_TAIL = (
    '| fillnull value="unknown" user src '
    '| stats count min(_time) as first_seen max(_time) as last_seen values(stage) as stages values(action) as actions by user src '
    '| convert ctime(first_seen) ctime(last_seen) '
)
det("HL - Authentik login failures",
    "T1110.001", "Credential Access", "medium", "DET0551",
    "`hl_authentik_event` | search ak_action=login_failed "
    "| eval user=coalesce(ak_login_user, ak_user), src=ak_src, stage=coalesce(ak_stage, \"unknown\"), action=ak_action "
    "| where NOT match(stage, \"(?i)mfa|otp|webauthn\") "
    + AK_TAIL +
    "| where count>=3 "
    "| eval detail=count.\" failed logins, stage=\".mvjoin(stages, \",\"), host=\"authentik\", object=\"auth.bmosan.com\" ",
    "enter a wrong password three times at https://auth.bmosan.com",
    suppress="user,src", period="1h",
    description="Three or more failed logins (wrong user name or password) for one user from one source within 15 minutes. Authentik guards Splunk Web and the Cribl UI.")

det("HL - Authentik MFA failed after a correct password",
    "T1078", "Initial Access", "high", "DET0927",
    "`hl_authentik_event` | search ak_action=login_failed "
    "| eval user=coalesce(ak_login_user, ak_user), src=ak_src, stage=coalesce(ak_stage, \"unknown\"), action=ak_action "
    "| where match(stage, \"(?i)mfa|otp|webauthn\") "
    + AK_TAIL +
    "| eval detail=count.\" failed second-factor checks, stage=\".mvjoin(stages, \",\").\". The password was accepted.\", host=\"authentik\", object=\"auth.bmosan.com\" ",
    "log in at https://auth.bmosan.com with the right password and a wrong TOTP code",
    suppress="user,src", period="1h",
    description="The password was right but the second factor failed: someone may have the password.")

det("HL - Authentik break-glass admin or impersonation",
    "T1078.001", "Initial Access", "high", "DET0465",
    "`hl_authentik_event` | search (ak_action=login ak_user=akadmin) OR ak_action=impersonation_started "
    "| eval user=ak_user, src=ak_src, stage=null(), action=ak_action "
    + AK_TAIL +
    "| eval detail=\"actions=\".mvjoin(actions, \",\").\" (akadmin is deactivated; a login means a recovery key was used)\", host=\"authentik\", object=\"auth.bmosan.com\" ",
    "kubectl -n authentik exec deploy/authentik-worker -- ak create_recovery_key 1 akadmin, open the link, then deactivate akadmin again",
    suppress="user,src", period="1h",
    description="The built-in akadmin account logged in (it is deactivated, so only through a recovery key), or an admin started impersonating a user.")

det("HL - Splunk local login",
    "T1078.001", "Initial Access", "high", "DET0465",
    # The CIM add-on rewrites action to success/failure; the raw text keeps it.
    # NOT search_id=*: _audit also logs every search with its text, and this search's
    # own text contains the terms (and src=="127.0.0.1"), so it matched itself.
    "index=_audit \"action=login attempt\" method=Splunk NOT search_id=* "
    "| where NOT (src==\"127.0.0.1\" OR src==\"192.168.4.11\" OR cidrmatch(\"172.16.0.0/12\", src)) "
    "| stats count min(_time) as first_seen max(_time) as last_seen values(info) as results by user src "
    "| eval host=\"security-tooling-01\", object=\"Splunk Web\", detail=\"results=\".mvjoin(results, \",\").\" (a local Splunk user, which skips Authentik and its second factor)\" "
    "| convert ctime(first_seen) ctime(last_seen) ",
    "log in at https://splunk.bmosan.com/en-US/account/login?loginType=splunk as admin",
    suppress="user,src", period="1h",
    description="Someone logged in to Splunk Web as a local user (the admin break-glass) instead of through Authentik. Logins from the host itself (deploy scripts, docker exec) are excluded.")

# ---- MCP gateway ---------------------------------------------------------------------------
det("HL - MCP gateway write tool, denial or new client",
    "T1078", "Initial Access", "medium", "DET0927",
    "index=audit sourcetype=toolhive:vmcp:audit " + since_baseline("hl_baseline_mcp_client", "-30d@d")
    + "latest=-5m@m "
    "| eval user=coalesce('subjects.user_id', \"unknown\"), tool='target.name', src=coalesce('source.value', \"unknown\") "
    "| eventstats min(_time) as user_first by user "
    "| where _time>=relative_time(now(), \"-20m@m\") "
    # A client in the baseline was seen before the window: any user_first before it will do.
    "| lookup hl_baseline_mcp_client user OUTPUT last_seen as baseline_last_seen baseline_end "
    + in_baseline("-30d@d") + "| eval user_first=if(in_baseline=1, baseline_last_seen, user_first) "
    "| eval reason=case(outcome!=\"success\", \"outcome=\".outcome, "
    "match(tool, \"(?i)(set|update|delete|create|add|remove|edit|block|unblock|reboot|toggle|apply|restart|upgrade|authorize)\"), \"write-capable tool\", "
    "user_first>=relative_time(now(), \"-20m@m\"), \"first call from this client in 30 days\") "
    "| where isnotnull(reason) "
    "| stats count min(_time) as first_seen max(_time) as last_seen values(tool) as tools values(reason) as reasons by user src "
    "| eval host=\"k3s-01\", object=mvjoin(mvindex(tools, 0, 9), \",\"), detail=mvjoin(reasons, \"; \") "
    "| convert ctime(first_seen) ctime(last_seen) ",
    "call a tool through the gateway with a token whose policy denies it",
    suppress="user,object", period="1h",
    description="Through the MCP gateway: a call that was denied or failed authorization, a write-capable tool (pipeline, notebook, firewall or device changes), or the first call from a client identity in 30 days. ATT&CK v20 has no detection strategy for agent tool abuse; DET0927 (AI-enabled exfiltration, SaaS analytic) is the closest.")

baseline("HL - Baseline: MCP gateway clients", "hl_baseline_mcp_client", ["user"],
         "index=audit sourcetype=toolhive:vmcp:audit "
         "| eval user=coalesce('subjects.user_id', \"unknown\") "
         "| stats max(_time) as last_seen by user ",
         "-30d@d", "21 0 * * *")

# ---- Telemetry health -------------------------------------------------------------------------
det("HL - Host stopped sending telemetry",
    "T1685", "Defense Impairment", "high", "DET0497",
    "| mstats latest_time(system.uptime) as last_seen earliest_time(system.uptime) as first_seen where index=metrics earliest=-24h latest=now by host "
    "| where last_seen<relative_time(now(), \"-15m\") "
    "| eval user=\"\", src=\"\", object=\"otel agent\", count=round((now()-last_seen)/60), detail=\"no host metrics for \".count.\" minutes\" "
    "| convert ctime(first_seen) ctime(last_seen) ",
    "systemctl stop otelcol-contrib on a test host for 20 minutes",
    suppress="host", period="6h", window=("-24h", "now"),
    description="A host that sent metrics in the last 24 hours has sent none for 15 minutes: powered off, collector stopped, or network cut. An attacker stopping telemetry looks the same as an outage, so both are reported.")


# Silent sources. A feed that normally never pauses for long has stopped while its host
# is still up: collector, Cribl route or the product's own logging broken or turned off.
# Limits are a little over the longest normal gap seen in a week (2026-11-03): journald
# on the Proxmox nodes went 48 minutes quiet, on the VMs 10, the UniFi gateway nearly
# 5 hours. A journald gap on a host that also stopped sending metrics is left to
# "Host stopped sending telemetry".
det("HL - Log source stopped sending",
    "T1685", "Defense Impairment", "high", "DET0497",
    '| tstats max(_time) as last_seen where (index=audit sourcetype IN ("kube:apiserver:audit", "hashicorp_vault_audit_log")) '
    'OR (index=k8s sourcetype="kube:container") OR (index=infra sourcetype="linux:journald") '
    'OR (index=network sourcetype IN ("ubiquiti:unifi:ocsf", "adguard:querylog")) by index sourcetype host '
    "| join type=left host [| mstats count(system.uptime) as host_metrics where index=metrics earliest=-15m by host] "
    '| eval limit=case(sourcetype=="ubiquiti:unifi:ocsf", 480, sourcetype=="linux:journald" AND match(host, "^proxmox"), 60, '
    'sourcetype=="linux:journald", 30, sourcetype=="adguard:querylog", 30, true(), 15) '
    "| eval count=round((now()-last_seen)/60) "
    '| where count>limit AND NOT (sourcetype=="linux:journald" AND isnull(host_metrics)) '
    '| eval object=case(sourcetype=="kube:apiserver:audit", "k3s API audit log", sourcetype=="hashicorp_vault_audit_log", "Vault audit log", '
    'sourcetype=="kube:container", "k3s container logs", sourcetype=="linux:journald", "journald", '
    'sourcetype=="ubiquiti:unifi:ocsf", "UniFi gateway syslog", sourcetype=="adguard:querylog", "AdGuard DNS query log (clients may have fallen back to the gateway)", true(), sourcetype), '
    'user="", src="", detail=object." (index=".index." sourcetype=".sourcetype.") has sent nothing for ".count." minutes (limit ".limit.")" '
    "| eval first_seen=last_seen | convert ctime(first_seen) ctime(last_seen) ",
    "stop the Vault audit device (vault audit disable) or Cribl's route for a source for 20 minutes",
    suppress="host,object", period="6h", window=("-24h", "now"),
    description="A log feed that normally never goes quiet for long (k3s API audit, Vault audit, k3s container logs, each host's journald, the UniFi gateway's syslog, the AdGuard DNS query log) has sent nothing past its limit while its host is still up. Turning audit logging off looks like this, and so does a broken pipeline.")

det("HL - Detection search failing",
    "T1685", "Defense Impairment", "high", "DET0497",
    "index=_internal sourcetype=scheduler log_level=ERROR component=SavedSplunker savedsearch_id=\"*;85_detections;HL - *\" "
    "| rex field=savedsearch_id \";85_detections;(?<object>HL - .+)$\" "
    "| rex field=_raw \"message=\\\"(?<error>[^\\\"]+)\" "
    "| stats count min(_time) as first_seen max(_time) as last_seen latest(error) as error by object "
    "| eval host=\"security-tooling-01\", user=\"\", src=\"\", detail=count.\" failed runs: \".error "
    "| convert ctime(first_seen) ctime(last_seen) ",
    "add a syntax error to a copy of an HL - search, or check index=_internal sourcetype=scheduler log_level=ERROR",
    suppress="object", period="6h",
    description="An HL - detection errored when the scheduler ran it, so it isn't detecting anything. Found after the Authentik detections failed every run for their first hour without anyone knowing.")

# Falco is quiet when nothing happens, so its alerts can't show it is running. The OTel
# host agent reports the falco process's uptime every minute (hostmetrics process
# scraper, otel-agent/base.yaml). k3s-01's DaemonSet pod counts too: the agent sees the
# node's processes.
det("HL - Falco not running",
    "T1685", "Defense Impairment", "high", "DET0497",
    '| mstats latest_time(process.uptime) as last_seen earliest_time(process.uptime) as first_seen where index=metrics process.executable.name="falco" earliest=-24h latest=now by host '
    "| join type=left host [| mstats count(system.uptime) as host_metrics where index=metrics earliest=-10m by host] "
    '| where last_seen<relative_time(now(), "-10m") AND host_metrics>0 '
    '| eval user="", src="", object="falco", count=round((now()-last_seen)/60), detail="falco not running for ".count." minutes; the host is still sending metrics" '
    "| convert ctime(first_seen) ctime(last_seen) ",
    "systemctl stop falco-modern-bpf on a test VM for 15 minutes",
    suppress="host", period="6h", window=("-24h", "now"),
    description="Falco ran on this host in the last 24 hours but has not for 10 minutes, while the host itself is still reporting. Someone stopping runtime detection looks like this, and so does a crash.")

# ---- write savedsearches.conf ----------------------------------------------------------------
def conf_escape(s):
    return s.replace("\n", " ")


# Settings every detection shares. They are written into each stanza, not a
# [default] stanza: a [default] in an app's savedsearches.conf applies to every
# saved search on the instance, in every app.
COMMON = [
    "enableSched = 1",
    "counttype = number of events",
    "relation = greater than",
    "quantity = 0",
    "alert.track = 1",
    "alert.digest_mode = 0",
    "alert.suppress = 1",
    "action.logevent = 1",
    "action.logevent.param.index = security",
    "action.logevent.param.sourcetype = homelab:detection",
    "action.logevent.param.source = $name$",
    "action.logevent.param.host = $result.host$",
    "action.logevent.param.event = $result.finding$",
]

out = [
    "# Generated by build_detections.py; edit that, not this file.",
    "# Every stanza is a MITRE ATT&CK v20 detection. Findings go to index=security",
    "# (sourcetype homelab:detection), which the agents read.",
    "",
]
for d in D:
    search = d["search"].strip() + (
        f' | `hl_finding("{d["name"]}", "{d["technique"]}", "{d["tactic"]}", "{d["severity"]}", "{d["strategy"]}")`'
    )
    out += [
        f'[{d["name"]}]',
        f'description = ATT&CK v20 {d["technique"]} ({d["tactic"]}), strategy {d["strategy"] or "none"}. {d["description"]} Test: {d["test"]}',
        f"search = {conf_escape(search)}",
        f'dispatch.earliest_time = {d["window"][0]}',
        f'dispatch.latest_time = {d["window"][1]}',
        f'cron_schedule = {d["cron"] or cron_for(d["name"])}',
        f'alert.severity = {SEVERITY[d["severity"]]}',
        f'alert.suppress.fields = {d["suppress"]}',
        f'alert.suppress.period = {d["period"]}',
        *COMMON,
        "",
    ]
# The baseline rebuilds: not detections (no alert), but named HL - so that
# "HL - Detection search failing" reports them when they error.
collections = ["# Generated by build_detections.py; edit that, not this file.",
               "# KV store baselines for the \"first seen\" HL - detections.", ""]
transforms = list(collections)
for b in B:
    out += [
        f'[{b["name"]}]',
        f'description = Rebuilds the {b["lookup"]} KV store lookup once a day: the last time each '
        f'{", ".join(b["keys"])} was seen in [{b["span"]}, @d). The "first seen" detection reads it instead '
        'of re-reading the whole span every 15 minutes, and reads raw events itself when it is empty or stale.',
        f'search = {conf_escape(b["search"].strip())} | eval baseline_end=relative_time(now(), "@d") '
        f'| outputlookup {b["lookup"]}',
        f'dispatch.earliest_time = {b["span"]}',
        "dispatch.latest_time = @d",
        f'cron_schedule = {b["cron"]}',
        "enableSched = 1",
        "",
    ]
    collections += [f'[{b["lookup"]}]', "field.last_seen = number", "field.baseline_end = number", ""]
    transforms += [f'[{b["lookup"]}]', "external_type = kvstore", f'collection = {b["lookup"]}',
                   f'fields_list = {", ".join(b["keys"] + ["last_seen", "baseline_end"])}', ""]
here = pathlib.Path(__file__).parent
for name, lines in (("savedsearches", out), ("collections", collections), ("transforms", transforms)):
    (here / f"default/{name}.conf").write_text("\n".join(lines))
print(f"wrote default/savedsearches.conf ({len(D)} detections, {len(B)} baselines), collections.conf, transforms.conf")
