"""Diagram 2: How it's deployed — CI, Argo CD, secrets, and the Splunk host."""

import os
from diagrams import Diagram, Cluster, Edge
from diagrams.onprem.compute import Server
from diagrams.onprem.proxmox import Pve
from diagrams.onprem.vcs import Github
from diagrams.onprem.ci import GithubActions
from diagrams.onprem.iac import Terraform
from diagrams.onprem.network import Consul
from diagrams.onprem.gitops import ArgoCD
from diagrams.onprem.security import Vault
from diagrams.onprem.certificates import CertManager, LetsEncrypt
from diagrams.onprem.monitoring import Splunk
from diagrams.onprem.container import Docker, K3S
from diagrams.saas.cdn import Cloudflare
from diagrams.k8s.podconfig import Secret
from diagrams.k8s.compute import Cronjob

from config import OUTPUT_DIR, GRAPH_ATTR, NODE_ATTR, EDGE_ATTR, COLORS


def generate():
    filepath = os.path.join(OUTPUT_DIR, "deployment_pipeline")

    with Diagram(
        "How it's deployed",
        filename=filepath,
        show=False,
        direction="LR",
        graph_attr=GRAPH_ATTR,
        node_attr=NODE_ATTR,
        edge_attr=EDGE_ATTR,
    ):
        dev = Server("Developer\n(gitleaks before push)")
        repo = Github("bmorri13/homelab\npublic; Renovate PRs")

        with Cluster("CI (GitHub Actions)", graph_attr={"bgcolor": COLORS["cicd"]}):
            with Cluster("GitHub-hosted"):
                image = GithubActions("homelab-agents-image\nChainguard multi-stage")
            with Cluster("ARC runners on k3s-01"):
                packer = GithubActions("proxmox-packer-builder\nweekly + on change")
            with Cluster("VM runner (github-action-runner)"):
                tf = Terraform("terraform plan / apply")

        ghcr = Docker("GHCR (private)\nhomelab-agents:main")

        with Cluster("Proxmox", graph_attr={"bgcolor": COLORS["proxmox1"]}):
            pve = Pve("Templates + VMs")
        consul = Consul("Consul on Unraid\nTerraform state")

        with Cluster("k3s-01 (GitOps)", graph_attr={"bgcolor": COLORS["gitops"]}):
            argocd = ArgoCD("Argo CD\n~20 apps, auto-sync")
            k3s = K3S("Workloads")
            agents = Cronjob("Agent CronJobs\npull :main each run")
            vault = Vault("Vault")
            eso = Secret("External Secrets\n→ k8s Secrets")
            certs = CertManager("cert-manager")

        le = LetsEncrypt("Let's Encrypt")
        cf = Cloudflare("Cloudflare DNS-01")

        with Cluster("security-tooling-01 (not GitOps)", graph_attr={"bgcolor": COLORS["monitoring"]}):
            splunk = Splunk("Splunk apps\nscripts/deploy.sh")
            cribl = Docker("Cribl pipelines\nAPI sync scripts")

        dev >> Edge(label="PR → merge") >> repo
        repo >> Edge(label="image/**") >> image >> Edge(label="push") >> ghcr
        repo >> Edge(label="packer/**") >> packer >> pve
        repo >> Edge(label="terraform/**") >> tf >> pve
        tf >> Edge(style="dashed", label="state") >> consul

        repo >> Edge(label="polls main", color="darkgreen") >> argocd >> k3s
        argocd >> agents
        ghcr >> Edge(style="dashed", label="pull") >> agents
        vault >> eso >> k3s
        vault >> Edge(style="dashed", label="pod token login") >> agents
        certs >> le
        certs >> cf

        repo >> Edge(style="dashed", label="manual deploy") >> splunk
        repo >> Edge(style="dashed") >> cribl

    print(f"  Generated: {filepath}.png")


if __name__ == "__main__":
    generate()
