#!/usr/bin/env python3
"""Generate default/data/ui/views/homelab_security.xml (Dashboard Studio) from the
searches below. Edit here, run `python3 build_dashboard.py`, commit both files.

Layout: status tiles, then the findings queue the security agent reads
(85_detections, ATT&CK v20), every fired alert, Falco, the honeypot, logins,
the Kubernetes API, Vault, the MCP gateway, and whether the detections run.
Everything follows the time picker except detection health (last 24 hours).
Macros `hl_k8s_audit`, `hl_vault_audit` and `hl_k8s_platform_user` come from
85_detections (exported to all apps).
"""
import json
import pathlib

# Fields are extracted automatically (JSON); host is both the indexed host and the
# finding's own host field (the same value), so keep one.
FINDINGS = "index=security sourcetype=homelab:detection | eval host=mvindex(host, 0)"
FALCO = "index=security sourcetype=falco:ocsf"
CANARY = "index=security sourcetype=opencanary logtype>=2000"
AUTH = "index IN (infra, audit) tag=authentication"
MCP = ("index=audit sourcetype=toolhive:vmcp:audit "
       "| eval client=replace('subjects.user_id', \"^system:serviceaccount:toolhive-clients:\", \"\"), tool='target.name'")
OUR_APPS = "(85_detections, 85_falco, 85_opencanary, 85_secret_leaks)"
SEV_ORDER = ("| eval sev_rank=case(severity==\"critical\",1, severity==\"high\",2, severity==\"medium\",3, "
             "severity==\"low\",4, true(),5)")
WHEN = "| eval When=strftime(last, \"%Y-%m-%d %H:%M\")"

S = {}

# --- status tiles ---------------------------------------------------------------
S["t_findings"] = f"{FINDINGS} | stats count"
S["t_findings_hi"] = f"{FINDINGS} | search severity IN (critical, high) | stats count"
S["t_falco_hi"] = f"{FALCO} severity IN (high, critical) | stats count"
S["t_canary"] = f"{CANARY} | stats count"
S["t_auth_fail"] = f"{AUTH} action=failure | stats count"
S["t_mcp_denied"] = f"{MCP} | search outcome=denied | stats count"

# --- findings (85_detections) -----------------------------------------------------
S["f_over_time"] = f"{FINDINGS} | timechart count by severity"
S["f_by_technique"] = (
    f"{FINDINGS} | stats count latest(_time) as last values(detection) as Detections by tactic technique "
    f"{WHEN} | sort - last | rename tactic as Tactic technique as Technique count as Findings "
    "| table Tactic Technique Detections Findings When"
)
S["f_recent"] = (
    f"{FINDINGS} {SEV_ORDER} | sort sev_rank - _time | head 200 "
    "| eval Time=strftime(_time, \"%Y-%m-%d %H:%M\"), detail=substr(detail, 1, 200) "
    "| rename severity as Severity detection as Detection technique as Technique host as Host user as User "
    "src as Source object as Object detail as Detail "
    "| table Time Severity Detection Technique Host User Source Object Detail"
)

# --- every fired alert (detections, Falco, honeypot, secret leaks) ------------------
S["alerts_fired"] = (
    f"index=_audit action=alert_fired ss_app IN {OUR_APPS} "
    "| stats count latest(_time) as last by ss_app ss_name severity "
    "| eval Severity=case(severity==5,\"critical\", severity==4,\"high\", severity==3,\"medium\", "
    "severity==2,\"low\", true(),\"info\") "
    f"{WHEN} | sort - last | rename ss_name as Alert ss_app as App count as Fired "
    "| table Alert App Severity Fired When"
)

# --- the security agent (homelab_agents on coding-box) --------------------------------
AGENT = "index=ai_agents sourcetype=homelab:agent agent=sec"
AGENT_ITEMS = (f"{AGENT} kind=item | rename triage.verdict as verdict triage.confidence as confidence "
               "triage.headline as headline triage.summary as summary triage.severity as agent_severity")
QUIET = '("expected_activity", "benign", "transient")'
S["ag_t_runs"] = f"{AGENT} kind=run | stats count"
S["ag_t_failed"] = f'{AGENT} kind=run status=error | stats count'
S["ag_t_triaged"] = f"{AGENT} kind=run | stats sum(triaged) as n | fillnull value=0 n"
S["ag_t_attention"] = f"{AGENT_ITEMS} | where isnotnull(verdict) AND NOT verdict IN {QUIET} | stats count"
S["ag_t_cost"] = f'{AGENT} kind=run | stats sum(cost_usd) as c | eval c=round(coalesce(c, 0), 2)'
S["ag_verdicts"] = f'{AGENT_ITEMS} | eval verdict=coalesce(verdict, "not triaged") | timechart count by verdict'
S["ag_items"] = (
    f"{AGENT_ITEMS} | where isnotnull(verdict) "
    f"| eval rank=if(verdict IN {QUIET}, 1, 0), What=coalesce(headline, summary) "
    "| sort rank - _time | eval When=strftime(_time, \"%Y-%m-%d %H:%M\") "
    "| rename title as Item target_host as Host verdict as Verdict confidence as Confidence agent_severity as \"Agent severity\" "
    "| table When Verdict Confidence \"Agent severity\" Item Host What"
)
S["ag_runs"] = (
    f"{AGENT} kind=run | eval When=strftime(_time, \"%Y-%m-%d %H:%M\"), \"Cost $\"=round(cost_usd, 2) "
    "| sort - _time | rename status as Status collected as Collected new as New triaged as Triaged "
    "triage_errors as \"Triage errors\" duration_s as \"Seconds\" error as Error "
    "| table When Status Collected New Triaged \"Triage errors\" \"Cost $\" Seconds Error"
)

# --- Falco ------------------------------------------------------------------------
S["falco_over_time"] = f"{FALCO} | timechart count by severity"
S["falco_rules"] = (
    f"{FALCO} | rename finding_info.title as rule "
    "| stats count dc(host) as hosts values(host) as Hosts latest(_time) as last by rule severity "
    f"{SEV_ORDER} {WHEN} | sort sev_rank - count "
    "| rename rule as Rule severity as Severity count as Events "
    "| table Severity Rule Events Hosts When"
)
S["falco_recent"] = (
    f"{FALCO} severity IN (medium, high, critical) {SEV_ORDER} | sort sev_rank - _time | head 100 "
    "| eval Time=strftime(_time, \"%Y-%m-%d %H:%M\"), Detail=substr(message, 1, 220) "
    "| rename severity as Severity finding_info.title as Rule host as Host "
    "| table Time Severity Rule Host Detail"
)

# --- honeypot ---------------------------------------------------------------------
S["canary"] = (
    f"{CANARY} | stats count min(_time) as first latest(_time) as last values(signature) as Activity "
    "values(dest_port) as Ports values(user) as Usernames by src "
    "| eval \"First seen\"=strftime(first, \"%Y-%m-%d %H:%M\") "
    f"{WHEN} | sort - last | rename src as Source count as Events "
    "| table Source Activity Ports Usernames Events \"First seen\" When"
)

# --- logins -----------------------------------------------------------------------
S["auth_over_time"] = (
    f"{AUTH} action IN (success, failure) app=* | eval series=app.\" \".action | timechart usenull=f count by series"
)
S["auth_sources"] = (
    f"{AUTH} action IN (success, failure) "
    "| stats count(eval(action==\"success\")) as Successes count(eval(action==\"failure\")) as Failures "
    "dc(dest) as hosts values(dest) as Targets latest(_time) as last by app user src "
    f"{WHEN} | sort - Failures - last | rename app as Method user as User src as Source "
    "| table Method User Source Targets Successes Failures When"
)

# --- Kubernetes API (k3s-01) ----------------------------------------------------------
S["k8s_denied"] = (
    "`hl_k8s_audit` | where 'responseStatus.code'==401 OR 'responseStatus.code'==403 "
    "| eval code='responseStatus.code' | timechart count by code"
)
S["k8s_people_writes"] = (
    "`hl_k8s_audit` verb IN (create, update, patch, delete, deletecollection) "
    "| where NOT `hl_k8s_platform_user` "
    "| eval resource='objectRef.resource'.if(isnotnull('objectRef.subresource'), \"/\".'objectRef.subresource', \"\") "
    "| rename objectRef.name as obj_name objectRef.namespace as ns user.username as who "
    "| stats count latest(_time) as last values(obj_name) as Names by who verb resource ns "
    f"{WHEN} | sort - last | rename who as User verb as Verb resource as Resource "
    "ns as Namespace count as Requests | eval Names=mvjoin(mvindex(Names, 0, 4), \", \") "
    "| table User Verb Resource Namespace Names Requests When"
)
S["k8s_denied_tbl"] = (
    "`hl_k8s_audit` | where 'responseStatus.code'==401 OR 'responseStatus.code'==403 "
    "| eval user=coalesce('user.username', \"(unauthenticated)\"), src=mvindex('sourceIPs{}', 0), "
    "code='responseStatus.code', res='objectRef.resource' "
    "| stats count latest(_time) as last values(verb) as Verbs values(res) as Resources by user src code "
    f"{WHEN} | sort - count | rename user as User src as Source code as Code count as Requests "
    "| table User Source Code Verbs Resources Requests When"
)

# --- Vault ------------------------------------------------------------------------
S["vault_identities"] = (
    "`hl_vault_audit` | eval who=coalesce('auth.display_name', \"(no token)\"), "
    "status=if(isnotnull(error), \"error\", \"ok\"), op='request.operation', path='request.path' "
    "| stats count(eval(status==\"ok\")) as OK count(eval(status==\"error\")) as Errors "
    "values(op) as Operations dc(path) as Paths latest(_time) as last by who "
    f"{WHEN} | sort - Errors - OK | rename who as Identity | table Identity Operations Paths OK Errors When"
)
S["vault_errors"] = (
    "`hl_vault_audit` error=* | eval who=coalesce('auth.display_name', \"(no token)\"), "
    "op='request.operation', path='request.path' "
    "| stats count latest(_time) as last values(error) as Error by who path op "
    f"{WHEN} | sort - last | rename who as Identity path as Path op as Operation "
    "count as Requests | table Identity Operation Path Error Requests When"
)

# --- MCP gateway ------------------------------------------------------------------
S["mcp_clients"] = (
    f"{MCP} | search NOT tool IN (initialized, gw-probe) "
    "| stats count(eval(outcome==\"success\")) as OK count(eval(outcome==\"denied\")) as Denied "
    "count(eval(outcome==\"failure\")) as Failed dc(tool) as Tools latest(_time) as last by client "
    f"{WHEN} | sort - Denied - OK | rename client as Client | table Client Tools OK Denied Failed When"
)
S["mcp_denied"] = (
    f"{MCP} | search outcome IN (denied, failure) "
    "| stats count latest(_time) as last by client tool outcome "
    f"{WHEN} | sort - last | rename client as Client tool as Tool outcome as Outcome count as Calls "
    "| table Client Tool Outcome Calls When"
)

# --- detection health (fixed: last 24 hours) ----------------------------------------
S["det_health"] = (
    f"| rest /servicesNS/-/-/saved/searches splunk_server=local count=0 "
    f"| search eai:acl.app IN {OUR_APPS} is_scheduled=1 disabled=0 "
    "| rename title as savedsearch_name eai:acl.app as App | fields savedsearch_name App cron_schedule "
    f"| join type=left savedsearch_name [search index=_internal sourcetype=scheduler app IN {OUR_APPS} earliest=-24h "
    "| stats count(eval(status==\"success\")) as ok count(eval(status!=\"success\")) as not_ok "
    "latest(_time) as last avg(run_time) as rt by savedsearch_name] "
    "| fillnull value=0 ok not_ok "
    "| eval Status=case(isnull(last), \"NEVER RAN (24 h)\", not_ok>0, \"SKIPPED OR FAILED\", true(), \"ok\"), "
    "\"Last run\"=strftime(last, \"%Y-%m-%d %H:%M\"), \"Avg run (s)\"=round(rt, 1) "
    "| eval rank=if(Status==\"ok\", 1, 0) | sort rank savedsearch_name "
    "| rename savedsearch_name as Search cron_schedule as Schedule ok as Runs not_ok as \"Not run\" "
    "| table Status Search App Schedule Runs \"Not run\" \"Avg run (s)\" \"Last run\""
)
S["t_det_unhealthy"] = S["det_health"] + " | search Status!=ok | stats count"

FIXED_24H = {"det_health", "t_det_unhealthy"}

# --- visualizations and layout -----------------------------------------------------
SEVERITY_COLORS = {"critical": "#D41F1F", "high": "#EC9960", "medium": "#F8BE34", "low": "#7B98BD",
                   "informational": "#A7A9AC"}
viz = {}
layout = []
y = 0


def place(vid, x, w, h):
    layout.append({"item": vid, "type": "block", "position": {"x": x, "y": y, "w": w, "h": h}})


def heading(vid, text, note=""):
    global y
    md = f"## {text}" + (f"\n{note}" if note else "")
    viz[vid] = {"type": "splunk.markdown", "options": {"markdown": md, "fontSize": "large" if not note else "default"}}
    h = 40 if not note else 60
    place(vid, 0, 1200, h)
    y += h


def single(vid, ds, title, red_above=None):
    opts = {"majorValue": "> primary | seriesByIndex(0) | lastPoint()"}
    v = {"type": "splunk.singlevalue", "dataSources": {"primary": ds}, "title": title, "options": opts}
    if red_above is not None:
        opts["majorColor"] = "> majorValue | rangeValue(colors)"
        v["context"] = {"colors": [
            {"to": red_above + 0.5, "value": "#118832"},
            {"from": red_above + 0.5, "value": "#D41F1F"},
        ]}
    viz[vid] = v


def table(vid, ds, title):
    viz[vid] = {"type": "splunk.table", "dataSources": {"primary": ds}, "title": title,
                "options": {"count": 20}}


def stacked(vid, ds, title, colors=None):
    opts = {"stackMode": "stacked", "xAxisTitleVisibility": "hide", "yAxisTitleText": "events",
            "legendDisplay": "right"}
    if colors:
        opts["seriesColorsByField"] = colors
    viz[vid] = {"type": "splunk.column", "dataSources": {"primary": ds}, "title": title, "options": opts}


heading("viz_h_status", "Status", "Counts for the selected time range; red means anything to look at.")
tiles = [
    ("viz_t_findings", "ds_t_findings", "Detection findings", None),
    ("viz_t_findings_hi", "ds_t_findings_hi", "Findings: critical or high", 0),
    ("viz_t_falco_hi", "ds_t_falco_hi", "Falco: high or critical", 0),
    ("viz_t_canary", "ds_t_canary", "Honeypot connections", 0),
    ("viz_t_auth_fail", "ds_t_auth_fail", "Failed logins", 0),
    ("viz_t_mcp_denied", "ds_t_mcp_denied", "MCP calls denied", 0),
]
for i, (vid, ds, title, red) in enumerate(tiles):
    single(vid, ds, title, red)
    place(vid, i * 200, 200, 110)
y += 110

heading("viz_h_findings", "Detection findings (ATT&CK v20)",
        "From the HL detections in `85_detections`: `index=security sourcetype=homelab:detection`, the queue the security agent reads.")
stacked("viz_f_time", "ds_f_over_time", "Findings by severity", SEVERITY_COLORS)
place("viz_f_time", 0, 600, 280)
table("viz_f_tech", "ds_f_by_technique", "By ATT&CK tactic and technique")
place("viz_f_tech", 600, 600, 280)
y += 280
table("viz_f_recent", "ds_f_recent", "Findings, most severe first")
place("viz_f_recent", 0, 1200, 420)
y += 420

heading("viz_h_alerts", "Fired alerts", "Every alert from the detections, Falco, honeypot and secret-leak apps.")
table("viz_alerts", "ds_alerts_fired", "Alerts fired")
place("viz_alerts", 0, 1200, 300)
y += 300

heading("viz_h_agent", "Security agent",
        "The triage agent on coding-box (`homelab_agents`), every 30 minutes. Cost is the API-equivalent the SDK reports; "
        "on the subscription it counts against usage limits. If it stops running, the *Agents - not running or failing* alert posts to Slack.")
agent_tiles = [
    ("viz_ag_t_runs", "ds_ag_t_runs", "Runs", None),
    ("viz_ag_t_failed", "ds_ag_t_failed", "Failed runs", 0),
    ("viz_ag_t_triaged", "ds_ag_t_triaged", "Items triaged", None),
    ("viz_ag_t_attention", "ds_ag_t_attention", "Not routine", 0),
    ("viz_ag_t_cost", "ds_ag_t_cost", "Cost (USD, API-equivalent)", None),
]
for i, (vid, ds, title, red) in enumerate(agent_tiles):
    single(vid, ds, title, red)
    place(vid, i * 240, 240, 110)
y += 110
stacked("viz_ag_verdicts", "ds_ag_verdicts", "Items by verdict")
place("viz_ag_verdicts", 0, 400, 300)
table("viz_ag_items", "ds_ag_items", "Triaged items, not routine first")
place("viz_ag_items", 400, 800, 300)
y += 300
table("viz_ag_runs", "ds_ag_runs", "Runs")
place("viz_ag_runs", 0, 1200, 260)
y += 260

heading("viz_h_falco", "Falco runtime")
stacked("viz_falco_time", "ds_falco_over_time", "Falco events by severity", SEVERITY_COLORS)
place("viz_falco_time", 0, 600, 300)
table("viz_falco_rules", "ds_falco_rules", "Rules, most severe first")
place("viz_falco_rules", 600, 600, 300)
y += 300
table("viz_falco_recent", "ds_falco_recent", "Medium and above, most severe first")
place("viz_falco_recent", 0, 1200, 360)
y += 360

heading("viz_h_canary", "Honeypot (OpenCanary, 192.168.4.240)", "Nothing should ever connect to it.")
table("viz_canary", "ds_canary", "Connections by source")
place("viz_canary", 0, 1200, 200)
y += 200

heading("viz_h_auth", "Logins", "Tailscale SSH, OpenSSH, sudo, Proxmox and UniFi admin, via CIM tag=authentication.")
stacked("viz_auth_time", "ds_auth_over_time", "Logins by method and result")
place("viz_auth_time", 0, 600, 300)
table("viz_auth_src", "ds_auth_sources", "Who logged in from where (failures first)")
place("viz_auth_src", 600, 600, 300)
y += 300

heading("viz_h_k8s", "Kubernetes API (k3s-01)",
        "Audit log. Platform controllers (`hl_k8s_platform_user`) are left out of the writes table.")
stacked("viz_k8s_denied", "ds_k8s_denied", "Unauthenticated (401) and forbidden (403) requests")
place("viz_k8s_denied", 0, 600, 280)
table("viz_k8s_denied_tbl", "ds_k8s_denied_tbl", "401 and 403 by caller")
place("viz_k8s_denied_tbl", 600, 600, 280)
y += 280
table("viz_k8s_writes", "ds_k8s_people_writes", "Changes made by people and apps")
place("viz_k8s_writes", 0, 1200, 320)
y += 320

heading("viz_h_vault", "Vault")
table("viz_vault_ids", "ds_vault_identities", "Requests by identity")
place("viz_vault_ids", 0, 600, 280)
table("viz_vault_err", "ds_vault_errors", "Errors (denied or failed requests)")
place("viz_vault_err", 600, 600, 280)
y += 280

heading("viz_h_mcp", "MCP gateway (mcp.bmosan.com)")
table("viz_mcp_clients", "ds_mcp_clients", "Tool calls by client")
place("viz_mcp_clients", 0, 600, 260)
table("viz_mcp_denied", "ds_mcp_denied", "Denied or failed calls")
place("viz_mcp_denied", 600, 600, 260)
y += 260

heading("viz_h_health", "Detection health (last 24 hours)",
        "Every scheduled search in the detection, Falco, honeypot and secret-leak apps, and whether it ran.")
single("viz_t_det", "ds_t_det_unhealthy", "Searches not running cleanly", 0)
place("viz_t_det", 0, 200, 110)
table("viz_det_health", "ds_det_health", "Scheduled searches")
place("viz_det_health", 200, 1000, 520)
y += 520

data_sources = {}
for key, q in S.items():
    ds = {"type": "ds.search", "name": key, "options": {"query": q}}
    if key in FIXED_24H:
        ds["options"]["queryParameters"] = {"earliest": "-24h", "latest": "now"}
        ds["options"]["refresh"] = "5m"
    else:
        ds["options"]["queryParameters"] = {"earliest": "$global_time.earliest$", "latest": "$global_time.latest$"}
    data_sources[f"ds_{key}"] = ds

definition = {
    "title": "Homelab Security",
    "description": "ATT&CK v20 findings, fired alerts, Falco, the honeypot, logins, the Kubernetes API, Vault and the MCP gateway, and detection health.",
    "inputs": {
        "input_global_trp": {"type": "input.timerange", "title": "Time range",
                             "options": {"token": "global_time", "defaultValue": "-24h@h,now"}}
    },
    "defaults": {"dataSources": {"ds.search": {"options": {"queryParameters": {}}}}},
    "visualizations": viz,
    "dataSources": data_sources,
    "layout": {"type": "absolute", "options": {"width": 1200, "height": y, "display": "auto-scale"},
               "structure": layout, "globalInputs": ["input_global_trp"]},
}

xml = (
    '<dashboard version="2" theme="dark">\n'
    "  <label>Homelab Security</label>\n"
    "  <description>Generated by apps/85_homelab_security/build_dashboard.py; edit that, not this file.</description>\n"
    "  <definition><![CDATA[\n" + json.dumps(definition, indent=2) + "\n  ]]></definition>\n"
    '  <meta type="hiddenElements"><![CDATA[{"hideEdit": false, "hideOpenInSearch": false, "hideExport": false}]]></meta>\n'
    "</dashboard>\n"
)
out = pathlib.Path(__file__).parent / "default/data/ui/views/homelab_security.xml"
out.write_text(xml)
print(f"wrote {out} ({len(S)} searches, {len(viz)} panels)")
