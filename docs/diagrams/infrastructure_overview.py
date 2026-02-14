"""Diagram 1: Infrastructure Overview — Proxmox nodes, VMs, K3s clusters."""

import os
from diagrams import Diagram, Cluster, Edge
from diagrams.onprem.compute import Server
from diagrams.onprem.network import Internet
from diagrams.onprem.container import Docker
from diagrams.generic.compute import Rack
from diagrams.generic.storage import Storage
from diagrams.generic.network import Subnet
from diagrams.k8s.compute import Pod
from diagrams.k8s.infra import Node

from config import OUTPUT_DIR, GRAPH_ATTR, NODE_ATTR, EDGE_ATTR, COLORS, VMS, vm_label


def generate():
    filepath = os.path.join(OUTPUT_DIR, "infrastructure_overview")

    with Diagram(
        "Homelab Infrastructure Overview",
        filename=filepath,
        show=False,
        direction="TB",
        graph_attr=GRAPH_ATTR,
        node_attr=NODE_ATTR,
        edge_attr=EDGE_ATTR,
    ):
        inet = Internet("Home Network\nvmbr0 bridge")

        with Cluster("Proxmox Node 1 — Intel NUC (proxmox)", graph_attr={"bgcolor": COLORS["proxmox1"]}):
            proxmox1_vms = []
            for vm in VMS["proxmox"]:
                proxmox1_vms.append(Server(vm_label(vm)))

            with Cluster("Primary K3s Cluster", graph_attr={"bgcolor": COLORS["k8s"]}):
                k3s_primary = Node("K3s Control Plane")
                k3s_apps = [
                    Pod("ArgoCD"),
                    Pod("Vault"),
                    Pod("Cert Manager"),
                    Pod("MetalLB"),
                    Pod("ESO"),
                    Pod("Traefik"),
                ]

        with Cluster("Proxmox Node 2 — Beelink Mini PC (proxmox2)", graph_attr={"bgcolor": COLORS["proxmox2"]}):
            proxmox2_vms = []
            for vm in VMS["proxmox2"]:
                proxmox2_vms.append(Server(vm_label(vm)))

            with Cluster("Secondary K3s Cluster\n(on docker-compose-03)", graph_attr={"bgcolor": COLORS["k8s"]}):
                k3s_secondary = Node("K3s Control Plane")
                k3s_secondary_apps = [
                    Pod("Vault"),
                    Pod("ESO"),
                    Pod("Tailscale Op"),
                    Pod("ARC"),
                ]

        with Cluster("Unraid NAS", graph_attr={"bgcolor": COLORS["unraid"]}):
            consul = Storage("Consul\n(TF State)")

        # Connections
        inet >> Edge(label="192.168.x.x") >> proxmox1_vms[0]
        inet >> proxmox2_vms[0]

        k3s_primary >> k3s_apps
        k3s_secondary >> k3s_secondary_apps

        # Consul connection
        proxmox1_vms[0] >> Edge(style="dashed", label="state") >> consul

    print(f"  Generated: {filepath}.png")


if __name__ == "__main__":
    generate()
