# Core Infrstructure Ingress
- How the main tooling (Vault and ArgoCD) is reached

> **Application ingresses live in their own charts**, not in `ingress_configs/`. A chart that ships its `Ingress` in `templates/` is deployed and owned by ArgoCD, so there is nothing to apply by hand.
>
> `ingress_configs/` is a standing hazard: anything in it exists only as a manual `kubectl apply` and is invisible to ArgoCD, so it silently disappears on a cluster rebuild. That is how an app ingress once ended up returning 404 on k3s-01. Nothing in it is part of the current setup. Use a chart template or an ArgoCD app for anything new.

## Vault
- Vault's ingress is the `vault-ingress` ArgoCD app in [`infrastructure_tooling/vault_ingress`](../../infrastructure_tooling/vault_ingress/README.md), reachable only from the tailnet

1. Apply the Application
```bash
kubectl apply -f infrastructure_tooling/vault_ingress/application.yaml
```

2. You should now be able to go to Vault with a secure connection (e.g. https://vault.bmosan.com/ui/)

## ArgoCD
- ArgoCD has no ingress. Its UI is reached on k3s-01's NodePorts, see [`infrastructure_tooling/argocd`](../../infrastructure_tooling/argocd/README.md)
