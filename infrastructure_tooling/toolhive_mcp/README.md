# ToolHive MCP gateway (`mcp.bmosan.com`)

One MCP endpoint for every coding agent on every machine, served from k3s-01
and reachable only over the tailnet:

```
Claude Code / Codex / OpenCode / Kiro / Cline   (any machine on the tailnet)
        │  HTTPS + Authorization: Bearer <client token>
        ▼
mcp.bmosan.com → 100.124.194.87 (k3s-01's tailnet IP, Cloudflare DNS-only)
        ▼
Traefik (hostNetwork :443) ── ipAllowList: tailnet ranges only ── TLS: bmosan-cert
        ▼
vMCP "homelab"  (VirtualMCPServer, Service vmcp-homelab:4483, path /mcp)
  ├─ authN: client token = Kubernetes ServiceAccount JWT, audience https://mcp.bmosan.com
  ├─ authZ: Cedar — listed clients only; read-only tools, plus authz.writeTools
  ├─ audit: one JSON event per request on stdout
  └─ aggregates every MCPServer in MCPGroup "homelab", tools prefixed "<server>_"
        ▼
MCPServer toolhive-docs (proxy runner → server pod)
MCPServer cribl (proxy runner → login shim → https://192.168.4.11:9000/api/v1/mcp)
MCPRemoteProxy excalidraw (proxy pod → https://mcp.excalidraw.com/mcp)
MCPRemoteProxy splunk (proxy pod → https://192.168.4.11:8089/services/mcp)
more added via values.yaml
```

## Layout

Three ArgoCD Applications, one chart directory each (the repo's usual
`infrastructure_tooling/<component>/` pattern, rather than a separate
`kubernetes/` tree):

| Directory | ArgoCD app | What it holds |
|---|---|---|
| `toolhive_crds/` | `toolhive-crds` | Vendored `toolhive-operator-crds` chart (14 CRDs, `toolhive.stacklok.dev/v1beta1`) |
| `toolhive_operator/` | `toolhive-operator` | Vendored `toolhive-operator` chart, namespace `toolhive-system`; also creates `toolhive-mcp` |
| `toolhive_mcp/` | `toolhive-mcp` | Everything you edit day to day, in namespace `toolhive-mcp` |

`toolhive_mcp/templates/` is split by concern:

- `mcp-servers/` — MCPGroup, one MCPServer per `values.mcpServers` entry, one MCPRemoteProxy (+ its egress policy) per `values.remoteServers` entry, Vault ExternalSecrets, the `mcp-trusted-cas` ConfigMap (`files/*.crt`) and the `mcp-scripts` ConfigMap (`scripts/*.py`)
- `gateway/` — VirtualMCPServer (aggregation, Cedar policies, audit, JWKS sidecar) and MCPOIDCConfig
- `ingress/` — Traefik Ingress + tailnet-only Middleware
- `security/` — client ServiceAccounts (namespace `toolhive-clients`) and NetworkPolicies

Versions: ToolHive **v0.57.0** (operator, proxy runner and vMCP images; both
charts 0.57.0). The chart tarballs are vendored under `charts/` and their
`Chart.lock` digests match. Renovate is disabled for them (see Upgrading).

## Installation (from scratch)

There is no app-of-apps, so apply the three Applications in order, waiting
for each to be `Synced/Healthy`:

```bash
kubectl apply -f infrastructure_tooling/toolhive_crds/application.yaml
kubectl apply -f infrastructure_tooling/toolhive_operator/application.yaml
kubectl apply -f infrastructure_tooling/toolhive_mcp/application.yaml
```

Outside Git, only the DNS record is needed: Cloudflare `A mcp.bmosan.com →
100.124.194.87`, **DNS-only** (not proxied), like the other `*.bmosan.com`
records. It points at k3s-01's tailnet IP, so it resolves anywhere but only
connects from the tailnet. The TLS certificate (`mcp-bmosan-com-tls`) comes
from cert-manager DNS-01, which needs no inbound access.

## Gateway URLs

| URL | Purpose | Auth |
|---|---|---|
| `https://mcp.bmosan.com/mcp` | MCP Streamable HTTP endpoint for clients | Bearer token |
| `https://mcp.bmosan.com/.well-known/oauth-protected-resource/mcp` | RFC 9728 metadata | none |
| `http://vmcp-homelab.toolhive-mcp.svc:4483/health`, `/status`, `/api/backends/health` | In-cluster health | none (NetworkPolicy-limited) |

## Client tokens

Each machine has its own ServiceAccount in `toolhive-clients` (listed in
`values.yaml` → `auth.clients`). Its token is a JWT signed by the k3s-01 API
server with audience `https://mcp.bmosan.com`. The vMCP validates the
signature against the cluster's JWKS, and Cedar only admits the listed
subjects.

```bash
# On a machine that can `ssh root@k3s-01`; prints the token to stdout.
infrastructure_tooling/toolhive_mcp/mint-client-token.sh macbook-air          # 90 days
infrastructure_tooling/toolhive_mcp/mint-client-token.sh linux-vm 720h        # 30 days
```

Put it in `TOOLHIVE_MCP_TOKEN` on the client machine (a password manager or
OS keychain, loaded into your shell profile). Never put it in Git or in a
synced dotfile.

- **New machine:** add a name to `auth.clients`, merge, wait for sync, then mint.
- **Revoke a machine:** remove it from `auth.clients` and merge. The Cedar
  allowlist is rebuilt from that list, so every token minted for it is denied
  on the next sync, even unexpired ones. Deleting the ServiceAccount alone
  is **not** enough, because JWKS validation can't see deletions.
- **Rotate:** mint a new token; the old one keeps working until it expires.
  To kill it early, rename the client (remove the old name, add a new one).
- These tokens are useless against the Kubernetes API (wrong audience), and
  the ServiceAccounts have no RoleBindings.

### Agents

The security and operations agents on coding-box (`agent-sec`, `agent-ops`)
are also ServiceAccounts in `toolhive-clients`, listed under `auth.agents` in
`values.yaml` with an explicit tool list each. They get none of the client
rules: an agent sees and can call only its own list (everything else is
**403** and missing from `tools/list`), never a `writeTools` entry. Their
Splunk tools come from a separate backend, `splunk-agent` (see
[Splunk and Cribl](#splunk-and-cribl)), which only agents can use.

The runner in [`homelab_agents`](../homelab_agents/README.md) mints each
agent's token itself, for one hour, every run. It authenticates as a third
ServiceAccount, `agent-runner` (`auth.agentRunner`), whose only permission is
creating tokens for the two agents and for itself (a Vault login); it is in no
Cedar policy, so it can't use the gateway directly.

```bash
# Short-lived token for one agent run, by hand
infrastructure_tooling/toolhive_mcp/mint-client-token.sh agent-sec 1h
```

Adding a tool for an agent means adding its aggregated name to that agent's
list. Revoking works as for clients: remove the agent from `auth.agents`.

## Connecting coding agents

All examples assume `TOOLHIVE_MCP_TOKEN` is set in the environment the agent
starts from. **Tested** means connected, tools listed and a tool called from
that client against `https://mcp.bmosan.com/mcp` over the tailnet. **Example** means taken from the client's
official docs or source but not run here.

### Claude Code — tested (v2.1.303)

User scope, all projects. The `--header` value is expanded by your shell, so
the token itself is written to `~/.claude.json`:

```bash
claude mcp add --scope user --transport http toolhive https://mcp.bmosan.com/mcp \
  --header "Authorization: Bearer $TOOLHIVE_MCP_TOKEN"
```

To keep only a variable reference on disk, use `.mcp.json` (project) instead:

```json
{
  "mcpServers": {
    "toolhive": {
      "type": "http",
      "url": "https://mcp.bmosan.com/mcp",
      "headers": { "Authorization": "Bearer ${TOOLHIVE_MCP_TOKEN}" }
    }
  }
}
```

### OpenAI Codex CLI — connection tested (0.163.0)

Verified against the gateway: authenticates via `bearer_token_env_var`,
initializes, and lists tools (audit log shows `codex-mcp-client/0.163.0`).
A tool *call* couldn't be completed on the test Mac because Codex's local
code-mode host timed out for every tool, including plain shell commands, with
no MCP configured. That is a local Codex fault, not the gateway.

```bash
codex mcp add toolhive --url https://mcp.bmosan.com/mcp --bearer-token-env-var TOOLHIVE_MCP_TOKEN
```

which writes to `~/.codex/config.toml`:

```toml
[mcp_servers.toolhive]
url = "https://mcp.bmosan.com/mcp"
bearer_token_env_var = "TOOLHIVE_MCP_TOKEN"
```

`bearer_token_env_var` also stops Codex from trying OAuth against the
server's 401 metadata.

### OpenCode — tested (1.18.35)

`~/.config/opencode/opencode.json` (global) or `opencode.json` (project):

```json
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "toolhive": {
      "type": "remote",
      "url": "https://mcp.bmosan.com/mcp",
      "oauth": false,
      "headers": { "Authorization": "Bearer {env:TOOLHIVE_MCP_TOKEN}" }
    }
  }
}
```

`"oauth": false` is required. Without it OpenCode sees the 401 challenge and
starts an OAuth flow this gateway doesn't offer.

### Kiro (IDE / kiro-cli) — example

`~/.kiro/settings/mcp.json` (user) or `.kiro/settings/mcp.json` (workspace):

```json
{
  "mcpServers": {
    "toolhive": {
      "url": "https://mcp.bmosan.com/mcp",
      "headers": { "Authorization": "Bearer ${TOOLHIVE_MCP_TOKEN}" }
    }
  }
}
```

Kiro only expands variables you have approved: add `TOOLHIVE_MCP_TOKEN` under
the *Mcp Approved Env Vars* setting. It isn't documented whether Kiro tries
OAuth when a static header is present.

### Cline (VS Code) — example

`cline_mcp_settings.json` (MCP Servers → Configure → Configure MCP Servers):

```json
{
  "mcpServers": {
    "toolhive": {
      "type": "streamableHttp",
      "url": "https://mcp.bmosan.com/mcp",
      "headers": { "Authorization": "Bearer ${env:TOOLHIVE_MCP_TOKEN}" }
    }
  }
}
```

`type` must be `streamableHttp`; if it is omitted, Cline assumes legacy SSE.
The VS Code extension expands `${env:VAR}`; the Cline CLI apparently doesn't,
so use a literal token in its `~/.cline/data/settings/cline_mcp_settings.json`.

### Windows

Same configs. Set the variable per user, then restart the agent:

```powershell
[Environment]::SetEnvironmentVariable("TOOLHIVE_MCP_TOKEN", "<token>", "User")
```

The Windows machine must be on the tailnet. Non-tailnet LAN clients are
deliberately refused (HTTP 403 from Traefik).

### Raw check from any machine

```bash
curl -s https://mcp.bmosan.com/mcp \
  -H "Authorization: Bearer $TOOLHIVE_MCP_TOKEN" \
  -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"curl","version":"0"}}}'
# 200 + serverInfo "homelab" = network, TLS and token are all fine
```

## Adding an MCP server

Add an entry under `mcpServers` in `values.yaml` and merge. `spec` is passed
verbatim to a `toolhive.stacklok.dev/v1beta1` MCPServer (see `kubectl explain
mcpserver.spec`); `groupRef` is filled in for you. The vMCP picks the server up
automatically, with its tools prefixed `<name>_`.

Then decide what clients may call. **New tools are callable only if** the
server marks them `readOnlyHint: true` or you add them to
`authz.readOnlyTools`. Tools that change things go in `authz.writeTools`
(allowed for every listed client), or narrowly per client via
`authz.extraPolicies`.

Example: GitHub MCP, read-only, token from Vault.

```yaml
externalSecrets:
  secrets:
    github-mcp:                     # Secret name in toolhive-mcp
      vaultPath: kv/toolhive-github-mcp
      keys: [token]                 # vault kv put kv/toolhive-github-mcp token=<PAT>

mcpServers:
  github:
    enabled: true
    spec:
      image: ghcr.io/github/github-mcp-server:<pinned tag>@sha256:<digest>
      transport: stdio              # proxied to streamable-http by ToolHive
      args: ["stdio", "--read-only"]
      secrets:
        - name: github-mcp
          key: token
          targetEnvName: GITHUB_PERSONAL_ACCESS_TOKEN
      resources:
        requests: {cpu: 20m, memory: 64Mi}
        limits: {cpu: 500m, memory: 256Mi}
      podTemplateSpec:
        spec:
          automountServiceAccountToken: false
          securityContext:          # namespace enforces Pod Security "restricted"
            runAsNonRoot: true
            runAsUser: 65532
            seccompProfile: {type: RuntimeDefault}
          containers:
            - name: mcp
              securityContext:
                allowPrivilegeEscalation: false
                capabilities: {drop: ["ALL"]}
```

Checklist for each new server:

1. **Pin the image** by digest. Prefer images that run as a numeric non-root
   UID; the namespace enforces Pod Security `restricted`, so the pod will be
   rejected otherwise.
2. **Credentials** come only from `externalSecrets` + `spec.secrets`, never
   `env`. Use a scoped, read-only token where the service supports one.
3. **Egress:** `default-deny` blocks everything outside the namespace except
   DNS. List what the server may reach under its `egress:` key
   (`- cidr: <addr>/32` with `ports: [...]`); that renders an
   `allow-egress-<name>` policy for its pods only. Don't widen the
   namespace-wide rules.
4. **Kubernetes MCP / filesystem MCP:** give the server its own ServiceAccount
   (`spec.serviceAccount`) bound to a read-only Role, never the operator's or
   the vMCP's. Avoid hostPath `volumes`.
5. Remote (SaaS) MCP servers join the same group via `MCPRemoteProxy` or
   `MCPServerEntry` (both take a `groupRef`).

## Adding a remote (hosted) MCP server

For servers someone else hosts (ToolHive registry entries of type `remote`,
e.g. `thv registry info <name>` shows a URL), add an entry under
`remoteServers` instead. It becomes an `MCPRemoteProxy`: a small proxy pod
that makes the outbound HTTPS call, so only that pod gets internet egress
(public addresses on 443; LAN, tailnet and cluster ranges are excluded).

```yaml
remoteServers:
  excalidraw:
    enabled: true
    spec:
      remoteUrl: https://mcp.excalidraw.com/mcp
      transport: streamable-http
      proxyPort: 8080
```

Credentials for a remote server go in `spec.headerForward.addHeadersFromSecret`
(from an `externalSecrets` entry), never `addPlaintextHeaders`. When the
header needs more than the raw secret (e.g. `Bearer <token>`), give the
`externalSecrets` entry a `template:` that builds it. Check the remote's tool
annotations (`tools/list`) and allowlist accordingly.

A remote server **on the LAN** needs `spec.allowPrivateEndpoint: true` and an
`egress:` list on the entry. That replaces the public-internet egress policy
with exactly those destinations. If its cert is from a private CA, put the CA in
`files/` and mount `mcp-trusted-cas` into the proxy (see `splunk`).

**Excalidraw** (added from the ToolHive registry entry
`io.github.stacklok/excalidraw-remote`): `read_me` and `create_view` are
annotated read-only; `read_checkpoint` and `save_checkpoint` are allowlisted
(diagram edit state). `export_to_excalidraw` is **blocked**, because it
uploads the diagram to a public excalidraw.com link; add
`excalidraw_export_to_excalidraw` to `authz.readOnlyTools` to enable it.
Diagram content is sent to Excalidraw's hosted service either way.

## Splunk and Cribl

Both run on security-tooling-01 (`infrastructure_tooling/monitoring_stack/splunk/`)
and present certs from the homelab CA (`files/homelab-ca.crt`, made by that
stack's `scripts/tls-certs.sh`).

**Splunk** (`remoteServers.splunk`) is the Splunk MCP Server app on splunkd's
port 8089, which only accepts connections from k3s-01. The proxy sends
`Authorization: Bearer <token>`. The token is an encrypted Splunk MCP token for
the user `mcp-gateway` (role `mcp_reader`: search on every index, no admin),
valid for at most 180 days. Vault: `kv/apiary/shared/splunk`, field `token`. To
rotate it, run this on a machine that can ssh to both hosts. The token goes from
one host to the other without being printed:

```bash
ssh root@security-tooling-01 /opt/splunk-stack/scripts/splunk-mcp-token.sh |
  ssh root@k3s-01 'VT=$(kubectl -n default get secret vault-token -o jsonpath="{.data.token}" | base64 -d);
    { printf "%s\n" "$VT"; cat; } | kubectl -n vault exec -i vault-0 -- sh -c \
    "read -r VT; read -r T; export VAULT_TOKEN=\$VT; printf %s \"\$T\" | vault kv put kv/apiary/shared/splunk token=-"'
```

Or paste a token into Apiary. Then delete the old token in Splunk under
Settings > Tokens.

**Splunk for agents** (`remoteServers.splunk-agent`) is the same app as the user
`mcp-agent`, role `mcp_agent` (`00_homelab_roles`). That role imports no other
role and switches off the write capabilities Splunk gives every role by default,
so SPL that writes fails in Splunk itself: `collect`, `mcollect`, `outputlookup`,
`outputcsv`, `sendalert` and `dump` were each tested and refused. It also caps
the agents at 4 concurrent searches of at most 5 minutes, with no real-time
searches. Cedar hides its tools (`splunk-agent_splunk_*`) from every caller
except the agents in `auth.agents`. Vault: `kv/apiary/shared/splunk-agent`,
field `token`. Rotate it as above, with
`MCP_USER=mcp-agent MCP_ROLE=mcp_agent` in front of `splunk-mcp-token.sh`
and `kv/apiary/shared/splunk-agent` as the Vault path.

**Cribl** (`mcpServers.cribl`) is Cribl's MCP server on the Leader. It needs a
session token that expires hourly, so the "server" is
[`scripts/cribl_mcp_auth.py`](scripts/cribl_mcp_auth.py) on a stock Python
image. It logs in as the Cribl user from Vault (`kv/apiary/shared/cribl`,
`username`/`password`), renews the token before it expires or on a 401, and
forwards `/mcp` only. The rest of the Cribl API is not reachable through it. To
create the user or rotate its password, pipe
`/opt/splunk-stack/scripts/cribl-mcp-user.sh` into
`vault kv put kv/apiary/shared/cribl -` the same way, then restart the server
(`kubectl -n toolhive-mcp rollout restart sts cribl`). On Cribl's free license
every user is an admin, so Cedar is what limits it.

Tool names repeat the prefix (`splunk_splunk_run_query`,
`cribl_cribl_getSources`): the workload prefix plus the servers' own. For
testing, every tool of both is allowed:

- Splunk's read tools carry `readOnlyHint`.
- `splunk_run_query`, `splunk_run_saved_search` (SPL), `cribl_runSearchQuery`,
  `cribl_setPipelineConfig` (rewrites a live pipeline), and the three Cribl
  notebook editors are in `authz.writeTools`.
- The 11 Cribl readers, which carry no annotations, are in `authz.readOnlyTools`.

Remove lines from `authz.writeTools` to tighten it.

## Argo CD

**Argo CD** (`mcpServers.argocd`) is the Argo project's MCP server
(`argoproj-labs/mcp-for-argocd`). It calls `argocd-server` in-cluster over
HTTPS, and its egress policy allows only the argocd-server pods. Argo CD serves
its own self-signed cert (from `argocd-secret`, `tls.crt`); the server trusts
exactly that cert, copied to `files/argocd-server.crt`. **It expires
2027-05-10.** When Argo CD's cert changes, copy the new one
(`kubectl -n argocd get secret argocd-secret -o jsonpath='{.data.tls\.crt}' | base64 -d`)
over that file and merge. It is read-only twice over:

- It authenticates as the Argo CD account `mcp-gateway`, which has an API key
  only (no UI login) and `role:readonly`. Both are set in
  `infrastructure_tooling/argocd_config` (`apiAccounts`).
- `MCP_READ_ONLY=true` stops the server from offering `create_application`,
  `update_application`, `delete_application`, `sync_application` and
  `run_resource_action`.

Its 11 read tools carry no annotations, so they are listed in
`authz.readOnlyTools` (`argocd_list_applications`, ...). Note that
`argocd_get_application_workload_logs` returns pod logs, and `role:readonly`
can read any application's logs and manifests (Argo CD masks Secret data).

The API token lives in Vault at `kv/apiary/shared/argocd`, field `token`. To
mint or rotate it (180 days), run this on k3s-01 (`bash -s`). It logs in to
the Argo CD API as admin, mints a token for `mcp-gateway` and writes it to
Vault; nothing is printed. (`argocd account generate-token --core` doesn't work
from inside the argocd-server pod: its ServiceAccount can't list Services.)

```bash
set -euo pipefail
A=https://argocd-server.argocd.svc.cluster.local
CA=$(mktemp); trap 'rm -f "$CA"' EXIT
kubectl -n argocd get secret argocd-secret -o jsonpath='{.data.tls\.crt}' | base64 -d > "$CA"
C=(curl -sf --cacert "$CA" --resolve "argocd-server.argocd.svc.cluster.local:443:$(kubectl -n argocd get svc argocd-server -o jsonpath='{.spec.clusterIP}')")
ADMIN=$(kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath='{.data.password}' | base64 -d |
  jq -Rr '{username:"admin",password:.}' | "${C[@]}" -H 'Content-Type: application/json' -d @- $A/api/v1/session | jq -r .token)
TOK=$(jq -n --arg id "mcp-gateway-$(date +%Y%m%d)" '{id:$id, expiresIn:15552000}' |
  "${C[@]}" -H "Authorization: Bearer $ADMIN" -H 'Content-Type: application/json' -d @- $A/api/v1/account/mcp-gateway/token | jq -r .token)
VT=$(kubectl -n default get secret vault-token -o jsonpath='{.data.token}' | base64 -d)
printf '%s\n%s\n' "$VT" "$TOK" | kubectl -n vault exec -i vault-0 -- sh -c \
  'read -r VT; read -r T; export VAULT_TOKEN=$VT VAULT_ADDR=http://127.0.0.1:8200; printf %s "$T" | vault kv put kv/apiary/shared/argocd token=-'
kubectl -n toolhive-mcp annotate externalsecret argocd-mcp force-sync=$(date +%s) --overwrite
```

Then restart the server (`kubectl -n toolhive-mcp rollout restart sts argocd`)
once the Secret has updated. Old tokens stay valid until they expire; list and
revoke them in the Argo CD UI under Settings > Accounts > mcp-gateway.

## Removing / updating / stopping a server

- **Remove:** delete its `mcpServers` entry (and any `externalSecrets` entry)
  and merge. The app prunes, so the MCPServer and everything the operator
  built for it is deleted, and the vMCP drops its tools.
- **Stop without removing:** `enabled: false`.
- **Update:** change `spec.image` (tag + digest) or other fields and merge.
  The operator rolls the server. A vMCP rollout drops open MCP sessions;
  clients reconnect.
- **Rollback:** `git revert` the commit. ArgoCD converges back.

## Secrets

Vault is the source of truth (`ClusterSecretStore vault-backend`):

```bash
kubectl -n vault exec -it vault-0 -- vault kv put kv/toolhive-<server> token=<value>
```

Reference the path under `externalSecrets.secrets` as shown above. Nothing in
this directory holds a secret. Client tokens are minted on demand and never
stored in the cluster or in Git.

## Management UI options

There is **no supported self-hosted web admin UI for open-source ToolHive on
Kubernetes** (checked at v0.57.0):

- `stacklok/toolhive-cloud-ui` is archived/retired. Not deployed.
- **ToolHive Studio** is a desktop app for running servers locally. It can
  browse a registry, but it doesn't manage operator-deployed servers.
- **ToolHive Registry Server** (`oci://ghcr.io/stacklok/toolhive-registry-server`)
  is an API (a catalog of servers), needs PostgreSQL, and has no UI.
  The `MCPRegistry` CRD is deprecated. Not deployed: no value until there is a
  catalog worth curating.
- The web console is a **Stacklok Enterprise** feature.

So **ArgoCD is the management UI**: the `toolhive-mcp` app shows every
MCPServer, the vMCP, and their health; adds, removes and updates are Git
changes. Limitations: no button to add a server or flip a tool; per-tool
visibility lives in `values.yaml`; live tool inventory is only visible via
`kubectl` or an MCP client:

```bash
kubectl -n toolhive-mcp get mcpserver,vmcp,mcpgroup
kubectl -n toolhive-mcp get vmcp homelab -o jsonpath='{.status.discoveredBackends}' | jq
```

## Monitoring and logs

- **Health:** `kubectl -n toolhive-mcp get vmcp homelab` (`PHASE`/`READY`);
  per-backend status in `.status.discoveredBackends`. ArgoCD health covers
  every object.
- **Uptime Kuma:** an HTTP monitor on
  `http://vmcp-homelab.toolhive-mcp.svc:4483/health` works from the
  `uptime-kuma` namespace (NetworkPolicy `allow-health-checks`). The public
  URL won't work from in-cluster pods: they are not tailnet sources.
- **Audit:** `kubectl -n toolhive-mcp logs deploy/homelab -c vmcp | grep audit_event`.
  Each event carries `subjects.user_id` (the client SA), the tool, and
  `outcome` (`success` / `denied`).
- **Metrics:** not wired up (no Prometheus in this cluster). To add them,
  create an `MCPTelemetryConfig` and set `telemetryConfigRef`. The inline
  `telemetry` field is gone in v1beta1.

## Resource usage

Measured on k3s-01 (4 vCPU / 16 GiB):

| Pod | CPU req / limit | Mem req / limit | Observed |
|---|---|---|---|
| toolhive-operator | 10m / 500m | 64Mi / 128Mi | ~7m, ~23Mi |
| vMCP (`homelab`) incl. jwks-proxy | 55m / 600m | 88Mi / 320Mi | ~2m, ~30Mi |
| toolhive-docs proxy runner | operator default | operator default | ~2m, ~14Mi |
| toolhive-docs server | 50m / 500m | 256Mi / 768Mi | ~5m idle, ~290Mi (embedded vector DB) |
| excalidraw remote proxy | 10m / 200m | 32Mi / 128Mi | ~1m, ~15Mi |
| splunk remote proxy | 10m / 200m | 32Mi / 128Mi | ~2m, ~23Mi |
| cribl proxy runner | operator default | operator default | ~5m, ~39Mi |
| cribl login shim | 10m / 200m | 32Mi / 128Mi | ~6m, ~18Mi |

## Upgrading ToolHive

Releases are frequent. Upgrade the CRDs and operator **together**:

1. Fetch both chart tarballs at the same version into `toolhive_crds/charts/`
   and `toolhive_operator/charts/` (remove the old ones). `helm pull
   oci://ghcr.io/stacklok/toolhive/toolhive-operator{,-crds} --version X`.
2. Bump `version:` in both `Chart.yaml`s, then run `helm dependency update` in
   each to regenerate `Chart.lock`.
3. Read the release notes for CRD field changes and run `helm template` on
   `toolhive_mcp` against the new CRDs (`kubectl apply --dry-run=server`).
4. Merge the CRDs first, then the operator. Watch
   `kubectl -n toolhive-mcp get vmcp,mcpserver`.

## Security model

Layers, outermost first. Each one was tested against a live request:

1. **Network:** DNS points at the tailnet IP; Traefik's `ipAllowList` admits
   only `100.64.0.0/10` and `fd7a:115c:a1e0::/48`. Same host via the LAN IP:
   **403**. Tailscale ACLs decide which of your devices reach k3s-01 at all.
2. **TLS:** Let's Encrypt cert via `bmosan-cert`; HTTPS only (no route on :80).
3. **Authentication:** no token, a malformed token, or a client-SA token with
   the API-server audience → **401**.
4. **Authorization (Cedar):** a valid-audience token for any ServiceAccount
   not in `auth.clients`, including a deleted client's, can complete
   `initialize` but gets an empty tool list and **403** on every call. Tools
   not marked read-only or allowlisted → **403** for everyone. Agents
   (`auth.agents`) get only their own tool list; clients get **403** on the
   agents' `splunk-agent_*` tools.
5. **Kubernetes:** the operator is namespace-scoped (its write access to
   Secrets/RBAC/Deployments is limited to `toolhive-mcp`); Pod Security
   `restricted` is enforced in `toolhive-system`, `toolhive-mcp` and
   `toolhive-clients`; all pods run non-root with every capability dropped.
   MCP server pods have no ServiceAccount token and no API egress;
   `default-deny` NetworkPolicy plus explicit allows (intra-namespace, DNS,
   API server for the vMCP/proxy runners, health checks).
6. **GitOps:** self-heal reverts drift. Patching the vMCP to `anonymous` auth
   was reverted by ArgoCD within ~4s. Anyone with write access to
   `toolhive-mcp` or to this repo can still change policy, so cluster RBAC and
   branch protection are the real boundary.

Known limitations and residual risks:

- **OSS auth is OIDC or nothing.** Static API keys don't exist in OSS
  ToolHive (Stacklok's "virtual API keys" are Enterprise), hence the
  ServiceAccount-JWT design. Human SSO (GitHub/Google login per user) is
  possible with the vMCP's embedded OAuth server (`authServerConfig`, OSS)
  plus an OAuth app; not set up.
- **JWKS sidecar:** k3s serves `/openid/v1/jwks` only to authenticated
  callers, and at v0.57.0 the vMCP can't send a token when fetching it (the
  `kubernetesServiceAccount` OIDC type's `useClusterAuth` only reaches
  MCPServer proxies). The vMCP pod therefore runs `kubectl proxy` on
  127.0.0.1, restricted to `GET /openid/v1/jwks`. That requires
  `insecureAllowHTTP`/`jwksAllowPrivateIP` on the OIDC config, which only
  affect that loopback hop. Re-check on upgrade and drop the sidecar if
  upstream fixes it.
- **Upstream fail-open on outages:** if the vMCP cannot aggregate backend
  capabilities, its Cedar gate admits `tools/call` instead of returning 403
  ("vmcp authz gate: non-authorization error, admitting request" WARN). This
  is deliberate upstream (`pkg/vmcp/server/call_gate.go`), and the call then
  fails for lack of a routable backend. The residual risk is a narrow race
  where a backend recovers between gate and dispatch. Alert on that WARN line.
- **`initialize` is not gated by Cedar.** Any valid-audience token can open a
  session; it just sees no tools. Only cluster admins can mint such tokens.
- **Proxy runners are unauthenticated in-cluster.** `mcp-<name>-proxy:8080`
  trusts its caller; only the vMCP and other pods inside `toolhive-mcp` can
  reach it (NetworkPolicy).
- **Single replica.** Restarts drop sessions; the vMCP needs ~60–90s after a
  restart before backends report healthy (30s health-check cadence plus
  kube-router programming the new pod's policy). Scaling past one replica
  requires Redis `sessionStorage`.
- **Node IP:** the tailnet IP and the NetworkPolicy API-server CIDR assume
  k3s-01 keeps `100.124.194.87` / `192.168.4.98`. The LAN IP is a DHCP lease;
  reserve it in UniFi.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `403 Forbidden` (plain text) | Request didn't come from a tailnet address. Check `tailscale status` on the client and that DNS gives `100.124.194.87`. |
| `401 authorization header required` | Client isn't sending the header (env var unset in the agent's environment, or the client started OAuth instead; see per-client notes). |
| `401 invalid audience` | Token minted without `--audience https://mcp.bmosan.com`; use `mint-client-token.sh`. |
| `401 ... token is expired` | Mint a new one. |
| `401 ... failed to lookup JWKS` | jwks-proxy sidecar unhealthy: `kubectl -n toolhive-mcp logs deploy/homelab -c jwks-proxy`. |
| Connected but no tools | Client isn't in `auth.clients` (Cedar), or backends are down: `kubectl -n toolhive-mcp get vmcp homelab -o jsonpath='{.status.discoveredBackends}'`. |
| `403 call denied by authorization policy` | Tool isn't read-only / allowlisted; see `authz` in `values.yaml`. |
| vMCP `Degraded` right after a sync | Normal for ~1–2 min after a rollout; persistent means a backend is down. |
| ArgoCD stuck retrying an old revision after a fix | `kubectl -n argocd patch app toolhive-mcp --type json -p '[{"op":"remove","path":"/operation"}]'` |
| `mcp.bmosan.com` doesn't resolve right after DNS changes | Cloudflare's negative-cache TTL is 30 min; UniFi/MagicDNS may hold an old NXDOMAIN until it expires. |
