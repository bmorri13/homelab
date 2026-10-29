#!/usr/bin/env python3
"""Generate default/data/ui/views/homelab_ops.xml (Dashboard Studio) from the
searches below. Edit here, run `python3 build_dashboard.py`, commit both files.

Layout follows the USE method (utilization, saturation, errors) per resource,
then Proxmox guests, Docker and Kubernetes. Counters arrive as per-interval
deltas (cumulativetodelta in the collectors), so sum() over a span is the amount
in that span; per-second rates divide by the span in seconds.
"""
import json
import pathlib

GIB = 1073741824
MIB = 1048576

# --- searches -------------------------------------------------------------------
S = {}

# Top-row status tiles (fixed short windows, independent of the time picker)
S["hosts_reporting"] = (
    "| mstats count(system.uptime) as c where index=metrics earliest=-5m by host "
    "| stats count as hosts"
)
S["hosts_silent"] = (
    "| mstats count(system.uptime) as c where index=metrics earliest=-24h span=1m by host "
    "| stats max(_time) as last by host "
    "| eval age_min=round((now()-last)/60) | where age_min>5 "
    "| eval \"Last seen\"=strftime(last, \"%Y-%m-%d %H:%M\") "
    "| rename host as Host age_min as \"Minutes silent\" | table Host \"Last seen\" \"Minutes silent\""
)
S["hosts_silent_count"] = S["hosts_silent"] + " | stats count"
S["cpu_hot"] = (
    "| mstats avg(system.cpu.utilization) as idle where index=metrics state=idle earliest=-5m by host "
    "| where (1-idle)*100>85 | stats count"
)
S["fs_full"] = (
    "| mstats latest(system.filesystem.utilization) as u where index=metrics earliest=-10m by host mountpoint "
    "| where u>0.85 | stats count"
)
S["vms_running"] = (
    "| mstats latest(proxmox_vm_uptime) as up where index=metrics earliest=-5m by vmid "
    "| where up>0 | stats count"
)
S["pods_bad"] = (
    "| mstats latest(k8s.pod.phase) as p where index=metrics earliest=-5m by k8s.namespace.name k8s.pod.name "
    "| where p!=2 AND p!=3 | stats count"
)
S["restarts_24h"] = (
    "| mstats max(k8s.container.restarts) as mx min(k8s.container.restarts) as mn where index=metrics earliest=-24h "
    "by k8s.pod.name k8s.container.name | eval r=mx-mn | stats sum(r) as k "
    "| appendcols [| mstats sum(container.restarts) as d where index=metrics earliest=-24h] "
    "| eval restarts=round(coalesce(k,0)+coalesce(d,0)) | fields restarts"
)

# Proxmox
S["pve_nodes"] = (
    "| mstats latest(proxmox_node_cpustat_cpu) as cpu latest(proxmox_node_cpustat_cpus) as cpus "
    "latest(proxmox_node_memory_memused) as mu latest(proxmox_node_memory_memtotal) as mt "
    "latest(proxmox_node_cpustat_avg5) as load5 latest(proxmox_node_uptime) as up "
    "where index=metrics earliest=-10m by node "
    "| join type=left node [| mstats latest(proxmox_vm_uptime) as vup where index=metrics earliest=-10m by node vmid "
    "| eval running=if(vup>0,1,0) | stats sum(running) as running count as guests by node] "
    f"| eval \"CPU %\"=round(cpu*100,1), \"Memory %\"=round(mu/mt*100,1), \"Memory GB\"=round(mu/{GIB},1).\" / \".round(mt/{GIB}), "
    "\"Load (5m) per CPU\"=round(load5/cpus,2), \"Guests running\"=running.\" / \".guests, \"Uptime (days)\"=round(up/86400,1) "
    "| rename node as Node cpus as CPUs "
    "| table Node CPUs \"CPU %\" \"Load (5m) per CPU\" \"Memory %\" \"Memory GB\" \"Guests running\" \"Uptime (days)\""
)
S["pve_guests"] = (
    "| mstats latest(proxmox_vm_cpu) as cpu latest(proxmox_vm_cpus) as vcpu latest(proxmox_vm_mem) as mem "
    "latest(proxmox_vm_maxmem) as maxmem latest(proxmox_vm_uptime) as up "
    "avg(proxmox_vm_pressurecpusome) as cpu_psi avg(proxmox_vm_pressureiosome) as io_psi "
    "avg(proxmox_vm_pressurememorysome) as mem_psi "
    "sum(proxmox_vm_netin_total) as netin sum(proxmox_vm_netout_total) as netout "
    "sum(proxmox_vm_diskread_total) as dr sum(proxmox_vm_diskwrite_total) as dw "
    "where index=metrics earliest=-10m by name vmid node type "
    "| eval Status=if(up>0,\"running\",\"stopped\"), \"CPU %\"=round(cpu*100,1), "
    "\"Memory % (Proxmox view)\"=if(maxmem>0, round(mem/maxmem*100,1), null()), "
    f"\"Memory GB\"=round(mem/{GIB},1).\" / \".round(maxmem/{GIB},1), "
    "\"Net in KB/s\"=round(netin/600/1024,1), \"Net out KB/s\"=round(netout/600/1024,1), "
    f"\"Disk read MB/s\"=round(dr/600/{MIB},2), \"Disk write MB/s\"=round(dw/600/{MIB},2), "
    "\"PSI cpu/io/mem %\"=round(cpu_psi,1).\" / \".round(io_psi,1).\" / \".round(mem_psi,1), "
    "\"Uptime (days)\"=round(up/86400,1) "
    "| rename name as Guest vmid as VMID node as Node type as Type vcpu as vCPU "
    "| sort Status -\"CPU %\" "
    "| table Guest VMID Type Node Status vCPU \"CPU %\" \"Memory % (Proxmox view)\" \"Memory GB\" \"PSI cpu/io/mem %\" "
    "\"Net in KB/s\" \"Net out KB/s\" \"Disk read MB/s\" \"Disk write MB/s\" \"Uptime (days)\""
)
S["pve_storage"] = (
    "| mstats latest(proxmox_storage_used) as used latest(proxmox_storage_total) as total "
    "where index=metrics earliest=-10m by node storage | where total>0 "
    f"| eval \"Used %\"=round(used/total*100,1), \"Used GB\"=round(used/{GIB}), \"Total GB\"=round(total/{GIB}) "
    "| rename node as Node storage as Storage | sort - \"Used %\" "
    "| table Node Storage \"Used %\" \"Used GB\" \"Total GB\""
)

# Hosts (every VM and Proxmox node with the OTel agent)
S["cpu_busy"] = (
    "| mstats avg(system.cpu.utilization) as idle where index=metrics state=idle span=5m by host "
    "| eval busy=round((1-idle)*100,1) | xyseries _time host busy"
)
S["mem_used"] = (
    "| mstats avg(system.memory.utilization) as u where index=metrics state=used span=5m by host "
    "| eval u=round(u*100,1) | xyseries _time host u"
)
S["load_per_cpu"] = (
    "| mstats avg(system.cpu.load_average.5m) as l avg(system.cpu.logical.count) as n where index=metrics span=5m by host "
    "| eval l=round(l/n,2) | xyseries _time host l"
)
S["disk_busy"] = (
    "| mstats sum(system.disk.io_time) as t where index=metrics span=5m by host device "
    "| eval busy=round(t/300*100,1) | stats max(busy) as busy by _time host | xyseries _time host busy"
)
S["net_mbps"] = (
    "| mstats sum(system.network.io) as b where index=metrics span=5m by host "
    "| eval mbps=round(b*8/300/1000000,2) | xyseries _time host mbps"
)
S["net_errors"] = (
    "| mstats sum(system.network.errors) as e sum(system.network.dropped) as d where index=metrics earliest=-24h by host device "
    "| where e>0 OR d>0 | eval e=round(e), d=round(d) | rename host as Host device as Interface e as \"Errors (24h)\" d as \"Dropped (24h)\" "
    "| sort - \"Errors (24h)\""
)
S["disk_mbs"] = (
    "| mstats sum(system.disk.io) as b where index=metrics span=5m by host "
    f"| eval v=round(b/300/{MIB},2) | xyseries _time host v"
)
S["filesystems"] = (
    "| mstats latest(system.filesystem.usage) as v where index=metrics earliest=-10m by host mountpoint state "
    "| eval {state}=v | stats sum(used) as used sum(free) as free by host mountpoint "
    f"| eval \"Used %\"=round(used/(used+free)*100,1), \"Free GB\"=round(free/{GIB},1), \"Size GB\"=round((used+free)/{GIB}) "
    "| rename host as Host mountpoint as Mount | sort - \"Used %\" | table Host Mount \"Used %\" \"Free GB\" \"Size GB\""
)
S["swap"] = (
    "| mstats avg(system.paging.utilization) as u where index=metrics state=used earliest=-10m by host "
    "| eval \"Swap used %\"=round(u*100,1) | where 'Swap used %'>0 | rename host as Host | sort - \"Swap used %\" | table Host \"Swap used %\""
)

# Docker
S["containers"] = (
    "| mstats latest(container.cpu.utilization) as cpu latest(container.memory.usage.total) as mem "
    "latest(container.memory.percent) as mempct latest(container.uptime) as up "
    "where index=metrics earliest=-10m by host container.name container.image.name "
    f"| eval \"CPU %\"=round(cpu,1), \"Memory MB\"=round(mem/{MIB}), \"Memory % of limit\"=round(mempct,1), "
    "\"Uptime (h)\"=round(up/3600,1) "
    "| rename host as Host container.name as Container container.image.name as Image "
    "| sort - \"CPU %\" | table Host Container Image \"CPU %\" \"Memory MB\" \"Memory % of limit\" \"Uptime (h)\""
)

# Kubernetes (k3s-01)
S["k8s_phase"] = (
    "| mstats latest(k8s.pod.phase) as p where index=metrics span=5m by k8s.pod.name "
    "| eval phase=case(p==1,\"Pending\",p==2,\"Running\",p==3,\"Succeeded\",p==4,\"Failed\",true(),\"Unknown\") "
    "| timechart span=5m count by phase"
)
S["k8s_workloads"] = (
    "| mstats latest(k8s.deployment.desired) as want latest(k8s.deployment.available) as have "
    "where index=metrics earliest=-10m by k8s.namespace.name k8s.deployment.name "
    "| rename k8s.deployment.name as Workload | eval Kind=\"Deployment\" "
    "| append [| mstats latest(k8s.statefulset.desired_pods) as want latest(k8s.statefulset.ready_pods) as have "
    "where index=metrics earliest=-10m by k8s.namespace.name k8s.statefulset.name "
    "| rename k8s.statefulset.name as Workload | eval Kind=\"StatefulSet\"] "
    "| append [| mstats latest(k8s.daemonset.desired_scheduled_nodes) as want latest(k8s.daemonset.ready_nodes) as have "
    "where index=metrics earliest=-10m by k8s.namespace.name k8s.daemonset.name "
    "| rename k8s.daemonset.name as Workload | eval Kind=\"DaemonSet\"] "
    "| eval Status=if(have<want,\"DEGRADED\",\"ok\") "
    "| rename k8s.namespace.name as Namespace want as Desired have as Ready "
    "| sort - Status Namespace | table Status Namespace Kind Workload Desired Ready"
)
S["k8s_top_pods"] = (
    "| mstats latest(k8s.pod.cpu.usage) as cpu latest(k8s.pod.memory.working_set) as mem "
    "where index=metrics earliest=-10m by k8s.namespace.name k8s.pod.name "
    "| join type=left k8s.namespace.name k8s.pod.name [| mstats latest(k8s.container.memory_limit) as lim "
    "where index=metrics earliest=-10m by k8s.namespace.name k8s.pod.name k8s.container.name "
    "| stats sum(lim) as lim by k8s.namespace.name k8s.pod.name] "
    f"| eval \"CPU (millicores)\"=round(cpu*1000), \"Memory MB\"=round(mem/{MIB}), \"% of memory limit\"=if(lim>0, round(mem/lim*100,1), null()) "
    "| rename k8s.namespace.name as Namespace k8s.pod.name as Pod | sort - \"Memory MB\" | head 25 "
    "| table Namespace Pod \"CPU (millicores)\" \"Memory MB\" \"% of memory limit\""
)
S["k8s_restarts"] = (
    "| mstats max(k8s.container.restarts) as mx min(k8s.container.restarts) as mn where index=metrics earliest=-24h "
    "by k8s.namespace.name k8s.pod.name k8s.container.name | eval \"Restarts (24h)\"=mx-mn "
    "| where 'Restarts (24h)'>0 | rename k8s.namespace.name as Namespace k8s.pod.name as Pod "
    "k8s.container.name as Container mx as \"Restarts (total)\" | sort - \"Restarts (24h)\" "
    "| table Namespace Pod Container \"Restarts (24h)\" \"Restarts (total)\""
)
S["k8s_node"] = (
    "| mstats avg(k8s.node.cpu.usage) as cpu avg(k8s.node.memory.working_set) as mem where index=metrics span=5m "
    f"| eval \"CPU (cores)\"=round(cpu,2), \"Memory GB\"=round(mem/{GIB},2) | fields _time \"CPU (cores)\" \"Memory GB\""
)

# The ops agent (homelab_agents, CronJobs on k3s-01), and the health of both agents.
AGENT = "index=ai_agents sourcetype=homelab:agent agent=ops"
AGENT_ITEMS = (f"{AGENT} kind=item | rename triage.verdict as verdict triage.confidence as confidence "
               "triage.headline as headline triage.summary as summary triage.severity as agent_severity")
QUIET = '("expected_activity", "benign", "transient")'
S["agents_health"] = (
    "| makeresults | eval agent=split(\"sec,ops\", \",\") | mvexpand agent | fields agent "
    "| join type=left agent [search index=ai_agents sourcetype=homelab:agent kind=run earliest=-24h "
    "| stats max(_time) as last_run count as runs_24h by agent] "
    "| join type=left agent [search index=ai_agents sourcetype=homelab:agent kind=run earliest=-65m "
    "| stats count as runs sum(eval(if(status=\"error\",1,0))) as failed_runs sum(triaged) as triaged "
    "sum(triage_errors) as triage_errors latest(error) as last_error by agent] "
    "| fillnull value=0 runs failed_runs triaged triage_errors runs_24h "
    "| eval minutes_since=if(isnull(last_run), null(), round((now()-last_run)/60)) "
    "| eval Status=case(isnull(last_run), \"NOT RUNNING (24 h)\", minutes_since>75, \"NOT RUNNING\", "
    "runs>0 AND failed_runs==runs, \"RUNS FAILING\", triage_errors>0 AND triaged==0, \"TRIAGE FAILING\", true(), \"ok\") "
    "| eval Agent=if(agent==\"sec\", \"Security agent\", \"Ops agent\"), \"Last run\"=strftime(last_run, \"%Y-%m-%d %H:%M\") "
    "| rename minutes_since as \"Minutes ago\" runs as \"Runs (1 h)\" failed_runs as \"Failed (1 h)\" runs_24h as \"Runs (24 h)\" last_error as \"Last error (1 h)\" "
    "| table Status Agent \"Last run\" \"Minutes ago\" \"Runs (1 h)\" \"Failed (1 h)\" \"Runs (24 h)\" \"Last error (1 h)\""
)
S["ag_t_items"] = f"{AGENT} kind=run | stats sum(collected) as n | fillnull value=0 n"
S["ag_t_triaged"] = f"{AGENT} kind=run | stats sum(triaged) as n | fillnull value=0 n"
S["ag_t_attention"] = f"{AGENT_ITEMS} | where isnotnull(verdict) AND NOT verdict IN {QUIET} | stats count"
S["ag_t_cost"] = f"{AGENT} kind=run | stats sum(cost_usd) as c | eval c=round(coalesce(c, 0), 2)"
S["ag_items"] = (
    f"{AGENT_ITEMS} | where isnotnull(verdict) "
    f"| eval rank=if(verdict IN {QUIET}, 1, 0), What=coalesce(headline, summary) "
    "| sort rank - _time | eval When=strftime(_time, \"%Y-%m-%d %H:%M\") "
    "| rename title as Item target_host as Host verdict as Verdict confidence as Confidence agent_severity as \"Agent severity\" "
    "| table When Verdict Confidence \"Agent severity\" Item Host What"
)

# Searches that follow the time picker; the rest set their own short windows.
TIMED = {"cpu_busy", "mem_used", "load_per_cpu", "disk_busy", "net_mbps", "disk_mbs", "k8s_phase", "k8s_node"}

# --- visualizations and layout -----------------------------------------------------
viz = {}
layout = []
y = 0


def place(vid, x, w, h):
    layout.append({"item": vid, "type": "block", "position": {"x": x, "y": y, "w": w, "h": h}})


def heading(vid, text):
    global y
    viz[vid] = {"type": "splunk.markdown", "options": {"markdown": f"## {text}", "fontSize": "large"}}
    place(vid, 0, 1200, 40)
    y += 40


def single(vid, ds, title, red_above=None, unit=""):
    opts = {"majorValue": "> primary | seriesByIndex(0) | lastPoint()"}
    if red_above is not None:
        opts["majorColor"] = "> majorValue | rangeValue(colors)"
    v = {"type": "splunk.singlevalue", "dataSources": {"primary": ds}, "title": title, "options": opts}
    if red_above is not None:
        v["context"] = {"colors": [
            {"to": red_above + 0.5, "value": "#118832"},
            {"from": red_above + 0.5, "value": "#D41F1F"},
        ]}
    if unit:
        v["options"]["unit"] = unit
    viz[vid] = v


def table(vid, ds, title):
    viz[vid] = {"type": "splunk.table", "dataSources": {"primary": ds}, "title": title,
                "options": {"count": 20}}


def line(vid, ds, title, ytitle):
    viz[vid] = {"type": "splunk.line", "dataSources": {"primary": ds}, "title": title,
                "options": {"yAxisTitleText": ytitle, "xAxisTitleVisibility": "hide", "legendDisplay": "right"}}


heading("viz_h_status", "Status")
tiles = [
    ("viz_hosts", "ds_hosts_reporting", "Hosts reporting (5 min)", None),
    ("viz_silent", "ds_hosts_silent_count", "Hosts silent > 5 min", 0),
    ("viz_cpuhot", "ds_cpu_hot", "Hosts CPU > 85%", 0),
    ("viz_fsfull", "ds_fs_full", "Filesystems > 85% full", 0),
    ("viz_vms", "ds_vms_running", "Proxmox guests running", None),
    ("viz_pods", "ds_pods_bad", "Pods not Running/Succeeded", 0),
]
for i, (vid, ds, title, red) in enumerate(tiles):
    single(vid, ds, title, red)
    place(vid, i * 200, 200, 110)
y += 110
single("viz_restarts", "ds_restarts_24h", "Container restarts (24 h)", 0)
place("viz_restarts", 0, 200, 110)
table("viz_silent_tbl", "ds_hosts_silent", "Hosts that stopped sending metrics (last 24 h)")
place("viz_silent_tbl", 200, 1000, 110)
y += 110

heading("viz_h_pve", "Proxmox")
table("viz_pve_nodes", "ds_pve_nodes", "Nodes")
place("viz_pve_nodes", 0, 1200, 170)
y += 170
table("viz_pve_guests", "ds_pve_guests", "Guests (VMs and containers), last 10 min. Proxmox counts a VM's page cache as used memory; the Hosts section shows the guest's own view. PSI = % of time stalled on CPU / I/O / memory")
place("viz_pve_guests", 0, 1200, 420)
y += 420

heading("viz_h_hosts", "Hosts: utilization and saturation")
line("viz_cpu", "ds_cpu_busy", "CPU busy %", "%")
place("viz_cpu", 0, 600, 280)
line("viz_mem", "ds_mem_used", "Memory used % (excludes cache)", "%")
place("viz_mem", 600, 600, 280)
y += 280
line("viz_load", "ds_load_per_cpu", "Load (5 min) per CPU: above 1 means a run queue", "load / CPU")
place("viz_load", 0, 600, 280)
line("viz_diskbusy", "ds_disk_busy", "Busiest disk, % of time busy", "%")
place("viz_diskbusy", 600, 600, 280)
y += 280
line("viz_net", "ds_net_mbps", "Network throughput (Mbit/s, in + out)", "Mbit/s")
place("viz_net", 0, 600, 280)
line("viz_diskio", "ds_disk_mbs", "Disk throughput (MB/s, read + write)", "MB/s")
place("viz_diskio", 600, 600, 280)
y += 280
table("viz_fs", "ds_filesystems", "Filesystems")
place("viz_fs", 0, 600, 330)
table("viz_storage", "ds_pve_storage", "Proxmox storage pools")
place("viz_storage", 600, 600, 330)
y += 330
table("viz_neterr", "ds_net_errors", "Network errors and drops (24 h)")
place("viz_neterr", 0, 600, 200)
table("viz_swap", "ds_swap", "Swap in use")
place("viz_swap", 600, 600, 200)
y += 200

heading("viz_h_docker", "Docker containers")
table("viz_containers", "ds_containers", "Containers (last 10 min)")
place("viz_containers", 0, 1200, 380)
y += 380

heading("viz_h_k8s", "Kubernetes (k3s-01)")
viz["viz_k8s_phase"] = {"type": "splunk.area", "dataSources": {"primary": "ds_k8s_phase"},
                        "title": "Pods by phase", "options": {"stackMode": "stacked", "xAxisTitleVisibility": "hide"}}
place("viz_k8s_phase", 0, 600, 280)
line("viz_k8s_node", "ds_k8s_node", "Node usage (kubelet)", "")
place("viz_k8s_node", 600, 600, 280)
y += 280
table("viz_k8s_workloads", "ds_k8s_workloads", "Workloads: desired vs ready")
place("viz_k8s_workloads", 0, 600, 380)
table("viz_k8s_top", "ds_k8s_top_pods", "Top pods by memory")
place("viz_k8s_top", 600, 600, 380)
y += 380
table("viz_k8s_restarts", "ds_k8s_restarts", "Containers that restarted (24 h)")
place("viz_k8s_restarts", 0, 1200, 220)
y += 220

heading("viz_h_agents", "Agents (CronJobs on k3s-01, last 24 h)")
table("viz_agents_health", "ds_agents_health",
      "Agent health: both agents run every 30 min; the Agents - not running or failing alert posts to Slack when this isn't ok")
place("viz_agents_health", 0, 1200, 130)
y += 130
agent_tiles = [
    ("viz_ag_t_items", "ds_ag_t_items", "Ops agent: checks tripped", None),
    ("viz_ag_t_triaged", "ds_ag_t_triaged", "Ops agent: items triaged", None),
    ("viz_ag_t_attention", "ds_ag_t_attention", "Ops agent: not routine", 0),
    ("viz_ag_t_cost", "ds_ag_t_cost", "Ops agent: cost (USD, API-equivalent)", None),
]
for i, (vid, ds, title, red) in enumerate(agent_tiles):
    single(vid, ds, title, red)
    place(vid, i * 300, 300, 110)
y += 110
table("viz_ag_items", "ds_ag_items", "Ops agent: triaged items, not routine first")
place("viz_ag_items", 0, 1200, 260)
y += 260

data_sources = {}
for key, q in S.items():
    ds = {"type": "ds.search", "name": key, "options": {"query": q}}
    if key in TIMED:
        ds["options"]["queryParameters"] = {"earliest": "$global_time.earliest$", "latest": "$global_time.latest$"}
    else:
        ds["options"]["queryParameters"] = {"earliest": "-24h", "latest": "now"}
        ds["options"]["refresh"] = "5m"
    data_sources[f"ds_{key}"] = ds

definition = {
    "title": "Homelab Operations",
    "description": "Proxmox nodes and guests, VM hosts, Docker and k3s, from index=metrics (OTel collectors and Proxmox's OpenTelemetry metric server, 60 s resolution).",
    "inputs": {
        "input_global_trp": {"type": "input.timerange", "title": "Time range",
                             "options": {"token": "global_time", "defaultValue": "-4h@m,now"}}
    },
    "defaults": {"dataSources": {"ds.search": {"options": {"queryParameters": {}}}}},
    "visualizations": viz,
    "dataSources": data_sources,
    "layout": {"type": "absolute", "options": {"width": 1200, "height": y, "display": "auto-scale"},
               "structure": layout, "globalInputs": ["input_global_trp"]},
}

xml = (
    '<dashboard version="2" theme="dark">\n'
    "  <label>Homelab Operations</label>\n"
    "  <description>Generated by apps/85_homelab_ops/build_dashboard.py; edit that, not this file.</description>\n"
    "  <definition><![CDATA[\n" + json.dumps(definition, indent=2) + "\n  ]]></definition>\n"
    '  <meta type="hiddenElements"><![CDATA[{"hideEdit": false, "hideOpenInSearch": false, "hideExport": false}]]></meta>\n'
    "</dashboard>\n"
)
out = pathlib.Path(__file__).parent / "default/data/ui/views/homelab_ops.xml"
out.write_text(xml)
print(f"wrote {out} ({len(S)} searches, {len(viz)} panels)")
