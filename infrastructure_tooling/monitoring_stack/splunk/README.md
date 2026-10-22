# Splunk + Cribl - homelab logging box

Splunk Enterprise on a developer license (10 GB/day) behind Cribl Stream, both in one Docker Compose stack on `security-tooling-01` (proxmox3).

```
UniFi gateway ──syslog 5514──┐
Proxmox, Unraid ─syslog 514──┼──► Cribl ──S2S (compose network only)──► Splunk
OTel collectors ──HEC 8088───┘    drop noise, mask secrets,
                                  set index + sourcetype
```

Every source sends to Cribl. Splunk receives only from Cribl, and its receiving ports are not published on the host. If Splunk is down (restart, upgrade), Cribl holds data in a persistent queue (up to 10 GB) and catches up.

## Layout

| What | Where | Notes |
|---|---|---|
| Splunk config (`/opt/splunk/etc`) | `/srv/splunk/etc` on the VM | Survives container recreation and upgrades |
| Indexed data (`/opt/splunk/var`) | `/srv/splunk/var` on the VM | Hot, warm, and cold on local disk. Capped at 350 GB for the homelab indexes; the oldest data is deleted at the cap. |
| Cribl config, state, queue | `/srv/cribl` on the VM | Seeded from `cribl/` on first deploy |
| Frozen archive | Not enabled yet (issue #165) | Planned: Unraid NFS share at `/mnt/unraid-splunk/frozen` |
| Config backups | Unraid NFS share at `/mnt/unraid-splunk/backups` | Splunk `etc` + Cribl `local`, 14 nightly archives. Skipped until the share exists. |
| Repo-managed Splunk apps | `apps/` | Base config (`00_`), props/transforms for our data (`85_`), and Splunkbase apps. See [Splunk apps](#splunk-apps). |
| Repo-managed Cribl settings | `cribl/local/cribl/` | Inputs, routes, pipelines, the Splunk output |

### Indexes

Cribl picks the index for every event. Data older than the retention below, or past the 350 GB cap, is deleted. Archiving comes later (issue #165).

| Index | Contents | Retention |
|---|---|---|
| `network` | UniFi gateway: firewall, IDS/IPS, DHCP | 90 days |
| `infra` | Proxmox, Unraid, VM system logs, the Actions runner | 90 days |
| `k8s` | Cluster platform pod logs and events | 30 days |
| `apps` | Your own apps, on Docker hosts or k8s | 30 days |
| `audit` | Vault, ToolHive, admin actions, remote-access logins | 90 days |
| `ai_agents` | Claude Code, Codex, MCP tool calls, agents we build | 90 days |
| `cribl` | Cribl's own warnings and errors | 14 days |
| `unrouted` | Matched no route or named a missing index. Should stay empty. | 14 days |

### Ports

| Port | Service | Notes |
|---|---|---|
| `8000` | Splunk Web (HTTPS, self-signed) | |
| `9000` | Cribl UI (HTTP) | |
| `514` tcp/udp | Cribl syslog → `infra` | Proxmox, Unraid, other hosts |
| `5514` tcp/udp | Cribl syslog → `network` | UniFi gateway |
| `8088` | Cribl HEC (HTTP) | OTel collectors. Requires `CRIBL_HEC_TOKEN`. |

Syslog has no authentication. Restrict ports 514 and 5514 to the senders' IPs with a host firewall if that matters.

### Cribl routing

Routes match on the input. `hec_in` then picks the index:

1. Keep an index the collector set, if it is one of the indexes above.
2. `service.name` containing claude, codex, or agent → `ai_agents`.
3. k8s namespace `vault`, `toolhive-mcp`, `toolhive-system` → `audit`.
4. k8s namespace `alexprinter`, `stirling-pdf`, `uptime-kuma` → `apps`.
5. Any other k8s namespace → `k8s`.
6. A `container.name` field (Docker hosts) → `apps`.
7. Everything else → `infra`.

Dropped before indexing: the `arc-runners` namespace, `kube-probe` hits, debug-level lines, debug-severity syslog, and Cribl's own info-level logs. The `splunk_out` pipeline runs on everything sent to Splunk and redacts private keys, AWS/GitHub/Slack/Vault/API keys, JWTs, `Authorization:` headers, and `password=`/`token=`-style values.

Cribl's OpenTelemetry (OTLP) input is not used. In testing (Cribl 4.20.3) it accepted data with a missing or wrong token, so collectors send over HEC, which does enforce its token.

## Splunk apps

Everything Splunk loads from this repo lives in `apps/`, one directory per app, synced to `/srv/splunk/etc/apps/` by `deploy.sh`.

| Prefix | Use | Examples |
|---|---|---|
| `00_` | Base Splunk config: indexes, inputs, server settings, roles | `00_homelab_indexes`, `00_homelab_inputs`, `00_homelab_server`, `00_homelab_roles` |
| `85_` | Props and transforms for data from apps we build | `85_guardrail_props` (none yet) |
| none | Splunkbase apps, kept under their original names | `Splunk_TA_nix` |

In global context (index-time props, indexes, inputs, server settings) Splunk merges app directories in ASCII order and the earlier name wins: digits before uppercase before lowercase. So `00_` base config overrides anything a Splunkbase app ships, and `85_` props override a Splunkbase add-on's props for the same sourcetype. To change a Splunkbase app's behaviour, override it from a prefixed app rather than editing the vendor files, so upgrades stay a straight replace.

Current base apps:

- `00_homelab_indexes`: the indexes and retention above, the 350 GB volume cap, `lastChanceIndex`, and `bin/archive_bucket.sh` for when archiving is turned on.
- `00_homelab_inputs`: the `splunktcp` receiver on 9997 that Cribl sends to.
- `00_homelab_server`: stops indexing while less than 50 GB is free on the shared root disk.
- `00_homelab_roles`: see the file for which roles can search `audit` and `ai_agents`.

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
cp .env-example .env    # set SPLUNK_PASSWORD, CRIBL_ADMIN_PASSWORD, CRIBL_HEC_TOKEN
chmod 600 .env
./scripts/deploy.sh
```

`deploy.sh` is safe to re-run. It:

1. Seeds Cribl's config from `cribl/local/cribl/` if `/srv/cribl` has none yet.
2. Starts the stack and waits for Splunk to become healthy.
3. Syncs every app under `apps/` into Splunk, restarting splunkd only if something changed. Changes made in Splunk Web land in each app's `local/` and are never overwritten. Apps removed from the repo are not removed from Splunk.
4. Runs `cribl-bootstrap.sh`. On first deploy, while Cribl still accepts its default admin/admin login, this sets the Cribl admin password and the HEC token from `.env` and enables the HEC input. Later runs see the default login rejected and change nothing.

### Changing Cribl config

After the first deploy, the Cribl UI owns its config; `deploy.sh` never overwrites it. To keep a change, copy the changed files from `/srv/cribl/local/cribl/` back into `cribl/local/cribl/` and commit them. Never commit `auth/` or a file containing a `#42:`-prefixed value (an encrypted secret); `inputs.yml` in the repo keeps `authTokens: []`.

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
   tar -xzf /mnt/unraid-splunk/backups/<latest>.tar.gz -C /srv/cribl local
   ```
3. `./scripts/deploy.sh`

Config, users, dashboards, the license, and Cribl's routes and tokens come back. Indexed data is lost; frozen buckets, once archiving is enabled, can be thawed.

## Host tuning

Splunk warns when transparent huge pages are on. The VM is set to `madvise`, which is acceptable. To silence the warning, set `never` with a systemd unit or `transparent_hugepage=never` on the kernel command line.
