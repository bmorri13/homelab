# Splunk + Cribl - homelab logging box

Splunk Enterprise on a developer license (10 GB/day) behind Cribl Stream, both in one Docker Compose stack on `security-tooling-01` (proxmox3).

```
UniFi gateway ──syslog 5514──┐
Proxmox, Unraid ─syslog 514──┼──► Cribl Worker ──S2S (compose network only)──► Splunk
OTel collectors ──HEC 8088───┘    drop noise, mask secrets,                    ▲ 8089 (k3s-01 only)
                                  set index + sourcetype                       │
                                       ▲ config (4200)            MCP gateway on k3s-01
                                  Cribl Leader ◄── UI, API, MCP (9000, HTTPS) ─┘
```

Every source sends to Cribl. Splunk receives only from Cribl, and its receiving ports are not published on the host. If Splunk is down (restart, upgrade), Cribl holds data in a persistent queue (up to 10 GB) and catches up.

## Layout

| What | Where | Notes |
|---|---|---|
| Splunk config (`/opt/splunk/etc`) | `/srv/splunk/etc` on the VM | Survives container recreation and upgrades |
| Indexed data (`/opt/splunk/var`) | `/srv/splunk/var` on the VM | Hot, warm, and cold on local disk. Capped at 350 GB for the homelab indexes; the oldest data is deleted at the cap. |
| Cribl Leader: settings, users, Worker Group config | `/srv/cribl` on the VM | Group config in `groups/default/` (a git repo the Leader commits to). Seeded from `cribl/` on first deploy. |
| Cribl Worker: pulled config, persistent queue | `/srv/cribl-worker` on the VM | Nothing here that the Leader can't send again, except queued data |
| TLS certs for 8089 and the Cribl API | `/root/homelab-ca` (CA), `/srv/splunk/etc/auth/homelab/`, `/srv/cribl/local/cribl/auth/homelab-tls/` | Issued and renewed by `scripts/tls-certs.sh`. See [MCP access](#mcp-access). |
| Frozen archive | Not enabled yet (issue #165) | Planned: Unraid NFS share at `/mnt/unraid-splunk/frozen` |
| Config backups | Unraid NFS share at `/mnt/unraid-splunk/backups` | Splunk `etc` + Cribl Leader `local` and `groups`, 14 nightly archives. Skipped until the share exists. |
| Repo-managed Splunk apps | `apps/` | Base config (`00_`), props/transforms for our data (`85_`), and Splunkbase apps. See [Splunk apps](#splunk-apps). |
| Repo-managed Cribl settings | `cribl/local/cribl/` | Inputs, routes, pipelines, the Splunk output |

### Indexes

Cribl picks the index for every event. Data older than the retention below, or past the 350 GB cap, is deleted. Archiving comes later (issue #165).

| Index | Contents | Retention |
|---|---|---|
| `network` | UniFi gateway (OCSF): firewall, IDS/IPS findings, client connects, health events | 90 days |
| `infra` | Proxmox, Unraid, VM system logs, the Actions runner | 90 days |
| `k8s` | Cluster platform pod logs and events | 30 days |
| `apps` | Your own apps, on Docker hosts or k8s | 30 days |
| `audit` | Vault audit log and server log, ToolHive, UniFi admin logins and config changes, remote-access logins | 90 days |
| `ai_agents` | Claude Code, Codex, MCP tool calls, agents we build | 90 days |
| `security` | Detections: Falco runtime alerts as OCSF Detection Findings (`cc-falco-ocsf`) | 90 days |
| `cribl` | Cribl's own warnings and errors | 14 days |
| `unrouted` | Matched no route or named a missing index. Should stay empty. | 14 days |

### Ports

| Port | Service | Notes |
|---|---|---|
| `8000` | Splunk Web (HTTPS, self-signed) | |
| `8089` | Splunk REST API + MCP Server app (HTTPS, homelab CA) | Published on 192.168.4.11 only. `scripts/mgmt-firewall.sh` drops everything but k3s-01 and local containers. |
| `9000` | Cribl Leader UI, API and MCP (HTTPS, homelab CA) | |
| `514` tcp/udp | Cribl syslog → `infra` | Proxmox, Unraid, other hosts |
| `5514` tcp/udp | Cribl syslog → `network` | UniFi gateway |
| `8088` | Cribl HEC (HTTP) | OTel collectors. Requires `CRIBL_HEC_TOKEN`. |

Syslog has no authentication. Restrict ports 514 and 5514 to the senders' IPs with a host firewall if that matters.

### Cribl routing

Routes match on the input.

`syslog_network` (UniFi, port 5514) hands each event to the [`cc-unifi-ocsf`](cribl/packs/cc-unifi-ocsf/README.md) pack. The pack converts UniFi CEF and kernel firewall lines into OCSF 1.9.0 JSON with `sourcetype=ubiquiti:unifi:ocsf`. Authentication (3002) and Device Config State Change (5019) events go to `audit`; every other class goes to `network`.

Falco alerts arriving through `hec_in` (journald identifier `falco`, or the `falco` namespace on k3s) are chained to the [`cc-falco-ocsf`](cribl/packs/cc-falco-ocsf) pack and sent to `security`; see [`../falco/`](../falco/README.md).

Vault's audit log arrives through `hec_in` too. Vault writes it to stdout (a `file` audit device at path `stdout/`), so it shares the pod log with Vault's server log. `hec_in` recognises the audit entries, tags them `sourcetype=hashicorp_vault_audit_log` and sends them to `audit`, before any drop rule can see them. Vault HMACs tokens and secret values in the entries. To recreate the device after a Vault rebuild, run as a token with `sudo` on `sys/audit`:

```bash
kubectl -n vault exec -it vault-0 -- vault audit enable -path=stdout file file_path=stdout
```

If every enabled audit device fails to write, Vault refuses requests. Stdout doesn't fail in practice, but keep that in mind before adding a second device that can.

`hec_in` receives the OTel collectors: the host agents in [`otel-agent/`](otel-agent/README.md) (journald and Docker containers on the VMs and Proxmox nodes) and the k3s-01 DaemonSet in `infrastructure_tooling/otel_collector_k3s`. It picks the index:

1. Keep an index the collector set, if it is one of the indexes above.
2. `service.name` containing claude, codex, or agent → `ai_agents`.
3. k8s namespace `vault`, `toolhive-mcp`, `toolhive-system` → `audit`.
4. k8s namespace `alexprinter`, `stirling-pdf`, `uptime-kuma` → `apps`.
5. Any other k8s namespace → `k8s`.
6. A `container.name` field (Docker hosts) → `apps`.
7. Everything else → `infra`.

Dropped before indexing:

- the `arc-runners` namespace and `kube-probe` hits
- successful container health checks (`GET /health`, `/api/health`, `/ready`, and so on)
- SeaweedFS `LockRing` chatter
- MOTD news, and routine systemd unit start/stop messages (failures are kept)
- debug-level lines and debug-severity syslog
- Cribl's own info-level logs

Cribl runs distributed: a Leader container (`cribl`) and one Worker container (`cribl-worker`) in the `default` Worker Group, on the free license. Distributed mode is only needed for Cribl's MCP server, which exists on a Leader and not on a single instance. The Worker runs 2 worker processes (`cribl/local/cribl/cribl.yml`). The default of one per CPU minus two did not fit the 2 GiB container limit and was OOM-killed. The Leader needs about 2.1 GiB and has a 3 GiB limit. The `splunk_out` pipeline runs on everything sent to Splunk and redacts private keys, AWS/GitHub/Slack/Vault/API keys, JWTs, `Authorization:` headers, and `password=`/`token=`-style values.

Cribl's OpenTelemetry (OTLP) input is not used. In testing (Cribl 4.20.3) it accepted data with a missing or wrong token, so collectors send over HEC, which does enforce its token.

## MCP access

The MCP gateway on k3s-01 (`infrastructure_tooling/toolhive_mcp`) reaches Splunk and Cribl over the LAN.

### Homelab CA

`scripts/tls-certs.sh` keeps a small CA in `/root/homelab-ca` on this VM (key mode 600, never copied off). It can only sign names under `bmosan.com`, `localhost`, this VM's host names, and private IPs (`192.168.0.0/16`, `127.0.0.0/8`), so a leaked key cannot impersonate public sites. It issues 825-day certs for Splunk's port 8089 and the Cribl Leader API, and reissues them within 30 days of expiry on any deploy. The CA cert is public and lives in `toolhive_mcp/files/homelab-ca.crt`, which the gateway trusts.

### Splunk

The Splunk MCP Server app (Splunkbase) is not in the repo (Splunkbase license). Install it by hand into `/srv/splunk/etc/apps/Splunk_MCP_Server` (extract the `.tgz`, `chown -R 41812:41812`, restart Splunk). `deploy.sh` leaves it alone. Its endpoint is `https://192.168.4.11:8089/services/mcp`.

The gateway authenticates as the local user `mcp-gateway` with the `mcp_reader` role (`00_homelab_roles`): search on every index, plus the app's `mcp_tool_execute` capability, no admin rights. MCP tokens are encrypted Splunk tokens, valid for at most 180 days, minted by an admin:

```bash
# On security-tooling-01. Creates mcp-gateway if missing (random password, not kept)
# and prints a new token. Pipe it straight into Vault rather than to the terminal.
./scripts/splunk-mcp-token.sh
```

Store it in Vault at `kv/apiary/shared/splunk` (field `token`), or paste it into Apiary.

### Cribl

The Leader serves Cribl's MCP server at `https://192.168.4.11:9000/api/v1/mcp`. On the free license every user is an admin, so MCP clients get the full set of 16 tools, including the ones that change pipelines (`setPipelineConfig`) and edit search notebooks. Cribl session tokens expire after an hour (`api.authTokenTTL`, default 3600 s, also used for UI sessions), so the gateway needs something that logs in and refreshes the token rather than a static bearer token.

## Splunk apps

Everything Splunk loads from this repo lives in `apps/`, one directory per app, synced to `/srv/splunk/etc/apps/` by `deploy.sh`.

| Prefix | Use | Examples |
|---|---|---|
| `00_` | Base Splunk config: indexes, inputs, server settings, roles | `00_homelab_indexes`, `00_homelab_inputs`, `00_homelab_server`, `00_homelab_roles` |
| `85_` | Props and transforms for data we produce: our apps and our Cribl packs' output | `85_ocsf`, `85_hashicorp_vault` |
| none | Splunkbase apps, kept under their original names | `Splunk_TA_nix` |

In global context (index-time props, indexes, inputs, server settings) Splunk merges app directories in ASCII order and the earlier name wins: digits before uppercase before lowercase. So `00_` base config overrides anything a Splunkbase app ships, and `85_` props override a Splunkbase add-on's props for the same sourcetype. To change a Splunkbase app's behaviour, override it from a prefixed app rather than editing the vendor files, so upgrades stay a straight replace.

Current base apps:

- `00_homelab_indexes`: the indexes and retention above, the 350 GB volume cap, `lastChanceIndex`, and `bin/archive_bucket.sh` for when archiving is turned on.
- `00_homelab_inputs`: the `splunktcp` receiver on 9997 that Cribl sends to.
- `00_homelab_server`: stops indexing while less than 50 GB is free on the shared root disk.
- `00_homelab_roles`: see the file for which roles can search `audit` and `ai_agents`.
- `85_ocsf`: JSON field extraction for the OCSF sourcetypes the Cribl packs emit (`ubiquiti:unifi:ocsf`, `falco:ocsf`).
- `85_hashicorp_vault`: JSON field extraction for the Vault audit log (`hashicorp_vault_audit_log`).

Adding an index means adding it in `00_homelab_indexes`, routing to it in Cribl (`hec_in` keeps a list of valid indexes), and granting it in `00_homelab_roles`.

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

1. In Unraid, create a share (for example `splunk`) on the cache pool. Enable NFS export with the security rule limited to the VM's IP, for example `192.168.4.11(sec=sys,rw,all_squash,anonuid=99,anongid=100)`. Don't use `*`.
2. On the VM:

```bash
apt-get install -y nfs-common
mkdir -p /mnt/unraid-splunk
echo '192.168.1.227:/mnt/user/splunk /mnt/unraid-splunk nfs soft,timeo=150,retrans=3,_netdev,nofail 0 0' >> /etc/fstab
mount /mnt/unraid-splunk
mkdir -p /mnt/unraid-splunk/frozen /mnt/unraid-splunk/backups
touch /mnt/unraid-splunk/.splunk-archive
```

Until this share exists, nightly config backups are skipped. Archiving frozen data also needs `coldToFrozenScript` added back to each index in `apps/00_homelab_indexes/default/indexes.conf` (see the comment there).

The `.splunk-archive` marker matters. `archive_bucket.sh` and `backup-etc.sh` write only when they can see it. If the share is not mounted, they see the empty local directory instead, so they skip the write rather than fill the root disk. Splunk keeps the bucket and retries later.

The mount uses `soft` and `nofail` so a down Unraid never blocks boot or hangs splunkd. Archives are copied to a `.tmp` name and renamed when complete, so an interrupted copy is retried.

## Deploy

```bash
# On security-tooling-01, as root
mkdir -p /opt/splunk-stack
# copy this directory to /opt/splunk-stack (git archive from a checkout)
cd /opt/splunk-stack
cp .env-example .env    # set SPLUNK_PASSWORD, CRIBL_ADMIN_PASSWORD, CRIBL_HEC_TOKEN, CRIBL_DIST_TOKEN
chmod 600 .env
./scripts/deploy.sh
```

`deploy.sh` is safe to re-run. It:

1. Installs and runs the port 8089 firewall (`scripts/mgmt-firewall.sh`, and the `splunk-mgmt-firewall` systemd unit that reapplies it before Docker starts on boot).
2. Runs `tls-certs.sh`, which creates the homelab CA on first run and issues or renews the certs for Splunk's port 8089 and the Cribl Leader.
3. Starts the stack and waits for Splunk to become healthy.
4. Syncs every app under `apps/` into Splunk, restarting splunkd only if something or its cert changed. Changes made in Splunk Web land in each app's `local/` and are never overwritten. Apps removed from the repo are not removed from Splunk; apps added by hand (like the Splunk MCP Server app) are left alone.
5. Seeds the Cribl Worker Group config once, while the Leader is stopped: from the old single-instance config in `/srv/cribl/local/cribl/` if there is one (the one-time move to Leader + Worker), otherwise from `cribl/local/cribl/`.
6. Runs `cribl-bootstrap.sh`: switches the Leader API to HTTPS, sets the HEC token from `.env` on a freshly seeded group and deploys the group config to the Worker, and, on a brand-new Leader still on admin/admin, sets the admin password.
7. Runs `cribl-packs.sh`, which installs each pack under `cribl/packs/` in the Worker Group through the Leader API when it is missing or its `package.json` version changed, then deploys it to the Worker. Packs must go through the API: a pack copied into Cribl's config directory shows up in the list, but `pack:<id>` references to it don't resolve. The repo owns packs, so an upgrade replaces UI edits made to an installed pack.

Moving from the single-instance Cribl to Leader + Worker loses a minute or two of syslog while the containers are recreated. The OTel collectors buffer and retry HEC, so they lose nothing. Check that `/srv/cribl/state/queues` is empty first (Splunk up, queue drained): the old instance's persistent queue is not carried over.

### Changing Cribl config

After the first deploy, the Cribl UI owns its config; `deploy.sh` never overwrites it. Edit the `default` Worker Group in the Leader UI, then **Commit & Deploy** so the Worker gets the change. To keep a change, copy the changed files from `/srv/cribl/groups/default/local/cribl/` back into `cribl/local/cribl/` and commit them. To push a repo change to a running Cribl, copy the file the other way, `docker compose restart cribl`, then commit and deploy the group in the UI. Never commit `auth/` or a file containing a `#42:`-prefixed value (an encrypted secret); `inputs.yml` in the repo keeps `authTokens: []`.

### License

Install the developer license in Splunk Web under **Settings > Licensing > Add license**. It is stored in `/srv/splunk/etc/licenses` and persists. The license expires after 6 months. When it lapses, indexing continues but search is blocked until a new license is installed, so set a reminder about 2 weeks before expiry.

### Nightly config backup

```bash
echo '15 3 * * * root /opt/splunk-stack/scripts/backup-etc.sh >> /var/log/splunk-etc-backup.log 2>&1' > /etc/cron.d/splunk-etc-backup
```

## Upgrades

Change the pinned `image:` tag and digest in `docker-compose.yml`, then run `./scripts/deploy.sh`. This applies to both Splunk and Cribl. Take a Proxmox snapshot of the VM first, and delete it afterwards.

## Restore on a fresh VM

1. Mount the Unraid share (see above).
2. Extract the latest backup:
   ```bash
   mkdir -p /srv/splunk /srv/cribl
   tar -xzf /mnt/unraid-splunk/backups/<latest>.tar.gz -C /srv/splunk etc
   tar -xzf /mnt/unraid-splunk/backups/<latest>.tar.gz -C /srv/cribl local groups
   ```
3. `./scripts/deploy.sh`

Config, users, dashboards, the license, and Cribl's routes and tokens come back. The homelab CA (`/root/homelab-ca`) is not in the backup: `tls-certs.sh` creates a new one, so copy the new `ca.crt` to `toolhive_mcp/files/homelab-ca.crt`, and re-mint the Splunk MCP token (see below). Indexed data is lost; frozen buckets, once archiving is enabled, can be thawed.

## Host tuning

Splunk warns when transparent huge pages are on. The VM is set to `madvise`, which is acceptable. To silence the warning, set `never` with a systemd unit or `transparent_hugepage=never` on the kernel command line.
