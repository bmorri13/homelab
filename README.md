# homelab
- This repo contains the configurations and setup information for my homelab k3s cluster. Currnetly I am running this on a single Intel NUC

## Prerequisites
- Setup a machine running Linux (e.g. an old laptop, Intel Nuc, cloud VM, etc.)
    - For me, I am running this on an Intel Nuc with ubuntu server installed
- Setup [K3s](https://docs.k3s.io/quick-start)
    - After setting this up I recommend getting your kube-config.yml file so that you can do inital deployments, port forwarding, etc. from your computer
    - Installing tooling such as [kubectl](https://kubernetes.io/docs/tasks/tools/#kubectl) and [helm](https://helm.sh/docs/intro/install/) on your local computer are also recommended
- Once you have those setup you should be ready to start deploying!

## Tooling
## Continuous Deployment
### ArgoCD 
- In order to manage deployments in a GitOps workflow metholdogy, I will be using ArgoCD as my CD tool to deploy out my subsequent applications