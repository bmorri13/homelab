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
for indexing lag. "First seen" detections search their own longer baseline.
"""
import pathlib

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


def det(name, technique, tactic, severity, strategy, search, test, suppress="user,src,object",
        period="4h", window=("-20m@m", "-5m@m"), cron="*/15 * * * *", description=""):
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
    description="A service account token was requested by a person or an application. The kubelet's own pod tokens are not audited. Gateway client tokens (mint-client-token.sh) show up here, as intended.")

det("HL - Kubernetes workload created by hand",
    "T1610", "Execution", "medium", "DET0249",
    "`hl_k8s_audit` objectRef.resource IN (pods, deployments, daemonsets, statefulsets, jobs, cronjobs) verb=create responseStatus.code<400 "
    "| where NOT `hl_k8s_platform_user` AND NOT match('user.username', \"^system:serviceaccount:(arc-runners|toolhive-mcp):\") "
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
    "| eval user='auth.display_name', src='request.remote_address', object='request.path' "
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
    "| eval user='auth.display_name', src='request.remote_address' "
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
    "| eval user=coalesce(ts_user, user) "
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
    "| eval src=coalesce('src_endpoint.ip', src), user=coalesce('user.name', 'actor.user.name', user) "
    "| stats count min(_time) as first_seen max(_time) as last_seen values(status) as status by user src "
    "| where first_seen>=relative_time(now(), \"-20m@m\") OR match(mvjoin(status, \",\"), \"(?i)fail\") "
    "| eval host=\"unifi\", object=\"UniFi Network\", detail=\"status=\".mvjoin(status, \",\").\" (first login from this source in 30 days, or a failure)\" "
    "| convert ctime(first_seen) ctime(last_seen) ",
    "log in to the UniFi console from a device that hasn't been used in 30 days, or with a wrong password",
    suppress="user,src", period="24h",
    description="A UniFi admin login from a source address not seen in 30 days, or any failed login.")

det("HL - UniFi configuration changed",
    "T1686", "Defense Impairment", "medium", "DET0306",
    "index=audit sourcetype=ubiquiti:unifi:ocsf class_uid=5019 "
    "| eval user=coalesce('actor.user.name', 'user.name', user), src=coalesce('src_endpoint.ip', src), object=coalesce('device.name', 'device.hostname', \"unifi\") "
    "| stats count min(_time) as first_seen max(_time) as last_seen values(message) as messages by user src object "
    "| eval host=\"unifi\", detail=mvjoin(mvindex(messages, 0, 4), \" || \") "
    "| convert ctime(first_seen) ctime(last_seen) ",
    "create, then delete, a disabled dummy firewall rule in UniFi",
    suppress="object", period="1h",
    description="A UniFi configuration change: firewall policies, logging/SIEM settings, CyberSecure, networks. About one a day normally.")

# ---- MCP gateway ---------------------------------------------------------------------------
det("HL - MCP gateway write tool, denial or new client",
    "T1078", "Initial Access", "medium", "DET0927",
    "index=audit sourcetype=toolhive:vmcp:audit earliest=-30d@d latest=-5m@m "
    "| eval user='subjects.user_id', tool='target.name', src='source.value' "
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
        f'cron_schedule = {d["cron"]}',
        f'alert.severity = {SEVERITY[d["severity"]]}',
        f'alert.suppress.fields = {d["suppress"]}',
        f'alert.suppress.period = {d["period"]}',
        *COMMON,
        "",
    ]
path = pathlib.Path(__file__).parent / "default/savedsearches.conf"
path.write_text("\n".join(out))
print(f"wrote {path} ({len(D)} detections)")
