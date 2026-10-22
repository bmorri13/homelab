# Apiary (k3s-01)

Credential console for the ToolHive MCP gateway, at
`https://apiary.bmosan.com` (tailnet only). Source and image:
[bmorri13/apiary](https://github.com/bmorri13/apiary) (private).

Paste a backend credential into a form; Apiary writes it to Vault under
`kv/apiary/`, force-syncs the matching ExternalSecret in `toolhive-mcp`, and
restarts that MCP server so it picks up the new value. It can't read stored
values back.

## What this chart deploys (namespace `apiary`)

| Object | Purpose |
|---|---|
| Deployment `apiary` | Two containers: `oauth2-proxy` (GitHub login, port 4180, the only exposed port) and `apiary` (listens on 127.0.0.1:8080). |
| Ingress + Middleware `tailnet-only` | `apiary.bmosan.com`, cert-manager TLS, Tailscale source ranges only. |
| ExternalSecrets | `apiary-app` (cookie and CSRF keys), `apiary-github-oauth` (OAuth app), `apiary-ghcr-pull` (image pull secret). |
| ServiceAccount `apiary` | No automounted token. Two projected tokens: one for the API server, one with audience `vault`. |
| Role/RoleBinding in `toolhive-mcp` | get the `apiary-catalog` ConfigMap; get/list MCPServer and MCPRemoteProxy; get/list/patch ExternalSecrets; list/delete pods. |
| NetworkPolicies | Default deny. Egress only to DNS, Vault, the API server, and public HTTPS (GitHub). |

Everything is behind `enabled` (default `false`) until its Vault entries
exist and CI has published an image.

The list of servers and credentials comes from the `apiary-catalog`
ConfigMap, rendered by the `toolhive_mcp` chart from its
`externalSecrets.secrets` entries that have `fields:`. To add a credential to
the console, add it there; see `toolhive_mcp/values.yaml`.

## Vault setup (runtime, not GitOps)

Already applied on k3s-01. To recreate (for example after re-initialising
Vault), run as a Vault admin:

```bash
vault policy write apiary-write - <<'EOF'
# Apiary console: store MCP backend credentials, never read them back.
path "kv/data/apiary/*"     { capabilities = ["create", "update"] }
path "kv/metadata/apiary/*" { capabilities = ["read"] }
EOF

vault write auth/kubernetes/role/apiary \
  bound_service_account_names=apiary \
  bound_service_account_namespaces=apiary \
  audience=vault \
  token_policies=apiary-write token_ttl=1h token_max_ttl=4h

# Apiary's own keys, generated inside Vault so no one sees them.
CK=$(head -c 32 /dev/urandom | base64 | tr '+/' '-_' | tr -d '\n')
XK=$(head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n')
printf '{"cookie_secret":"%s","csrf_key":"%s"}' "$CK" "$XK" | vault kv put kv/apiary-system/app -
unset CK XK
```

The Kubernetes auth method itself is set up in
`eso_cluster_provider_helm/README.md`.

## Entries you create

Enter both without the values touching your shell history or this chat:

**GitHub OAuth app** (GitHub → Settings → Developer settings → OAuth Apps):
homepage `https://apiary.bmosan.com`, callback
`https://apiary.bmosan.com/oauth2/callback`.

```bash
ssh -t root@k3s-01 'read -rp "Client ID: " I; read -rsp "Client secret: " S; echo
VT=$(kubectl -n default get secret vault-token -o jsonpath="{.data.token}" | base64 -d)
printf "%s" "$S" | kubectl -n vault exec -i vault-0 -- env VAULT_TOKEN="$VT" VAULT_ADDR=http://127.0.0.1:8200 \
  vault kv put kv/apiary-github-oauth client_id="$I" client_secret=-'
```

**GHCR pull token** (GitHub → Settings → Developer settings → Personal access
tokens (classic), scope `read:packages` only, with an expiry):

```bash
ssh -t root@k3s-01 'read -rsp "GHCR read:packages token: " T; echo
VT=$(kubectl -n default get secret vault-token -o jsonpath="{.data.token}" | base64 -d)
printf "%s" "$T" | kubectl -n vault exec -i vault-0 -- env VAULT_TOKEN="$VT" VAULT_ADDR=http://127.0.0.1:8200 \
  vault kv put kv/apiary-ghcr-pull username=bmorri13 token=-'
```

## Install

```bash
kubectl apply -f infrastructure_tooling/apiary/application.yaml
```

Then set `enabled: true` and `image.digest` (from the apiary CI run summary)
in `values.yaml` and merge.

## Checks

```bash
kubectl -n apiary get externalsecret,pods,ingress
curl -s -o /dev/null -w '%{http_code}\n' https://apiary.bmosan.com/healthz   # 200 from the tailnet
curl -s -o /dev/null -w '%{http_code}\n' https://apiary.bmosan.com/          # 302 to GitHub login
kubectl -n apiary logs deploy/apiary -c apiary | grep apiary.audit           # credential changes
```

## Security notes

- The app trusts `X-Forwarded-User` only because nothing but oauth2-proxy in
  the same pod can reach `127.0.0.1:8080`. Don't add a Service port or Ingress
  path that points at 8080.
- Vault is reached over in-cluster HTTP, the same as ESO.
- Pod delete rights cover all of `toolhive-mcp`; the app only deletes pods
  labelled `toolhive-name=<server>` for a server in the catalog, never the
  gateway pod.
