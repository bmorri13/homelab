# Argo CD

Argo CD itself, as it runs in the `argocd` namespace on k3s-01: the upstream non-HA install (`manifests/cluster-install`) at the release pinned in `kustomization.yaml`, with this cluster's changes:

- `argocd-cm` and `argocd-rbac-cm` are left out. The `argocd-config` app owns them (`../argocd_config`).
- `argocd-server` is a NodePort Service on 30447 (HTTP) and 31836 (HTTPS). There is no ingress.

Argo CD doesn't manage itself here: a bad sync of its own Deployments would leave nothing to fix it. Changes are applied by hand, so merging a change to this directory does nothing until it is applied. Renovate is off for it for the same reason (`renovate.json`).

## Applying a change

From a checkout of `main` on a machine with `kubectl` (it builds the manifests, k3s-01 applies them):

```bash
kubectl kustomize infrastructure_tooling/argocd > /tmp/argocd.yaml

# What would change
ssh root@k3s-01 'k3s kubectl diff --server-side --force-conflicts -f -' < /tmp/argocd.yaml

# Apply. Server-side apply is required from 3.3 on (the ApplicationSet CRD is too big
# for kubectl's last-applied annotation).
ssh root@k3s-01 'k3s kubectl apply --server-side --force-conflicts -f -' < /tmp/argocd.yaml
ssh root@k3s-01 'k3s kubectl -n argocd rollout status deploy && k3s kubectl -n argocd rollout status sts'
```

## Upgrading

1. Read the upgrade notes for every minor version crossed: `docs/operator-manual/upgrading/<from>-<to>.md` in the argo-cd repo.
2. Check the target release supports the cluster's Kubernetes version (`docs/operator-manual/tested-kubernetes-versions.md` at that tag).
3. Back up the Applications and projects: `ssh root@k3s-01 'k3s kubectl -n argocd exec deploy/argocd-server -- argocd admin export -n argocd' > argocd-backup.yaml`. This file contains repository credentials; keep it off Git and delete it after.
4. Change `?ref=` in `kustomization.yaml`, open a PR, and after merge apply as above.
5. Check every Application is `Synced` and `Healthy`, and the gateway's Argo CD MCP tools still answer (they use the `mcp-gateway` account's token, which survives upgrades because `argocd-secret` is kept).

Rollback: set `?ref=` back and apply again.
