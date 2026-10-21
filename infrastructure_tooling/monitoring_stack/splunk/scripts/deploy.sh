#!/usr/bin/env bash
# Bring up Splunk and sync the repo-managed homelab_base app into it.
# Safe to re-run: restarts splunkd only when the app actually changed.
# Run on security-tooling-01 as root from this directory's checkout.
set -euo pipefail

cd "$(dirname "$0")/.."

[[ -f .env ]] || { echo "missing .env (copy .env-example)" >&2; exit 1; }
set -a; . ./.env; set +a
DATA_DIR="${SPLUNK_DATA_DIR:-/srv/splunk}"
ARCHIVE_DIR="${SPLUNK_ARCHIVE_HOST_DIR:-/mnt/unraid-splunk}"

wait_healthy() {
    local status
    for _ in $(seq 1 90); do
        status=$(docker inspect -f '{{.State.Health.Status}}' splunk 2>/dev/null || echo missing)
        if [[ "$status" == healthy ]]; then return 0; fi
        sleep 10
    done
    echo "splunk not healthy after 15 minutes (last status: $status)" >&2
    docker logs --tail 50 splunk >&2 || true
    return 1
}

mkdir -p "$DATA_DIR/etc" "$DATA_DIR/var" "$ARCHIVE_DIR"

docker compose up -d
echo "waiting for splunk to become healthy (first start takes several minutes)..."
wait_healthy

SPLUNK_UID=$(docker exec splunk id -u splunk)
SPLUNK_GID=$(docker exec splunk id -g splunk)

# local/ and local.meta hold changes made in Splunk Web; never overwrite them.
changes=$(rsync -a --delete --checksum --itemize-changes \
    --exclude 'local/' --exclude 'metadata/local.meta' \
    homelab_base/ "$DATA_DIR/etc/apps/homelab_base/")
chown -R "$SPLUNK_UID:$SPLUNK_GID" "$DATA_DIR/etc/apps/homelab_base"

if [[ -n "$changes" ]]; then
    echo "homelab_base changed:"; echo "$changes"
    docker compose restart splunk
    wait_healthy
else
    echo "homelab_base unchanged"
fi

if [[ ! -f "$ARCHIVE_DIR/.splunk-archive" ]]; then
    echo "WARNING: $ARCHIVE_DIR/.splunk-archive not found - frozen buckets will stay on local disk until the Unraid share is mounted" >&2
fi
echo "done: https://$(hostname):8000"
