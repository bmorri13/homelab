# ESO cluster provider (k3s-01)

Defines the `vault-backend` ClusterSecretStore that every ExternalSecret on
k3s-01 uses.

## How ESO authenticates to Vault

Vault Kubernetes auth. ESO requests a short-lived token for its own
ServiceAccount (`external-secrets/external-secrets-operator`, audience
`vault`), and Vault checks it with the Kubernetes TokenReview API. Vault
returns a token with the `eso-read` policy, valid for 1 hour.

`eso-read` can read secrets and list metadata under the `kv` mount. It
cannot write, delete, or change Vault configuration.

The Vault side is runtime configuration, not managed by Argo CD. To recreate
it (for example after re-initialising Vault), run as a Vault admin:

```bash
vault auth enable -description="k8s-01 service accounts" kubernetes
vault write auth/kubernetes/config kubernetes_host=https://kubernetes.default.svc:443

vault policy write eso-read - <<'EOF'
# External Secrets Operator: read-only access to the kv mount.
path "kv/data/*"     { capabilities = ["read"] }
path "kv/metadata/*" { capabilities = ["read", "list"] }
EOF

vault write auth/kubernetes/role/external-secrets \
  bound_service_account_names=external-secrets-operator \
  bound_service_account_namespaces=external-secrets \
  audience=vault \
  token_policies=eso-read token_ttl=1h token_max_ttl=4h
```

Vault validates ServiceAccount tokens using its own pod's credentials. The
Vault Helm chart's `vault-server-binding` ClusterRoleBinding grants it
`system:auth-delegator`, which this depends on.

## After Vault restarts

Vault is sealed after every restart and must be unsealed by hand. Until then
the store reports `ValidationFailed` and no ExternalSecret can refresh;
existing Kubernetes Secrets keep working. After unsealing, re-check
immediately instead of waiting for the next retry:

```bash
kubectl annotate clustersecretstore vault-backend force-sync=$(date +%s) --overwrite
```
