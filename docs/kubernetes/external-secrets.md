# External Secrets Operator
- In order to utlize external secret stors such as Hashicorp Vault, AWS Secrets Manager, GCP Secrets Manager, etc. [External Secrets Operator](https://external-secrets.io/latest/) will allow us to leverage those secrets witin our Cluster

## Setup
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
