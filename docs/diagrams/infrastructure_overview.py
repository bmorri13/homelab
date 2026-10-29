"""Diagram 1: Infrastructure — Proxmox nodes, VMs, what runs on k3s-01 and security-tooling-01."""

import os
from diagrams import Diagram, Cluster, Edge
from diagrams.onprem.compute import Server
from diagrams.onprem.proxmox import Pve
from diagrams.onprem.network import Internet, Consul
from diagrams.onprem.vcs import Github
from diagrams.onprem.monitoring import Splunk
from diagrams.onprem.container import Docker
from diagrams.generic.network import Router
from diagrams.generic.storage import Storage
from diagrams.saas.cdn import Cloudflare
from diagrams.saas.chat import Slack
from diagrams.k8s.compute import Pod, Cronjob

from config import OUTPUT_DIR, GRAPH_ATTR, NODE_ATTR, EDGE_ATTR, COLORS, VMS, NODES, K8S_CLUSTERS, NETWORK, vm_label


def generate():
    filepath = os.path.join(OUTPUT_DIR, "infrastructure_overview")
    graph = {**GRAPH_ATTR, "nodesep": "0.4", "ranksep": "0.9"}

    with Diagram(
        "Homelab infrastructure",
        filename=filepath,
        show=False,
        direction="TB",
        graph_attr=graph,
        node_attr=NODE_ATTR,
        edge_attr=EDGE_ATTR,
    ):
        with Cluster("Outside services", graph_attr={"bgcolor": COLORS["network"]}):
            cloudflare = Cloudflare("Cloudflare\nDNS, DNS-01 certs, tunnels")
            github = Github("GitHub + GHCR\nbmorri13/homelab")
            anthropic = Internet("Anthropic API")
            slack = Slack("Slack")

        with Cluster("Network", graph_attr={"bgcolor": COLORS["network"]}):
            unifi = Router("UniFi gateway\nIDS, syslog")
            tailnet = Internet("Tailscale tailnet\n" + "\n".join(NETWORK["tailnet_only"]))

        vms = {}
        for node, colour in (("proxmox", "proxmox1"), ("proxmox2", "proxmox2"), ("proxmox3", "proxmox3")):
            title = f"{node}" + (f" — {NODES[node]}" if NODES[node] else "")
            with Cluster(title, graph_attr={"bgcolor": COLORS[colour]}):
                Pve("Proxmox VE")
                for vm in VMS[node]:
                    if vm["name"] in ("k3s-01", "security-tooling-01"):
                        continue
                    vms[vm["name"]] = Server(vm_label(vm))

                if node == "proxmox3":
                    k3s = next(v for v in VMS[node] if v["name"] == "k3s-01")
                    with Cluster(vm_label(k3s).replace("\n", " "), graph_attr={"bgcolor": COLORS["k8s"]}):
                        groups = {}
                        for group, items in K8S_CLUSTERS["k3s-01"].items():
                            with Cluster(group):
                                node_cls = Cronjob if group == "Agents" else Pod
                                groups[group] = [node_cls(i) for i in items]

                    sec = next(v for v in VMS[node] if v["name"] == "security-tooling-01")
                    with Cluster(vm_label(sec).replace("\n", " "), graph_attr={"bgcolor": COLORS["monitoring"]}):
                        cribl = Docker("Cribl Stream\nHEC :8088")
                        splunk = Splunk("Splunk\nindexes, detections,\ndashboards, MCP app")
                        canary = Docker(f"OpenCanary\n{NETWORK['honeypot']}")

        with Cluster("Unraid NAS", graph_attr={"bgcolor": COLORS["unraid"]}):
            consul = Consul("Consul\nTerraform state")
            nfs = Storage("NFS\nSplunk backups + frozen")

        clients = Server("Clients\nMacBook, coding-box")

        # A few edges to show how the pieces connect; the other diagrams have the detail.
        github >> Edge(label="Argo CD sync", color="darkgreen") >> groups["Platform"][0]
        cribl >> splunk
        splunk >> Edge(style="dashed", label="backups") >> nfs
        vms["github-action-runner"] >> Edge(style="dashed", label="state") >> consul
        groups["Agents"][0] >> Edge(label="triage") >> anthropic
        groups["Agents"][0] >> Edge(label="cards") >> slack
        unifi >> Edge(style="dashed", label="syslog") >> cribl
        clients >> Edge(label="MCP gateway") >> tailnet >> groups["MCP gateway"][0]
        cloudflare >> Edge(style="dotted", label="certs") >> groups["Platform"][3]

    print(f"  Generated: {filepath}.png")


if __name__ == "__main__":
    generate()
