# homelab
- This repo contains the configurations and setup information for my homelab k3s cluster. Currnetly I am running this on a single Intel NUC

## Table of Contents
- [Prerequisites](#prerequisites)
- [Tooling](#tooling)
  - [Continuous Deployment](#continuous-deployment)
    - [ArgoCD](#argocd)
      - [Setup](#setup)
    - [Vault](#vault)
      - [Setup](#setup-1)
    - [External Secrets Operator](#external-secrets-operator)
      - [Setup](#setup-2)
- [Applications](#applications)
  - [Alex Printer tracker](#alex-printer-tracker)
    - [Setup](#setup-3)
- [To Do](#to-do)

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

#### Setup
- Following the [ArgoCD Getting Starting Guide](https://argo-cd.readthedocs.io/en/stable/getting_started/), we will deploy ArgoCD
> NOTE: I am only including the steps that I have run that are a subset of the steps on the guide (e.g. not installing argocd cli, etc.)

1. Install ArgoCD
```bash
kubectl create namespace argocd
kubectl apply -n argocd -f https://raw.githubusercontent.com/argoproj/argo-cd/stable/manifests/install.yaml
```
> NOTE: If you simply wish to port forward, you can run `kubectl port-forward svc/argocd-server -n argocd 8080:443` and skip steps 2, 3 & 4 and simply go to `localhost:8080`

2. Update `argocd-server` service to NodePort
- This will allow us to access the UI from our network. 
> NOTE: This will be replaces in the future once I setup a reverse proxy
```bash
kubectl patch svc argocd-server -n argocd -p '{"spec": {"type": "NodePort"}}'
```

3. Get the port for your ArgoCD UI frontend
    - It will be the first port that is connected to port 80
```bash
kubectl get svc argocd-server -n argocd
```
- Should look someting like:
```bash
NAME            TYPE       CLUSTER-IP      EXTERNAL-IP   PORT(S)                      AGE
argocd-server   NodePort   10.43.127.142   <none>        80:30447/TCP,443:31836/TCP   64s
```

4. On your local computer, you can enter in the IP address or local DNS name and then the port
```bash
https://192.168.3.170:30447/
https://bmo-nuc.com:30447/
```
> NOTE: If you are running this in the cloud, this will be the IP of your instance. You may need to update securiy groups to include allowing access to the port

5. Get the default ArgoCD admin password
```bash
kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath="{.data.password}" | base64 -d
```

6. Login to the UI
- Username: amdin
- Password: outputted results from Step 5

- You are now ready to install your first apps!

### Vault 
- In order to manage kubernetes secrets, we will deploy [Vault](https://developer.hashicorp.com/vault/docs/platform/k8s/helm)
>NOTE: This is just one example of a password manager, you can use local secrets, or other soultions such as AWS Secrets manager, GCP Secrets Manager, etc.

#### Setup
1. Using kubectl, create a new namespace for vault
```bash
kubectl create ns vault
```

2. Within the ArgoCD UI, create a new app with the below parameter
    - General
        - Application Name: vault
        - Project Name: default
    - Source
        - Change the `Git` dropdown to `Helm`
        - Repository URL: https://helm.releases.hashicorp.com
        - Chart: Vault
        - Version: 0.29.1
            - >NOTE: There may be a newer version when you view this
        - Cluster URL: https://kubernetes.default.svc
        - Namesapce: vault

3. Get the default vault key's
```bash
kubectl exec vault-0 -n vault -- vault operator init -key-shares=1 -key-threshold=1 -format=json > vault-keys.json
```

> NOTE: If you simply wish to port forward, you can run `kubectl port-forward svc/vault -n vault 8020:8200` and skip steps 2, 3 & 4 and simply go to `localhost:8020`

4. Update `vault` service to NodePort
- This will allow us to access the UI from our network. 
> NOTE: This will be replaces in the future once I setup a reverse proxy
```bash
kubectl patch svc vault -n vault -p '{"spec": {"type": "NodePort"}}'
```

5. Get the port for your ArgoCD UI frontend
    - It will be the first port that is connected to port 80
```bash
kubectl get svc vault -n vault
```
- Should look someting like:
```bash
NAME    TYPE       CLUSTER-IP     EXTERNAL-IP   PORT(S)                         AGE
vault   NodePort   10.43.123.36   <none>        8200:31080/TCP,8201:31973/TCP   16m
```

6. On your local computer, you can enter in the IP address or local DNS name and then the port
```bash
http://192.168.3.170:31080/
http://bmo-nuc.com:31080/
```
> NOTE: If you are running this in the cloud, this will be the IP of your instance. You may need to update securiy groups to include allowing access to the port

7. Unseal your Vault by going to your `vault-keys.json` and copying the valye for `unseal_keys_b64`

8. To login, use the `Token` method and from your `vault-keys.json` copy the vaule from `root_token`

9. Create a new Secret Engine to store your first secret by going to. `Secrets Engine` > `Enable new engine` > `KV` > Click `Enable engine`

10. Create our first secret by going to `Create Secret` and entering someting similar to the values below
    - Path for the secret: cloudflare
    - Secret data:
        - Key: token
        - Value: <enter your secret value here>

- You now have a local secrets store setup on your cluster and are ready to set up a GitOps tie in to leveraging these secrets in your Cluster

### External Secrets Operator 
- In order to utlize external secret stors such as Hashicorp Vault, AWS Secrets Manager, GCP Secrets Manager, etc. [External Secrets Operator](https://external-secrets.io/latest/) will allow us to leverage those secrets witin our Cluster

#### Setup
1. Using kubectl, create a new namespace for vault
```bash
kubectl create ns external-secrets
```

2. Within the ArgoCD UI, create a new app with the below parameter
    - General
        - Application Name: external-secrets-operator
        - Project Name: default
    - Source
        - Change the `Git` dropdown to `Helm`
        - Repository URL: https://charts.external-secrets.io
        - Chart: external-secrets
        - Version: 0.14.1
            - >NOTE: There may be a newer version when you view this
        - Cluster URL: https://kubernetes.default.svc
        - Namesapce: external-secrets

3. Setup up the Vault root key and deploy it as a secert. From your `vault-keys.json` copy the `root_token` value where `REPLACE_ME` is below
> NOTE: We are doing this since we are assuming `jq` is not installed. If it is, youc an simply do `VAULT_ROOT_KEY=$(cat vault-keys.json | jq -r ".root_token")`
```bash
# Get the root key in vault-keys.json
VAULT_ROOT_KEY="REPLACE_ME"

# Write Kubernetes secret which includes Vault credentials
kubectl create secret generic vault-token -n default --from-literal=token=$VAULT_ROOT_KEY
```


5. Create a new namespace where we will configure our first external secert to live in:
```bash
kubectl create ns alexprinter
```

5. We are now ready to deploy the `ClusterSecretStore`
    - You will need to update the [deployments.yaml](https://github.com/bmorri13/homelab) to match your cluster or needs
        - E.g. you will need to update at least the [server](https://github.com/bmorri13/homelab/blob/main/infrastructure_tooling/eso_stores_helm/templates/deployments.yaml#L11) line to reflect your correct cluster name and port

6. Within the ArgoCD UI, create a new app with the below parameter
    - General
        - Application Name: eso-cluster-provider-helm
        - Project Name: default
    - Source
        - Leave the `Git` dropdown
        - Repository URL: https://github.com/bmorri13/homelab
        - Revision: HEAD
        - Path: infrastructure_tooling/eso_cluster_provider_helm
        - Cluster URL: https://kubernetes.default.svc
        - Namesapce: external-secrets

7. We are now ready to deploy the `ExternalSecret`
    - You will need to update the [deployments.yaml](https://github.com/bmorri13/homelab) to match your cluster or needs
        - E.g. you will need to update at least the [server](https://github.com/bmorri13/homelab/blob/main/infrastructure_tooling/eso_stores_helm/templates/deployments.yaml#L11) line to reflect your correct cluster name and port

8. Within the ArgoCD UI, create a new app with the below parameter
    - General
        - Application Name: eso-external-secrets-helm
        - Project Name: default
    - Source
        - Leave the `Git` dropdown
        - Repository URL: https://github.com/bmorri13/homelab
        - Revision: HEAD
        - Path: infrastructure_tooling/eso_external_secrets_helm
        - Cluster URL: https://kubernetes.default.svc
        - Namesapce: external-secrets

9. Confirm both in the UI and backend with kubectl that the secert was created
```bash
kubectl get secrets -n alexprinter

NAME       TYPE     DATA   AGE
cf-token   Opaque   1      2m34s
```

- You have now hooked up your first secret to be made available for helm chart deployments!


### Cert Manager
- Deploying Cert Manager to configure valid certs within the enviornment
#### Setup


1. Deploy helm chart
```bash
helm install cert-manager jetstack/cert-manager \
  --namespace cert-manager \
  --version v1.17.1 \
  --set crds.enabled=true
```

## Applications
### Alex Printer tracker
- This is a sample app that I have created to track how long it is taking a buddy to build his printer with a nextjs app and as well test out Cloudflare Tunnels
- The repo for this is: [cloudflare_tunnels_helm](https://github.com/bmorri13/cloudflare_tunnels_helm)

#### Setup
1. Within the ArgoCD UI, create a new app with the below parameter
    - General
        - Application Name: alexprinter
        - Project Name: default
    - Source
        - Leave the `Git` dropdown
        - Repository URL: https://github.com/bmorri13/cloudflare_tunnels_helm
        - Revision: HEAD
        - Path: cloudflare-tunnels-helm
        - Cluster URL: https://kubernetes.default.svc
        - Namesapce: alexprinter

- You now have deployed a helm chart that is calling the secret that is stored in Vault successfully


To Do:
- [ ] Adding reverse proxy
- [ ] Adding Monitoring Stack
