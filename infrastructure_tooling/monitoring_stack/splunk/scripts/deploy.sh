#!/usr/bin/env bash
# Bring up Splunk and Cribl, sync the repo-managed apps under apps/ into Splunk,
# and seed Cribl's config on first deploy.
# Safe to re-run: restarts splunkd only when the app actually changed, and never
# overwrites Cribl config after the first deploy (the Cribl UI owns it from then on).
# Run on security-tooling-01 as root from this directory's checkout.
set -euo pipefail

cd "$(dirname "$0")/.."

[[ -f .env ]] || { echo "missing .env (copy .env-example)" >&2; exit 1; }
set -a; . ./.env; set +a
DATA_DIR="${SPLUNK_DATA_DIR:-/srv/splunk}"
ARCHIVE_DIR="${SPLUNK_ARCHIVE_HOST_DIR:-/mnt/unraid-splunk}"
CRIBL_DIR="${CRIBL_DATA_DIR:-/srv/cribl}"

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

mkdir -p "$DATA_DIR/etc" "$DATA_DIR/var" "$ARCHIVE_DIR" "$CRIBL_DIR"

# Seed Cribl's config before its first start. After that, changes are made in the
# Cribl UI; export them back to cribl/ in the repo to keep them.
if [[ ! -f "$CRIBL_DIR/local/cribl/inputs.yml" ]]; then
    mkdir -p "$CRIBL_DIR/local/cribl"
    rsync -a --exclude 'CLAUDE.md' cribl/local/cribl/ "$CRIBL_DIR/local/cribl/"
    echo "seeded Cribl config into $CRIBL_DIR/local/cribl"
fi

# Sync every app under apps/ into Splunk. local/ and local.meta hold changes made
# in Splunk Web; never overwrite them. Apps removed from the repo are not deleted
# from Splunk; remove those by hand. Sets $changes.
sync_apps() {
    local owner app name
    owner=$(stat -c '%u:%g' "$DATA_DIR/etc")
    changes=""
    for app in apps/*/; do
        name=$(basename "$app")
        # Compare content only (no -t), and set ownership in the copy itself, so an
        # unchanged app reports nothing and does not trigger a splunkd restart.
        changes+=$(rsync -rlp --chown="$owner" --delete --checksum --itemize-changes \
            --exclude 'local/' --exclude 'metadata/local.meta' --exclude 'CLAUDE.md' \
            "$app" "$DATA_DIR/etc/apps/$name/")
    done
    if [[ -n "$changes" ]]; then echo "apps changed:"; echo "$changes"; else echo "apps unchanged"; fi
}

if [[ -d "$DATA_DIR/etc/system" ]]; then
    # Splunk has started before: sync first, so a fix to an app that stops splunkd
    # from starting can still be deployed.
    sync_apps
    docker compose up -d
    if [[ -n "$changes" ]]; then docker compose restart splunk; fi
    echo "waiting for splunk to become healthy..."
    wait_healthy
else
    # First start: the image populates etc/ on startup, so apps go in afterwards.
    docker compose up -d
    echo "waiting for splunk to become healthy (first start takes several minutes)..."
    wait_healthy
    sync_apps
    if [[ -n "$changes" ]]; then
        docker compose restart splunk
        wait_healthy
    fi
fi

./scripts/cribl-bootstrap.sh
./scripts/cribl-packs.sh

# Installing a pack has made the Cribl server exit right afterwards, with no error,
# while the container stays up. Make sure the API is answering before finishing.
cribl_up() { curl -sf http://127.0.0.1:9000/api/v1/health >/dev/null; }
sleep 10
if ! cribl_up; then
    echo "cribl: API not answering after setup, restarting the container" >&2
    docker compose restart cribl
    for _ in $(seq 1 30); do cribl_up && break; sleep 5; done
    cribl_up || { echo "cribl: still not answering after restart" >&2; exit 1; }
fi
echo "cribl: healthy"

if [[ ! -f "$ARCHIVE_DIR/.splunk-archive" ]]; then
    echo "WARNING: $ARCHIVE_DIR/.splunk-archive not found - nightly config backups will be skipped until the Unraid share is mounted" >&2
fi
echo "done: Splunk https://$(hostname):8000  Cribl http://$(hostname):9000"
