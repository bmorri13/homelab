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
  ├─ authZ: Cedar — listed clients only, read-only tools only
  ├─ audit: one JSON event per request on stdout
  └─ aggregates every MCPServer in MCPGroup "homelab", tools prefixed "<server>_"
        ▼
MCPServer toolhive-docs (proxy runner → server pod)
MCPRemoteProxy excalidraw (proxy pod → https://mcp.excalidraw.com/mcp)
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

- `mcp-servers/` — MCPGroup, one MCPServer per `values.mcpServers` entry, one MCPRemoteProxy (+ its egress policy) per `values.remoteServers` entry, Vault ExternalSecrets
- `gateway/` — VirtualMCPServer (aggregation, Cedar policies, audit, JWKS sidecar) and MCPOIDCConfig
- `ingress/` — Traefik Ingress + tailnet-only Middleware
- `security/` — client ServiceAccounts (namespace `toolhive-clients`) and NetworkPolicies

Versions: ToolHive **v0.55.2** (operator, proxy runner and vMCP images; both
charts 0.55.2). The chart tarballs are vendored under `charts/` and their
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
`authz.readOnlyTools`. Grant write tools narrowly via `authz.extraPolicies`.

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
   DNS. A server that calls the internet (GitHub, AWS) needs a NetworkPolicy
   allowing its pods (label `app: <name>`) out on 443. Add it under
   `templates/security/` next to the others. Don't widen the namespace-wide
   rules.
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
(from an `externalSecrets` entry), never `addPlaintextHeaders`. Check the
remote's tool annotations (`tools/list`) and allowlist accordingly.

**Excalidraw** (added from the ToolHive registry entry
`io.github.stacklok/excalidraw-remote`): `read_me` and `create_view` are
annotated read-only; `read_checkpoint` and `save_checkpoint` are allowlisted
(diagram edit state). `export_to_excalidraw` is **blocked**, because it
uploads the diagram to a public excalidraw.com link; add
`excalidraw_export_to_excalidraw` to `authz.readOnlyTools` to enable it.
Diagram content is sent to Excalidraw's hosted service either way.

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
Kubernetes** (checked at v0.55.2):

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
   not marked read-only or allowlisted → **403** for everyone.
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
  callers, and at v0.55.2 the vMCP can't send a token when fetching it (the
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
