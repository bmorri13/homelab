# External Secrets Operator
- In order to utlize external secret stors such as Hashicorp Vault, AWS Secrets Manager, GCP Secrets Manager, etc. [External Secrets Operator](https://external-secrets.io/latest/) will allow us to leverage those secrets witin our Cluster

## Setup
1. Using kubectl, create a new namespace for External Secrets Operator
```bash
kubectl create ns external-secrets
```

2. Create the `external-secrets-operator` app from Git, then sync it in Argo CD:
```bash
kubectl apply -f infrastructure_tooling/external_secrets_operator/application.yaml
```

3. Set up how External Secrets Operator logs in to Vault. On k3s-01 this is Vault Kubernetes auth (ESO's own ServiceAccount, no stored token). As a Vault admin, run the commands in [`eso_cluster_provider_helm/README.md`](../../infrastructure_tooling/eso_cluster_provider_helm/README.md)

4. Create a new namespace where we will configure our first external secert to live in:
```bash
kubectl create ns alexprinter
```

5. We are now ready to deploy the `ClusterSecretStore`
    - You will need to update the [clustersecretstore.yaml](../../infrastructure_tooling/eso_cluster_provider_helm/templates/clustersecretstore.yaml) to match your cluster or needs
        - E.g. you will need to update at least the [server](https://github.com/bmorri13/homelab/blob/main/infrastructure_tooling/eso_cluster_provider_helm/templates/clustersecretstore.yaml#L17) line to reflect your correct cluster name and port

6. Create the `eso-cluster-provider-helm` app from Git, then sync it in Argo CD (it has no automated sync):
```bash
kubectl apply -f infrastructure_tooling/eso_cluster_provider_helm/application.yaml
```

7. We are now ready to deploy the `ExternalSecret`
    - You will need to update the [deployments.yaml](https://github.com/bmorri13/homelab) to match your cluster or needs
        - E.g. you will need to update at least the [server](https://github.com/bmorri13/homelab/blob/main/infrastructure_tooling/eso_cluster_provider_helm/templates/clustersecretstore.yaml#L17) line to reflect your correct cluster name and port

8. Create the `eso-external-secrets-helm` app from Git, then sync it in Argo CD (it has no automated sync). It also holds the `bmosan-cert` ClusterIssuer, so sync it only after `cert-manager-k3s` is Healthy (see the rebuild order in [argocd.md](argocd.md#rebuild-order)):
```bash
kubectl apply -f infrastructure_tooling/eso_external_secrets_helm/application.yaml
```

9. Confirm both in the UI and backend with kubectl that the secert was created
```bash
kubectl get secrets -n alexprinter

NAME       TYPE     DATA   AGE
cf-token   Opaque   1      2m34s
```

- You have now hooked up your first secret to be made available for helm chart deployments!
