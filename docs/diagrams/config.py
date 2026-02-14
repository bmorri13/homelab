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
        {"name": "monitoring-docker-compose-01", "cores": 2, "mem_gb": 4, "disk_gb": 250, "template": 9000, "tags": ["monitoring"]},
        {"name": "docker-compose-02", "cores": 2, "mem_gb": 4, "disk_gb": 500, "template": 9000, "tags": ["docker-compose"]},
    ],
    "proxmox2": [
        {"name": "docker-compose-03", "cores": 2, "mem_gb": 4, "disk_gb": 250, "template": 9001, "tags": ["monitoring"]},
        {"name": "openclaw-claude-01", "cores": 2, "mem_gb": 4, "disk_gb": 250, "template": 9001, "tags": ["openclaw-claude"]},
    ],
}

# K8s clusters
K8S_CLUSTERS = {
    "Primary K3s": {
        "node": "Intel NUC (proxmox)",
        "components": ["ArgoCD", "Vault", "Cert Manager", "MetalLB", "ESO", "Traefik"],
    },
    "Secondary K3s": {
        "node": "docker-compose-03 VM (proxmox2)",
        "components": ["Vault", "ESO", "Tailscale Operator", "ARC Controller", "ARC Runners"],
    },
}

# Network
NETWORK = {
    "bridge": "vmbr0",
    "metallb_pool": "192.168.3.100-199",
    "domain": "bmosan.com",
}

# Monitoring stack services
ELASTIC_SERVICES = ["es01", "kibana", "logstash01", "filebeat01", "metricbeat01"]
CRIBL_SERVICES = ["cribl-leader", "cribl-worker"]


def vm_label(vm):
    """Format a VM label with resource info."""
    return f"{vm['name']}\n({vm['cores']}c / {vm['mem_gb']}GB / {vm['disk_gb']}GB)"
