"""Shared constants for infrastructure diagrams."""

import os

OUTPUT_DIR = os.environ.get("OUTPUT_DIR", os.path.join(os.path.dirname(__file__), "..", "images"))

# Graph rendering defaults
GRAPH_ATTR = {
    "fontsize": "14",
    "bgcolor": "white",
    "pad": "0.5",
    "nodesep": "0.8",
    "ranksep": "1.0",
}

CLUSTER_ATTR = {
    "fontsize": "13",
    "style": "rounded",
    "labeljust": "l",
}

NODE_ATTR = {
    "fontsize": "11",
}

EDGE_ATTR = {
    "fontsize": "9",
}

# Cluster colors
COLORS = {
    "proxmox1": "#E8F5E9",
    "proxmox2": "#E3F2FD",
    "proxmox3": "#FFF8E1",
    "k8s": "#FFF3E0",
    "monitoring": "#F3E5F5",
    "cicd": "#E0F7FA",
    "network": "#FAFAFA",
    "unraid": "#FBE9E7",
    "gitops": "#F1F8E9",
}

# VM inventory from infrastructure_tooling/proxmox/terraform/main.tf
VMS = {
    "proxmox": [
        {"name": "splunk-docker-compose-mcp", "cores": 8, "mem_gb": 4, "disk_gb": 300, "template": 9002, "tags": ["splunk"]},
        {"name": "github-action-runner", "cores": 2, "mem_gb": 4, "disk_gb": 50, "template": 9002, "tags": ["github-runner"]},
        {"name": "docker-compose-02", "cores": 2, "mem_gb": 4, "disk_gb": 500, "template": 9000, "tags": ["docker-compose"]},
    ],
    "proxmox2": [
        {"name": "docker-compose-03", "cores": 2, "mem_gb": 4, "disk_gb": 250, "template": 9001, "tags": ["monitoring"]},
        {"name": "openclaw-claude-01", "cores": 2, "mem_gb": 4, "disk_gb": 250, "template": 9001, "tags": ["openclaw-claude"]},
        {"name": "ubuntu-desktop-01", "cores": 2, "mem_gb": 6, "disk_gb": 50, "template": 9004, "tags": ["desktop"]},
    ],
    "proxmox3": [
        {"name": "security-tooling-01", "cores": 8, "mem_gb": 16, "disk_gb": 600, "template": 9005, "tags": ["security", "splunk"]},
        {"name": "k3s-01", "cores": 4, "mem_gb": 16, "disk_gb": 300, "template": 9005, "tags": ["k3s"]},
        {"name": "siem-agent-test-vm", "cores": 8, "mem_gb": 24, "disk_gb": 250, "template": 9005, "tags": ["siem"]},
    ],
}

# Proxmox nodes (hardware label shown in the diagrams)
NODES = {
    "proxmox": "Intel NUC",
    "proxmox2": "Beelink Mini PC",
    "proxmox3": "",
}

# Notes shown under a VM's name (what it is for, when the name doesn't say)
VM_NOTES = {
    "github-action-runner": "Terraform CI runner",
    "docker-compose-03": "Docker + secondary k3s",
    "security-tooling-01": "Cribl, Splunk, OpenCanary",
    "k3s-01": "primary k3s",
}

# Kubernetes workloads by cluster (Argo CD applications on k3s-01, grouped)
K8S_CLUSTERS = {
    "k3s-01": {
        "Platform": ["Argo CD", "Vault", "External Secrets", "cert-manager", "Traefik"],
        "Security": ["Falco + k8saudit", "OTel collector"],
        "MCP gateway": ["ToolHive vMCP"],
        "Agents": ["homelab-agent-sec", "homelab-agent-ops"],
        "CI": ["ARC runners"],
        "Apps": ["Apiary", "Stirling-PDF", "Uptime Kuma", "alexprinter"],
    },
    "docker-compose-03 (secondary)": {
        "Platform": ["Vault", "External Secrets", "Tailscale operator"],
    },
}

# Network
NETWORK = {
    "bridge": "vmbr0",
    "domain": "bmosan.com",
    "tailnet_only": ["mcp.bmosan.com", "vault.bmosan.com", "apiary.bmosan.com"],
    "hec": "192.168.4.11:8088",
    "honeypot": "192.168.4.240",
}

# Log and metric sources, all sent to Cribl's HEC input
SOURCES = [
    "VM + Proxmox host agents (OTel)\njournald, Docker logs, host metrics",
    "Proxmox OTLP metrics",
    "k3s OTel DaemonSet\npod logs, events, cluster metrics",
    "k3s API audit log",
    "Falco (10 VMs + k3s, k8saudit)",
    "Vault audit",
    "UniFi syslog + IDS",
    "OpenCanary honeypot",
    "App logs (daily news)",
    "Agent run logs",
]

SPLUNK_INDEXES = ["security", "audit", "infra", "k8s", "apps", "network", "metrics", "ai_agents"]


def vm_label(vm):
    """Format a VM label with resource info."""
    note = VM_NOTES.get(vm["name"])
    label = f"{vm['name']}\n({vm['cores']}c / {vm['mem_gb']}GB / {vm['disk_gb']}GB)"
    return f"{label}\n{note}" if note else label
