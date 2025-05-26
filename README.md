# homelab
- This repo contains the configurations and setup information for my homelab k3s cluster and promox environment. Currnetly I am running an Intel NUC and [Beelink Mini PC](https://www.amazon.com/dp/B0DF7FFN22?ref=ppx_yo2ov_dt_b_fed_asin_title&th=1) running Proxmox.

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
    - [Cert Manager](#cert-manager)
      - [Setup](#setup-3)
    - [Metal LB](#metal-lb)
      - [Setup](#setup-4)
    - [Core Infrastructure Ingress](#core-infrstructure-ingress)
      - [Setup](#setup-5)
- [Applications](#applications)
  - [Alex Printer tracker](#alex-printer-tracker)
    - [Setup](#setup-6)
  - [Uptime Kuma](#uptime-kuma)
    - [Setup](#setup-7)
- [Proxmox Setup](#proxmox-setup)
  - [Fixing Repository Issues Without Subscription](#fixing-repository-issues-without-subscription)
    - [Update PVE List](#update-pve-list)
    - [Update Ceph List](#update-ceph-list)
  - [Adding Valid HTTPS Certs via Cloudflare](#adding-valid-https-certs-via-cloudflare)
  - [Setting up Virtual Machines](#setting-up-virtual-machines)
    - [Linux](#linux)
      - [Ubuntu](#ubuntu)
    - [Windows](#windows)
      - [Windows Pro](#windows-pro)      - [Tiny 11 Windows Image Builder](#tiny-11-windows-image-builder)
        - [Setup](#setup-8)
- [Monitoring Stack Setup - Elastic & Cribl](#monitoring-stack-setup---elastic--cribl)
  - [Prerequisites](#prerequisites-1)
  - [In Scope](#in-scope)
    - [Elastic](#elastic-1)
    - [Cribl](#cribl-1)
  - [Out of Scope](#out-of-scope)
  - [Elastic](#elastic-2)
  - [Cribl](#cribl-2)
    - [Setup](#setup-9)
  - [Additional Links](#additional-links)
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
2. Deploy out the `prod_cert_issuer.yaml` from the cert_manager directory
```bash
kubectl apply -f prod_cert_issuer.yaml
```

3. Verify that the issuer has successfully been created
```bash
kubectl get clusterissuers
- You should see someting like
```bash
NAME          READY   AGE
bmosan-cert   True    46h
```

4. Deploy out a test deployment chart, we can use the `nginx_deployment.yaml`
```bash
kubectl apply -f nginx_deployment.yaml
```

5. Test going to your site (e.g. https://nginx.bmosan.com)
- You should be able to get to the site and then validat that the cert is valid and issued by Let's Encrypt

6. You can also validate that the certificate has been created / view all certs in your cluster with the below command:
```bash
kubectl get certificate --all-namespaces
```

### Metal LB 
- Deploying the MetalLb Configuraiton via ArgoCD
#### Setup
1. Create the `metallb-system` namespace
```bash
kubectl create ns metallb-system
```

2. Within the ArgoCD UI, create a new app with the below parameter
    - General
        - Application Name: metallb
        - Project Name: default
    - Source
        - Leave the `Git` dropdown
        - Repository URL: https://github.com/bmorri13/homelab
        - Revision: HEAD
        - Path: infrastructure_tooling/metallb
        - Cluster URL: https://kubernetes.default.svc
        - Namesapce: metallb-system

3. You can now configure your services to get assigned an EXTERNAL-IP when using the `LoadBalancer` type. An exmaple of this with ArgoCD is below:
```bash
kubectl patch svc argocd-server -n argocd -p '{"spec": {"type": "LoadBalancer"}}'
```

4. You can now access the ArgoCD front end via the EXTERNAL-IP, get that by running
```
 kubectl get svc argocd-server -n argocd
NAME            TYPE           CLUSTER-IP     EXTERNAL-IP     PORT(S)                      AGE
argocd-server   LoadBalancer   10.43.45.220   192.168.3.101   80:30766/TCP,443:32660/TCP   2d
```

### Core Infrstructure Ingress
- Deploying the ingress configuratiosn for our main tooling (e.g. Vault, Uptime Kuma, and ArogCD (WIP))
#### Setup
1. Naviate to the `ingress_configs` directory

2. To configure Vault ingress, apply the `vault-ingress.ymal`
```bash
kubectl apply -f vault-ingress.yaml
kubectl apply -f uptime-kuma-ingress.yaml
```

3. You should now be able to go to Vault & Uptime Kuma with a secure connction (e.g. https://vault.bmosan.com/ui/ & https://uptime-kuma.bmosan.com/)
    - NOTE: You must natviate to `/ui` in order to get the secure connection
        - You can most likely add redirects for this, but not worried about this in my homelab currently



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

### Uptime Kuma
- Uptime Kuma is an easy-to-use self-hosted monitoring tool that provides status pages, monitoring for HTTP(s), TCP, DNS, and more
- The helm chart deploys Uptime Kuma in the `uptime-kuma` namespace with a LoadBalancer service type that uses MetalLB for IP assignment

#### Setup
1. Create the `uptime-kuma` namespace:
```bash
kubectl create ns uptime-kuma
```

2. Within the ArgoCD UI, create a new app with the below parameters:
    - General
        - Application Name: uptime-kuma
        - Project Name: default
    - Source
        - Leave the `Git` dropdown
        - Repository URL: https://github.com/bmorri13/homelab
        - Revision: HEAD
        - Path: infrastructure_tooling/uptime_kuma
        - Helm parameter:
            - Release Name: uptime-kuma (make sure to use all lowercase with hyphens, not spaces)
        - Cluster URL: https://kubernetes.default.svc
        - Namesapce: uptime-kuma

3. Once deployed, you can access Uptime Kuma using the dynamically assigned IP address provided by MetalLB:
```bash
kubectl get svc -n uptime-kuma
```

4. Navigate to the assigned IP address on port 3001 in your browser:
```
http://<EXTERNAL-IP>:3001
```

5. Follow the initial setup wizard to create your admin user and start monitoring your services.

## Proxmox Setup

### Fixing Repository Issues Without Subscription

- When you run `apt-get update` on Proxmox without a valid subscription, it often fails because it's trying to access the enterprise repository that requires a subscription. To resolve this, you can disable the enterprise repository and switch to the no-subscription repository:

#### Update PVE List
##### Disable the Enterprise Repository:
- Open the file `/etc/apt/sources.list.d/pve-enterprise.list` in your favorite text editor (e.g., nano or vim) and comment out the line by adding a `#` at the beginning:

```bash
# Original line
#deb https://enterprise.proxmox.com/debian/pve bookworm pve-enterprise
```

##### Enable the No-Subscription Repository:
- Next, add the no-subscription repository. Edit `/etc/apt/sources.list.d/pve-enterprise.list` and add the appropriate line:

For Proxmox VE 8.x (running on Debian Bookworm):
```bash
#deb https://enterprise.proxmox.com/debian/pve bookworm pve-enterprise
deb http://download.proxmox.com/debian/pve bookworm pve-no-subscription
```
#### Update Ceph List
##### Disable the Enterprise Repository:
- Open the file `/etc/apt/sources.list.d/ceph.list` in your favorite text editor (e.g., nano or vim) and comment out the line by adding a `#` at the beginning:

```bash
# Original line
#deb https://enterprise.proxmox.com/debian/ceph-quincy bookworm enterprise
```

##### Enable the No-Subscription Repository:
- Next, add the no-subscription repository. Edit `/etc/apt/sources.list.d/ceph.list` and add the appropriate line:

For Proxmox VE 8.x (running on Debian Bookworm):
```bash
#deb https://enterprise.proxmox.com/debian/ceph-quincy bookworm enterprise
deb http://download.proxmox.com/debian/ceph-quincy bookworm no-subscription
```

Update the Package List:
Finally, run:
```bash
apt-get update
```

> Note: Using the no-subscription repository means you won't have access to the enterprise support updates, but you'll still receive regular community updates. This setup is common for home labs or non-production environments.

### Adding Valid HTTPS Certs via Cloudflare
- Utlized the video [Secure Proxmox with LetsEncrypt HTTPS Certificates Validated with Cloudflare DNS](https://www.youtube.com/watch?v=2_PhwHOxytM)
- Once completed, you can now access your Proxmox instnace via the DNS name used with a valid cert
    - E.g. `https://proxmox.bmosan.com:8006`


### Setting up Virtual Machines
#### Linux
##### Ubuntu
- Uploaded Ubuntu Server ISO and installed, will update the steps later but it is pretty straight forward

#### Windows
##### Windows Pro
- Using the (Proxmox - Windows 11 VM (with VirtIO drivers))[https://www.youtube.com/watch?v=eboCDiDpOCs]
> NOTE: This gudie (e.g. needing the extra VirtIO drivers) is needed since Windows requires having the extra drivers to get it installed correctly.

##### Tiny 11 Windows Image Builder
###### Setup
1. Download the latest Windows 11 iso
2. Download the [Tiny11 Builder files](https://github.com/ntdevlabs/tiny11builder)
3. Mount the Windows 11 iso
4. Start up Powershell as Administrator
5. Update execusition policy to unrestricted
```bash
Set-ExecutionPolicy unrestricted
```
6. Run the `tiny11maker.ps1` script
```bash
& "C:\Users\bryan\Documents\tiny_11\tiny11builder-main\tiny11maker.ps1"
```
> NOTE: Hit `Y` to proced if needed
8. Select the Windows iso you wish to use (e.g. Windows 11 Pro - Index `6`)
9. Once finished, reenabled the exection policy
```bash
Set-ExecutionPolicy Restricted
```
> NOTE: Hit `Y` to proced if needed
10. Validatte the execution policy is set correctly again
```bash
Get-ExecutionPolicy -List
```
11. Your iso should be available for use with the name of `tiny11.iso`

> Video Guide: [Tiny11 Builder: Create custom ISO and install Windows 11 without bloatware or Microsoft account](https://www.youtube.com/watch?v=VdKVph3G6hQ)

## Monitoring Stack Setup - Elastic & Cribl
- Using Docker Compose on a VM within Proxmox, I have configured a basic monitoring stack leveraging Elastic and Cribl for my logging infastructure
    - In order to avoid requring a license (e.g. Splunk Developer license) I have chosen Elastic as my logging solution along with Cribl to allow for easy data ingestion
- In this section I will breakdown setting up both Elastic and Cribl utilizing running both Elastic and Cribl on a single VM with Docker Compose

### Prerequisites
- Have Virtual Machine
  - In my case, I am runnin Ubuntu Server on a VM within my Proxmox homelab
- Services that are required to run this
  - [Docker](https://docs.docker.com/engine/install/)
  - [Docker Compose](https://docs.docker.com/compose/install/linux/)
  - Git

### In Scope
- Initial stand up and configuration of Elastic and Cribl
#### Elastic
- Creating a `dev_logs` index via Elastic Dev Tools
- Creating a Cribl logging user via Elastic Dev Tools

#### Cribl
- Creating a Data Source for Splunk HEC
- Creating a Data Destionation for Elastic
- Creating a Processing Pipeline to override the default Elastic Destination
- Creating a Data Route utlizing the Splunk HEC Source, Processing Pipeline, and Elastic Destination

### Out of Scope
- Advanced Elastic index management (e.g. Sharding, correct field mappings (outside of timestamp), etc.)
- Configuring streaming of applications / infrastructure (e.g. I am only walking through initital setup of a test Cribl pipeline)
- Configuring valid TLS and domain names
- Deploying this in an automated method or via Kubernetes


### Elastic
- Following the [Getting started with the Elastic Stack and Docker Compose: Part 1](https://www.elastic.co/blog/getting-started-with-the-elastic-stack-and-docker-compose) guide, I have deployed the Elastic stack with Docker compose.
- The `docker-compose.yml` file can be found at [elastic-stack-docker-part-one](https://github.com/elkninja/elastic-stack-docker-part-one) or in the [monitoring_stack/elastic](infrastructure_tooling/monitoring_stack/elastic/docker-compose.yml) directory in this repo.


### Cribl
- Following the [Docker Deployment](https://docs.cribl.io/stream/deploy-docker/) guide, I have deployed Cribl with a Leader and worker node with Docker compose.
- The `docker-compose.yml` file can be found in the link above or in the [monitoring_stack/cribl](infrastructure_tooling/monitoring_stack/cribl/docker-compose.yml) directory in this repo.
> NOTE: I have modifed my `docker-compose.yml` file to expose Port 8088 in order to allow for us to ingest logs via the [Splunk HEC Source](https://docs.cribl.io/stream/sources-splunk-hec/)


#### Setup
1. To begin we will cofigure Elastic, change into the directory where you have your `docker-compose.yml` file for elastic and run the docker compose up command.
> NOTE: You just have all of the additional configuration (e..g. [monitoring_stack/elastic/filebeat.yml](infrastructure_tooling/monitoring_stack/elastic/filebeat.yml), [monitoring_stack/elastic/logstash.conf](infrastructure_tooling/monitoring_stack/elastic/logstash.conf), [monitoring_stack/elastic/metricbeat.yml](infrastructure_tooling/monitoring_stack/elastic/metricbeat.yml), and [monitoring_stack/elastic/.env-example](infrastructure_tooling/monitoring_stack/elastic/.env-example) file in the same directory as your `docker-compse.yml` file.

```bash
docker compose up -d
```
2. Once up, in a browser go to `http://<vm_instnace_ip>:5601` and login with the credentials you have in the `.env` file.
> NOTE: the default credentials that in this example are `elastic:changeme`. You can update this to whatever password you want in the `.env` file before starting the compose template.

3. Validate that you have logged into Elastic and click 'Explore on my own' to continue to the main interface.

4. On the left hand side, if it is not expanded, click the hamburger menu (e.g. three stacked bars) to expand the menu and then scroll down to Management > Click Dev Tools

5. In your Dev Tools Console, paste in the line below to create the `dev_logs` index and the `failover` index.
```
PUT /dev_logs
{
  "settings": {
    "number_of_shards": 1,
    "number_of_replicas": 0
  },
  "mappings": {
    "properties": {
      "@timestamp": { "type": "date" }
    }
  }
}

PUT /failover
{
  "settings": {
    "number_of_shards": 1,
    "number_of_replicas": 0
  },
  "mappings": {
    "properties": {
      "@timestamp": { "type": "date" }
    }
  }
}
```
> NOTE: The `failover` index is used as our catch all index for data we will be sending over via Cribl.

6. To verify the index has been created you can run the below command and you should see a a reponse with the informationf or the index.
```
GET /dev_logs/
GET /failover/
```

7. Next, we will create a custom Cribl role in Elastic to allow for us to write data to our `dev_logs` and `failover` indexes
```
POST /_security/role/cribl_writer_full
{
  "cluster": [ "monitor" ],
  "indices": [
    {
      "names": [ "dev_logs", "failover" ],
      "privileges": [ "write", "create", "index" ]
    }
  ]
}
```
> NOTE: You can and as my indexes to this as you need along with utilizing wildcard (e.g. "dev_logs*") if you want to allow for the user to write to any index prefixed (e.g. `dev_logs_blah`)

8. Create a Cribl user to allow for Cribl to write to the `dev_logs` index along with the custom role we created above.
```
POST /_security/user/cribl_writer
{
  "password" : "criblpass",
  "roles" : [ "cribl_writer_full" ],
  "full_name" : "Cribl Stream Log Writer User"
}
```

9. Verify that the user has been created and has the `cribl_writer_full` role that we created
```
GET /_security/user/cribl_writer
```

> At this point, the initial Elasitc componenets should be ready to go and we are onto setting up Cribl to ingest data.

10. To begin configuring Cribl, change into the directory where you have your `docker-compose.yml` file for Cribl and run
```bash
docker compose up -d
```

11. Once up, in a browser go to `http://<vm_instnace_ip>:19000` and login with the default credentials..
> NOTE: the default credentials that in this example are `admin:admin`. You can update this to whatever password you want at this time.

12. Fill out whatever information in the screen and regitster
>NOTE: This does not matter at all just put in an email that passes the form validate (e.g. a.b@c.com)

13. Enter in a new password that passes the password validate.

14. On the top left of your screen, click Products > Stream. You should now be at the Cribl Stream main page and see 1 worker in the `default` group

15. Near the top lieft, click on `default` to enter into your Cribl Stream interface

16. Click Data > Sources > Splunk HEC. On the new page, click on the grayed out line labeled `in_splunk_hec` to enter into your Splunk HEC source configuration page

17. At the top right of the window, toggle `Enabled` to true (it should now show as blue)

18. In the same window, click on Auth Tokens > Add Token > On the right hand side click `Generate`, add a Description such as `Sample HEC Logs`, and then hit `Save`.

19. At the top right, click Commit and enter in a message such as `Enabling Splunk HEC source.`. Then on the right hand side click `Commit & Deploy`
> NOTE: A common theme with Cribl will be to always validate you have done the `Commit & Deploy` as that is what will make your changes 'active'.

20. At the top left, click Data > Destinations > Elasticsearch. On the top right, click `Add Destionation` and then fill out the New Destination pane with infomration such as:
  - Output ID: elastic_dev
  - Description: Elastic Destination for necessary logs with a Default index set to `failover`
  - Bulk API URLs: https://<vm_instance_ip>:9200
  - Enable Authentication: Toggle to Enabled (e.g. it should show blue)
    - Authenitcation method: Manual
    - Username: cribl_writer
    - Password: criblpass

21. Under `Advanced Settings`, toggle Validate server certs to off (e.g. make it gray).

22. Once you have finished filling that out. hit `Save`. And then at the top right click, `Commit`. Add a message such as `Enabling elastic dev destination` and then at the right hit `Commit & Deploy`

23. Click back on the new destination that was created labeled `elastic_dev`

24. We will now capture some sample data to use in our Processing Pipeline. Click Data > Sources > Splunk HEC. Now click on the Splunk HEC source labeled `in_splunk_hec`. Go to Auth Tokens > Drop down the `Smaple HEC Logs` token and on the right hand side click on the crossed out eye to revael your HEC token and copy that.

25. In order to send sample logs, we will use the `sample_dev_logs.py` script to send data. In order to run this, copy the `.env-example` file to `.env` and update this to inclue the URL of your VM Server IP and the HEC token from the previous step.
> NOTE: This is assuming that you have copied down all of the contents fo the [monitoring_stack/cribl](homelab\infrastructure_tooling\monitoring_stack\cribl) directory and are running this from your local PC that has access to your VM.

26. After saving the `.env` file, run
```bash
docker build -t sample_dev_logs .
```

27. Begin running the container
```bash
docker run --rm sample_dev_logs
```
> NOTE: You should now see logs being generated such as `2025-05-26T03:19:24+0000 INFO Sent batch of 100 events → Status Code: 200` in your console

28. Go back into your Cribl browser window and on the HEC input, navigate up to `Live Data` > Click Capture... >Change Capture time to 60 and click `Start`.

29. After 30 to 60 seconds, you should see events populate on your feed. Once they are there, on the bottom right, click `Save as Sample File` with the information below:
  - File name: sample_dev_logs.txt
  - Description: Sample Dev Logs
  - Expiration: 0

30. Once you have filled that information out, click `Save`.

31. Exit out of the Splunk HEC input pane and at the top right click `Commit` and enter a message such as `Adding Sample Dev logs for processing pipeline` and click `Commit & Deploy`.

32. Now we will create our processing pipeline by navigating to > Processing > Pipelines > Add Pipeline > Add Pipeline > Give it the ID of `dev_logs_processing` and click `Save`.

33. We will have to add two function to clean up the events and enable us to dynamically route the data to our `dev_logs` index. First, on the right hand side double click the sample events that we have captured from above (e.g. `sample_dev_logs.txt`) in order to load them in.

34. Click Add Function > Type in `Parser` and then click Parser. Once it has loaded in, update the `Type` to `JSON Object` and click `Save`.
> NOTE: You should now see on the right hand side green highlighted fields added in which represent the events that will be send over to Cribl with our processing pipeline.

35. We will now add another function to remove the `_raw` field and update the metadata fields to route our events to the `dev_logs` index. Click Add Function > Type in `Eval` anbd click Eval. Configure the new Function as below:
  - Click Add Field
    - Name: __index
    - Value Expression: 'dev_logs'
      > NOTE: You must add the single quotes as Cribl requires this to be in a javascript string format
  - Remove fields, add each of these fields individually and hit tab. You should see it turn into a grab box for each of the fields accordingly.
    - index
    - _raw

36. Click `Save` and validate on the right hand side in our OUT pane that the _raw field and index field are highlighted red and crossed out indicating they will be removed
> NOTE: if you want to see the internal fields (e.g. __index), on the top right of your samples pane click the gear icon and click the `Show Internal Fields` for it to toggle blue. This will now so you the metadata fields (e.g. fields appended by `__`)

37. On the top right click Commit and enter in a message such as `Adding in dev logs processing pipeline` and click `Commit & Deploy`

38. We are now ready to build our route. On the top left, click Routing > Data Routes > Add Route. Fill out the route with the information below:
  - Route name: dev_logs
  - Filter: __inputId=='splunk_hec:in_splunk_hec' && sourcetype=='sample_dev_logs'
  - Pipeline: dev_logs_processing
  - Destination: elastic:elastic_dev

39. Click `Save` and move your new Route up to the first position (e.g. on the left hand side the stakced dots next to the numbers will allow you to click and drag) and on the top right, click `Commit` and enter a message such as `Adding dev_logs route` and click `Commit & Deploy` 

40. Once deployed, rerun the sample dev logs container on your PC
```bash
docker run --rm sample_dev_logs
```

41. Events should now be flowing through the Cribl pipeline and outputting to Elastic. In order to validate, go back to your Elastic Dev tools console and run:
```bash
GET /dev_logs/_count
```
> NOTE: ont he right hand side, you should see a number greater than `0` for the count value.

42. We can now go search the logs. On the left hand side, if it is not expanded, click the hamburger menu (e.g. three stacked bars) to expand the menu and under Analytics, click Discover > Create data view with the information below:
  - Name: dev_logs
  - Index pattern: dev_logs
  - Timestamp field: @timestamp

43. Click `Save data view to Kibana`

44. You should now see your sample data events populated and are available for search. If you do not see any events, you can try to click the Calender icon on the top right and select `Last 24 hours` to search back for data you may have ingested earlier.

- At this point your Elastic stack and Cribl pipelines should be ready for you to create additional dashboards, searches, and ingest additional data as necessary.

> NOTE: As stated, this guide is the initial stand up elements and does not cover more advanced use cases at this point.

### Additional Links
- [Don’t run Proxmox without these settings](https://www.youtube.com/watch?v=VAJWUZ3sTSI)
