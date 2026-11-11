# GitHub Actions Runner Controller (ARC)

[ARC](https://github.com/actions/actions-runner-controller) provides Kubernetes-native GitHub Actions runners that scale to zero when idle.

> ARC runs on the primary cluster, k3s-01 (runner sets `arc-k3s-homelab` and `arc-k3s-cointhieves`). Some steps below were written when it ran on docker-compose-03-k3s.

## On k3s-01

Both the controller and the runner scale sets are Argo CD apps:

| App | Source | Version | In Git |
|-----|--------|---------|--------|
| `arc-controller-k3s` | Helm chart `gha-runner-scale-set-controller` from `ghcr.io/actions/actions-runner-controller-charts` | `0.15.1` | No: created in the Argo CD UI (accepted) |
| `arc-runner-scaleset-k3s` | [`infrastructure_tooling/arc_runner_scaleset`](../../infrastructure_tooling/arc_runner_scaleset/application.yaml) | `gha-runner-scale-set` `0.15.1` | Yes |
| `arc-runner-cointhieves-k3s` | [`infrastructure_tooling/arc_runner_scaleset_cointhieves`](../../infrastructure_tooling/arc_runner_scaleset_cointhieves/application.yaml) | `gha-runner-scale-set` `0.15.1` | Yes |

The scale-set chart version in each `Chart.yaml` must match the controller version; upgrade all three together. Renovate is off for these charts for that reason.

To recreate `arc-controller-k3s` (as of 2026-11-11): destination namespace `arc-systems` on `https://kubernetes.default.svc`, project `default`, chart `gha-runner-scale-set-controller` version `0.15.1`, Helm values `flags.excludeLabelPropagationPrefixes: [app.kubernetes.io/instance]` (see [argocd.md](argocd.md#argocd-server-configuration-argocd-cm)), automated sync with prune and self-heal, and sync options `CreateNamespace=true` and `ServerSideApply=true`. The Application name matters: the scale sets refer to the controller's ServiceAccount, `arc-controller-k3s-gha-rs-controller`.

## Architecture
```
GitHub Actions Workflow
        ↓
    GitHub API
        ↓
ARC Controller (arc-systems namespace)
        ↓
Runner Scale Set (arc-runners namespace)
        ↓
Ephemeral Runner Pods
```

## Prerequisites
- GitHub Personal Access Token (Classic) with `repo` scope
- Token stored in Vault at `kv/github-arc`

## Setup
1. Create GitHub PAT at https://github.com/settings/tokens with `repo` scope

2. Store token in Vault:
```bash
KUBECONFIG=~/.kube/docker-compose-03-k3s.yaml kubectl exec vault-k3s-0 -n vault -- \
  vault kv put kv/github-arc \
    github_token="ghp_xxxxxxxxxxxxxxxxxxxx"
```

3. Deploy ARC Controller:
```bash
KUBECONFIG=~/.kube/docker-compose-03-k3s.yaml helm upgrade --install arc-controller \
  oci://ghcr.io/actions/actions-runner-controller-charts/gha-runner-scale-set-controller \
  --namespace arc-systems \
  --create-namespace \
  --version 0.15.1
```

4. Create the ExternalSecret for the runner:
```bash
KUBECONFIG=~/.kube/docker-compose-03-k3s.yaml kubectl create namespace arc-runners
KUBECONFIG=~/.kube/docker-compose-03-k3s.yaml kubectl apply -f - <<EOF
apiVersion: external-secrets.io/v1
kind: ExternalSecret
metadata:
  name: arc-github-token
  namespace: arc-runners
spec:
  refreshInterval: "1h"
  secretStoreRef:
    name: vault-backend
    kind: ClusterSecretStore
  target:
    name: arc-github-token
    creationPolicy: Owner
  data:
    - secretKey: github_token
      remoteRef:
        key: kv/github-arc
        property: github_token
EOF
```

5. Deploy Runner Scale Set:
```bash
KUBECONFIG=~/.kube/docker-compose-03-k3s.yaml helm upgrade --install arc-runner-scaleset \
  infrastructure_tooling/arc_runner_scaleset \
  --namespace arc-runners
```

6. Verify deployment:
```bash
KUBECONFIG=~/.kube/docker-compose-03-k3s.yaml kubectl get pods -n arc-systems
KUBECONFIG=~/.kube/docker-compose-03-k3s.yaml kubectl get autoscalingrunnersets -n arc-runners
```

## Using ARC Runners

The ARC runner uses Kubernetes container mode. Workflows must specify a container:

```yaml
jobs:
  build:
    runs-on: arc-k3s-homelab
    container:
      image: ubuntu:24.04
    steps:
      - name: Install Dependencies
        run: apt-get update && apt-get install -y <packages>
      - uses: actions/checkout@v4
      # ... rest of your steps
```

## Available Runners

| Runner | Label | Type |
|--------|-------|------|
| VM Runner | `[self-hosted, Linux, X64]` | Persistent VM |
| ARC Runner | `arc-k3s-homelab` | Ephemeral K8s pods |
