# Splunk - homelab logging box

Splunk Enterprise on a developer license (10 GB/day), running in Docker Compose on `security-tooling-01` (proxmox3).

## Layout

| What | Where | Notes |
|---|---|---|
| Config (`/opt/splunk/etc`) | `/srv/splunk/etc` on the VM | Survives container recreation and upgrades. Backed up nightly to Unraid. |
| Indexed data (`/opt/splunk/var`) | `/srv/splunk/var` on the VM | Hot, warm, and cold on local disk. Capped at 350 GB for the homelab indexes; the oldest data is deleted at the cap. |
| Frozen archive | Not enabled yet (issue #165) | Planned: Unraid NFS share at `/mnt/unraid-splunk/frozen` |
| Config backups | Unraid NFS share at `/mnt/unraid-splunk/backups` | 14 nightly archives |
| Repo-managed settings | `homelab_base/` app | Indexes, retention, and the 50 GB free-space guard |

Indexes: `homelab`, `network`, and `k8s`, each kept for 90 days. Data older than that, or past the 350 GB cap, is deleted. Archiving to the Unraid share comes later (issue #165).

Ports: `8000` Splunk Web (HTTPS), `8088` HEC (HTTPS), `8089` REST API, `9997` forwarders.

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

Until this share exists, nightly config backups are skipped. Archiving frozen data also needs `coldToFrozenScript` added back to each index in `homelab_base/default/indexes.conf` (see the comment there).

The `.splunk-archive` marker matters. `archive_bucket.sh` and `backup-etc.sh` write only when they can see it. If the share is not mounted, they see the empty local directory instead, so they skip the write rather than fill the root disk. Splunk keeps the bucket and retries later.

The mount uses `soft` and `nofail` so a down Unraid never blocks boot or hangs splunkd. Archives are copied to a `.tmp` name and renamed when complete, so an interrupted copy is retried.

## Deploy

```bash
# On security-tooling-01, as root
mkdir -p /opt/splunk-stack
# copy this directory to /opt/splunk-stack (rsync from a checkout)
cd /opt/splunk-stack
cp .env-example .env    # set SPLUNK_PASSWORD and SPLUNK_HEC_TOKEN
chmod 600 .env
./scripts/deploy.sh
```

`deploy.sh` is safe to re-run. It starts the stack, waits for it to become healthy, and syncs `homelab_base/` into Splunk. It restarts splunkd only if the app changed. Changes made in Splunk Web land in the app's `local/` and are never overwritten.

### License

Install the developer license in Splunk Web under **Settings > Licensing > Add license**. It is stored in `/srv/splunk/etc/licenses` and persists. The license expires after 6 months. When it lapses, indexing continues but search is blocked until a new license is installed, so set a reminder about 2 weeks before expiry.

### Nightly config backup

```bash
echo '15 3 * * * root /opt/splunk-stack/scripts/backup-etc.sh >> /var/log/splunk-etc-backup.log 2>&1' > /etc/cron.d/splunk-etc-backup
```

## Upgrades

Change the pinned `image:` tag and digest in `docker-compose.yml`, then run `./scripts/deploy.sh`. Take a Proxmox snapshot of the VM first, and delete it afterwards.

## Restore on a fresh VM

1. Mount the Unraid share (see above).
2. `mkdir -p /srv/splunk && tar -xzf /mnt/unraid-splunk/backups/<latest>.tar.gz -C /srv/splunk`
3. `./scripts/deploy.sh`

Config, users, dashboards, and the license come back. Searchable data since the last archived bucket is lost; frozen buckets can be thawed.

## Host tuning

Splunk warns when transparent huge pages are on. The VM is set to `madvise`, which is acceptable. To silence the warning, set `never` with a systemd unit or `transparent_hugepage=never` on the kernel command line.
