# Secondary K3s Cluster (docker-compose-03-k3s)

This cluster runs on a VM provisioned via Terraform and hosts Vault, External Secrets and the Tailscale operator.

## Components Deployed

> ARC used to run here; it has since moved to the primary cluster, k3s-01 (see [ARC](kubernetes/arc.md)).

| Component | Namespace | Purpose |
|-----------|-----------|---------|
| Vault | `vault` | Secrets management |
| External Secrets Operator | `external-secrets` | Kubernetes secrets from Vault |
| Tailscale Operator | `tailscale` | Secure network access via Tailnet |

## Tailscale Kubernetes Operator

The [Tailscale Kubernetes Operator](https://tailscale.com/kb/1236/kubernetes-operator) provides secure access to cluster services via the Tailnet.

### Prerequisites
- Tailscale account with OAuth client credentials
- Vault running with `kv/tailscale-operator` secret containing `clientId` and `clientSecret` (synced into the `operator-oauth` Secret by the chart's ExternalSecret)

### Setup
1. Store Tailscale OAuth credentials in Vault (prompt for them rather than putting them on the command line):
```bash
KUBECONFIG=~/.kube/docker-compose-03-k3s.yaml kubectl exec -it vault-k3s-0 -n vault -- \
  sh -c 'read -rp "clientId: " id; read -rsp "clientSecret: " sec; echo; vault kv put kv/tailscale-operator clientId="$id" clientSecret="$sec"'
```

2. Deploy. The operator is not managed by Argo CD and has no Helm release; it is rendered and applied with `kubectl`, so use the same path for upgrades (`helm upgrade --install` would fail on the existing resources):
```bash
helm template tailscale-operator infrastructure_tooling/tailscale_operator --namespace tailscale \
  > /tmp/tailscale-operator.yaml
KUBECONFIG=~/.kube/docker-compose-03-k3s.yaml kubectl apply --dry-run=server -f /tmp/tailscale-operator.yaml
KUBECONFIG=~/.kube/docker-compose-03-k3s.yaml kubectl apply -f /tmp/tailscale-operator.yaml
```

To upgrade, change the dependency version in `Chart.yaml`, run `helm dependency update` in the chart directory (the chart tgz is vendored under `charts/`), then render and apply as above. Renovate is disabled for this chart for that reason.

3. Verify the operator is running:
```bash
KUBECONFIG=~/.kube/docker-compose-03-k3s.yaml kubectl get pods -n tailscale
```

## External Secrets ClusterSecretStore

[`infrastructure_tooling/eso_cluster_provider_docker_compose_03`](../infrastructure_tooling/eso_cluster_provider_docker_compose_03/templates/clustersecretstore.yaml) defines the `vault-backend` ClusterSecretStore on this cluster. It points at this cluster's Vault (`vault-k3s`) and logs in with the token in the `vault-token` Secret in the `default` namespace (created by hand; not in Git). The Tailscale operator's ExternalSecret reads `kv/tailscale-operator` through it.

It is not managed by Argo CD; it is applied by hand. Never apply it to k3s-01: k3s-01's store ([`eso_cluster_provider_helm`](../infrastructure_tooling/eso_cluster_provider_helm/README.md)) has the same name, `vault-backend`, and this one would overwrite it. Do not rename the store either; the ExternalSecrets on this cluster refer to it by name.

Before applying, check whether it was installed as a Helm release. Before this chart was renamed it was `external-secrets-clusterstore-k3s`, so an existing release shows that name in the `CHART` column:
```bash
KUBECONFIG=~/.kube/docker-compose-03-k3s.yaml helm list -A
```

- **A release using this chart is listed:** keep using that release name and namespace. Helm tracks a release by its name, not by the chart's directory or `name:`, so this upgrades the same release in place:
  ```bash
  KUBECONFIG=~/.kube/docker-compose-03-k3s.yaml helm upgrade <existing-release> \
    infrastructure_tooling/eso_cluster_provider_docker_compose_03 --namespace <existing-namespace>
  ```
  Do not install it under a new release name. Helm refuses to adopt a store another release owns, and uninstalling a release deletes the store it owns.
- **No release is listed:** it was applied with `kubectl`, like the Tailscale operator. Keep using that path (`helm upgrade --install` would fail on the existing store). The store is cluster-scoped and the chart does not use the release name, so `eso-cluster-provider` below is only a placeholder:
  ```bash
  helm template eso-cluster-provider infrastructure_tooling/eso_cluster_provider_docker_compose_03 \
    > /tmp/eso-cluster-provider.yaml
  KUBECONFIG=~/.kube/docker-compose-03-k3s.yaml kubectl apply --dry-run=server -f /tmp/eso-cluster-provider.yaml
  KUBECONFIG=~/.kube/docker-compose-03-k3s.yaml kubectl apply -f /tmp/eso-cluster-provider.yaml
  ```

Check that the store is ready:
```bash
KUBECONFIG=~/.kube/docker-compose-03-k3s.yaml kubectl get clustersecretstore vault-backend
```
