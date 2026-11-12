# Splunk + Cribl - homelab logging box

Splunk Enterprise on a developer license (10 GB/day) behind Cribl Stream, both in one Docker Compose stack on `security-tooling-01` (proxmox3).

```
UniFi gateway ──syslog 5514──┐
                             ├──► Cribl Worker ──S2S (compose network only)──► Splunk
OTel collectors ──HEC 8443───┘    drop noise, mask secrets,                    ▲ 8089 (k3s-01 only)
                                  set index + sourcetype                       │
                                       ▲ config (4200)            MCP gateway on k3s-01
                                  Cribl Leader ◄── UI, API, MCP (9000, HTTPS) ─┘
```

The Cribl UI is at `https://cribl.bmosan.com` (tailnet only, behind Authentik; see `infrastructure_tooling/authentik`).

Every source sends to Cribl. Splunk receives only from Cribl, and its receiving ports are not published on the host. If Splunk is down (restart, upgrade), Cribl holds data in a persistent queue (up to 10 GB) and catches up.

## Layout

| What | Where | Notes |
|---|---|---|
| Splunk config (`/opt/splunk/etc`) | `/srv/splunk/etc` on the VM | Survives container recreation and upgrades |
| Indexed data (`/opt/splunk/var`) | `/srv/splunk/var` on the VM | Hot, warm, and cold on local disk. Capped at 350 GB for the homelab indexes; at the cap the oldest data is frozen (archived, then deleted locally). |
| Cribl Leader: settings, users, Worker Group config | `/srv/cribl` on the VM | Group config in `groups/default/` (a git repo the Leader commits to). Seeded from `cribl/` on first deploy. |
| Cribl Worker: pulled config, persistent queue | `/srv/cribl-worker` on the VM | Nothing here that the Leader can't send again, except queued data |
| TLS certs for 8089, Splunk Web (8000), the Cribl API and the HEC input (8443) | `/root/homelab-ca` (CA), `/srv/splunk/etc/auth/homelab/`, `/srv/cribl/local/cribl/auth/homelab-tls/`, `/srv/cribl-worker-tls/` | Issued and renewed by `scripts/tls-certs.sh`. `/srv/cribl-worker-tls` holds the HEC input's cert and key and is mounted read-only into the Worker. See [MCP access](#mcp-access). |
| Frozen archive | Unraid NFS share at `/mnt/unraid-splunk/frozen/<index dir>/` | Raw data of every bucket that ages out, except `metrics`, `cribl` and `unrouted`. Kept forever; see [Frozen archive](#frozen-archive). |
| Config backups | Unraid NFS share at `/mnt/unraid-splunk/backups` | Splunk `etc` + Cribl Leader `local` and `groups`, 14 nightly archives of about 80 MB each. Files the Splunk image ships in its built-in apps are left out (the image has them); their `local/`, `lookups/` and `metadata/local.meta` are kept. |
| Repo-managed Splunk apps | `apps/` | Base config (`00_`), props/transforms for our data (`85_`), and Splunkbase apps. See [Splunk apps](#splunk-apps). |
| Repo-managed Cribl settings | `cribl/local/cribl/` | Inputs, routes, pipelines, the Splunk output |

### Indexes

Cribl picks the index for every event. Data older than the retention below, or past the 350 GB cap, is frozen: copied to the Unraid share and then deleted locally. `metrics`, `cribl` and `unrouted` are deleted without archiving.

| Index | Contents | Retention |
|---|---|---|
| `network` | UniFi gateway (OCSF): firewall, IDS/IPS findings, client connects, health events. AdGuard Home's DNS query log, and Cloudflare Tunnel connector logs | 90 days |
| `infra` | Proxmox and VM system logs, the Actions runner | 90 days |
| `k8s` | Cluster platform pod logs and events | 30 days |
| `apps` | Your own apps, on Docker hosts or k8s | 30 days |
| `audit` | Kubernetes API audit log (k3s-01), Vault audit log and server log, ToolHive, UniFi admin logins and config changes, remote-access logins | 90 days |
| `ai_agents` | Claude Code, Codex, MCP tool calls, agents we build | 90 days |
| `security` | Detections: Falco runtime alerts as OCSF Detection Findings (`cc-falco-ocsf`), findings from the ATT&CK detections (`homelab:detection`, `apps/85_detections`), OpenCanary honeypot events, Trivy Operator reports, and kube-bench results | 90 days |
| `metrics` | Metric index (`mstats`): host, container, Kubernetes and Proxmox metrics from the OTel collectors, 60 s resolution. Not archived; capped at 30 GB | 90 days |
| `cribl` | Cribl's own warnings and errors | 14 days |
| `unrouted` | Matched no route or named a missing index. Should stay empty. | 14 days |

### Ports

| Port | Service | Notes |
|---|---|---|
| `8000` | Splunk Web (HTTPS, homelab CA) | k3s-01 only (`SPLUNK_WEB_ALLOWED`): Traefik serves it as `https://splunk.bmosan.com`, which logs in through Authentik. See [Splunk Web login](#splunk-web-login). |
| `8089` | Splunk REST API + MCP Server app (HTTPS, homelab CA) | Published on 192.168.4.11 only. `scripts/mgmt-firewall.sh` drops everything but k3s-01 and local containers. |
| `9000` | Cribl Leader UI, API and MCP (HTTPS, homelab CA) | k3s-01 only (`CRIBL_UI_ALLOWED`): the MCP gateway, and Traefik serving the UI at `https://cribl.bmosan.com` behind Authentik. Cribl's Free license has no external login, so Authentik's forward auth sits in front of Cribl's own login. |
| `5514` tcp/udp | Cribl syslog → `network` | UniFi gateways only (`SYSLOG_NETWORK_ALLOWED`). |
| `8443` | Cribl HEC (HTTPS, homelab CA), input `hec_tls` | OTel collectors and the homelab agents. Requires `CRIBL_HEC_TOKEN`. Server LAN only (`HEC_ALLOWED`, default 192.168.4.0/24). |

Syslog has no authentication, so `scripts/mgmt-firewall.sh` limits it by source address, in the same `DOCKER-USER` chain as 8089: 5514 accepts only the UniFi gateways, and still accepts this host's containers. HEC needs its token as well, but the script also limits 8443 to the server LAN. Docker also publishes these ports on IPv6, where its userland proxy takes the connection, so the script drops 5514, 8000, 8443 and 9000 in `ip6tables` `INPUT` as well (the only IPv6 address is Tailscale's). Tailscale's own `ts-forward` chain runs ahead of `DOCKER-USER` and accepts everything arriving on `tailscale0`, so the script also drops those ports on that interface in the `raw` table, before Docker's DNAT. 8089 is published on the LAN address only, so the tailnet can't reach it. The allowlists are comma-separated CIDRs with defaults in the script. To add a sender, set the variable in `/opt/splunk-stack/.env` to the full list (it replaces the default; the commented lines in `.env-example` show each default) and run `./scripts/mgmt-firewall.sh`. The script reads the allowlists from `.env` itself, so the boot-time unit applies the same rules; if `.env` is missing or unreadable it applies the defaults. If a gateway's address changes its syslog is dropped silently, until *HL - Log source stopped sending* reports the UniFi feed quiet.

### Cribl routing

Routes match on the input.

`syslog_network` (UniFi, port 5514) hands each event to the [`cc-unifi-ocsf`](cribl/packs/cc-unifi-ocsf/README.md) pack. The pack converts UniFi CEF and kernel firewall lines into OCSF 1.9.0 JSON with `sourcetype=ubiquiti:unifi:ocsf`. Authentication (3002) and Device Config State Change (5019) events go to `audit`; every other class goes to `network`.

Firewall blocks are logged only for isolated networks (2026-11-03). That takes two places: Activity Logging (SIEM Server) needs the **Gateway** and **Firewall Default Policy** contents ticked, and the policy needs its own Syslog Logging flag. UniFi's built-in "Isolated Networks" policy can't be changed (the UI greys the flag out and the Network API returns 404), so a custom policy, *Log isolated network block*, sits ahead of it in Internal → Internal with the same source networks, Block, connection state New only (so replies to connections opened from trusted networks are untouched) and Syslog Logging on. Each attempt arrives as CEF 203 "Blocked by Firewall", which the pack maps to Network Activity (`disposition=Blocked`, `firewall_rule.name`, `src_endpoint.*`), and *HL - Isolated network device tried to reach a trusted network* alerts on it. The External → Gateway block is deliberately not logged: about 17,000 lines a day of internet scanning, which the IDS/IPS findings already cover. The pack also parses the gateway's kernel firewall lines.

With Gateway ticked, the gateway's own system log (systemd, mcad, DPI, ulogd, about 6,500 lines a day) arrives too, as `sourcetype=syslog` with the gateway's hostname as `host`. Those lines carry local time without a zone, so the `syslog_network` input sets `timestampTimezone: America/New_York`; with `local` (UTC in the container) they landed 4 hours early. The `syslog_network` pipeline drops the info-level part of it (anything the pack didn't convert, at syslog severity info or debug); notice and above is kept.

AdGuard Home's DNS query log (k3s-01, [`../../adguard`](../../adguard/README.md)) arrives through `hec_in` from its `querylog` sidecar and goes to `network` as `sourcetype=adguard:querylog`; `apps/85_adguard` maps its fields to CIM Network_Resolution.

Trivy Operator's reports (sourcetype `trivy:<kind>`, from the k3s collector) and kube-bench's monthly CIS k3s benchmark (journald identifier `kube-bench` on k3s-01) go to `security` through `hec_in`, ahead of its drop rules; `apps/85_k8s_posture` parses them.

Falco alerts arriving through `hec_in` (journald identifier `falco`, or the `falco` namespace on k3s) are chained to the [`cc-falco-ocsf`](cribl/packs/cc-falco-ocsf) pack and sent to `security`; see [`../falco/`](../falco/README.md).

Vault's audit log arrives through `hec_in` too. Vault writes it to stdout (a `file` audit device at path `stdout/`), so it shares the pod log with Vault's server log. `hec_in` recognises the audit entries, tags them `sourcetype=hashicorp_vault_audit_log` and sends them to `audit`, before any drop rule can see them. Vault HMACs tokens and secret values in the entries. To recreate the device after a Vault rebuild, run as a token with `sudo` on `sys/audit`:

```bash
kubectl -n vault exec -it vault-0 -- vault audit enable -path=stdout file file_path=stdout
```

If every enabled audit device fails to write, Vault refuses requests. Stdout doesn't fail in practice, but keep that in mind before adding a second device that can.

The daily news app's backend (`news-backend` in compose project `bmosan-news` on docker-compose-03) logs JSON. `hec_in` tags it `sourcetype=bmosan:news:json` and splits it by its `event` field: `llm_call` (every Claude API call, with tokens and latency) goes to `ai_agents`, `trigger_invoked`/`trigger_denied` go to `security`, and the rest goes to `apps`. Cloudflare Tunnel connectors (`cloudflared-tunnel` containers) go to `network` as `sourcetype=cloudflare:cloudflared`.

`hec_in` receives the OTel collectors: the host agents in [`otel-agent/`](otel-agent/README.md) (journald, Docker containers and metrics on the VMs and Proxmox nodes) and the k3s-01 DaemonSet in `infrastructure_tooling/otel_collector_k3s`. It picks the index:

0. Metric events (`__criblMetrics`, or `index=metrics` from the collector) → `metrics`, before every other rule. The last rule would otherwise move them to an event index, where Splunk silently drops metric data.
1. Keep an index the collector set, if it is one of the indexes above.
2. `service.name` containing claude, codex, or agent → `ai_agents`.
3. k8s namespace `vault` → `audit`. (ToolHive: only the vMCP gateway's audit records go to `audit`, by an earlier rule; the rest of its logs go to `k8s`.)
4. k8s namespace `alexprinter` → `apps`.
5. Any other k8s namespace → `k8s`.
6. A `container.name` field (Docker hosts) → `apps`.
7. Everything else → `infra`.

Dropped before indexing:

- the `arc-runners` namespace and `kube-probe` hits
- successful container health checks (`GET /health`, `/api/health`, `/ready`, and so on)
- SeaweedFS `LockRing` chatter
- MOTD news, and routine systemd unit start/stop messages (failures are kept)
- ArgoCD's routine reconcile chatter (warnings, errors and sync actions are kept)
- the ARC controller and listener's INFO reconcile loop in `arc-systems` (scale events, warnings and errors are kept)
- ToolHive's per-request access lines from the MCP servers, and the vMCP cache-sync loop (each tool call is in the vMCP audit record)
- debug-level lines and debug-severity syslog
- Cribl's own info-level logs

Cribl runs distributed: a Leader container (`cribl`) and one Worker container (`cribl-worker`) in the `default` Worker Group, on the free license. Distributed mode is only needed for Cribl's MCP server, which exists on a Leader and not on a single instance. The Worker runs 2 worker processes (`cribl/local/cribl/cribl.yml`). The default of one per CPU minus two did not fit the 2 GiB container limit and was OOM-killed. The Leader needs about 2.1 GiB and has a 3 GiB limit. The `splunk_out` pipeline runs on everything sent to Splunk and redacts credentials before they are stored; see [Secret masking](#secret-masking).

### Secret masking

`splunk_out` masks `_raw` in two stages:

1. **Its own rules**, for formats that matter here and that gitleaks has no rule for:
   - Tailscale keys and Cloudflare tunnel tokens (base64 JSON);
   - Vault tokens and unseal/recovery keys, and Proxmox API tokens;
   - base64-wrapped private keys (kubeconfig `client-key-data`; certificates are left alone);
   - passwords in URLs (`postgres://user:pass@…`) and signed-URL signatures;
   - Docker Hub and Google OAuth tokens;
   - `Authorization:` headers and key/value pairs whose name says secret (`password`, `secret_access_key`, `PRIVATE_KEY`, `"auth"` …).
2. **gitleaks' default rules.** `splunk_out` chains to the `secret_mask` pipeline, which `scripts/gen-secret-mask.py` generates from gitleaks' 221 regex rules (MIT, version pinned in the script). Each rule is one Mask function whose filter is the rule's keyword list, so most events skip its regex. gitleaks' entropy thresholds and allowlists are kept.
   - Do not edit `secret_mask/conf.yml` by hand: change the version in the script and rerun it.
   - To make a rule case-insensitive, the script removes inline flags, which Cribl regexes don't support. A rule may therefore mask slightly more than gitleaks would report.

Masked values become `[REDACTED]` or `[REDACTED <rule>]`. Indexed fields are not masked. Metric data points (`index=metrics`, set only by `hec_in`'s first rule) skip both stages: the metric index stores no `_raw`.

The `85_secret_leaks` app is the backstop. Its hourly alert, *Secret leak - unmasked credential in indexed events*, looks for high-confidence credential formats that still reached Splunk unmasked. It reports the format, index, sourcetype and host, never the value, and appears under Triggered Alerts. If it fires:
1. Rotate the credential.
2. Add a Cribl rule and a must-mask case.
3. Remove the events with `| delete`, which needs the `can_delete` role.

`scripts/cribl-test-mask.py`, run on this VM from the stack directory, tests the masking with Cribl's own preview:
- **`cribl/tests/secret-mask/must-mask.txt`:** 75 formats, each of which must be masked.
- **`cribl/tests/secret-mask/must-keep.txt`:** ordinary log lines, which must pass through unchanged.

The test files are templates: the fake values are generated at run time, so no token-shaped string is committed. Other options:
- `--live` tests the deployed pipelines instead of the repo copy.
- `--events file.jsonl` reports how many real events the rules would change, by rule name only, never printing values. Use it to check for false positives before deploying.

Cribl's OpenTelemetry (OTLP) input is not used. In testing (Cribl 4.20.3) it accepted data with a missing or wrong token, so collectors send over HEC, which does enforce its token.

## MCP access

The MCP gateway on k3s-01 (`infrastructure_tooling/toolhive_mcp`) reaches Splunk and Cribl over the LAN.

### Homelab CA

`scripts/tls-certs.sh` keeps a small CA in `/root/homelab-ca` on this VM (key mode 600, never copied off). It can only sign names under `bmosan.com`, `localhost`, this VM's host names, and private IPs (`192.168.0.0/16`, `127.0.0.0/8`), so a leaked key cannot impersonate public sites. It issues 825-day certs for Splunk's port 8089, Splunk Web (8000), the Cribl Leader API and the Cribl Worker's HEC input (8443), and reissues them within 30 days of expiry on any deploy. The CA cert is public, and the repo holds five copies of it under `infrastructure_tooling/`: `toolhive_mcp/files/homelab-ca.crt` (which the gateway trusts), `authentik/files/homelab-ca.crt` (Traefik, for Splunk Web and the Cribl UI), `homelab_agents/files/homelab-ca.crt`, `otel_collector_k3s/files/homelab-ca.crt` and `monitoring_stack/splunk/otel-agent/homelab-ca.crt` (the host agents). If the CA is ever recreated, replace all five and re-run `otel-agent/install.sh` on every host.

### Splunk

The Splunk MCP Server app (Splunkbase) is not in the repo (Splunkbase license). Install it by hand into `/srv/splunk/etc/apps/Splunk_MCP_Server` (extract the `.tgz`, `chown -R 41812:41812`, restart Splunk). `deploy.sh` leaves it alone. Its endpoint is `https://192.168.4.11:8089/services/mcp`.

The gateway authenticates as the local user `mcp-gateway` with the `mcp_reader` role (`00_homelab_roles`): search on every index, plus the app's `mcp_tool_execute` capability, no admin rights. MCP tokens are encrypted Splunk tokens, valid for at most 180 days, minted by an admin:

```bash
# On security-tooling-01. Creates mcp-gateway if missing (random password, not kept)
# and prints a new token. Pipe it straight into Vault rather than to the terminal.
./scripts/splunk-mcp-token.sh
```

Store it in Vault at `kv/apiary/shared/splunk` (field `token`), or paste it into Apiary.

The agents use a second user, `mcp-agent`, with the read-only `mcp_agent` role: it imports no role and turns off the write capabilities every role gets by default (`run_collect`, `run_mcollect`, `edit_own_objects`, `schedule_rtsearch`), and limits searches to 4 at a time, 5 minutes each. Mint its token with `MCP_USER=mcp-agent MCP_ROLE=mcp_agent ./scripts/splunk-mcp-token.sh` and store it at `kv/apiary/shared/splunk-agent`.

### Cribl

The Leader serves Cribl's MCP server at `https://192.168.4.11:9000/api/v1/mcp`. On the free license every user is an admin, so MCP clients get the full set of 16 tools, including the ones that change pipelines (`setPipelineConfig`) and edit search notebooks. The MCP tools call the Leader API at the Leader's own interface address, so the Leader sits alone on the `cribl-leader` compose network at a fixed `192.168.237.2`, which is in its cert, and trusts the homelab CA through `NODE_EXTRA_CA_CERTS`. Cribl session tokens expire after an hour (`api.authTokenTTL`, default 3600 s, also used for UI sessions), so the gateway needs something that logs in and refreshes the token rather than a static bearer token.

## Splunk Web login

`https://splunk.bmosan.com` (tailnet only) is Splunk Web behind Traefik on k3s-01 (`infrastructure_tooling/authentik/templates/splunk.yaml`). Splunk sends the login to Authentik with SAML (`apps/00_homelab_auth`), so every login needs Authentik's second factor. Members of Authentik's `authentik Admins` group get the application entitlement `splunk-admin`, which Splunk maps to its `admin` role; the user name is the Authentik user name. Splunk Web presents a homelab-CA cert (`web.crt`, `scripts/tls-certs.sh`), which Traefik verifies.

- Authentik signs with the keypair `splunk-saml-signing` (self-signed RSA 4096, valid 100 years), generated inside Authentik so its key never left it. It must be self-signed: Splunk rejects certificates from Authentik's internal CA ("unable to get local issuer certificate"). Its public cert is `auth/authentik-saml.crt`; `deploy.sh` copies it to `etc/auth/idpCerts/`. If the keypair is ever replaced, export the new cert there and redeploy.
- Local users still work: the REST API and its tokens (`mcp-gateway`, `mcp-agent`), and the local `admin` as break-glass at `https://splunk.bmosan.com/en-US/account/login?loginType=splunk`. If Traefik or Authentik is down, tunnel to the host instead: `ssh -L 8000:127.0.0.1:8000 root@security-tooling-01`, then `https://localhost:8000/en-US/account/login?loginType=splunk`. *HL - Splunk local login* reports every local login that doesn't come from the host itself.
- SAML users can't own scheduled searches: Splunk can't look them up while they're logged out (Authentik has no SAML attribute query). Keep scheduled searches and alerts in the repo's apps, which run as `nobody`.

## Splunk apps

Everything Splunk loads from this repo lives in `apps/`, one directory per app, synced to `/srv/splunk/etc/apps/` by `deploy.sh`.

| Prefix | Use | Examples |
|---|---|---|
| `00_` | Base Splunk config: indexes, inputs, server settings, roles, SAML login | `00_homelab_indexes`, `00_homelab_inputs`, `00_homelab_server`, `00_homelab_roles`, `00_homelab_auth` |
| `85_` | Props, transforms and searches for data we produce: our apps and our Cribl packs' output | `85_ocsf`, `85_hashicorp_vault`, `85_kubernetes_audit`, `85_bmosan_news`, `85_linux_auth`, `85_secret_leaks`, `85_opencanary`, `85_falco`, `85_homelab_ops`, `85_detections` |
| none | Splunkbase apps, kept under their original names. Installed by hand, not stored in this repo (see [Splunkbase apps](#splunkbase-apps)) | `Splunk_SA_CIM`, `ocsf_cim_addon_for_splunk`, `Splunk_TA_nix` |

In global context (index-time props, indexes, inputs, server settings) Splunk merges app directories in ASCII order and the earlier name wins: digits before uppercase before lowercase. So `00_` base config overrides anything a Splunkbase app ships, and `85_` props override a Splunkbase add-on's props for the same sourcetype. To change a Splunkbase app's behaviour, override it from a prefixed app rather than editing the vendor files, so upgrades stay a straight replace.

Current base apps:

- `00_homelab_indexes`: the indexes and retention above, the 350 GB volume cap, `lastChanceIndex`, and `bin/archive_bucket.sh`, which archives frozen buckets to the Unraid share.
- `00_homelab_inputs`: the `splunktcp` receiver on 9997 that Cribl sends to.
- `00_homelab_server`: stops indexing while less than 50 GB is free on the shared root disk, points splunkd (8089, `server.conf` `[sslConfig]`) and Splunk Web (8000, `web.conf`) at their homelab-CA certs from `scripts/tls-certs.sh`, and raises splunkd's IOWait health thresholds (`health.conf`) so short I/O bursts from the other containers on this VM don't turn health red.
- `00_homelab_roles`: the `admin` role and the two MCP roles; all three can search every homelab index, `audit` and `ai_agents` included. The two MCP roles can also read server status, the health report and the license (`list_introspection`, `list_health`, `license_read`).
- `00_homelab_auth`: Splunk Web login through Authentik with SAML (`authentication.conf`), mapping the Authentik entitlement `splunk-admin` to the `admin` role. See [Splunk Web login](#splunk-web-login).
- `85_ocsf`: JSON field extraction for the OCSF sourcetypes the Cribl packs emit (`ubiquiti:unifi:ocsf`, `falco:ocsf`), and `src` for UniFi IDS/IPS findings, which the OCSF-CIM add-on leaves unmapped.
- `85_hashicorp_vault`: JSON field extraction for the Vault audit log (`hashicorp_vault_audit_log`).
- `85_kubernetes_audit`: JSON field extraction for the k3s API audit log (`kube:apiserver:audit`, see `infrastructure_tooling/k3s_audit`).
- `85_bmosan_news`: the daily news app's backend JSON (`bmosan:news:json`) and Cloudflare Tunnel logs (`cloudflare:cloudflared`).
- `85_linux_auth`: adds to the Unix and Linux add-on's `linux_secure` parsing: Tailscale SSH logins (`tailscaled` "audit: SSH login", how the Ubuntu VMs are reached), Proxmox web UI and API logins (`pvedaemon`/`pveproxy`), sudo sessions in the Ubuntu 24.04 log format, and the sudo command line. Each Tailscale SSH session's command (or login shell) is mapped to CIM Endpoint Processes, with `parent_process_name=tailscaled`, the local `user`, the tailnet identity `ts_user` and `src`.
- `85_secret_leaks`: the hourly backstop alert for credentials that got past Cribl's masking (see [Secret masking](#secret-masking)).
- `85_falco`: two alerts, run every 5 minutes: one on any Falco finding from the AI agent rules (`falco/rules.d/ai_agents.yaml`), one on every other Falco finding at Warning or above.
- `85_homelab_ops`: the Homelab Operations dashboard (Dashboard Studio) over `index=metrics`: status tiles, Proxmox nodes, guests and storage, host utilization and saturation, Docker containers, and k3s workloads. The view is generated by `build_dashboard.py` in the app; edit that, rerun it and commit both. See [`otel-agent/`](otel-agent/README.md#metrics) for what is collected. It also holds the eleven alerts that post to Slack themselves through `homelab_slack` (see [Detections](#detections)).
- `85_detections`: scheduled detections mapped to MITRE ATT&CK v20, and an optional `homelab_slack` alert action. See [Detections](#detections).
- `85_homelab_security`: the Homelab Security dashboard (Dashboard Studio): detection findings by severity and ATT&CK technique, every fired alert, Falco rules and events, honeypot connections, logins (CIM `tag=authentication`), Kubernetes API 401/403s and changes made by people, Vault requests and errors, MCP gateway calls and denials, and whether each scheduled detection ran in the last 24 hours. Generated by `build_dashboard.py` in the app; edit that, rerun it and commit both.
- `85_opencanary`: the OpenCanary honeypot (`infrastructure_tooling/opencanary`) as CIM Intrusion Detection, and an alert, run every 5 minutes, on any connection to it.
- `85_adguard`: search-time fields for AdGuard Home's DNS query log (`adguard:querylog`), mapped to CIM Network_Resolution names.
- `85_k8s_posture`: search-time fields for the Trivy Operator reports (`trivy:<kind>`, JSON) and kube-bench's check lines (`kube-bench`: `status`, `check_id`, `check`).
- `85_toolhive_audit`: JSON field extraction for the MCP gateway's audit records (`toolhive:vmcp:audit`), with `user` and `src` aliased from the client ServiceAccount and client IP.

Adding an index means adding it in `00_homelab_indexes`, routing to it in Cribl (`hec_in` keeps a list of valid indexes), and granting it in `00_homelab_roles`.

### Splunkbase apps

Splunkbase apps are licensed by Splunk, so they are not committed here. Download them from Splunkbase and install them by hand. They live only in `/srv/splunk/etc/apps/`, where `deploy.sh` leaves them alone and the nightly backup includes them.

| App | Version | Why |
|---|---|---|
| `Splunk_MCP_Server` (Splunk MCP Server) | 2.0.0 | The MCP endpoint the gateway on k3s-01 uses. See [MCP access](#mcp-access). |
| `Splunk_SA_CIM` (Common Information Model) | 8.7.0 | CIM data models. Everything CIM-based (Security Essentials, detections) needs it. |
| `ocsf_cim_addon_for_splunk` (OCSF-CIM Add-On) | 1.1.0 | Maps the OCSF sourcetypes onto CIM: `ubiquiti:unifi:ocsf` to Network Traffic, `falco:ocsf` to Alerts. |
| `Splunk_Security_Essentials` (Security Essentials) | 3.8.3 | Detection library (Splunk Security Content), MITRE ATT&CK coverage, data inventory. Downloads content from `securitycontent.scs.splunk.com` when opened. Nothing is scheduled out of the box. About 290 MB, which also makes each nightly config backup larger. |
| `Splunk_TA_nix` (Splunk Add-on for Unix and Linux) | 10.3.4 | Search-time parsing and CIM mapping (Authentication, Change) for `linux_secure`. Cribl's `hec_in` rewrites login, sudo/su and account-change events from journald into classic auth.log lines with that sourcetype; `85_linux_auth` covers what the add-on doesn't. Its scripted and file inputs ship disabled and stay that way: journald comes from the OTel agents. |

To install or upgrade one, copy the app directory in and restart splunkd. From a Mac, set `COPYFILE_DISABLE=1` for `tar` so no `._*` AppleDouble files come along (splunkd tries to parse `._*.conf`):

```bash
# On the Mac
COPYFILE_DISABLE=1 tar --no-xattrs -C <unpacked dir> -czf - <app_dir> | ssh root@security-tooling-01 \
  'tar -xzf - -C /srv/splunk/etc/apps/ && chown -R 41812:41812 /srv/splunk/etc/apps/<app_dir>'
# On security-tooling-01
docker exec -u splunk splunk /opt/splunk/bin/splunk btool check   # should print nothing
docker exec -u splunk splunk /opt/splunk/bin/splunk restart
```

The OCSF-CIM add-on applies its mappings only to the sourcetypes it is told are OCSF. After installing or upgrading it, set them through its REST endpoint. The endpoint regenerates the add-on's `local/props.conf` and the `ocsf_sourcetypes` macro. Add a sourcetype here whenever a Cribl pack starts emitting a new OCSF one.

```bash
docker exec -it splunk curl -sk -u admin https://127.0.0.1:8089/services/ocsf-cim/ocsf-sourcetypes \
  -d name=homelab -d sourcetypes=ubiquiti:unifi:ocsf,falco:ocsf
```

Security Essentials on Splunk 10.6 (it is certified up to 10.5): its automatic content updates break every SSE page with "A custom JavaScript error caused an issue loading your dashboard". The Splunk Research channels fail the app's own signature check ("Signed digest did not match"), and the MITRE ATT&CK download fails when SSE saves it to the KV store (`/services/pushJSON` resets the connection, so the handler returns HTTP 500). Those channels are switched off in `Splunk_Security_Essentials/local/essentials_updates.conf` (`disabled = 1` on `Splunk_Research_*` and `mitreattack`, the same as the toggles under SSE's **Configuration → Content Update**), so SSE uses the content bundled with 3.8.3. After upgrading SSE, turn them back on and check whether the banner returns. The MITRE ATT&CK overview page still throws a TypeError in Splunk's dashboard framework, which is an SSE incompatibility with 10.6 and can't be fixed here.

Check it with `index=security sourcetype=falco:ocsf | stats count by tag` (expect `alert`) and `| datamodel Alerts Alerts search | search sourcetype=falco:ocsf`. Data model acceleration is off; turn it on per data model only when a dashboard or detection needs it.

## Detections

`apps/85_detections` holds the `HL - ...` saved searches: 29 detections mapped to MITRE ATT&CK v20 (technique, tactic, and the v20 detection strategy `DET####` the logic follows where MITRE has one). They are generated by `build_detections.py` in the app; edit that, rerun it and commit both. Each detection's description says how to test it.

| Area | Detections |
|---|---|
| Kubernetes audit (k3s-01) | exec/attach/port-forward (T1609), RBAC change (T1098.006), secrets read by a person or app (T1552.007), service account token minted (T1528), workload created by hand, flagging privileged and host-access pods (T1610), anonymous or repeated denied requests (T1613) |
| Vault audit | audit device changed (T1685), root token used (T1078.001), auth method or policy changed (T1556), permission-denied burst and first read of a secret by an identity (T1555.006) |
| Hosts | Tailscale SSH login from a new identity/host/source (T1021.004), Proxmox login failures (T1110.001), sensitive commands over SSH or sudo: stopping security tools, cron and authorized_keys, writes to PAM/sudoers/sshd_config, account changes, firewall flush, log or history clearing, download piped to a shell (T1685 and others) |
| UniFi | admin login from a new source or a failure (T1078.001), configuration change (T1686), a device on an isolated network blocked from reaching another Internal network (T1046) |
| DNS (AdGuard Home query log) | one client asking for many distinct long subdomains or TXT/NULL names under one domain in 15 minutes, a sign of tunnelling (T1071.004); one client getting NXDOMAIN for 15 or more registered domains in 15 minutes, a sign of a DGA (T1568.002); a zone transfer or ANY query (T1018). AdGuard blocks nothing, so there are no blocked-domain findings. Legitimate chatty domains go in the `hl_dns_chatty_domain` macro |
| Logins (Authentik, Splunk Web) | three or more failed Authentik logins (T1110.001), an Authentik second factor failing after a correct password (T1078), the deactivated `akadmin` logging in or an admin impersonating a user (T1078.001), a local Splunk Web login that skipped Authentik (T1078.001) |
| MCP gateway | denied call, write-capable tool, or first call from a client identity |
| Telemetry | a host that stopped sending metrics for 15 minutes; a log feed that stopped while its host is up (k3s API audit, Vault audit and k3s container logs after 15 minutes, a host's journald and the AdGuard DNS query log after 30, or 60 on the Proxmox nodes, the UniFi gateway after 8 hours); Falco not running for 10 minutes, from its process heartbeat in `index=metrics`; an `HL - ...` search that errors when the scheduler runs it, so it silently detects nothing (all T1685) |

- Most run every 15 minutes over `[-20m@m, -5m@m)`, each at its own minute of the quarter hour (from a hash of its name, never a multiple of 5) so they don't all start together; the telemetry ones look back 24 hours to now. "First seen" detections search their own 14- or 30-day baseline. All suppress repeats per entity (1 to 24 hours).
- Every finding is written to `index=security sourcetype=homelab:detection` as one JSON event (Log Event action), with `detection`, `severity`, `technique`, `tactic`, `attack_strategy`, `host`, `user`, `src`, `object` and `detail`. This is the queue the security agent reads.
- Splunk does not post to Slack. The security and operations agents read this queue and post their triage to Slack; the webhook URL is kept in Vault at `kv/slack-alerts` (key `webhook_url`). The agents are in [`homelab_agents`](../../homelab_agents/README.md); their run log is `index=ai_agents sourcetype=homelab:agent`. The exceptions to "Splunk does not post to Slack" are eleven searches in `85_homelab_ops` that watch what the agents depend on, and use `homelab_slack`: the agents' own watchdog, *Agents - not running or failing* (a stopped agent can't report itself), *Splunk - health degraded* (a splunkd health feature yellow or red for 15 minutes, checked every 10), *Splunk - license expiring* (the Developer license within 30 days of expiry; when it lapses Splunk falls back to Free, with no logins and no alerts), *Splunk - detection validation failed* (a weekly test in [`../../art_validation`](../../art_validation/README.md) ran and the detection it targets produced no finding), *Vault - sealed* (no Vault audit records for 6 minutes while k3s logs still arrive, or a restart logged with no unseal after it; checked every 5 minutes, high), *Kubernetes - k3s crashed or restarted* (from k3s-01's journald: a fatal, panic or abnormal exit of `k3s.service` is high, a clean start or one after a host boot is medium; every card says to restart the OTel collector, whose Kubernetes-events receiver does not recover from an API restart), *Kubernetes - External Secrets failing to sync* (an ExternalSecret with `UpdateFailed` events for 30 minutes and no `Updated` since, one card per object, or one card when 3 or more fail at once), *Authentik - database backup failed or missing* (no dump written for 26 hours), and the Kubernetes posture reports: *Kubernetes - CIS benchmark failures* (monthly, [`../../kube_bench`](../../kube_bench/README.md)), *Trivy - critical vulnerabilities in running images* (weekly) and *Trivy - secret found in a running image* ([`../../trivy_operator`](../../trivy_operator/README.md)).
- The `homelab_slack` alert action (`bin/homelab_slack.py`) is attached only to those eleven searches, and kept as a fallback if the agents are down for long. To use it elsewhere, add `action.homelab_slack = 1` to a saved search and copy the webhook from Vault into Splunk's credential store (`storage/passwords`, app `85_detections`, realm `homelab_slack`) without it touching shell history:

  ```bash
  read -rs SLACK_URL && printf '%s' "$SLACK_URL" | ssh root@security-tooling-01 /opt/splunk-stack/scripts/set-slack-webhook.sh; unset SLACK_URL
  ```

  Paste the value from Vault when it waits for input. The action refuses anything that is not a `https://hooks.slack.com/` URL, and verifies splunkd's TLS certificate with the homelab CA. Failures are logged to `index=_internal sourcetype=splunkd component=sendmodalert`, never with the URL.
- Known gaps: there is no network flow or process telemetry outside Falco (DNS queries are logged by AdGuard Home, in `network`), so command and control, exfiltration and most discovery are not covered. People's Kubernetes requests are audited with the body, but controllers' are not.

## Prerequisites

### 1. Grow the VM disk (one time)

After the Terraform change that sets `disk_size = "600"` is applied, extend the guest partition and filesystem online:

```bash
growpart /dev/sda 2
resize2fs /dev/sda2
df -h /
```

Changing `cores` in the same apply reboots the VM, because the bpg provider defaults `reboot_after_update` to true.

### 2. Unraid NFS share (one time)

1. In Unraid, create a share named `splunk` (cache or array storage both work). Under **NFS Security Settings**, set Export to Yes. Security `Private` with the rule `192.168.4.11(sec=sys,rw,all_squash,anonuid=99,anongid=100)` limits it to this VM; with `Public`, any host on the network can read the backups, which contain `splunk.secret`, `cribl.secret`, TLS keys, and password hashes. Turn SMB export off.
2. On the VM:

```bash
apt-get install -y nfs-common
mkdir -p /mnt/unraid-splunk
echo '192.168.1.227:/mnt/user/splunk /mnt/unraid-splunk nfs soft,timeo=150,retrans=3,_netdev,nofail 0 0' >> /etc/fstab
mount /mnt/unraid-splunk
mkdir -p /mnt/unraid-splunk/frozen /mnt/unraid-splunk/backups
touch /mnt/unraid-splunk/.splunk-archive
```

The `.splunk-archive` marker matters. `archive_bucket.sh` and `backup-etc.sh` write only when they can see it. If the share is not mounted, they see the empty local directory instead, so they skip the write rather than fill the root disk. Splunk keeps the bucket and retries later. While the share is down, buckets due to freeze stay on local disk, so the 350 GB cap can be exceeded; `00_homelab_server` stops indexing before the disk fills. A failing archive shows up as `archive_bucket` errors in `index=_internal`.

The mount uses `soft` and `nofail` so a down Unraid never blocks boot or hangs splunkd. Archives are copied to a `.tmp` name and renamed when complete, so an interrupted copy is retried.

### 3. Packages the scripts need (one time)

```bash
apt-get install -y jq python3-yaml rsync sysstat
```

`jq` and `python3-yaml` are used by the Cribl scripts (`cribl-bootstrap.sh`, `cribl-sync-pipelines.sh`, `cribl-test-mask.py`), `rsync` by `deploy.sh`, and `pidstat` from `sysstat` by the `splunk-io-top` unit, which otherwise fails and restarts every 30 seconds.

## Deploy

```bash
# On security-tooling-01, as root
mkdir -p /opt/splunk-stack
# copy this directory to /opt/splunk-stack (git archive from a checkout; on a Mac
# set COPYFILE_DISABLE=1 so tar adds no ._* files)
cd /opt/splunk-stack
cp .env-example .env    # set SPLUNK_PASSWORD, CRIBL_ADMIN_PASSWORD, CRIBL_HEC_TOKEN, CRIBL_DIST_TOKEN
chmod 600 .env
./scripts/deploy.sh
```

`deploy.sh` is safe to re-run. It:

1. Installs and runs the host firewall for ports 8089, 8000, 9000, 5514 and 8443 (`scripts/mgmt-firewall.sh`, and the `splunk-mgmt-firewall` systemd unit that reapplies it before Docker starts on boot).
2. Runs `tls-certs.sh`, which creates the homelab CA on first run and issues or renews the certs for Splunk's port 8089, Splunk Web (8000), the Cribl Leader, and the Worker's HEC input (`/srv/cribl-worker-tls`, mounted read-only into the Worker, which is restarted when it changes).
3. Starts the stack and waits for Splunk to become healthy.
4. Syncs every app under `apps/` into Splunk, restarting splunkd only if something or its cert changed. The `build_*.py` generators and `.gitignore` stay in the repo; copies left by older deploys are removed. Changes made in Splunk Web land in each app's `local/` and are never overwritten. Apps removed from the repo are not removed from Splunk; apps added by hand (like the Splunk MCP Server app) are left alone.
5. Seeds the Cribl Worker Group config once, from `cribl/local/cribl/`, while the Leader is stopped.
6. Runs `cribl-bootstrap.sh`: switches the Leader API to HTTPS, sets the HEC token from `.env` on a freshly seeded group and deploys the group config to the Worker, stops with an error if the group has no HTTPS HEC input `hec_tls`, removes the retired syslog 514 input `syslog_infra` with its route and pipeline if the group still has them, and, on a brand-new Leader still on admin/admin, sets the admin password.
7. Runs `cribl-packs.sh`, which installs each pack under `cribl/packs/` in the Worker Group through the Leader API when it is missing or its `package.json` version changed, then deploys it to the Worker. Packs must go through the API: a pack copied into Cribl's config directory shows up in the list, but `pack:<id>` references to it don't resolve. The repo owns packs, so an upgrade replaces UI edits made to an installed pack.

### Changing Cribl config

After the first deploy, the Cribl UI owns its config; `deploy.sh` never overwrites it. Edit the `default` Worker Group in the Leader UI, then **Commit & Deploy** so the Worker gets the change. To keep a change, copy the changed files from `/srv/cribl/groups/default/local/cribl/` back into `cribl/local/cribl/` and commit them. To push a repo change to pipelines or routes to a running Cribl, run `./scripts/cribl-sync-pipelines.sh --check` to list what differs, then `./scripts/cribl-sync-pipelines.sh` to apply it: it updates the pipelines under `cribl/local/cribl/pipelines/` and the route list through the Leader API, then commits and deploys. Pipelines that exist only in Cribl are left alone, but the route list is replaced with the repo's. For `inputs.yml`, `outputs.yml` and `cribl.yml`, copy the file the other way, `docker compose restart cribl`, then commit and deploy the group in the UI. Never commit `auth/` or a file containing a `#42:`-prefixed value (an encrypted secret); `inputs.yml` in the repo keeps `authTokens: []`.

### License

Install the developer license in Splunk Web under **Settings > Licensing > Add license**. It is stored in `/srv/splunk/etc/licenses` and persists. The license expires after 6 months. When it lapses Splunk falls back to Free, with no logins and no scheduled searches or alerts, until a new license is installed. *Splunk - license expiring* (`85_homelab_ops`) warns 30 days ahead.

### Nightly config backup

```bash
echo '15 3 * * * root /opt/splunk-stack/scripts/backup-etc.sh >> /var/log/splunk-etc-backup.log 2>&1' > /etc/cron.d/splunk-etc-backup
```

### Frozen archive

`archive_bucket.sh` copies only a bucket's `rawdata` (the compressed events) to `/mnt/unraid-splunk/frozen/<index dir>/<bucket>/`. The directory is the index's directory on disk, so the `audit` index archives to `frozen/homelab_audit/`. Nothing prunes the archive. At current volumes it grows by roughly 2 GB a year; delete old bucket directories by hand if it ever matters.

To search archived data again, thaw it:

```bash
# On security-tooling-01, as root. Example: one bucket from the infra index.
cp -R /mnt/unraid-splunk/frozen/infra/db_<newest>_<oldest>_<id> /srv/splunk/var/lib/splunk/infra/thaweddb/
chown -R 41812:41812 /srv/splunk/var/lib/splunk/infra/thaweddb/
docker exec -u splunk splunk /opt/splunk/bin/splunk rebuild /opt/splunk/var/lib/splunk/infra/thaweddb/db_<newest>_<oldest>_<id>
docker restart splunk
```

Thawed buckets are searchable in their index and are never frozen again; delete them from `thaweddb/` when you are done.

## Upgrades

Change the pinned `image:` tag and digest in `docker-compose.yml`, then run `./scripts/deploy.sh`. This applies to both Splunk and Cribl. Take a Proxmox snapshot of the VM first, and delete it afterwards.

## Restore on a fresh VM

1. Mount the Unraid share (see above).
2. Lay down the image's own `etc` (the backup leaves out the files Splunk's built-in apps ship with), then extract the latest backup over it. `<splunk image>` is the `image:` in `docker-compose.yml`.
   ```bash
   mkdir -p /srv/splunk/etc /srv/cribl
   docker run --rm -u 0 --entrypoint tar <splunk image> -C /opt/splunk-etc -cf - . | tar -xf - -C /srv/splunk/etc
   tar -xzf /mnt/unraid-splunk/backups/<latest>.tar.gz -C /srv/splunk etc
   tar -xzf /mnt/unraid-splunk/backups/<latest>.tar.gz -C /srv/cribl local groups
   ```
3. `./scripts/deploy.sh`

Config, users, dashboards, the license, and Cribl's routes and tokens come back. The homelab CA (`/root/homelab-ca`) is not in the backup: `tls-certs.sh` creates a new one, so copy the new `ca.crt` over all five `homelab-ca.crt` copies in the repo (listed under [Homelab CA](#homelab-ca)), re-run `otel-agent/install.sh` on every host, and re-mint the Splunk MCP token (see [MCP access](#mcp-access)). Indexed data is lost; frozen buckets can be thawed (see [Frozen archive](#frozen-archive)).

## Host tuning

`splunk-io-top` (`scripts/io-top.sh`, installed by `deploy.sh`) logs, once a minute, every process doing heavy disk I/O (512 kB/s or 1 s of I/O wait), containers' included, to the journal and so to `index=infra`. When *Splunk - health degraded* fires for IOWait, this shows which process caused it:

```
index=infra host=security-tooling-01 syslog.identifier=splunk-io-top | table _time comm pid read_kBps write_kBps iodelay
```

Splunk warns when transparent huge pages are on. The VM is set to `madvise`, which is acceptable. To silence the warning, set `never` with a systemd unit or `transparent_hugepage=never` on the kernel command line.
