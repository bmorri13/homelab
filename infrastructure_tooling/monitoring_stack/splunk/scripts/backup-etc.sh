#!/usr/bin/env bash
# Nightly backup of Splunk config ($SPLUNK_DATA_DIR/etc) and the Cribl Leader's
# config ($CRIBL_DATA_DIR/local for Leader settings and users, groups/ for the Worker
# Group config) to the Unraid NFS share. Indexed data is not included. The Worker
# holds nothing it cannot pull from the Leader again.
# Files the image ships in Splunk's built-in apps (/opt/splunk-etc/apps) are left
# out, except local/, lookups/ and metadata/local.meta: they are about 1.3 GB that
# the image already has, and reading it every night tripped splunkd's IOWait health
# check. A restore lays the image's etc down first (see README "Restore on a fresh
# VM"). If the splunk container isn't running, everything is backed up.
# The archive contains splunk.secret, cribl.secret, password hashes, the homelab TLS
# keys, and the encrypted HEC token, so keep the share restricted to this VM.
#   cron: 15 3 * * * /opt/splunk-stack/scripts/backup-etc.sh >> /var/log/splunk-etc-backup.log 2>&1
set -euo pipefail

cd "$(dirname "$0")/.."
[[ -f .env ]] && { set -a; . ./.env; set +a; }
DATA_DIR="${SPLUNK_DATA_DIR:-/srv/splunk}"
CRIBL_DIR="${CRIBL_DATA_DIR:-/srv/cribl}"
ARCHIVE_DIR="${SPLUNK_ARCHIVE_HOST_DIR:-/mnt/unraid-splunk}"
KEEP="${SPLUNK_BACKUP_KEEP:-14}"
DEST="$ARCHIVE_DIR/backups"

# Refuse to write into the bare local mount point when the share is down.
if [[ ! -f "$ARCHIVE_DIR/.splunk-archive" ]]; then
    echo "$(date -Is) backup skipped: $ARCHIVE_DIR/.splunk-archive missing (share not mounted)" >&2
    exit 1
fi

mkdir -p "$DEST"
out="$DEST/splunk-etc-$(date +%Y%m%d-%H%M%S).tar.gz"
# Restore: see README "Restore on a fresh VM" (the image's etc goes down first).
groups=()
[[ -d "$CRIBL_DIR/groups" ]] && groups=(groups)

excludes=$(mktemp)
trap 'rm -f "$excludes"' EXIT
# One "<app>/<entry>" line per file or directory the image ships at the top of each
# built-in app, plus "<app>/metadata/<file>".
if stock=$(docker exec splunk sh -c 'cd /opt/splunk-etc/apps && find . -mindepth 2 -maxdepth 2 && find . -mindepth 3 -maxdepth 3 -path "./*/metadata/*"' 2>/dev/null) \
    && [[ -n "$stock" ]]; then
    # Keep what holds our changes: local/, lookups/ and metadata/local.meta.
    grep -vE '^\./[^/]+/(local|lookups|metadata)$|/metadata/local\.meta$' <<<"$stock" \
        | sed 's|^\./|etc/apps/|' > "$excludes" || true
else
    echo "$(date -Is) splunk container not running: backing up built-in apps in full" >&2
fi

nice -n 19 ionice -c 3 tar -czf "$out.tmp" --anchored --no-wildcards --exclude-from="$excludes" \
    -C "$DATA_DIR" etc -C "$CRIBL_DIR" local "${groups[@]}"
mv "$out.tmp" "$out"
echo "$(date -Is) wrote $out ($(du -h "$out" | cut -f1))"

ls -1t "$DEST"/splunk-etc-*.tar.gz | tail -n +"$((KEEP + 1))" | xargs -r rm -f
