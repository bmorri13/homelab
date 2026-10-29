# Vault ingress

`https://vault.bmosan.com`: Vault's UI and API on k3s-01, reachable only from the
tailnet. Traefik routes to the existing `vault` Service (port 8200); the
`tailnet-only` middleware returns 403 to anything not from 100.64.0.0/10 or the
Tailscale IPv6 range, and cert-manager issues the certificate with DNS-01.

Outside Git: Cloudflare `A vault.bmosan.com → 100.124.194.87` (k3s-01's tailnet
IP), DNS-only, like `mcp` and `apiary`.

```bash
kubectl apply -f infrastructure_tooling/vault_ingress/application.yaml
curl -s https://vault.bmosan.com/v1/sys/health   # from a tailnet device
```

Vault's Service is still also a NodePort (31080, plain HTTP) from before the
cluster migration; nothing in this repo needs it now.
