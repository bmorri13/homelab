#!/usr/bin/env bash
# Nightly backup of Splunk config ($SPLUNK_DATA_DIR/etc) and Cribl config
# ($CRIBL_DATA_DIR/local) to the Unraid NFS share. Indexed data is not included.
# The archive contains splunk.secret, cribl.secret, password hashes, and the
# encrypted HEC token, so keep the share restricted to this VM.
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
# Restore: tar -xzf <file> -C "$DATA_DIR" etc && tar -xzf <file> -C "$CRIBL_DIR" local
tar -czf "$out.tmp" -C "$DATA_DIR" etc -C "$CRIBL_DIR" local
mv "$out.tmp" "$out"
echo "$(date -Is) wrote $out ($(du -h "$out" | cut -f1))"

ls -1t "$DEST"/splunk-etc-*.tar.gz | tail -n +"$((KEEP + 1))" | xargs -r rm -f
