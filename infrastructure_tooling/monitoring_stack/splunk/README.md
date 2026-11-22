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
| `network` | UniFi gateway (OCSF): firewall, IDS/IPS findings, client connects, health events. AdGuard Home's DNS query log, Traefik's access log on k3s-01 (`traefik:access`), Cloudflare Tunnel connector logs, and Cloudflare tunnel status, firewall events and hourly traffic totals | 90 days |
| `infra` | Proxmox and VM system logs, the Actions runner | 90 days |
| `k8s` | Cluster platform pod logs and events | 30 days |
| `apps` | Your own apps, on Docker hosts or k8s | 30 days |
| `audit` | Kubernetes API audit log (k3s-01), Vault audit log and server log, ToolHive, UniFi admin logins and config changes, remote-access logins, Cloudflare's account audit log and Access logins | 90 days |
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

Traefik's access log on k3s-01 ([`../../k3s_node/traefik-config.yaml`](../../k3s_node/traefik-config.yaml)) is one JSON line per request on Traefik's stdout, next to its own text log. `hec_in` sends the JSON lines to `network` as `sourcetype=traefik:access`, with `_time` set from the request's start (`StartUTC`), and leaves the text log in `k8s`. The rule is final, so the generic drops (debug lines, `kube-probe/`, health-check paths) can't discard a request because of what its path or user agent says. Traefik also logs the kubelet's `/ping` probes (`ping@internal`, two every 10 seconds); a drop rule before it keeps those that start in the first 10 seconds of every tenth minute, about 290 lines a day, as the heartbeat for *Traefik - access log stopped*. Only the `User-Agent`, `Referer`, `Cf-Connecting-Ip` and `X-Forwarded-For` request headers are logged (Traefik drops `Authorization`, `Cookie` and the rest before writing). `ClientHost` (`src`) is the TCP peer: with no trusted proxies configured, Traefik deletes a client's `X-Forwarded-For` before logging, so a tailnet client can't choose its logged address. `Cf-Connecting-Ip` is logged as sent and is `claimed_client_ip`, never `src`. Traefik routes only tailnet-only hosts (apiary, auth, cribl, splunk, mcp and vault `.bmosan.com`); the Cloudflare-tunnel apps reach their Services or containers directly and are not in this log. Volume is tiny: about 20 requests in 4 days before it was turned on (Traefik's own counters, 2026-11-15), so a few hundred lines a day with the heartbeat; a line is about 700 bytes, so even a steady request a second (an open Splunk Web tab polling) would be about 60 MB a day. `apps/85_traefik` maps the fields to CIM Web (eventtype `traefik_web`, tag `web`).

Trivy Operator's reports (sourcetype `trivy:<kind>`, from the k3s collector) and kube-bench's monthly CIS k3s benchmark (journald identifier `kube-bench` on k3s-01) go to `security` through `hec_in`, ahead of its drop rules; `apps/85_k8s_posture` parses them.

Falco alerts arriving through `hec_in` (journald identifier `falco`, or the `falco` namespace on k3s) are chained to the [`cc-falco-ocsf`](cribl/packs/cc-falco-ocsf) pack and sent to `security`; see [`../falco/`](../falco/README.md).

Vault's audit log arrives through `hec_in` too. Vault writes it to stdout (a `file` audit device at path `stdout/`), so it shares the pod log with Vault's server log. `hec_in` recognises the audit entries, tags them `sourcetype=hashicorp_vault_audit_log` and sends them to `audit`, before any drop rule can see them. Vault HMACs tokens and secret values in the entries. To recreate the device after a Vault rebuild, run as a token with `sudo` on `sys/audit`:

```bash
kubectl -n vault exec -it vault-0 -- vault audit enable -path=stdout file file_path=stdout
```

If every enabled audit device fails to write, Vault refuses requests. Stdout doesn't fail in practice, but keep that in mind before adding a second device that can.

The daily news app's backend (`news-backend` in compose project `bmosan-news` on docker-compose-03) logs JSON. `hec_in` tags it `sourcetype=bmosan:news:json` and splits it by its `event` field: `llm_call` (every Claude API call, with tokens and latency) goes to `ai_agents`, `trigger_invoked`/`trigger_denied` go to `security`, and the rest goes to `apps`. Cloudflare Tunnel connectors (`cloudflared-tunnel` containers) go to `network` as `sourcetype=cloudflare:cloudflared`.

The software-factory agent (k8s namespace `software-factory`) prints one JSON line per pull request it triages, per merge it checks and per retro finding. `hec_in` tags those `sourcetype=software_factory:json` and sends them to `ai_agents`; the pod's plain progress lines stay in `k8s`.

Cloudflare's account logs arrive through `hec_in` from `hl-cloudflare-logs.service` on this host (`source=cloudflare-logs:<stream>`, see [Cloudflare logs](#cloudflare-logs)). A final rule sends `cloudflare:audit` and `cloudflare:access` to `audit`, and `cloudflare:tunnel`, `cloudflare:firewall` and `cloudflare:traffic` to `network`, before the drop rules (a blocked request's path can contain "debug").

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
| `85_` | Props, transforms and searches for data we produce: our apps and our Cribl packs' output | `85_ocsf`, `85_hashicorp_vault`, `85_kubernetes_audit`, `85_bmosan_news`, `85_linux_auth`, `85_secret_leaks`, `85_opencanary`, `85_falco`, `85_traefik`, `85_homelab_ops`, `85_detections`, `85_cloudflare` |
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
- `85_software_factory`: the software-factory agent's JSON events (`software_factory:json`, `index=ai_agents`): each triage, each after-merge check, each retro finding.
- `85_cloudflare`: search-time fields for the Cloudflare account logs (`cloudflare:audit`, `cloudflare:access`, `cloudflare:tunnel`, `cloudflare:firewall`, `cloudflare:traffic`), with CIM-style `user`, `src`, `action` and `dest`. See [Cloudflare logs](#cloudflare-logs).
- `85_linux_auth`: adds to the Unix and Linux add-on's `linux_secure` parsing: Tailscale SSH logins (`tailscaled` "audit: SSH login", how the Ubuntu VMs are reached), Proxmox web UI and API logins (`pvedaemon`/`pveproxy`), sudo sessions in the Ubuntu 24.04 log format, and the sudo command line. Each Tailscale SSH session's command (or login shell) is mapped to CIM Endpoint Processes, with `parent_process_name=tailscaled`, the local `user`, the tailnet identity `ts_user` and `src`.
- `85_secret_leaks`: the hourly backstop alert for credentials that got past Cribl's masking (see [Secret masking](#secret-masking)).
- `85_falco`: two alerts, run every 5 minutes: one on any Falco finding from the AI agent rules (`falco/rules.d/30-ai-agents.yaml`), one on every other Falco finding at Warning or above, leaving out the upstream rules on trial (macro `falco_trial_rules`).
- `85_homelab_ops`: the Homelab Operations dashboard (Dashboard Studio) over `index=metrics`: status tiles, Proxmox nodes, guests and storage, host utilization and saturation, Docker containers, and k3s workloads. The view is generated by `build_dashboard.py` in the app; edit that, rerun it and commit both. See [`otel-agent/`](otel-agent/README.md#metrics) for what is collected. It also holds the eighteen alerts that post to Slack themselves through `homelab_slack` (see [Detections](#detections)).
- `85_detections`: scheduled detections mapped to MITRE ATT&CK v20, and an optional `homelab_slack` alert action. See [Detections](#detections).
- `85_homelab_security`: the Homelab Security dashboard (Dashboard Studio): detection findings by severity and ATT&CK technique, every fired alert, Falco rules and events, honeypot connections, logins (CIM `tag=authentication`), Kubernetes API 401/403s and changes made by people, Vault requests and errors, MCP gateway calls and denials, and whether each scheduled detection ran in the last 24 hours. Generated by `build_dashboard.py` in the app; edit that, rerun it and commit both.
- `85_opencanary`: the OpenCanary honeypot (`infrastructure_tooling/opencanary`) as CIM Intrusion Detection, and an alert, run every 5 minutes, on any connection to it.
- `85_adguard`: search-time fields for AdGuard Home's DNS query log (`adguard:querylog`), mapped to CIM Network_Resolution names.
- `85_traefik`: search-time fields for Traefik's access log on k3s-01 (`traefik:access`), mapped to CIM Web (`src`, `dest`, `site`, `http_method`, `uri_path`, `uri_query`, `status`, `http_user_agent`, `http_referrer`, `bytes_in`/`bytes_out`/`bytes`, `duration`, `response_time`), plus `claimed_client_ip` (a header the client sent, unverified) and `backend_answered`.
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

`apps/85_detections` holds the `HL - ...` saved searches: 55 detections mapped to MITRE ATT&CK v20 (technique, tactic, and the v20 detection strategy `DET####` the logic follows where MITRE has one), or, for attacks on the agents themselves and LLM API use, to MITRE ATLAS v5.6.0 (`AML.T####`, with `attack_version` `atlas-5.6.0`). They are generated by `build_detections.py` in the app; edit that, rerun it and commit both. Each detection's description says how to test it.

| Area | Detections |
|---|---|
| Kubernetes audit (k3s-01) | any request on the decoy secret (T1552.007, critical; see [Decoys](#decoys)), exec/attach/port-forward (T1609), RBAC change (T1098.006), secrets read by a person or app (T1552.007), service account token minted (T1528), workload created by hand, flagging privileged and host-access pods (T1610), anonymous or repeated denied requests (T1613) |
| Vault audit | any request on the decoy path, including denied (T1555.006, critical), audit device changed (T1685), root token used (T1078.001), auth method or policy changed (T1556), permission-denied burst and first read of a secret by an identity (T1555.006) |
| Hosts | Tailscale SSH login from a new identity/host/source (T1021.004), Proxmox login failures (T1110.001), a Proxmox VM or container destroyed by anyone but Packer's token on its own template ids 9000-9006 (T1485), sensitive commands over SSH or sudo: stopping security tools, cron and authorized_keys, writes to PAM/sudoers/sshd_config, account changes, firewall flush, log or history clearing, download piped to a shell (T1685 and others) |
| UniFi | admin login from a new source or a failure (T1078.001), configuration change (T1686), a device on an isolated network blocked from reaching another Internal network (T1046) |
| DNS (AdGuard Home query log) | one client asking for many distinct long subdomains or TXT/NULL names under one domain in 15 minutes, a sign of tunnelling (T1071.004); one client getting NXDOMAIN for 15 or more registered domains in 15 minutes, a sign of a DGA (T1568.002); a zone transfer or ANY query (T1018); a lookup of a watched data-drop or callback service, one finding per client and category: request-capture webhooks (T1567.004, high), out-of-band callback servers such as Interactsh and Burp Collaborator (T1071.004, high), paste sites (T1102), anonymous file drops (T1567.002), ad-hoc tunnels (T1572), the Tor Project (T1090.003); a client's first lookup in 14 days of an LLM API (ATLAS AML.T0096, low) or of an IP geolocation service (T1614, low). AdGuard blocks nothing, so there are no blocked-domain findings. Legitimate chatty domains go in the `hl_dns_chatty_domain` macro; the watched services are in `lookups/hl_dns_watch_domains.csv` (see [Watch lists and threat indicators](#watch-lists-and-threat-indicators)) |
| Web (Traefik access log on k3s-01) | one client asking for 20 or more distinct missing paths in 15 minutes, or for any path no app here serves but wordlist scanners try (`.env`, `.git` and other dotfiles, WordPress and PHP pages, actuator, server-status, cgi-bin, `../` also encoded) (T1595.003); an exploit payload in the path or query (JNDI, `${`, `<script`, SQL injection, `/etc/passwd`, `php://`, `cmd=`, shell commands, NUL byte) or a scanner's user agent (sqlmap, nuclei, zgrab, masscan, nikto, nmap, ffuf and others) (T1190, high); 20 or more 401/403 answered by one host's backend for one client in 15 minutes, not counting the tailnet allowlist's own refusals (T1110); a backend answering a path prefix or router on a host that it had not answered in 14 days (T1190, low; hosts with less than 7 days of logs are skipped). Traefik routes only the tailnet-only hosts, so this covers the tailnet and LAN, not the Cloudflare-tunnel apps. The user's own scanners go in the host-only `hl_web_scanner_allowed` macro |
| Threat indicators | an IP address from `lookups/hl_threat_indicators.csv` in any event index (T1071, high), and a DNS lookup of an indicator domain or a subdomain of it (T1071, high); rows past their `expires` date are ignored. Seeded from FBI FLASH-20261113-001 (ARTEX); see [Watch lists and threat indicators](#watch-lists-and-threat-indicators). The same two checks against the threat feeds (abuse.ch ThreatFox, URLhaus, Feodo Tracker, OpenPhish, CIRCL OSINT) at medium, skipping indicators the hand-picked list already has; a Tor exit node in any event (T1090.003, low) and a blocklist.de reported attacker in any event (T1110, low); see [Threat-intel feeds](#threat-intel-feeds) |
| Logins (Authentik, Splunk Web) | three or more failed Authentik logins (T1110.001), an Authentik second factor failing after a correct password (T1078), the deactivated `akadmin` logging in or an admin impersonating a user (T1078.001), an Authentik object (user, group, MFA device, application, provider, flow, outpost) created, changed or deleted, except MFA devices saving their last-used time (T1098), a local Splunk Web login that skipped Authentik (T1078.001) |
| Splunk itself | a config change outside a deploy: anything under an `00_`/`85_` app's `local/`, a user's private saved searches, or auth, outputs, inputs, server, indexes, props, transforms or limits in `etc/system/local`, reported with stanza and property names but no values (T1685); a local user or role created, changed or deleted (T1098) |
| The agents' inputs | text addressing a language model ("ignore previous instructions", "system prompt", ...) in the indexes the agents read, quoted with the phrase broken so the finding can't instruct the agent that reads it (ATLAS AML.T0051.001). The agents' own logs, the MCP gateway's logs and `homelab:detection` are not searched, so findings can't feed back |
| MCP gateway | denied call, write-capable tool, or first call from a client identity |
| Cloudflare (account audit log, tunnels, firewall events) | an API token created, rolled, changed or deleted, or the Global API Key viewed (T1098.001, high); a member, role, SSO or two-factor change (T1098.003, high); a DNS record created, changed or deleted on any zone, high for NS/MX or a record repointed at a target not seen in 30 days, ACME challenge records left out (T1584.001); a tunnel created, deleted, reconfigured or its token read (T1572, high) and Zero Trust Access changes (T1556, high); a zone's security setting turned down (T1686, high); a dashboard login from an address new for the user in 30 days, high if the country is new too (T1078.004); a tunnel connector from a new origin address (T1572); one client blocked or challenged on 3 or more hosts, or 50 times, in 15 minutes (T1595, needs Zone Analytics on the token). They look at [-40m@m, -25m@m), since the collector runs every 10 minutes and Cloudflare's log lags; see [Cloudflare logs](#cloudflare-logs) |
| Telemetry | a host that stopped sending metrics for 15 minutes; a log feed that stopped while its host is up (k3s API audit, Vault audit and k3s container logs after 15 minutes, a host's journald and the AdGuard DNS query log after 30, or 60 on the Proxmox nodes, the UniFi gateway after 8 hours); Falco not running for 10 minutes, from its process heartbeat in `index=metrics`; an `HL - ...` search that errors when the scheduler runs it, so it silently detects nothing (all T1685) |

- Most run every 15 minutes over `[-20m@m, -5m@m)`, each at its own minute of the quarter hour (from a hash of its name, never a multiple of 5) so they don't all start together; the telemetry ones look back 24 hours to now. "First seen" detections search their own 14- or 30-day baseline. All suppress repeats per entity (1 to 24 hours).
- Every finding is written to `index=security sourcetype=homelab:detection` as one JSON event (Log Event action), with `detection`, `severity`, `technique`, `tactic`, `attack_strategy`, `host`, `user`, `src`, `object` and `detail`. This is the queue the security agent reads.
- Splunk does not post to Slack. The security and operations agents read this queue and post their triage to Slack; the webhook URL is kept in Vault at `kv/slack-alerts` (key `webhook_url`). The agents are in [`homelab_agents`](../../homelab_agents/README.md); their run log is `index=ai_agents sourcetype=homelab:agent`. The exceptions to "Splunk does not post to Slack" are eighteen searches in `85_homelab_ops` that watch what the agents depend on, and use `homelab_slack`: the agents' own watchdog, *Agents - not running or failing* (a stopped agent can't report itself), *Agents - spend or turn limit exceeded* (in 24 hours an agent's runs cost more than 15 USD, one run more than 4 USD, or one item took 30 or more turns; hourly at :44), *Splunk - health degraded* (a splunkd health feature yellow or red for 15 minutes, checked every 10), *Splunk - license expiring* (the Developer license within 30 days of expiry; when it lapses Splunk falls back to Free, with no logins and no alerts), *Splunk - detection validation failed* (a weekly test in [`../../art_validation`](../../art_validation/README.md) ran and the detection it targets produced no finding), *Vault - sealed* (no Vault audit records for 6 minutes while k3s logs still arrive, or a restart logged with no unseal after it; checked every 5 minutes, high), *Kubernetes - k3s crashed or restarted* (from k3s-01's journald: a fatal, panic or abnormal exit of `k3s.service` is high, a clean start or one after a host boot is medium; a "Transaction commit failed" crash card points at the SQLite datastore's slow queries instead of CPU and memory), *Kubernetes - External Secrets failing to sync* (an ExternalSecret with `UpdateFailed` events for 30 minutes and no `Updated` since, one card per object, or one card when 3 or more fail at once), *Authentik - database backup failed or missing* (no dump written for 26 hours), and the Kubernetes posture reports: *Kubernetes - CIS benchmark failures* (monthly, [`../../kube_bench`](../../kube_bench/README.md)), *Trivy - critical vulnerabilities in running images* (weekly), *Trivy - known exploited vulnerabilities in running images* (daily, against CISA KEV) and *Trivy - secret found in a running image* ([`../../trivy_operator`](../../trivy_operator/README.md)), *Threat feeds - not refreshed* (see [Threat-intel feeds](#threat-intel-feeds)), *Cloudflare logs - not collected* and *Cloudflare tunnel down* (see [Cloudflare logs](#cloudflare-logs)), *Traefik - access log stopped* (no `traefik:access` event, not even the `/ping` heartbeat, for 60 minutes while the Traefik container is ready and other k3s logs arrive; hourly, medium) and *Traefik - backend errors* (one host answered 20 or more requests with a 5xx in 15 minutes, at least half of its traffic; a sealed Vault's own 503s are left to *Vault - sealed*).
- The `homelab_slack` alert action (`bin/homelab_slack.py`) is attached only to those eighteen searches, and kept as a fallback if the agents are down for long. To use it elsewhere, add `action.homelab_slack = 1` to a saved search and copy the webhook from Vault into Splunk's credential store (`storage/passwords`, app `85_detections`, realm `homelab_slack`) without it touching shell history:

  ```bash
  read -rs SLACK_URL && printf '%s' "$SLACK_URL" | ssh root@security-tooling-01 /opt/splunk-stack/scripts/set-slack-webhook.sh; unset SLACK_URL
  ```

  Paste the value from Vault when it waits for input. The action refuses anything that is not a `https://hooks.slack.com/` URL, and verifies splunkd's TLS certificate with the homelab CA. Failures are logged to `index=_internal sourcetype=splunkd component=sendmodalert`, never with the URL.
- Known gaps: there is no network flow or process telemetry outside Falco (DNS queries are logged by AdGuard Home, in `network`), so command and control, exfiltration and discovery are covered only where they show in DNS (the watch lists) or an indicator address appears in a log. People's Kubernetes requests are audited with the body, but controllers' are not.

### Decoys

*HL - Decoy Kubernetes secret touched* and *HL - Decoy Vault secret touched* watch a Kubernetes secret and a Vault secret that nothing uses. This repo is public, so their names are never written here: the macros `hl_decoy_k8s_secret` and `hl_decoy_vault_path` in `85_detections/default/macros.conf` match nothing (`false()`), and the real predicates are set by hand on the Splunk host, in `local/`, which `deploy.sh` never overwrites. To set or change them:

1. Create the decoys. The Kubernetes secret goes alone in its own namespace: a `list` request has no object name, so the predicate matches the namespace. The Vault secret goes alone under its own folder, so a `list` of that folder matches too.
2. On security-tooling-01, as root, write `/srv/splunk/etc/apps/85_detections/local/macros.conf` (owned like the files next to it), with your own names in place of the placeholders:

   ```ini
   [hl_decoy_k8s_secret]
   definition = coalesce('objectRef.namespace', "")=="<decoy-namespace>"

   [hl_decoy_vault_path]
   definition = match(coalesce('request.path', ""), "^kv/(data|metadata|delete|undelete|destroy)/<decoy-folder>(/|$)")
   ```

3. `docker compose restart splunk` in `/opt/splunk-stack` so splunkd reads it, then check with `` | makeresults | eval objectRef.namespace="<decoy-namespace>" | where `hl_decoy_k8s_secret` `` (one row) and the same for the Vault macro.
4. Expect one *HL - Splunk configuration changed outside a deploy* finding for the edit. Creating the decoys may fire their own detections once too.

The nightly config backup keeps `local/`, so a restore brings the overrides back.

### Watch lists and threat indicators

Two CSV lookups in `85_detections/lookups/` (defined in `default/transforms.conf`). The whole app, `lookups/` included, is synced with `rsync --delete`, so edit them in the repo and deploy; a change made in Splunk Web is overwritten. (The `hl_ti_feed_*` lookups are the exception: written on the host, see [Threat-intel feeds](#threat-intel-feeds).)

- `hl_threat_indicators.csv` (`indicator,type,source,context,added,expires`): IP addresses (`type=ip`) and domains (`type=domain`) from published advisories, for *HL - Threat indicator IP seen in logs* and *HL - DNS lookup of a threat indicator domain*. Seeded with the 24 IPv4 addresses and the domain `xcai[.]pro` from FBI FLASH-20261113-001, *Threat Overview of ARTEX Autonomous Penetration Testing Framework* (TLP:CLEAR, 2026-11-13, <https://www.ic3.gov/CSA/2026/261113.pdf>, pages 6 and 7). One more row, `198.51.100.73` (an RFC 5737 documentation address, source `art_validation`), is the canary the weekly [detection validation](../../art_validation/README.md) writes to a log.
- `hl_dns_watch_domains.csv` (`domain,category,note`): services with a DNS signal of their own. Categories `webhook`, `callback`, `paste`, `filedrop`, `tunnel` and `tor` feed *HL - DNS lookup of a data-drop or callback service*; `llm_api` and `geoip` feed the two first-lookup detections. An entry matches itself and any subdomain (`m.pipedream.net` matches `<id>.m.pipedream.net`), also with a search domain appended.

To add indicators from a new advisory:

1. Copy each indicator refanged (`[.]` to `.`; the CSV is a data file, keep the defanged form in prose), one row per indicator. If it is already there, add the new source to that row instead of a second row. Keep commas and double quotes out of the fields.
2. `source` is the advisory's ID, `context` what the advisory says the indicator was used for (and who reported it), `added` the advisory's date, `expires` when to stop matching (`YYYY-MM-DD`, the row stops counting at the start of that day). IP addresses get reassigned, so give them about 180 days unless the advisory says otherwise; FLASH-20261113-001's rows expire on 2027-05-12. A row with a missing or malformed `expires` is ignored (`hl_ti_active` in `macros.conf`).
3. Check the file: `` | inputlookup hl_threat_indicators | where NOT `hl_ti_active(expires)` `` lists the rows that are ignored now, and `| inputlookup hl_threat_indicators | stats count by indicator | where count>1` must return nothing.
4. Deploy, then run *HL - Threat indicator IP seen in logs* over the last 30 days once (in Search, with the time range picker) to see whether the new addresses were already seen.

Remove rows long past `expires` when convenient; they cost nothing until then. Watch-list entries have 2 to 4 labels.

### Threat-intel feeds

`hl-threat-feeds.service` (`scripts/threat-feeds.py`, Python 3 standard library), started by `hl-threat-feeds.timer` every 6 hours and 5 minutes after boot, downloads free feeds into three more lookups in `85_detections/lookups/`:

| Lookup | From | What is kept |
|---|---|---|
| `hl_ti_feed_indicators.csv` (`indicator,type,source,context,added,expires`, the columns of `hl_threat_indicators.csv`) | abuse.ch [ThreatFox](https://threatfox.abuse.ch/) (`get_iocs`, last day) | `ip:port` IOCs as the IP, and domains, with confidence 50 or more; context is the malware family and threat type |
| | abuse.ch [URLhaus](https://urlhaus.abuse.ch/) (most recent 1,000 URLs) | the host (domain or IP) of URLs still online; context is the threat and tags |
| | abuse.ch [Feodo Tracker](https://feodotracker.abuse.ch/) (IP blocklist) | botnet C2 IPs online, or online within 30 days |
| | [OpenPhish](https://openphish.com/) community feed (free for non-commercial use; source credited in every row) | the host of each phishing URL; rows expire 7 days after the feed last listed them |
| | [CIRCL OSINT feed](https://www.circl.lu/doc/misp/feed-osint/) (MISP feed format) | from events dated in the last 30 days (newest 60 events, at most 20,000 rows a run): `to_ids` attributes of type `ip-dst`, `ip-src`, `ip-dst\|port`, `domain`, `hostname`, `domain\|ip` and the host of `url`; context is the event title. CIRCL publishes in bursts (no new event from mid-August to mid-November 2026), so 0 rows is normal |
| `hl_ti_feed_lowrisk.csv` (`indicator,type,category,source,context,added,expires`) | Tor Project [exit list](https://check.torproject.org/torbulkexitlist) | exit node IPs, `category=tor`; rows expire 3 days after the list last had them, since exits churn |
| | [blocklist.de](https://www.blocklist.de/) `all.txt` | IPs reported for SSH, mail and web brute force in the last 48 hours, `category=attacker`; 3 days. About 21,000 rows, 2.6 MB (2026-11-15) |
| `hl_ti_feed_kev.csv` (`cve,vendor,product,name,date_added,due_date,ransomware`) | [CISA KEV](https://www.cisa.gov/known-exploited-vulnerabilities-catalog) | the whole catalog |

- Each indicator expires 30 days after its feed last listed it: every run merges the new download into the feed's previous rows (kept in `/var/lib/hl-threat-feeds/<feed>.csv`), so one day of ThreatFox IOCs a run still adds up to a month. An indicator in several feeds is one row, with the sources joined by `;`. Dropped: anything but public IPv4 addresses (the detections only match IPv4) and hostnames of 2 to 4 labels (what `hl_dns_name_candidates` can match), our own domains, and hosts on big shared platforms (GitHub, Google, Microsoft, AWS, Discord, Dropbox and the like, `IGNORED_DOMAINS` in the script), where malware is uploaded but the platform is not the threat. About 1,450 indicators after the first run (2026-11-15), mostly ThreatFox domains.
- Known-benign indicators are dropped before the lookups are written, using [MISP warninglists](https://github.com/MISP/misp-warninglists) (CC0): public DNS resolvers, CDN and edge ranges (Cloudflare, Fastly and others), sinkholes, research scanners (every `*-scanning` list: Censys, Shadowserver and the like), Microsoft's and Apple's own domains, URL shorteners, IPFS gateways, what's-my-IP services, public suffixes, and the Cisco and Cloudflare top-10k domains (exact names only, so `x.vercel.app` stays even though `vercel.app` is popular). Whole hosting providers are not used, nor Akamai's or GitHub's ranges (they include Linode and Azure compute), since most real C2 runs there. The chosen lists are `WARNINGLISTS` in the script. They are cached in `/var/lib/hl-threat-feeds/warninglists.json` and downloaded again (about 130 small files from `raw.githubusercontent.com`) only when the repository's `main` moves; if that fails the cached copy is used. On 2026-11-15 they dropped 369 of about 22,500 low-tier rows (mostly research scanners blocklist.de lists) and none of the abuse.ch indicators then live. The canary rows are never dropped.
- A feed is only replaced when its download parsed and has at least its minimum rows (ThreatFox, URLhaus and OpenPhish 20, Tor 500, blocklist.de 2,000, KEV 1,000; Feodo Tracker and CIRCL are often empty, so 0). Otherwise its last good rows stay. Lookups are written to a temporary file next to them and renamed, owned like the directory so splunkd can read them.
- Two canary rows are always in `hl_ti_feed_indicators.csv`, source `art_validation`: `198.51.100.74` (RFC 5737), which the weekly [detection validation](../../art_validation/README.md) writes to a log, and `hl-feed-canary.invalid`, for testing the DNS detection with `dig`.
- ThreatFox and URLhaus need the abuse.ch Auth-Key, `ABUSECH_AUTH_KEY=` in `.env` (from <https://auth.abuse.ch/>, free). The script reads only that line and sends it only as a request header. Without it those two feeds fail and the others still run.
- The lookups are written on the host, not kept in git: `deploy.sh` excludes `lookups/hl_ti_feed_*` from the app sync (`--delete` would otherwise remove them on every deploy), installs the units, and runs the job once before syncing the apps so the searches that read the lookups never find them missing. The nightly config backup includes them.
- Each run logs one line per feed to the journal, so to `index=infra`: `index=infra host=security-tooling-01 syslog.identifier=hl-threat-feeds | table _time feed status rows error`. `feed=lookup` and `feed=lookup_lowrisk` are the combined files, with `dropped=` (indicators a warninglist removed); `feed=warninglists` has `rows=` (lists loaded), `dropped=` and `sha=` (the warninglists commit). *Threat feeds - not refreshed* (`85_homelab_ops`, hourly) posts to Slack when a feed has had no `status=ok` for 24 hours (high if none in 3 days, except the low-tier and auxiliary `tor`, `blocklistde`, `circl` and `warninglists`, which stay medium).

Using them:

- *HL - Threat feed IP seen in logs* and *HL - DNS lookup of a threat feed domain*: the two indicator checks above, against the feed lookup, at medium. An indicator that is also an active hand-picked row is left to the high-severity detections (`hl_ti_handpicked` macro). An indicator seen on 5 or more hosts (IP) or by 5 or more clients (DNS) in one window is skipped as more likely a shared service; so is anything in `hl_ti_feed_allowed` (host-only, below).
- *HL - Connection with a Tor exit node* (T1090.003, low) and *HL - Reported attacker IP seen in logs* (T1110, low): the same IP check against `hl_ti_feed_lowrisk.csv`, by category, one finding per address and host a day. Tor and blocklist.de addresses never reach the medium detections. An address also on the hand-picked or medium feed lists is left to those; a Tor exit blocklist.de also reports is only the Tor finding; `hl_ti_feed_allowed` applies. The reported-attacker detection skips requests from the address that Cloudflare blocked at the edge (`cloudflare:firewall`, `action=block`, an error status), which never reached a site. A day of logs (2026-11-15) held 2 blocklist.de addresses, both in Cloudflare firewall events, and no Tor exit.
- *Trivy - known exploited vulnerabilities in running images* (`85_homelab_ops`, daily at 13:10 UTC): packages in running images (the newest Trivy report per container) whose CVE is in KEV, one Slack card per image, high when CISA lists ransomware use or KEV's due date has passed with a fix available, medium otherwise. Repeats only when the image's KEV list changes, or after 7 days.

Run by hand, and check:

```bash
# On security-tooling-01
systemctl start hl-threat-feeds && journalctl -u hl-threat-feeds -n 10 --no-pager
systemctl list-timers hl-threat-feeds.timer
```

```spl
| inputlookup hl_ti_feed_indicators | stats count by type source
| inputlookup hl_ti_feed_lowrisk | stats count by category
| inputlookup hl_ti_feed_kev | stats count by ransomware
```

Host-only macros, set in `85_detections/local/macros.conf` on the Splunk host like the [decoy](#decoys) ones (each defaults to `false()`, matching nothing):

- `hl_dns_llm_client_ignore`: clients the two first-lookup detections skip. Set it to the Kubernetes pod range before deploying these detections, `cidrmatch("<pod-cidr>", src)`: pods get a new address each run, so without it every agent run that calls an LLM API is a new client.
- `hl_ti_feed_allowed`: feed indicators that are known false positives here, for the two feed detections. Fields `ti_indicator`, `ti_source`, `ti_context`, and `host` (IP detection) or `src` and `qname` (DNS detection): `in(ti_indicator, "<ip>", "<domain>")`.
- `hl_web_scanner_allowed`: the user's own web scanners, for the `HL - Web` detections. Fields `src` (Traefik's `ClientHost`, the TCP peer, which can't be spoofed), `site`, `path`, `ua`: `cidrmatch("<scanner-range>", src)`, narrowed with `match(ua, "<scanner-agent>")` where the scanner sends a distinctive user agent.
- `hl_dns_watch_allowed`: known-good lookups for the data-drop and callback detection. Fields `src`, `qname`, `watch_domain`, `watch_category`. A security scanner that uses OOB callbacks (Interactsh) runs in pods, so allow it by the marker its payloads put in the name, not by address: `(watch_category=="callback" AND match(qname, "<scanner-marker>"))`.

### Cloudflare logs

`hl-cloudflare-logs.service` (`scripts/cloudflare-logs.py`, Python 3 standard library), started by `hl-cloudflare-logs.timer` every 10 minutes and 3 minutes after boot, pulls the Cloudflare account's logs from the API and sends them to Cribl's HEC input on `127.0.0.1:8443` (verified with the homelab CA). The account is on Cloudflare's Free plan, which has no per-request Logpush, so this is what there is:

| Stream | Sourcetype, index | From | Each run |
|---|---|---|---|
| `audit` | `cloudflare:audit`, `audit` | account audit log v2 (`/accounts/{id}/logs/audit`) | every dashboard and API action on the account (tokens, members, DNS, tunnels, Zero Trust, zone settings, logins), with the request and response bodies. Views are logged too, including this job's own reads of the log (about 150 a day) |
| `access` | `cloudflare:access`, `audit` | Zero Trust Access authentication log | Access logins (none so far: one Access application) |
| `tunnel` | `cloudflare:tunnel`, `network` | `/accounts/{id}/cfd_tunnel` | one event per tunnel: status, connection and connector counts, colos, origin addresses, connector versions |
| `firewall` | `cloudflare:firewall`, `network` | GraphQL `firewallEventsAdaptive`, per zone | blocked, challenged and logged requests: action, source, client IP, country and ASN, host, path, rule, user agent |
| `traffic` | `cloudflare:traffic`, `network` | GraphQL `httpRequests1hGroups`, per zone | requests, cached requests, bytes, threats, page views and uniques per completed hour |

- Event time is Cloudflare's own timestamp (the snapshot time for `tunnel`); `host=cloudflare`, `source=cloudflare-logs:<stream>`. `apps/85_cloudflare` parses them.
- `audit`, `access` and `firewall` keep a checkpoint in `/var/lib/hl-cloudflare-logs/state.json`: the newest event time sent and the IDs sent in the last 30 minutes. Each run asks again from 30 minutes before the checkpoint, so late events are picked up, and skips the IDs it already sent. The checkpoint only moves after Cribl accepted the events, so a failed send is retried on the next run. The audit log's first run goes back 29 days (a few hundred events, a baseline for the "first seen" detections); the others go back a day. `traffic` remembers the last hour sent per zone.
- `firewall` and `traffic` need **Zone > Analytics > Read** on the token. Until it is added they log `status=skip` and alert nothing; once it is there they start on the next run (firewall from the last 23 hours).
- The token and account ID are `CLOUDFLARE_LOGS_API_TOKEN=` and `CLOUDFLARE_ACCOUNT_ID=` in `.env`, an account-owned token (its prefix is `cfat_`; check it with `/accounts/{id}/tokens/verify`, not `/user/tokens/verify`). The script reads only those lines and `CRIBL_HEC_TOKEN=`, and sends the token only as a request header. Before sending, strings under keys in the audit bodies that name a secret (`secret`, `password`, `private_key`, `credentials`, a bare `token`) are replaced with `[REDACTED]`; API token values are never in the log (only a prefix and suffix). `deploy.sh` installs the units and enables the timer only when the token is in `.env`.
- Each run logs one line per stream to the journal, so to `index=infra`: `index=infra host=security-tooling-01 syslog.identifier=hl-cloudflare-logs | table _time stream status events error`. *Cloudflare logs - not collected* (`85_homelab_ops`, hourly) posts to Slack when `audit`, `access` or `tunnel` has had no `status=ok` for an hour, or `firewall` or `traffic` has failed (rather than skipped) for an hour.
- *Cloudflare tunnel down* (`85_homelab_ops`, every 10 minutes) posts when a tunnel that was healthy in the last 7 days is down or degraded in its last two snapshots; high with no connections. Tunnels that have been down for longer (unused dev tunnels) don't alert.

Run by hand, and check:

```bash
# On security-tooling-01. A dry run sends nothing, saves no state, and prints two
# events per stream with the account ID, emails and IP addresses masked.
python3 -I /opt/splunk-stack/scripts/cloudflare-logs.py --dry-run --state-dir /tmp/cf-dry
systemctl start hl-cloudflare-logs && journalctl -u hl-cloudflare-logs -n 10 --no-pager
systemctl list-timers hl-cloudflare-logs.timer
```

```spl
index=audit sourcetype=cloudflare:audit | stats count by action.type action.description | sort - count
index=network sourcetype=cloudflare:tunnel | stats latest(status) latest(connections) by tunnel_name
```

Host-only macros, set in `85_detections/local/macros.conf` on the Splunk host like the [decoy](#decoys) ones (each defaults to `false()`):

- `hl_cf_expected_change`: changes made on purpose that the HL - Cloudflare audit detections skip, by the owner or by automation. Nothing in this repo manages Cloudflare through Terraform or calls its API except cert-manager's DNS-01 solver (`kv/cf-api-token`, ACME TXT records, already left out by name) and this collector (read-only, views only). Exempt an automation token by its ID, never its name (anyone who can create a token can name it): `(cf_token=="<token-id>" AND match(cf_uri, "/dns_records"))`. Fields: `cf_token`, `cf_actor`, `src`, `cf_desc`, `cf_uri`, `cf_zone`, and `hl_names`/`hl_types` in the DNS detection.
- `hl_cf_firewall_ignore`: client addresses the firewall burst detection skips (your own uptime monitor): `in(clientIP, "<ip>")`.

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
2. Installs the `splunk-io-top` unit (below, Host tuning) and `hl-threat-feeds.timer`, and runs the feed job once ([Threat-intel feeds](#threat-intel-feeds)); a failed feed only warns.
3. Runs `tls-certs.sh`, which creates the homelab CA on first run and issues or renews the certs for Splunk's port 8089, Splunk Web (8000), the Cribl Leader, and the Worker's HEC input (`/srv/cribl-worker-tls`, mounted read-only into the Worker, which is restarted when it changes).
4. Starts the stack and waits for Splunk to become healthy.
5. Syncs every app under `apps/` into Splunk, restarting splunkd only if something or its cert changed. The `build_*.py` generators and `.gitignore` stay in the repo; copies left by older deploys are removed. Changes made in Splunk Web land in each app's `local/` and are never overwritten, nor are the feed lookups `85_detections/lookups/hl_ti_feed_*`. Apps removed from the repo are not removed from Splunk; apps added by hand (like the Splunk MCP Server app) are left alone.
6. Seeds the Cribl Worker Group config once, from `cribl/local/cribl/`, while the Leader is stopped.
7. Runs `cribl-bootstrap.sh`: switches the Leader API to HTTPS, sets the HEC token from `.env` on a freshly seeded group and deploys the group config to the Worker, stops with an error if the group has no HTTPS HEC input `hec_tls`, removes the retired syslog 514 input `syslog_infra` with its route and pipeline if the group still has them, and, on a brand-new Leader still on admin/admin, sets the admin password.
8. Runs `cribl-packs.sh`, which installs each pack under `cribl/packs/` in the Worker Group through the Leader API when it is missing or its `package.json` version changed, then deploys it to the Worker. Packs must go through the API: a pack copied into Cribl's config directory shows up in the list, but `pack:<id>` references to it don't resolve. The repo owns packs, so an upgrade replaces UI edits made to an installed pack.

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
