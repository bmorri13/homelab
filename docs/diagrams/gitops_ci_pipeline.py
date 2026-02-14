"""Diagram 3: GitOps & CI/CD Pipeline — GitHub Actions + ArgoCD deployment flow."""

import os
from diagrams import Diagram, Cluster, Edge
from diagrams.onprem.compute import Server
from diagrams.onprem.vcs import Github
from diagrams.onprem.ci import GithubActions
from diagrams.onprem.iac import Terraform
from diagrams.onprem.security import Vault
from diagrams.onprem.container import Docker
from diagrams.generic.storage import Storage
from diagrams.k8s.compute import Pod
from diagrams.k8s.infra import Node

from config import OUTPUT_DIR, GRAPH_ATTR, NODE_ATTR, EDGE_ATTR, COLORS


def generate():
    filepath = os.path.join(OUTPUT_DIR, "gitops_ci_pipeline")

    with Diagram(
        "GitOps & CI/CD Pipeline",
        filename=filepath,
        show=False,
        direction="LR",
        graph_attr=GRAPH_ATTR,
        node_attr=NODE_ATTR,
        edge_attr=EDGE_ATTR,
    ):
        dev = Server("Developer")
        repo = Github("GitHub Repo\nbmorri13/homelab")

        with Cluster("CI/CD Workflows (GitHub Actions)", graph_attr={"bgcolor": COLORS["cicd"]}):

            with Cluster("Packer Workflow\nrunner: arc-k3s-homelab"):
                packer_validate = GithubActions("Validate\nTemplate")
                packer_build = GithubActions("Build Template\n(matrix: proxmox,\nproxmox2)")

            with Cluster("Terraform Workflows\nrunner: self-hosted VM"):
                tf_validate = GithubActions("TF Validate\n& Plan")
                tf_apply = GithubActions("TF Apply\n(manual)")

        with Cluster("Proxmox Cluster", graph_attr={"bgcolor": COLORS["proxmox1"]}):
            proxmox_api = Server("Proxmox API")
            templates = Storage("VM Templates\n9000 / 9001 / 9002")
            vms = Server("Provisioned VMs")

        consul = Storage("Consul\n(TF State)")

        with Cluster("GitOps Loop", graph_attr={"bgcolor": COLORS["gitops"]}):
            argocd = Pod("ArgoCD")
            k8s = Node("K8s Cluster")
            vault = Vault("Vault")
            eso = Pod("ESO")

        # CI/CD flow
        dev >> Edge(label="git push") >> repo

        repo >> Edge(label="push/schedule\npacker/**") >> packer_validate
        packer_validate >> packer_build
        packer_build >> Edge(label="API") >> proxmox_api
        proxmox_api >> templates

        repo >> Edge(label="push/PR\nterraform/**") >> tf_validate
        tf_validate >> Edge(style="dashed", label="manual\ntrigger") >> tf_apply
        tf_apply >> Edge(label="API") >> proxmox_api
        tf_apply >> Edge(style="dashed", label="state") >> consul
        proxmox_api >> vms

        # GitOps flow
        repo >> Edge(label="sync", color="darkgreen") >> argocd
        argocd >> Edge(label="deploy") >> k8s
        eso >> Edge(label="fetch secrets") >> vault
        eso >> Edge(label="inject") >> k8s

    print(f"  Generated: {filepath}.png")


if __name__ == "__main__":
    generate()
