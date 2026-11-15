#!/usr/bin/env python3
"""Generate default/savedsearches.conf for the HL - detections. Edit here, run
`python3 build_detections.py`, commit both files.

Every detection is mapped to MITRE ATT&CK v20 (released 2026-10-13): technique,
tactic and, where MITRE has one for this kind of data, the detection strategy
(DET####) the logic follows. Attacks on the agents themselves have no ATT&CK
technique; those use MITRE ATLAS v5.6.0 (technique AML.T####), and hl_finding sets
attack_version to atlas-5.6.0 for them. Each search ends in `hl_finding(...)` (macros.conf),
so every finding has the same fields. Firing alerts:
  - write one event per finding to index=security, sourcetype=homelab:detection
    (Log Event action), which is what the security agent reads. The agents post
    to Slack. The homelab_slack action (bin/homelab_slack.py) is installed but
    not attached to any search; add `action.homelab_slack = 1` to send directly.

Windows: most run every 15 minutes over [-20m@m, -5m@m), contiguous and late enough
for indexing lag. "First seen" detections search their own longer baseline. Each
detection runs at its own minute in the quarter hour (cron_for) so they don't all
queue at once.
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
    "| where NOT `hl_k8s_platform_user` AND NOT `hl_k8s_expected_secret_read` "
    + K8S_TAIL,
    "kubectl -n <scratch> get secrets",
    description="Secrets were read by a person (system:admin is the root kubeconfig on k3s-01) or an application service account. Platform controllers' reads are not audited. Exempt (hl_k8s_expected_secret_read): Trivy Operator in its own namespace and getting *-ghcr-pull image-pull secrets by name, and the ARC kube-mode runner listing its own job secrets by label.")

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
    "`hl_vault_audit` earliest=-14d@d latest=-5m@m request.operation=read request.path=\"kv/data/*\" "
    "| eval user='auth.display_name', object='request.path', src='request.remote_address' "
    "| stats count min(_time) as first_seen max(_time) as last_seen latest(src) as src by user object "
    "| where first_seen>=relative_time(now(), \"-20m@m\") "
    "| eval host=\"vault\", detail=\"first read of this secret by this identity in 14 days\" "
    "| convert ctime(first_seen) ctime(last_seen) ",
    "read a kv secret with an identity that never read it before",
    suppress="user,object", period="24h",
    description="An identity read a KV secret it had not read in the previous 14 days (ESO and AppRole clients read the same paths on a loop, so a new pair stands out).")

# ---- Decoys (honeytokens) --------------------------------------------------------------
# A Kubernetes secret and a Vault secret that nothing uses: any request for them is
# someone exploring. Their names must never be in this public repo. The macros
# hl_decoy_k8s_secret and hl_decoy_vault_path match nothing here; the real predicates
# are set by hand in etc/apps/85_detections/local/macros.conf on the Splunk host,
# which deploys never touch (README, Detections). The decoy secret lives alone in its
# own namespace, because a list carries no objectRef.name: the predicate matches the
# namespace. No exemptions, any verb, any response code: a denied attempt counts.
det("HL - Decoy Kubernetes secret touched",
    "T1552.007", "Credential Access", "critical", "DET0198",
    "`hl_k8s_audit` objectRef.resource=secrets "
    "| where `hl_decoy_k8s_secret` "
    + K8S_TAIL.replace("user='user.username'", "user=coalesce('user.username', \"(unauthenticated)\")"),
    "kubectl get secrets in the decoy namespace (set in the hl_decoy_k8s_secret override on the Splunk host); a denied attempt fires too",
    period="1h",
    description="Any API request, allowed or denied, on secrets in the decoy namespace. Nothing uses the decoy, so a request means someone is looking for credentials. Platform controllers' requests are not audited.")

det("HL - Decoy Vault secret touched",
    "T1555.006", "Credential Access", "critical", "DET0130",
    "`hl_vault_audit` "
    "| where `hl_decoy_vault_path` "
    + VAULT_TAIL,
    "vault kv get the decoy path (set in the hl_decoy_vault_path override on the Splunk host); permission denied fires too",
    period="1h",
    description="Any Vault request, including permission denied, on the decoy path. Nothing uses the decoy, so a request means someone is looking for secrets.")

# ---- Hosts: logins and commands --------------------------------------------------------
det("HL - SSH login from a new source or to a new host",
    "T1021.004", "Lateral Movement", "medium", "DET0596",
    "index=infra sourcetype=linux_secure eventtype=homelab_tailscale_ssh_login earliest=-30d@d latest=-5m@m "
    "| eval who=coalesce(ts_user, user) "
    "| stats count min(_time) as first_seen max(_time) as last_seen by who dest src user "
    "| where first_seen>=relative_time(now(), \"-20m@m\") "
    "| eval host=dest, object=\"local user \".user, user=who, detail=\"first Tailscale SSH login for this identity, host and source in 30 days\" "
    "| convert ctime(first_seen) ctime(last_seen) ",
    "Tailscale SSH to a host you haven't logged in to for 30 days (or from a new device)",
    suppress="user,host,src", period="24h",
    description="A Tailscale SSH login whose identity, target host and source device were not seen together in the previous 30 days.")

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

# Packer (.github/workflows/proxmox-packer-builder.yml, token root@pam!packer) deletes
# and rebuilds its templates every week: VM ids 9000, 9001 and 9005 (server) and 9004
# and 9006 (desktop), plus the templates' defaults 9002 and 9003 for a local build.
# Only that token on an id in 9000-9006 is exempt. terraform-apply uses the same token,
# so a VM Terraform replaces is reported, as intended. pvedaemon logs each task as
# "<user> starting task UPID:node:pid:pstart:starttime:type:id:user:" and again at
# "end task"; the start line is used, one finding per task.
det("HL - Proxmox guest destroyed",
    "T1485", "Impact", "high", "",
    "index=infra sourcetype=linux:journald host=proxmox* \"starting task\" (qmdestroy OR vzdestroy) "
    "| rex \"<(?<pve_user>[^>]+)> starting task (?<upid>UPID:[^:]+:(?:[^:]*:){3}(?<task>qmdestroy|vzdestroy):(?<vmid>\\d+):[^:\\s]*:)\" "
    "| where isnotnull(task) AND NOT (pve_user==\"root@pam!packer\" AND tonumber(vmid)>=9000 AND tonumber(vmid)<=9006) "
    "| stats count min(_time) as first_seen max(_time) as last_seen by host pve_user task vmid upid "
    "| eval user=pve_user, src=\"\", object=if(task==\"vzdestroy\", \"CT \", \"VM \").vmid, detail=task.\" of \".object.\" by \".pve_user.\" (task \".upid.\")\" "
    "| convert ctime(first_seen) ctime(last_seen) ",
    "in the Proxmox web UI, create an empty VM with no disk (id 9990, hl-test) and remove it",
    suppress="host,object,detail", period="1h",
    description="A VM (qmdestroy) or container (vzdestroy) was deleted. Packer's token deleting its own template ids (9000-9006) before a rebuild is exempt; any other user or token, or Packer on any other id, is reported. Proxmox user, token and permission changes are not logged, so they are not covered here.")

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

# ---- DNS (AdGuard Home query log) ---------------------------------------------------------
# AdGuard only logs (filtering, Safe Browsing and parental control are off, no
# blocklists), so there are no blocked queries to alert on. QH, QT, IP and Answer are
# search-time JSON fields, not indexed: the searches stay on a 15-minute window, a few
# hundred events. Clients append the tailnet search domain, so github.com is also asked
# as github.com.tail<id>.ts.net; that suffix is stripped. domain is the registered
# domain: the last two labels, or three under a two-letter country code's
# co/com/net/org/gov/edu/ac. A single-label name has none and is skipped.
DNS_PREP = (
    "index=network sourcetype=adguard:querylog "
    '| eval qname=replace(replace(lower(QH), "\\.$", ""), "\\.tail[0-9a-f]+\\.ts\\.net$", "") '
    '| rex field=qname "(?<domain>[^.]+\\.(?:(?:co|com|net|org|gov|edu|ac)\\.[a-z]{2}|[^.]+))$" '
    '| eval subdomain=if(len(qname)>len(domain), substr(qname, 1, len(qname)-len(domain)-1), ""), src=coalesce(IP, "unknown") '
    "| where isnotnull(domain) AND NOT `hl_dns_chatty_domain` "
)

# 666 distinct names in a week from 43 clients (2026-11): a small, stable set, so 20
# distinct 30+ character subdomains of one domain from one client in 15 minutes, or 10
# distinct TXT/NULL names, is far outside normal. Tunnels (iodine, dnscat2, DNS exfil)
# must make every name unique to get past caches, which is what this counts.
det("HL - DNS possible tunnelling from a client",
    "T1071.004", "Command and Control", "high", "DET0400",
    DNS_PREP +
    "| eval long_name=if(len(subdomain)>=30, qname, null()), txt_name=if(in(QT, \"TXT\", \"NULL\"), qname, null()) "
    "| stats count dc(long_name) as long_names dc(txt_name) as txt_names values(QT) as types values(long_name) as samples "
    "min(_time) as first_seen max(_time) as last_seen by src domain "
    "| where long_names>=20 OR txt_names>=10 "
    "| eval host=\"adguard\", user=\"\", object=domain, detail=long_names.\" distinct long names and \".txt_names.\" distinct TXT/NULL names under \".domain"
    ".\" in \".count.\" queries; types=\".mvjoin(types, \",\").if(isnotnull(samples), \"; e.g. \".mvjoin(mvindex(samples, 0, 2), \" \"), \"\") "
    "| convert ctime(first_seen) ctime(last_seen) ",
    "from a client that uses AdGuard, look up 25 different random 40-character names under a domain you own (they fail, which is fine)",
    suppress="src,object", period="12h",
    description="One client asked for many distinct long subdomains of one domain, or many distinct TXT/NULL names, in 15 minutes: the shape of DNS tunnelling or exfiltration. A legitimate chatty domain (reputation lookups, DNSBLs) goes in the hl_dns_chatty_domain macro; reverse lookups (.arpa) are already there.")

# NXDOMAIN read from the answer itself: Answer is the base64 DNS reply, and RCODE is the
# low 4 bits of byte 3, i.e. ((5th base64 char & 3) << 2) | (6th char >> 4). RCODE 3
# means the 5th char is A E I M Q U Y c g k o s w 0 4 8 and the 6th is w-z 0-9 + /.
# Names that only failed because a search domain was appended are left out.
det("HL - DNS lookups failing for many domains from a client",
    "T1568.002", "Command and Control", "medium", "DET0419",
    DNS_PREP +
    "| where match(Answer, \"^.{4}[AEIMQUYcgkosw048][w-z0-9+/]\") AND NOT match(lower(QH), \"\\.tail[0-9a-f]+\\.ts\\.net\\.?$\") "
    "| stats count dc(domain) as domains values(domain) as domain_list min(_time) as first_seen max(_time) as last_seen by src "
    "| where domains>=15 "
    "| eval host=\"adguard\", user=\"\", object=mvjoin(mvindex(domain_list, 0, 4), \",\"), "
    "detail=domains.\" registered domains returned NXDOMAIN in \".count.\" queries: \".mvjoin(mvindex(domain_list, 0, 9), \" \") "
    "| convert ctime(first_seen) ctime(last_seen) ",
    "from a client that uses AdGuard, look up 20 random names under 20 different nonexistent .com domains",
    suppress="src", period="12h",
    description="One client got NXDOMAIN for 15 or more different registered domains in 15 minutes: malware trying domain-generation-algorithm (DGA) names until one resolves. Lookups that only failed because the tailnet search domain was appended, and single-label names, are not counted.")

# AdGuard answers ANY (refuse_any: false) and has no rate limit. Nothing here does zone
# transfers or ANY lookups routinely.
det("HL - DNS zone transfer or ANY query",
    "T1018", "Discovery", "medium", "DET0574",
    "index=network sourcetype=adguard:querylog (QT=AXFR OR QT=IXFR OR QT=ANY) "
    '| eval qname=replace(replace(lower(QH), "\\.$", ""), "\\.tail[0-9a-f]+\\.ts\\.net$", ""), src=coalesce(IP, "unknown") '
    "| stats count values(QT) as types values(qname) as names min(_time) as first_seen max(_time) as last_seen by src "
    "| eval host=\"adguard\", user=\"\", object=mvjoin(mvindex(names, 0, 4), \",\"), "
    "detail=count.\" queries of type \".mvjoin(types, \",\").\" for \".mvjoin(mvindex(names, 0, 9), \" \") "
    "| convert ctime(first_seen) ctime(last_seen) ",
    "from a client that uses AdGuard: dig AXFR example.com (it fails; the query is logged)",
    suppress="src", period="24h",
    description="A client asked AdGuard for a zone transfer (AXFR/IXFR) or an ANY record: listing a zone's names, or probing the resolver for amplification. Ordinary software asks for specific record types.")

# ---- Watch lists and threat indicators (lookups/) ----------------------------------------
# hl_dns_watch_domains.csv (domain,category,note): services with a DNS signal of their
# own. A name matches an entry if it is the entry or a subdomain of it, also with a
# search domain appended (hl_dns_name_candidates). hl_threat_indicators.csv
# (indicator,type,source,context,added,expires): indicators from published advisories,
# each ignored from its expires date on (hl_ti_active). Both are in git and synced with
# --delete, so they are edited here, never in Splunk Web; README, Detections.
def dns_prep(base):
    """DNS_PREP with more base-search terms: a time range, a raw-term prefilter."""
    return DNS_PREP.replace("sourcetype=adguard:querylog ", "sourcetype=adguard:querylog " + base, 1)


def lookup_terms(lookup, where, empty):
    """A subsearch that ORs a lookup's rows as raw search terms, to cut the events read
    before the exact match. With no rows it returns a term that matches nothing:
    an empty subsearch would leave the base search matching everything."""
    return (f"[| inputlookup {lookup} {where} "
            "| stats count values(hl_term) as hl_terms "
            f"| eval search=if(count>0, \"(\".mvjoin(hl_terms, \" OR \").\")\", \"TERM({empty})\") | fields search] ")


# Each name's watch-list entry, one row per entry. The first lookup takes every
# candidate suffix of the name; the second, on the single matched entry, keeps
# category aligned with it.
WATCH_MATCH = (
    "| `hl_dns_name_candidates` "
    "| lookup hl_dns_watch_domains domain AS hl_cand OUTPUT domain AS watch_domain "
    "| where isnotnull(watch_domain) "
    "| mvexpand watch_domain "
    "| lookup hl_dns_watch_domains domain AS watch_domain OUTPUT category AS watch_category "
)

# Intel matches have no ATT&CK technique or detection strategy of their own, and a
# matched address says nothing about direction (a scanner hitting the honeypot, or a
# host calling out). T1071: every row from FLASH-20261113-001 is a C2, proxy or attack
# server, or the LLM reseller the framework calls over HTTPS; the context column in
# the finding says which. Searched: every event index except the agents' run log, the
# findings themselves, and the agents' gateway and pods, which log the agents' own
# queries (so a triage can't re-fire it); _audit (searches' text) is not an event index
# in index=*. Not TERM(): = and : are minor breakers, so TERM(1.2.3.4) misses
# src=1.2.3.4 and 1.2.3.4:443. The plain terms only narrow the events; every IPv4
# address in each event is then looked up exactly. The sample is up to 80 characters
# either side of the first occurrence.
det("HL - Threat indicator IP seen in logs",
    "T1071", "Command and Control", "high", "",
    "index=* NOT index=ai_agents NOT sourcetype=homelab:detection NOT sourcetype=toolhive:vmcp:audit "
    "NOT k8s.namespace.name IN (toolhive-clients, toolhive-mcp, toolhive-system) "
    + lookup_terms("hl_threat_indicators", "where type=\"ip\" | where `hl_ti_active(expires)` | eval hl_term=indicator",
                   "hl-no-active-ip-indicators") +
    "| rex max_match=0 field=_raw \"(?<hl_ipv4>(?<![0-9.])(?:[0-9]{1,3}\\.){3}[0-9]{1,3}(?!\\.?[0-9]))\" "
    "| lookup hl_threat_indicators indicator AS hl_ipv4 OUTPUT indicator AS ti_indicator "
    "| where isnotnull(ti_indicator) "
    "| mvexpand ti_indicator "
    "| lookup hl_threat_indicators indicator AS ti_indicator OUTPUT type AS ti_type expires AS ti_expires context AS ti_context source AS ti_source "
    "| where ti_type==\"ip\" AND `hl_ti_active(ti_expires)` "
    "| eval sample=substr(replace(replace(_raw, \"(?s)^.*?(.{0,80}(?<![0-9.])\".replace(ti_indicator, \"\\.\", \"[.]\").\"(?![0-9]).{0,80}).*$\", \"\\1\"), \"[\\x00-\\x1f\\s]+\", \" \"), 1, 200) "
    "| stats count min(_time) as first_seen max(_time) as last_seen first(sample) as sample values(ti_context) as context values(ti_source) as sources by ti_indicator index sourcetype host "
    "| eval user=\"\", src=ti_indicator, object=index.\"/\".sourcetype, "
    "detail=\"threat indicator \".ti_indicator.\" (\".mvjoin(sources, \", \").\": \".mvjoin(context, \" | \").\") in \".count.\" events, index=\".index.\" sourcetype=\".sourcetype.\" host=\".host.\"; sample: \".sample "
    "| convert ctime(first_seen) ctime(last_seen) ",
    "on a monitored host: logger 'hl-test threat indicator 198.51.100.73' (the art_validation canary row, an RFC 5737 documentation address)",
    suppress="src,host,object", period="6h",
    description="An IP address from hl_threat_indicators.csv (published advisories, ignored after each row's expires date) appeared in any event: an SSH or login source, a honeypot connection, a firewall or proxy line, a pod log. The detail names the advisory and what the address was used for. Advisories warn that addresses get reassigned, so corroborate before acting. Not searched: the agents' run log and gateway, and homelab:detection.")

det("HL - DNS lookup of a threat indicator domain",
    "T1071", "Command and Control", "high", "",
    DNS_PREP +
    "| `hl_dns_name_candidates` "
    "| lookup hl_threat_indicators indicator AS hl_cand OUTPUT indicator AS ti_indicator "
    "| where isnotnull(ti_indicator) "
    "| mvexpand ti_indicator "
    "| lookup hl_threat_indicators indicator AS ti_indicator OUTPUT type AS ti_type expires AS ti_expires context AS ti_context source AS ti_source "
    "| where ti_type==\"domain\" AND `hl_ti_active(ti_expires)` "
    "| stats count min(_time) as first_seen max(_time) as last_seen values(qname) as names values(QT) as types values(ti_context) as context values(ti_source) as sources by src ti_indicator "
    "| eval host=\"adguard\", user=\"\", object=ti_indicator, "
    "detail=\"threat indicator domain \".ti_indicator.\" (\".mvjoin(sources, \", \").\": \".mvjoin(context, \" | \").\") looked up \".count.\" times; types=\".mvjoin(types, \",\").\"; names: \".mvjoin(mvindex(names, 0, 4), \" \") "
    "| convert ctime(first_seen) ctime(last_seen) ",
    "from a client that uses AdGuard: dig xcai.pro.invalid (matched as if a search domain were appended; the reserved .invalid name never reaches the indicator's servers)",
    suppress="src,object", period="12h",
    description="A client looked up a domain from hl_threat_indicators.csv, or a subdomain of it, also with a search domain appended. Rows past their expires date are ignored. The detail names the advisory and what the domain was used for.")

# hl_ti_feed_indicators.csv: the same columns, written every 6 hours on the Splunk host
# by hl-threat-feeds.service (scripts/threat-feeds.py) from abuse.ch ThreatFox, URLhaus
# and Feodo Tracker; each row expires 30 days after its feed last listed it. Feeds are
# broad and machine-curated, so a match is medium, not high, and an indicator that is
# also an active hand-picked row is left to the two detections above
# (hl_ti_handpicked). An indicator seen by FEED_MAX_SEEN or more hosts (IPs) or
# clients (DNS) in one window is more likely a shared service than an implant, and is
# skipped; so is anything in the host-only hl_ti_feed_allowed macro. Feed IPs number
# in the thousands, too many for the raw-term prefilter above; a window is about 2,500
# events (2026-11), so every event's IPv4 addresses are looked up directly.
FEED_MAX_SEEN = 5
FEED_IP_MATCH = (
    "| lookup hl_ti_feed_indicators indicator AS hl_ipv4 OUTPUT indicator AS ti_indicator "
    "| where isnotnull(ti_indicator) "
    "| mvexpand ti_indicator "
    "| lookup hl_ti_feed_indicators indicator AS ti_indicator OUTPUT type AS ti_type expires AS ti_expires context AS ti_context source AS ti_source "
    "| where ti_type==\"ip\" AND `hl_ti_active(ti_expires)` "
    "| `hl_ti_handpicked(ti_indicator)` "
    "| where hl_handpicked==0 AND NOT `hl_ti_feed_allowed` "
)
det("HL - Threat feed IP seen in logs",
    "T1071", "Command and Control", "medium", "",
    "index=* NOT index=ai_agents NOT sourcetype=homelab:detection NOT sourcetype=toolhive:vmcp:audit "
    "NOT k8s.namespace.name IN (toolhive-clients, toolhive-mcp, toolhive-system) "
    "| rex max_match=0 field=_raw \"(?<hl_ipv4>(?<![0-9.])(?:[0-9]{1,3}\\.){3}[0-9]{1,3}(?!\\.?[0-9]))\" "
    "| where isnotnull(hl_ipv4) "
    + FEED_IP_MATCH +
    "| eval sample=substr(replace(replace(_raw, \"(?s)^.*?(.{0,80}(?<![0-9.])\".replace(ti_indicator, \"\\.\", \"[.]\").\"(?![0-9]).{0,80}).*$\", \"\\1\"), \"[\\x00-\\x1f\\s]+\", \" \"), 1, 200) "
    "| stats count min(_time) as first_seen max(_time) as last_seen first(sample) as sample values(ti_context) as context values(ti_source) as sources by ti_indicator index sourcetype host "
    "| eventstats dc(host) as hl_seen by ti_indicator "
    f"| where hl_seen<{FEED_MAX_SEEN} "
    "| eval user=\"\", src=ti_indicator, object=index.\"/\".sourcetype, "
    "detail=\"threat feed IP \".ti_indicator.\" (\".mvjoin(sources, \"; \").\": \".mvjoin(context, \" | \").\") in \".count.\" events, index=\".index.\" sourcetype=\".sourcetype.\" host=\".host.\"; sample: \".sample "
    "| convert ctime(first_seen) ctime(last_seen) ",
    "on a monitored host: logger 'hl-test threat feed 198.51.100.74' (the feed canary row threat-feeds.py always adds, an RFC 5737 documentation address)",
    suppress="src,host,object", period="24h",
    description="An IP address from the abuse.ch feeds (hl_ti_feed_indicators.csv: ThreatFox C2 and payload hosts, URLhaus malware download hosts, Feodo Tracker botnet C2s, refreshed every 6 hours) appeared in any event. Lower severity than the hand-picked list, which wins when both list the address; addresses seen on 5 or more hosts in one window, and those in the hl_ti_feed_allowed macro, are skipped. The detail names the feed and the malware family. Feed addresses are often shared hosting and get reassigned, so corroborate before acting. Not searched: the agents' run log and gateway, and homelab:detection.")

det("HL - DNS lookup of a threat feed domain",
    "T1071", "Command and Control", "medium", "",
    DNS_PREP +
    "| `hl_dns_name_candidates` "
    "| lookup hl_ti_feed_indicators indicator AS hl_cand OUTPUT indicator AS ti_indicator "
    "| where isnotnull(ti_indicator) "
    "| mvexpand ti_indicator "
    "| lookup hl_ti_feed_indicators indicator AS ti_indicator OUTPUT type AS ti_type expires AS ti_expires context AS ti_context source AS ti_source "
    "| where ti_type==\"domain\" AND `hl_ti_active(ti_expires)` "
    "| `hl_ti_handpicked(ti_indicator)` "
    "| where hl_handpicked==0 AND NOT `hl_ti_feed_allowed` "
    "| stats count min(_time) as first_seen max(_time) as last_seen values(qname) as names values(QT) as types values(ti_context) as context values(ti_source) as sources by src ti_indicator "
    "| eventstats dc(src) as hl_seen by ti_indicator "
    f"| where hl_seen<{FEED_MAX_SEEN} "
    "| eval host=\"adguard\", user=\"\", object=ti_indicator, "
    "detail=\"threat feed domain \".ti_indicator.\" (\".mvjoin(sources, \"; \").\": \".mvjoin(context, \" | \").\") looked up \".count.\" times; types=\".mvjoin(types, \",\").\"; names: \".mvjoin(mvindex(names, 0, 4), \" \") "
    "| convert ctime(first_seen) ctime(last_seen) ",
    "from a client that uses AdGuard: dig hl-feed-canary.invalid (the feed canary row threat-feeds.py always adds; the reserved .invalid name never resolves)",
    suppress="src,object", period="24h",
    description="A client looked up a domain from the abuse.ch feeds (hl_ti_feed_indicators.csv: ThreatFox C2 and payload domains, URLhaus malware download hosts), or a subdomain of it, also with a search domain appended. Lower severity than the hand-picked list, which wins when both list the domain; domains looked up by 5 or more clients in one window, and those in the hl_ti_feed_allowed macro, are skipped. The detail names the feed and the malware family.")

# Category -> ATT&CK v20 technique, tactic, severity, and how the detail names it.
# Webhook collectors and OOB callbacks are high: nothing here uses them, and a callback
# lookup is how a blind injection (SSRF, template, deserialisation) proves it worked.
# OOB callbacks are T1071.004: the lookup itself carries the token to the scanner's
# DNS server. Paste sites are T1102 (they are used as dead drops more than for
# exfiltration). Geolocation and LLM APIs have their own first-lookup detections below.
WATCH_CATEGORIES = {
    "webhook": ("T1567.004", "Exfiltration", "high", "request-capture (webhook) service"),
    "callback": ("T1071.004", "Command and Control", "high", "out-of-band callback service"),
    "paste": ("T1102", "Command and Control", "medium", "paste site"),
    "filedrop": ("T1567.002", "Exfiltration", "medium", "anonymous file-upload service"),
    "tunnel": ("T1572", "Command and Control", "medium", "ad-hoc tunnel service"),
    "tor": ("T1090.003", "Command and Control", "medium", "Tor download or gateway"),
}


def by_category(i):
    return "case(" + ", ".join(f'watch_category=="{c}", "{v[i]}"' for c, v in WATCH_CATEGORIES.items()) + ")"


det("HL - DNS lookup of a data-drop or callback service",
    "T1567.004", "Exfiltration", "medium", "",
    DNS_PREP + WATCH_MATCH +
    "| where in(watch_category, " + ", ".join(f'"{c}"' for c in WATCH_CATEGORIES) + ") AND NOT `hl_dns_watch_allowed` "
    "| stats count min(_time) as first_seen max(_time) as last_seen values(watch_domain) as domains values(qname) as names by src watch_category "
    f"| eval hl_technique={by_category(0)}, hl_tactic={by_category(1)}, hl_severity={by_category(2)}, label={by_category(3)} "
    "| eval host=\"adguard\", user=\"\", object=watch_category, "
    "detail=count.\" lookups of a \".label.\" (\".mvjoin(domains, \", \").\"); names: \".mvjoin(mvindex(names, 0, 4), \" \") "
    "| convert ctime(first_seen) ctime(last_seen) ",
    "from a client that uses AdGuard: dig webhook.site",
    suppress="src,object", period="24h",
    description="A client looked up a service from hl_dns_watch_domains.csv that malware and intrusion tools use to move data or prove a blind exploit worked: request-capture webhooks (T1567.004, high), out-of-band callback servers such as Interactsh and Burp Collaborator (T1071.004, high), paste sites (T1102), anonymous file drops (T1567.002), ad-hoc tunnels such as ngrok and Cloudflare quick tunnels (T1572), and the Tor Project (T1090.003); the finding's technique and severity follow the category. One finding per client and category. Known-good client and name pairs, such as a security scanner that uses OOB callbacks, go in the hl_dns_watch_allowed macro on the Splunk host.")


def first_lookup(category, ignore):
    """A client's first lookup of a domain in one watch-list category in 14 days. The
    baseline reads only the events that name a domain of that category."""
    return (
        dns_prep("earliest=-14d@d latest=-5m@m "
                 + lookup_terms("hl_dns_watch_domains", f"where category=\"{category}\" | eval hl_term=domain",
                                "hl-no-watch-domains"))
        + WATCH_MATCH +
        f"| where watch_category==\"{category}\" AND NOT `{ignore}` "
        "| stats count min(_time) as first_seen max(_time) as last_seen values(qname) as names by src watch_domain "
        "| where first_seen>=relative_time(now(), \"-20m@m\") "
        "| eval host=\"adguard\", user=\"\", object=watch_domain, "
        "detail=\"first lookup of \".watch_domain.\" by this client in 14 days (\".count.\" lookups since); names: \".mvjoin(mvindex(names, 0, 4), \" \") "
        "| convert ctime(first_seen) ctime(last_seen) "
    )


# Client and domain pairs, so no addresses are needed here. A pod gets a new address
# each run, so every pod that calls an LLM API is new: hl_dns_llm_client_ignore (both
# detections) takes the pod range, set on the Splunk host. ATLAS v5.6.0 AML.T0096 (AI
# Service API, Command and Control) is the closest: a tool or implant driving an LLM
# over its API, as the agent frameworks in FLASH-20261113-001 do.
det("HL - DNS first lookup of an LLM API by a client",
    "AML.T0096", "Command and Control", "low", "",
    first_lookup("llm_api", "hl_dns_llm_client_ignore"),
    "from a client that hasn't looked it up in 14 days: dig api.mistral.ai",
    suppress="src,object", period="24h",
    description="A client looked up an LLM API host from hl_dns_watch_domains.csv (category llm_api: Anthropic, OpenAI, Gemini, xAI, DeepSeek, Zhipu GLM, Moonshot Kimi, Mistral, Groq, OpenRouter, Together, Fireworks, Qwen DashScope, Cohere) that it had not looked up in the previous 14 days. Agentic attack frameworks call these APIs from the host they run on. Pods get a new address each run, so set the pod range in the hl_dns_llm_client_ignore macro on the Splunk host.")

# T1614 names online geolocation IP-lookup services; FLASH-20261113-001 reports
# api.db-ip.com used that way. Some software checks its public address routinely, so
# only a client's first lookup in 14 days counts.
det("HL - DNS first lookup of an IP geolocation service by a client",
    "T1614", "Discovery", "low", "",
    first_lookup("geoip", "hl_dns_llm_client_ignore"),
    "from a client that hasn't looked it up in 14 days: dig api.db-ip.com",
    suppress="src,object", period="24h",
    description="A client looked up a what-is-my-IP or IP geolocation service from hl_dns_watch_domains.csv (category geoip: db-ip, ip-api, ipinfo, ifconfig.me, icanhazip, ipify) that it had not looked up in the previous 14 days. Implants and attack tools check where they landed this way. Clients that do it routinely stop firing after their first lookup. Pod addresses are skipped with hl_dns_llm_client_ignore, as for the LLM API detection.")

# ---- Cloudflare (account audit log, tunnels, firewall events) ----------------------------
# scripts/cloudflare-logs.py pulls these every 10 minutes and Cloudflare's own audit log
# lags by a few minutes, so an event can reach Splunk 15 to 20 minutes after it happened.
# These detections look at [-40m@m, -25m@m) instead of the usual window. The audit log's
# "first seen" baselines are 30 days (the collector's first run backfills 29).
# Changes the owner or the homelab's own automation make on purpose (a Terraform or
# cert-manager token, a known script) go in the host-only hl_cf_expected_change macro;
# the token IDs are never written here. cert-manager's ACME TXT records are left out
# here, by name; its deletes carry only the record ID, which is matched to the create.
CF_WINDOW = ("-40m@m", "-25m@m")
CF_SINCE = 'relative_time(now(), "-40m@m")'
CF_CHANGE = "| where cf_result==\"success\" AND cf_type!=\"view\" AND NOT `hl_cf_expected_change` "
CF_TAIL = (
    "| stats count min(_time) as first_seen max(_time) as last_seen values(cf_desc) as actions values(cf_uri) as uris values(cf_via) as via by user src object "
    "| eval host=\"cloudflare\", detail=\"actions=\".mvjoin(mvindex(actions, 0, 4), \"; \").\" via=\".mvjoin(via, \",\").\" requests=\".mvjoin(mvindex(uris, 0, 4), \" \") "
    "| convert ctime(first_seen) ctime(last_seen) "
)

det("HL - Cloudflare API token or API key changed",
    "T1098.001", "Persistence", "high", "",
    "`hl_cf_audit` "
    "| where cf_result==\"success\" AND NOT `hl_cf_expected_change` "
    "| where (cf_product==\"tokens\" AND cf_type!=\"view\") OR match(cf_desc, \"(?i)^TOKEN_|api.?key\") "
    "OR match(cf_uri, \"^/user/(api_key|service_keys)\") OR (cf_type!=\"view\" AND match(cf_uri, \"/tokens(/|$)\")) "
    # Explicit paths: a token's response lists every permission group, past the
    # 5,000 characters automatic JSON extraction reads.
    "| spath output=hl_token_name path=resource.response.name | spath output=hl_token_req_name path=resource.request.name "
    "| spath output=hl_perms path=resource.response.policies{}.permission_groups{}.name "
    "| eval user=cf_actor, hl_obj=coalesce(hl_token_name, hl_token_req_name, nullif(cf_uri, \"\")) "
    # Creating a token logs twice (Create Token, and TOKEN_CREATE with no name): one finding.
    "| stats count min(_time) as first_seen max(_time) as last_seen values(hl_obj) as objects values(cf_desc) as actions values(hl_perms) as perms values(cf_via) as via by user src "
    "| eval object=coalesce(mvjoin(mvindex(objects, 0, 4), \",\"), \"account\"), host=\"cloudflare\", detail=\"actions=\".mvjoin(actions, \"; \").\" via=\".mvjoin(via, \",\").if(isnotnull(perms), \" permissions=\".mvjoin(mvindex(perms, 0, 14), \", \").if(mvcount(perms)>15, \" (+\".(mvcount(perms)-15).\" more)\", \"\"), \"\") "
    "| convert ctime(first_seen) ctime(last_seen) ",
    "in the Cloudflare dashboard, create a read-only API token hl-test (Account API Tokens), then delete it",
    window=CF_WINDOW, period="4h",
    description="An API token was created, rolled, changed or deleted, or the Global API Key was viewed or rolled. A token is a credential that outlives a password change, and Cloudflare holds DNS, tunnels and Zero Trust for every public site. object is the token name, and the detail lists its permission groups. Creating one logs twice (Create Token and TOKEN_CREATE); both are in one finding.")

det("HL - Cloudflare account member, role or login settings changed",
    "T1098.003", "Persistence", "high", "",
    "`hl_cf_audit` " + CF_CHANGE +
    "| where match(cf_uri, \"^/(accounts/<id>/(members|roles|iam|sso)|memberships|user/(two_factor|tfa|password|email))\") "
    "OR match(cf_desc, \"(?i)member|invit|two.?factor|\\btfa|2fa|mfa|password|email.?change|sso_(?!login)|super.?admin\") "
    "| where NOT match(cf_desc, \"(?i)^(sso_)?login\") "
    "| eval user=cf_actor, object=coalesce('resource.request.email', 'resource.response.email', 'resource.response.user.email', nullif(cf_uri, \"\"), \"account\") "
    + CF_TAIL,
    "invite a test address to the account with a read-only role, then revoke the invitation",
    window=CF_WINDOW, period="4h",
    description="A member was invited, added, removed or given a different role, or SSO, two-factor, password or email settings changed. One owner runs this account, so any of these is worth a look.")

det("HL - Cloudflare DNS record changed",
    "T1584.001", "Resource Development", "medium", "",
    "`hl_cf_audit` earliest=-30d@d latest=-25m@m "
    "| where cf_result==\"success\" AND cf_type!=\"view\" AND (cf_product==\"dns_records\" OR (cf_product==\"dns\" AND cf_rtype==\"record\")) "
    "| eval hl_rec_id=coalesce('resource.response.id', 'resource.id', 'resource.value.id'), "
    "hl_names=mvdedup(mvappend('resource.response.name', 'resource.value.name', 'resource.response.posts{}.name', 'resource.response.patches{}.name', 'resource.response.puts{}.name', 'resource.response.deletes{}.name')), "
    "hl_types=mvdedup(mvappend('resource.response.type', 'resource.value.type', 'resource.response.posts{}.type', 'resource.response.patches{}.type', 'resource.response.puts{}.type', 'resource.response.deletes{}.type')), "
    "hl_contents=mvdedup(mvappend('resource.response.content', 'resource.value.content', 'resource.response.posts{}.content', 'resource.response.patches{}.content', 'resource.response.puts{}.content')), "
    "hl_repoint=if(cf_type==\"update\" OR isnotnull('resource.response.patches{}.name') OR isnotnull('resource.response.puts{}.name'), 1, 0) "
    # A delete by API token carries only the record ID: take its name and type from the create.
    "| eventstats values(hl_names) as hl_known_names values(hl_types) as hl_known_types by hl_rec_id "
    "| eval hl_names=coalesce(hl_names, hl_known_names), hl_types=coalesce(hl_types, hl_known_types), hl_content1=mvindex(hl_contents, 0) "
    "| eventstats min(_time) as hl_content_first by hl_content1 "
    f"| where _time>={CF_SINCE} "
    "| where NOT (mvjoin(hl_types, \",\")==\"TXT\" AND match(mvjoin(hl_names, \" \"), \"^_acme-challenge\\.\\S+( _acme-challenge\\.\\S+)*$\")) "
    "| where NOT `hl_cf_expected_change` "
    "| eval hl_severity=if(match(mvjoin(hl_types, \",\"), \"\\b(NS|MX)\\b\") OR (hl_repoint==1 AND isnotnull(hl_content1) AND hl_content_first>=" + CF_SINCE + "), \"high\", \"medium\"), hl_rank=if(hl_severity==\"high\", 2, 1), "
    "user=cf_actor, object=cf_zone.\": \".coalesce(mvjoin(hl_types, \",\"), \"?\").\" \".coalesce(mvjoin(mvindex(hl_names, 0, 4), \",\"), \"record \".hl_rec_id), "
    "hl_change=case(isnotnull('resource.response.deletes{}.name'), \"delete\", hl_repoint==1, \"update\", true(), cf_type).if(isnotnull(hl_contents), \" -> \".mvjoin(mvindex(hl_contents, 0, 2), \",\"), \"\") "
    "| stats count min(_time) as first_seen max(_time) as last_seen values(cf_desc) as actions values(hl_change) as changes values(cf_via) as via max(hl_rank) as hl_rank by user src object "
    "| eval hl_severity=if(hl_rank==2, \"high\", \"medium\"), host=\"cloudflare\", detail=\"changes=\".mvjoin(mvindex(changes, 0, 4), \"; \").\" actions=\".mvjoin(actions, \"; \").\" via=\".mvjoin(via, \",\") "
    "| convert ctime(first_seen) ctime(last_seen) ",
    "in the Cloudflare dashboard, add a TXT record hl-test with any text to a zone, then delete it (medium); point an existing test record at a new address (high)",
    window=CF_WINDOW, period="1h",
    description="A DNS record was created, changed or deleted on any zone, by a person, an API token or Cloudflare itself. High for NS or MX records, and for a record changed to point at a target not seen in a DNS change in 30 days (a hijacked name looks like that). ACME challenge TXT records (cert-manager, Proxmox, Cloudflare's own certificates) are left out.")

det("HL - Cloudflare tunnel or Zero Trust Access changed",
    "T1572", "Command and Control", "high", "",
    "`hl_cf_audit` "
    "| where cf_result==\"success\" AND NOT `hl_cf_expected_change` "
    "| eval hl_tunnel=if(cf_product IN (\"tunnels\", \"cfd_tunnel\") OR match(cf_uri, \"/(cfd_tunnel|tunnels|warp_connector|teamnet)(/|$)\"), 1, 0), "
    "hl_access=if(cf_product IN (\"access\", \"gateway\", \"devices\", \"zero_trust\") OR match(cf_uri, \"/(access|gateway|devices)/\"), 1, 0) "
    # A tunnel's token is its connector credential: reading it counts.
    "| where (cf_type!=\"view\" AND (hl_tunnel==1 OR hl_access==1)) OR match(cf_uri, \"/cfd_tunnel/[^/]+/token$\") "
    "| eval hl_technique=if(hl_tunnel==1, \"T1572\", \"T1556\"), hl_tactic=if(hl_tunnel==1, \"Command and Control\", \"Persistence\"), "
    "user=cf_actor, object=coalesce('resource.request.name', 'resource.response.name', cf_uri), "
    "hl_ingress=mvdedup('resource.request.config.ingress{}.hostname') "
    "| stats count min(_time) as first_seen max(_time) as last_seen values(cf_desc) as actions values(cf_uri) as uris values(hl_ingress) as ingress values(cf_via) as via by user src object hl_technique hl_tactic "
    "| eval host=\"cloudflare\", detail=\"actions=\".mvjoin(mvindex(actions, 0, 4), \"; \").\" via=\".mvjoin(via, \",\").\" requests=\".mvjoin(mvindex(uris, 0, 4), \" \").if(isnotnull(ingress), \" ingress hostnames=\".mvjoin(mvindex(ingress, 0, 9), \",\"), \"\") "
    "| convert ctime(first_seen) ctime(last_seen) ",
    "in Zero Trust > Networks > Tunnels, add a public hostname hl-test to an existing test tunnel, then remove it; or create an Access application and delete it",
    window=CF_WINDOW, period="4h",
    description="A Cloudflare Tunnel was created, deleted or reconfigured (its ingress rules decide which hostname reaches which internal service), its connector token was read, or a Zero Trust Access application, policy, group, identity provider, service token, Gateway or device setting changed. A new ingress rule or tunnel exposes an internal service to the internet; an Access change can open an application to anyone (T1556 for Access changes). The detail lists ingress hostnames for a configuration change.")

det("HL - Cloudflare zone security setting weakened",
    "T1686", "Defense Impairment", "high", "",
    "`hl_cf_audit` " + CF_CHANGE +
    "| where cf_method!=\"GET\" AND match(cf_uri, \"^/zones/<id>/(settings|rulesets|firewall|filters|rate_limits|bot_management|page_shield|waf|managed_headers|ssl)\") "
    "| eval hl_req=coalesce(cf_req, \"\"), "
    "hl_pairs=if(match(cf_uri, \"/settings/[a-z0-9_]+$\"), replace(cf_uri, \"^.*/settings/\", \"\").\"=\".coalesce('resource.request.value', \"\"), null()) "
    "| rex field=hl_req max_match=30 \"\\\"id\\\":\\s*\\\"(?<hl_bulk_id>[a-z0-9_]+)\\\",\\s*\\\"value\\\":\\s*\\\"(?<hl_bulk_val>[^\\\"]*)\\\"\" "
    "| eval hl_pairs=mvappend(hl_pairs, mvzip(hl_bulk_id, hl_bulk_val, \"=\")) "
    "| eval hl_weak=mvfilter(match(hl_pairs, \"^(security_level=(essentially_off|low|off)|ssl=(off|flexible)|min_tls_version=1\\.0|(always_use_https|automatic_https_rewrites|browser_check|waf|tls_1_3|hotlink_protection|email_obfuscation|server_side_exclude)=off)$\")) "
    "| eval hl_weak=mvappend(hl_weak, "
    "if(cf_method==\"DELETE\" AND match(cf_uri, \"/(rulesets|firewall|filters|rate_limits|page_shield)\"), \"deleted \".cf_uri, null()), "
    "if(match(hl_req, \"\\\"enabled\\\":\\s*false\") AND match(cf_uri, \"/(rulesets|firewall|rate_limits|page_shield|managed_headers)\"), \"rule or ruleset disabled\", null()), "
    "if(match(hl_req, \"\\\"mode\\\":\\s*\\\"whitelist\\\"\"), \"IP access rule allowlist\", null()), "
    "if(match(cf_uri, \"/bot_management$\") AND match(hl_req, \"\\\"(fight_mode|enable_js|optimize_wordpress|sbfm_[a-z_]+|ai_bots_protection|crawler_protection)\\\":\\s*(false|\\\"(allow|allowed|disabled)\\\")\"), \"bot protection turned down\", null())) "
    "| where isnotnull(hl_weak) "
    "| eval user=cf_actor, object=coalesce(cf_zone, \"account\").\": \".mvjoin(mvindex(hl_weak, 0, 4), \"; \") "
    + CF_TAIL,
    "in the Cloudflare dashboard set a test zone's Security Level to Low (Security > Settings), then back",
    window=CF_WINDOW, period="4h",
    description="A zone's protection was turned down: security level to Low or off, SSL/TLS mode to Off or Flexible (Flexible sends traffic to the origin unencrypted), minimum TLS 1.0, Always Use HTTPS, Browser Integrity Check, TLS 1.3 or the WAF switched off, a WAF, firewall or rate-limit rule deleted or disabled, an IP allowlisted, or Bot Fight Mode turned off. Settings turned up are not reported.")

det("HL - Cloudflare dashboard login from a new IP or country",
    "T1078.004", "Initial Access", "medium", "",
    "`hl_cf_audit` earliest=-30d@d latest=-25m@m "
    "| where match(cf_desc, \"(?i)^(sso_)?login\") AND src!=\"\" "
    "| iplocation src | eval hl_country=coalesce(nullif(Country, \"\"), \"unknown\") "
    "| eventstats min(_time) as hl_ip_first by cf_actor src "
    "| eventstats min(_time) as hl_country_first by cf_actor hl_country "
    "| eventstats min(_time) as hl_data_start "
    f"| where _time>={CF_SINCE} AND hl_ip_first>={CF_SINCE} AND hl_data_start<relative_time(now(), \"-1d\") "
    f"| eval hl_severity=if(hl_country_first>={CF_SINCE}, \"high\", \"medium\"), hl_rank=if(hl_severity==\"high\", 2, 1), user=cf_actor, object=\"cloudflare dashboard\" "
    "| stats count min(_time) as first_seen max(_time) as last_seen values(cf_desc) as actions values(cf_result) as results values(hl_country) as countries max(hl_rank) as hl_rank by user src object "
    "| eval hl_severity=if(hl_rank==2, \"high\", \"medium\"), host=\"cloudflare\", detail=\"first login from this address in 30 days, country=\".mvjoin(countries, \",\").if(hl_severity==\"high\", \" (new country)\", \"\").\" actions=\".mvjoin(actions, \",\").\" results=\".mvjoin(results, \",\") "
    "| convert ctime(first_seen) ctime(last_seen) ",
    "log in to the Cloudflare dashboard from a network you have not used in 30 days (a phone hotspot)",
    window=CF_WINDOW, period="24h", suppress="user,src",
    description="A login to the Cloudflare account (SSO_LOGIN or LOGIN in the audit log, success or failure) from an address not seen for that user in the previous 30 days; high when the country is new too (iplocation). A home address that changes after an ISP lease renewal also shows here, once.")

det("HL - Cloudflare tunnel connector from a new origin address",
    "T1572", "Command and Control", "medium", "",
    "index=network sourcetype=cloudflare:tunnel earliest=-30d@d latest=-25m@m connections>0 "
    "| stats min(_time) as first_seen max(_time) as last_seen values(tunnel_name) as tunnels by origin_ips{} "
    "| rename origin_ips{} as src "
    "| eventstats min(first_seen) as hl_data_start "
    f"| where first_seen>={CF_SINCE} AND hl_data_start<relative_time(now(), \"-1d\") "
    "| eval host=\"cloudflare\", user=\"\", object=mvjoin(mvindex(tunnels, 0, 9), \",\"), count=mvcount(tunnels), "
    "detail=\"first tunnel connection from this address in 30 days, tunnels=\".object "
    "| convert ctime(first_seen) ctime(last_seen) ",
    "run cloudflared for a test tunnel from another network (a laptop on a hotspot) for one snapshot",
    window=CF_WINDOW, period="24h", suppress="src",
    description="A Cloudflare Tunnel connector connected from a public address none of the tunnels used in the previous 30 days. Anyone holding a tunnel's token can run a connector and receive that tunnel's traffic. A changed home address shows here too, once, naming every tunnel.")

det("HL - Cloudflare firewall burst from one address",
    "T1595", "Reconnaissance", "medium", "",
    "index=network sourcetype=cloudflare:firewall action IN (block, challenge, managed_challenge, jschallenge, js_challenge, challenge_failed, managed_challenge_failed, jschallenge_failed) "
    "| where NOT `hl_cf_firewall_ignore` "
    "| stats count min(_time) as first_seen max(_time) as last_seen dc(clientRequestHTTPHost) as hosts values(clientRequestHTTPHost) as host_list values(action) as actions values(source) as sources values(clientCountryName) as countries values(clientASNDescription) as asn values(clientRequestPath) as paths by clientIP "
    "| where hosts>=3 OR count>=50 "
    "| eval hl_severity=if(count>=500, \"high\", \"medium\"), host=\"cloudflare\", user=\"\", src=clientIP, object=mvjoin(mvindex(host_list, 0, 9), \",\"), "
    "detail=count.\" blocked or challenged requests to \".hosts.\" hosts, actions=\".mvjoin(actions, \",\").\" sources=\".mvjoin(sources, \",\").\" country=\".mvjoin(countries, \",\").\" asn=\".mvjoin(asn, \",\").\" paths=\".mvjoin(mvindex(paths, 0, 4), \" \") "
    "| convert ctime(first_seen) ctime(last_seen) ",
    "needs Zone > Analytics > Read on the collector's token; then add a custom WAF rule blocking a test path and request it on three hostnames, or 50 times on one",
    window=CF_WINDOW, period="4h", suppress="src",
    description="One client address had 50 or more requests blocked or challenged by Cloudflare in 15 minutes, or was blocked or challenged on 3 or more hostnames: a scanner working through the sites. High at 500. Addresses in the hl_cf_firewall_ignore macro are skipped. Finds nothing until the collector's token has Zone > Analytics > Read.")

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

# Authentik logs model_created/updated/deleted for objects changed in its admin UI or
# API. A TOTP or WebAuthn device is saved on every login (its last-used time,
# auth_via=unauthenticated, about ten a month), so model_updated on
# authentik_stages_authenticator_* is left out; a new or deleted MFA device still counts.
# Impersonation and akadmin logins are the break-glass detection's.
det("HL - Authentik admin object changed",
    "T1098", "Persistence", "medium", "",
    "`hl_authentik_event` | search ak_action IN (model_created, model_updated, model_deleted) "
    "| spath output=ak_model_app path=context.model.app "
    "| spath output=ak_model path=context.model.model_name "
    "| spath output=ak_model_obj path=context.model.name "
    "| spath output=ak_auth_via path=auth_via "
    "| where NOT (ak_action==\"model_updated\" AND match(coalesce(ak_model_app, \"\"), \"^authentik_stages_authenticator_\")) "
    "| eval user=coalesce(ak_user, \"unknown\"), src=coalesce(ak_src, \"unknown\"), "
    "object=coalesce(ak_model_app, \"?\").\"/\".coalesce(ak_model, \"?\").\": \".coalesce(ak_model_obj, \"?\") "
    "| stats count min(_time) as first_seen max(_time) as last_seen values(ak_action) as actions values(ak_auth_via) as via by user src object "
    "| eval host=\"authentik\", detail=\"actions=\".mvjoin(actions, \",\").\" auth_via=\".coalesce(mvjoin(via, \",\"), \"unknown\") "
    "| convert ctime(first_seen) ctime(last_seen) ",
    "in the Authentik admin UI, create a group hl-test, then delete it",
    period="1h",
    description="A user, group, MFA device, application, provider, flow, outpost or other Authentik object was created, changed or deleted. About ten a month, all by the owner. MFA devices saving their last-used time at login are left out.")

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

# deploy.sh rsyncs each app's default/ and never touches local/, so a change under an
# app's local/, a user's saved searches, or a security-relevant file in
# etc/system/local was made in Splunk Web, over REST or on the host. None under 00_
# or 85_ apps in the week to 2026-11-12. restmap.conf and web.conf in system/local
# (about 270 rewrites a week), telemetry and the siem_factory_poc app write their own
# files and don't match. Only stanza and property names are reported: values can hold
# secrets.
det("HL - Splunk configuration changed outside a deploy",
    "T1685", "Defense Impairment", "high", "DET0497",
    "index=_configtracker "
    "| eval path='data.path', action='data.action', stanzas='data.changes{}.stanza', props='data.changes{}.properties{}.name' "
    "| where (match(path, \"(^|/)etc/apps/(00_|85_)[^/]+/local/\") "
    "OR match(path, \"(^|/)etc/users/[^/]+/[^/]+/local/savedsearches\\.conf$\") "
    "OR match(path, \"(^|/)etc/system/local/(authentication|authorize|outputs|inputs|server|indexes|props|transforms|limits)\\.conf$\")) "
    "AND NOT match(path, \"/siem_factory_poc/\") "
    "| stats count min(_time) as first_seen max(_time) as last_seen values(action) as actions values(stanzas) as stanzas values(props) as props by path "
    "| eval host=\"security-tooling-01\", user=\"\", src=\"\", object=path, "
    "detail=\"actions=\".mvjoin(actions, \",\").\" stanzas=\".coalesce(mvjoin(mvindex(stanzas, 0, 9), \",\"), \"(none)\")"
    ".\" properties=\".coalesce(mvjoin(mvindex(props, 0, 19), \",\"), \"(none)\").\" (values not shown)\" "
    "| convert ctime(first_seen) ctime(last_seen) ",
    "in Splunk Web, create a search macro hl_test in the 85_detections app (Settings > Advanced search), then delete it",
    suppress="object", period="1h",
    description="A Splunk config file changed outside a deploy: anything under an 00_ or 85_ app's local/ (detections, alert actions, roles, auth, indexes, inputs), a user's private saved searches, or authentication, authorize, outputs, inputs, server, indexes, props, transforms or limits in etc/system/local. Turning off or rewriting a detection, or redirecting data, looks like this. Setting the decoy macros in 85_detections/local fires it once, as intended.")

# info=granted rows are capability checks, not changes; denied attempts are left out.
det("HL - Splunk user or role changed",
    "T1098", "Persistence", "high", "",
    "index=_audit action IN (create_user, edit_user, delete_user, edit_roles) info=succeeded NOT search_id=* "
    "| eval src=coalesce(src, \"local\"), object=coalesce(object, \"unknown\") "
    "| stats count min(_time) as first_seen max(_time) as last_seen values(action) as actions by user src object "
    "| eval host=\"security-tooling-01\", detail=\"actions=\".mvjoin(actions, \",\").\" (a local Splunk user or role was created, changed or deleted)\" "
    "| convert ctime(first_seen) ctime(last_seen) ",
    "in Splunk Web, create a local user hl-test with the user role, then delete it",
    suppress="user,object", period="1h",
    description="A local Splunk user or role was created, changed or deleted. A new admin or a role with more capabilities is how someone keeps access, or gets around Authentik.")

# ---- MCP gateway ---------------------------------------------------------------------------
det("HL - MCP gateway write tool, denial or new client",
    "T1078", "Initial Access", "medium", "DET0927",
    "index=audit sourcetype=toolhive:vmcp:audit earliest=-30d@d latest=-5m@m "
    "| eval user=coalesce('subjects.user_id', \"unknown\"), tool='target.name', src=coalesce('source.value', \"unknown\") "
    "| eventstats min(_time) as user_first by user "
    "| where _time>=relative_time(now(), \"-20m@m\") "
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

# ---- The agents' inputs ----------------------------------------------------------------------
# The security and ops agents read these indexes, so text written into a log can reach
# a model. Phrases that address a model; none in the week to 2026-11-12. Left out so
# findings can't loop: index=ai_agents (not searched), the findings themselves
# (homelab:detection, which quote the text), and the agents' own pods and gateway
# (toolhive-clients, toolhive-mcp, toolhive-system, the gateway's audit log), which log
# the agents' queries. The quoted text has each phrase broken with [-] so the finding
# is not itself an instruction when an agent reads it.
INJECTION_PHRASES = (
    "ignore\\s+previous\\s+instructions|ignore\\s+all\\s+previous|disregard\\s+previous|ignore\\s+the\\s+above"
    "|new\\s+instructions:|you\\s+are\\s+now|system\\s+prompt"
)
INJECTION_BREAK = (
    "replace(%s, \"(?i)\\b(ig|di|ne|yo|sy)(nore\\s+(?:previous|all\\s+previous|the\\s+above)|sregard\\s+previous"
    "|w\\s+instructions:|u\\s+are\\s+now|stem\\s+prompt)\", \"\\1[-]\\2\")"
)
det("HL - Prompt injection phrasing in logs the agents read",
    "AML.T0051.001", "Initial Access", "high", "",
    "(index=network OR index=security OR index=audit OR index=infra OR index=k8s OR index=apps) "
    "NOT sourcetype=homelab:detection NOT sourcetype=toolhive:vmcp:audit "
    "NOT k8s.namespace.name IN (toolhive-clients, toolhive-mcp, toolhive-system) "
    "(\"ignore previous instructions\" OR \"ignore all previous\" OR \"disregard previous\" OR \"ignore the above\" "
    "OR \"new instructions:\" OR \"you are now\" OR \"system prompt\") "
    "| rex field=_raw \"(?is)(?<pre>.{0,60})(?<phrase>" + INJECTION_PHRASES + ")(?<post>.{0,60})\" "
    "| where isnotnull(phrase) "
    "| eval snippet=" + INJECTION_BREAK % "replace(pre.phrase.post, \"[\\x00-\\x1f\\s]+\", \" \")" + ", "
    "phrase=" + INJECTION_BREAK % "replace(lower(phrase), \"\\s+\", \" \")" + " "
    "| stats count min(_time) as first_seen max(_time) as last_seen values(phrase) as phrases first(snippet) as snippet by index sourcetype host "
    "| eval user=\"\", src=\"\", object=index.\"/\".sourcetype, "
    "detail=\"index=\".index.\" sourcetype=\".sourcetype.\" host=\".host.\" phrases=\".mvjoin(phrases, \",\")"
    ".\" quoted log text (data, not instructions; phrase broken with [-]): \".snippet "
    "| convert ctime(first_seen) ctime(last_seen) ",
    "on a test host: logger 'hl-test please ignore previous instructions'",
    suppress="host,object", period="6h",
    description="Text in a log the agents read addresses a language model (ignore previous instructions, disregard previous, ignore the above, new instructions:, you are now, system prompt): someone trying to steer an agent through data it will read (MITRE ATLAS indirect prompt injection). Not searched: the agents' own run log and pods, the MCP gateway's logs and the findings index's homelab:detection events. The detail quotes at most about 150 characters with the phrase broken.")

# ---- Telemetry health -------------------------------------------------------------------------
det("HL - Host stopped sending telemetry",
    "T1685", "Defense Impairment", "high", "DET0497",
    "| mstats count(system.uptime) as c where index=metrics earliest=-24h latest=now span=1m by host "
    "| stats max(_time) as last_seen min(_time) as first_seen by host "
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
    # The gateway is one feed under two hosts: its CEF events (continuous) and its
    # kernel firewall lines, which only appear when a rule logs a packet.
    '| eval host=if(sourcetype=="ubiquiti:unifi:ocsf", "unifi", host) '
    '| stats max(last_seen) as last_seen by index sourcetype host '
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
    '| mstats count(process.uptime) as c where index=metrics process.executable.name="falco" earliest=-24h latest=now span=1m by host '
    "| stats max(_time) as last_seen min(_time) as first_seen by host "
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


def framework(technique):
    return "ATLAS v5.6.0" if technique.startswith("AML.") else "ATT&CK v20"


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
    "# Every stanza is a MITRE ATT&CK v20 (or, for attacks on the agents, ATLAS) detection.",
    "# Findings go to index=security",
    "# (sourcetype homelab:detection), which the agents read.",
    "",
]
for d in D:
    search = d["search"].strip() + (
        f' | `hl_finding("{d["name"]}", "{d["technique"]}", "{d["tactic"]}", "{d["severity"]}", "{d["strategy"]}")`'
    )
    out += [
        f'[{d["name"]}]',
        f'description = {framework(d["technique"])} {d["technique"]} ({d["tactic"]}), strategy {d["strategy"] or "none"}. {d["description"]} Test: {d["test"]}',
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
path = pathlib.Path(__file__).parent / "default/savedsearches.conf"
path.write_text("\n".join(out))
print(f"wrote {path} ({len(D)} detections)")
