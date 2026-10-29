# Secondary K3s Cluster (docker-compose-03-k3s)

This cluster runs on a VM provisioned via Terraform and hosts GitHub Actions runners accessible via Tailscale.

## Components Deployed

> ARC has since moved to the primary cluster, k3s-01 (see [ARC](kubernetes/arc.md)); the ARC rows below are from when it ran here.

| Component | Namespace | Purpose |
|-----------|-----------|---------|
| Vault | `vault` | Secrets management |
| External Secrets Operator | `external-secrets` | Kubernetes secrets from Vault |
| Tailscale Operator | `tailscale` | Secure network access via Tailnet |
| ARC Controller | `arc-systems` | GitHub Actions runner management |
| ARC Runner Scale Set | `arc-runners` | Ephemeral runner pods |

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
