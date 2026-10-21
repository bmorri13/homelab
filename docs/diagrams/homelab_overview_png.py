"""Diagram 4: Homelab Overview — Complete infrastructure with all VMs, K8s, and services."""

import os
from diagrams import Diagram, Cluster, Edge
from diagrams.onprem.compute import Server
from diagrams.onprem.network import Internet
from diagrams.onprem.container import Docker
from diagrams.onprem.vcs import Github
from diagrams.onprem.security import Vault
from diagrams.generic.storage import Storage
from diagrams.k8s.compute import Pod
from diagrams.k8s.infra import Node

from config import OUTPUT_DIR, GRAPH_ATTR, NODE_ATTR, EDGE_ATTR, COLORS, VMS, vm_label


def generate():
    filepath = os.path.join(OUTPUT_DIR, "homelab_infrastructure_overview")

    # Extend base graph_attr with anti-overlap settings for this denser diagram
    graph = {
        **GRAPH_ATTR,
        "splines": "spline",
        "concentrate": "true",
        "overlap": "false",
        "sep": "+15",
    }

    with Diagram(
        "Homelab Infrastructure Overview",
        filename=filepath,
        show=False,
        direction="TB",
        graph_attr=graph,
        node_attr=NODE_ATTR,
        edge_attr=EDGE_ATTR,
    ):
        inet = Internet("Home Network\nvmbr0 bridge")
        github = Github("GitHub\nbmorri13/homelab")

        with Cluster("Proxmox Node 1 — Intel NUC", graph_attr={"bgcolor": COLORS["proxmox1"]}):
            proxmox1_vms = [Server(vm_label(vm)) for vm in VMS["proxmox"]]

            with Cluster("Primary K3s Cluster", graph_attr={"bgcolor": COLORS["k8s"]}):
                k3s_cp = Node("K3s Control Plane")
                k3s_apps = [
                    Pod("ArgoCD"),
                    Pod("Vault"),
                    Pod("Cert Manager"),
                    Pod("MetalLB"),
                    Pod("ESO"),
                    Pod("Traefik"),
                ]

        with Cluster("Proxmox Node 2 — Beelink", graph_attr={"bgcolor": COLORS["proxmox2"]}):
            proxmox2_vms = [Server(vm_label(vm)) for vm in VMS["proxmox2"]]

            with Cluster("Secondary K3s Cluster\n(on docker-compose-03)", graph_attr={"bgcolor": COLORS["k8s"]}):
                k3s_cp2 = Node("K3s Control Plane")
                k3s_apps2 = [
                    Pod("Vault"),
                    Pod("ESO"),
                    Pod("Tailscale Op"),
                    Pod("ARC Runners"),
                ]

        with Cluster("Unraid NAS", graph_attr={"bgcolor": COLORS["unraid"]}):
            consul = Storage("Consul\n(TF State)")

        # --- Connections (minimal cross-cluster edges) ---

        # Network connectivity
        inet >> Edge(label="192.168.x.x") >> proxmox1_vms[0]
        inet >> proxmox2_vms[0]

        # K8s control plane -> apps
        k3s_cp >> k3s_apps
        k3s_cp2 >> k3s_apps2

        # GitOps: GitHub -> ArgoCD (critical flow)
        github >> Edge(color="#FF9800", label="sync") >> k3s_apps[0]

        # State: Terraform -> Consul
        proxmox1_vms[0] >> Edge(style="dashed", label="state") >> consul

    print(f"  Generated: {filepath}.png")


if __name__ == "__main__":
    generate()
