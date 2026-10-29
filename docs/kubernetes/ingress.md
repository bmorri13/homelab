# Core Infrstructure Ingress
- Deploying the ingress configurations for our main tooling (e.g. Vault and ArgoCD (WIP))

> **Application ingresses now live in their own charts**, not here. Uptime Kuma and Stirling PDF each ship an `Ingress` in `templates/`, so ArgoCD deploys and owns it — nothing to apply by hand. `ingress_configs/` holds only the ingresses for tooling that is not deployed from a chart in this repo.
>
> This directory is a standing hazard: anything in it exists only as a manual `kubectl apply` and is invisible to ArgoCD, so it silently disappears on a cluster rebuild. That is exactly how `stirling-pdf.bmosan.com` ended up returning 404 on k3s-01. Prefer a chart template for anything new.

## Setup
1. Naviate to the `ingress_configs` directory

2. Apply the ingresses for chart-less tooling
```bash
kubectl apply -f vault-ingress.yaml
```

3. You should now be able to go to Vault & Uptime Kuma with a secure connction (e.g. https://vault.bmosan.com/ui/ & https://uptime-kuma.bmosan.com/)
    - NOTE: You must natviate to `/ui` in order to get the secure connection
        - You can most likely add redirects for this, but not worried about this in my homelab currently
