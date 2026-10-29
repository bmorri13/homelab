"""The work queues. The runner, not the model, decides what each agent looks at:
these queries run through the gateway as the agent's own read-only identity, and
every row becomes an item with a stable dedupe key.

An item: key, source, title, severity (low|medium|high|critical), host, facts (dict of
the row as Splunk returned it), runbook (file name in runbooks/).
"""
import json

SEV_RANK = {"low": 1, "medium": 2, "high": 3, "critical": 4}
WINDOW = "-2h"   # every run looks back this far; dedupe makes the overlap harmless


def _item(key, source, title, severity, host, facts, runbook):
    severity = severity if severity in SEV_RANK else "medium"
    return {"key": key, "source": source, "title": title, "severity": severity,
            "host": host or "", "facts": facts, "runbook": runbook}


def _first(value):
    return value[0] if isinstance(value, list) and value else (value or "")


def _top_severity(value):
    values = value if isinstance(value, list) else [value]
    return max((v for v in values if v in SEV_RANK), key=SEV_RANK.get, default="medium")


# ---- Security --------------------------------------------------------------------------

DETECTION_RUNBOOKS = [
    ("HL - Kubernetes", "kubernetes.md"),
    ("HL - Vault", "vault.md"),
    ("HL - SSH", "logins.md"),
    ("HL - Proxmox", "logins.md"),
    ("HL - UniFi admin", "logins.md"),
    ("HL - UniFi configuration", "unifi-config.md"),
    ("HL - Sensitive command", "host-commands.md"),
    ("HL - MCP gateway", "mcp-gateway.md"),
    ("HL - Host stopped", "telemetry.md"),
]

# Alerts that don't write to the homelab:detection queue. Each firing's results are
# read back with loadjob, and the alert's own suppression fields are the dedupe key.
ALERTS = {
    "Falco - AI agent finding": (("agent_host", "rule"), "falco.md", None),
    "Falco - runtime finding": (("finding_host", "rule"), "falco.md", None),
    "Honeypot - connection to OpenCanary": (("src_host",), "honeypot.md", "high"),
    "Secret leak - unmasked credential in indexed events": (("format", "index", "host"), "secret-leak.md", "high"),
}


def security(gw):
    items = []
    rows = gw.splunk(
        "search index=security sourcetype=homelab:detection | eval host=mvindex(host, 0) "
        "| table _time detection severity technique tactic host user src object detail count first_seen last_seen",
        WINDOW)
    for r in rows:
        name = r.get("detection", "")
        runbook = next((f for prefix, f in DETECTION_RUNBOOKS if name.startswith(prefix)), "general.md")
        key = "|".join(["hl", name, r.get("host", ""), r.get("user", ""), r.get("src", ""), r.get("object", "")])
        items.append(_item(key, "detection", name, r.get("severity"), r.get("host"), r, runbook))

    names = ", ".join(json.dumps(n) for n in ALERTS)
    fired = gw.splunk(
        f"search index=_audit action=alert_fired ss_name IN ({names}) | dedup sid | table _time ss_name sid",
        WINDOW)
    for f in fired:
        name = f["ss_name"]
        fields, runbook, fixed_severity = ALERTS[name]
        sid = f["sid"].replace('"', "")
        for r in gw.splunk(f'| loadjob "{sid}"', WINDOW):
            parts = [_first(r.get(k)) for k in fields]
            host = parts[0] if "host" in fields[0] else _first(r.get("host"))
            title = f"{name}: {' / '.join(p for p in parts if p)}"
            severity = fixed_severity or _top_severity(r.get("severity"))
            items.append(_item("|".join(["alert", name, *parts]), "alert", title, severity, host, r, runbook))
    return _dedupe(items)


# ---- Operations ------------------------------------------------------------------------

OPS_CHECKS = [
    # (name, SPL, key fields, severity rule, runbook)
    ("Filesystem nearly full",
     "| mstats latest(system.filesystem.utilization) as u where index=metrics earliest=-15m by host mountpoint "
     "| where u>0.85 | eval used_pct=round(u*100,1) | table host mountpoint used_pct",
     ("host", "mountpoint"), lambda r: "high" if float(r["used_pct"]) >= 95 else "medium", "capacity.md"),
    ("Proxmox storage nearly full",
     "| mstats latest(proxmox_storage_used) as used latest(proxmox_storage_total) as total "
     "where index=metrics earliest=-15m by node storage | where total>0 AND used/total>0.85 "
     "| eval used_pct=round(used/total*100,1), host=node | table host storage used_pct",
     ("host", "storage"), lambda r: "high" if float(r["used_pct"]) >= 95 else "medium", "capacity.md"),
    ("CPU saturated for 30 minutes",
     "| mstats avg(system.cpu.utilization) as idle where index=metrics state=idle earliest=-30m by host "
     "| eval busy_pct=round((1-idle)*100,1) | where busy_pct>90 | table host busy_pct",
     ("host",), lambda r: "medium", "capacity.md"),
    ("Memory nearly exhausted for 30 minutes",
     "| mstats avg(system.memory.utilization) as u where index=metrics state=used earliest=-30m by host "
     "| eval used_pct=round(u*100,1) | where used_pct>92 | table host used_pct",
     ("host",), lambda r: "medium", "capacity.md"),
    ("Kubernetes workload not fully available for 15 minutes",
     "| mstats max(k8s.deployment.available) as have latest(k8s.deployment.desired) as want "
     "where index=metrics earliest=-15m by k8s.namespace.name k8s.deployment.name "
     "| rename k8s.deployment.name as workload | eval kind=\"Deployment\" "
     "| append [| mstats max(k8s.statefulset.ready_pods) as have latest(k8s.statefulset.desired_pods) as want "
     "where index=metrics earliest=-15m by k8s.namespace.name k8s.statefulset.name "
     "| rename k8s.statefulset.name as workload | eval kind=\"StatefulSet\"] "
     "| append [| mstats max(k8s.daemonset.ready_nodes) as have latest(k8s.daemonset.desired_scheduled_nodes) as want "
     "where index=metrics earliest=-15m by k8s.namespace.name k8s.daemonset.name "
     "| rename k8s.daemonset.name as workload | eval kind=\"DaemonSet\"] "
     "| where have<want | rename k8s.namespace.name as namespace | eval host=\"k3s-01\" "
     "| table host namespace kind workload want have",
     ("namespace", "kind", "workload"), lambda r: "high" if float(r["have"]) == 0 else "medium", "kubernetes-health.md"),
    ("Kubernetes containers restarting",
     "| mstats max(k8s.container.restarts) as mx min(k8s.container.restarts) as mn where index=metrics earliest=-1h "
     "by k8s.namespace.name k8s.pod.name k8s.container.name | eval restarts=mx-mn | where restarts>=3 "
     "| rename k8s.namespace.name as namespace k8s.pod.name as pod k8s.container.name as container "
     "| eval host=\"k3s-01\" | table host namespace pod container restarts",
     ("namespace", "container", "pod"), lambda r: "medium", "kubernetes-health.md"),
    ("Kubernetes pods stuck outside Running",
     "| mstats latest(k8s.pod.phase) as p min(k8s.pod.phase) as pmin where index=metrics earliest=-15m "
     "by k8s.namespace.name k8s.pod.name | where p!=2 AND p!=3 AND pmin!=2 AND 'k8s.namespace.name'!=\"arc-runners\" "
     "| eval phase=case(p==1,\"Pending\",p==4,\"Failed\",true(),\"Unknown\") "
     "| rename k8s.namespace.name as namespace k8s.pod.name as pod | eval host=\"k3s-01\" "
     "| table host namespace pod phase",
     ("namespace", "pod"), lambda r: "medium", "kubernetes-health.md"),
    ("Docker containers restarting",
     "| mstats max(container.restarts) as mx min(container.restarts) as mn where index=metrics earliest=-1h "
     "by host container.name | eval restarts=mx-mn | where restarts>=3 "
     "| rename container.name as container | table host container restarts",
     ("host", "container"), lambda r: "medium", "docker.md"),
    ("Splunk scheduled searches skipped or failing",
     "search index=_internal sourcetype=scheduler status IN (skipped, failed, delegated_remote_error) "
     "| stats count values(reason) as reasons by app savedsearch_name status "
     "| eval host=\"security-tooling-01\" | table host app savedsearch_name status count reasons",
     ("app", "savedsearch_name", "status"), lambda r: "medium", "splunk-health.md"),
]


def operations(gw):
    items = []
    for name, spl, fields, severity, runbook in OPS_CHECKS:
        earliest = "-1h" if spl.startswith("search") else "-30m"
        for r in gw.splunk(spl, earliest):
            parts = [_first(r.get(k)) for k in fields]
            title = f"{name}: {' / '.join(str(p) for p in parts if p)}"
            items.append(_item("|".join(["ops", name, *map(str, parts)]), "check", title,
                               severity(r), _first(r.get("host")), r, runbook))

    apps = json.loads(gw.call("argocd_list_applications", {})).get("items", [])
    for a in apps:
        name = a["metadata"]["name"]
        status = a.get("status", {})
        sync = status.get("sync", {}).get("status", "Unknown")
        health = status.get("health", {}).get("status", "Unknown")
        facts = {"app": name, "sync": sync, "health": health,
                 "revision": status.get("sync", {}).get("revision", ""),
                 "path": a["spec"].get("source", {}).get("path", "")}
        if health not in ("Healthy", "Progressing"):
            items.append(_item(f"ops|argocd-health|{name}|{health}", "argocd", f"Argo CD app {name} is {health}",
                               "high" if health in ("Degraded", "Missing") else "medium", "k3s-01", facts, "argocd.md"))
        if sync == "OutOfSync":
            items.append(_item(f"ops|argocd-sync|{name}", "argocd", f"Argo CD app {name} is OutOfSync",
                               "low", "k3s-01", facts, "argocd.md"))
    return _dedupe(items)


def _dedupe(items):
    seen = {}
    for item in items:
        if item["key"] not in seen or SEV_RANK[item["severity"]] > SEV_RANK[seen[item["key"]]["severity"]]:
            seen[item["key"]] = item
    return sorted(seen.values(), key=lambda i: -SEV_RANK[i["severity"]])


COLLECTORS = {"sec": security, "ops": operations}
