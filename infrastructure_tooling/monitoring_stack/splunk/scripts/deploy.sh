#!/usr/bin/env bash
# Bring up Splunk and Cribl, sync the repo-managed apps under apps/ into Splunk,
# issue the TLS certs, restrict port 8089, and seed Cribl's Worker Group config on
# first deploy.
# Safe to re-run: restarts splunkd only when an app or its cert actually changed, and
# never overwrites Cribl config after the first deploy (the Cribl UI owns it from then
# on).
# Run on security-tooling-01 as root from this directory's checkout.
set -euo pipefail

cd "$(dirname "$0")/.."

[[ -f .env ]] || { echo "missing .env (copy .env-example)" >&2; exit 1; }
set -a; . ./.env; set +a
DATA_DIR="${SPLUNK_DATA_DIR:-/srv/splunk}"
ARCHIVE_DIR="${SPLUNK_ARCHIVE_HOST_DIR:-/mnt/unraid-splunk}"
CRIBL_DIR="${CRIBL_DATA_DIR:-/srv/cribl}"
CRIBL_WORKER_DIR="${CRIBL_WORKER_DATA_DIR:-/srv/cribl-worker}"
CRIBL_GROUP=default

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

mkdir -p "$DATA_DIR/etc" "$DATA_DIR/var" "$ARCHIVE_DIR" "$CRIBL_DIR" "$CRIBL_WORKER_DIR"

# Only k3s-01 may reach the published Splunk management port. Applied before any
# container starts, and on every boot by the systemd unit.
install -m 644 systemd/splunk-mgmt-firewall.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable splunk-mgmt-firewall.service >/dev/null 2>&1
./scripts/mgmt-firewall.sh

# TLS certs for Splunk's management port and the Cribl Leader API. Sets
# $certs_changed to the services whose cert was (re)issued.
issue_certs() { certs_changed+=" $(./scripts/tls-certs.sh | sed -n 's/^changed: //p' | tr '\n' ' ')"; }
certs_changed=""
issue_certs

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
        # ._* are macOS AppleDouble files from copying off a Mac; splunkd tries to
        # parse ._*.conf as config.
        changes+=$(rsync -rlp --chown="$owner" --delete --checksum --itemize-changes \
            --exclude 'local/' --exclude 'metadata/local.meta' --exclude 'CLAUDE.md' \
            --exclude '._*' \
            "$app" "$DATA_DIR/etc/apps/$name/")
    done
    if [[ -n "$changes" ]]; then echo "apps changed:"; echo "$changes"; else echo "apps unchanged"; fi
}

if [[ -d "$DATA_DIR/etc/system" ]]; then
    # Splunk has started before: sync first, so a fix to an app that stops splunkd
    # from starting can still be deployed.
    sync_apps
    docker compose up -d --remove-orphans
    if [[ -n "$changes" || "$certs_changed" == *splunk* ]]; then docker compose restart splunk; fi
    echo "waiting for splunk to become healthy..."
    wait_healthy
else
    # First start: the image populates etc/ (including the CA bundle tls-certs.sh
    # builds on) on startup, so the cert and apps go in afterwards.
    docker compose up -d --remove-orphans
    echo "waiting for splunk to become healthy (first start takes several minutes)..."
    wait_healthy
    issue_certs
    sync_apps
    if [[ -n "$changes" ]]; then
        docker compose restart splunk
        wait_healthy
    fi
fi

# The Leader reads its cert at startup. A brand-new Leader is still on HTTP and gets
# switched to HTTPS by cribl-bootstrap.sh.
if [[ "$certs_changed" == *cribl* && -f "$CRIBL_DIR/groups/$CRIBL_GROUP/local/cribl/inputs.yml" ]]; then
    docker compose restart cribl
fi

# Seed the Worker Group config once. The Leader creates the empty default group on
# its first start in master mode; the inputs, outputs, pipelines and routes are then
# copied in while it is stopped. The source is the config of the old single-instance
# Cribl if this volume has one (the one-time move to Leader + Worker), otherwise the
# repo seed in cribl/local/cribl. cribl-bootstrap.sh then sets the HEC token and
# deploys the config to the Worker. After that the Cribl UI owns the config; export
# changes back to cribl/ in the repo to keep them.
seed_cribl_group() {
    local group_dir="$CRIBL_DIR/groups/$CRIBL_GROUP/local/cribl" src
    [[ -f "$group_dir/inputs.yml" ]] && return 0
    for _ in $(seq 1 60); do
        [[ -f "$group_dir/auth/cribl.secret" ]] && break
        sleep 5
    done
    [[ -f "$group_dir/auth/cribl.secret" ]] || { echo "cribl: Leader did not create group $CRIBL_GROUP" >&2; return 1; }
    if [[ -f "$CRIBL_DIR/local/cribl/inputs.yml" ]]; then src="$CRIBL_DIR/local/cribl"; else src=cribl/local/cribl; fi
    docker compose stop cribl
    cp "$src/inputs.yml" "$src/outputs.yml" "$src/cribl.yml" "$group_dir/"
    rsync -a --exclude 'CLAUDE.md' "$src/pipelines/" "$group_dir/pipelines/"
    touch "$CRIBL_DIR/.group-seeded"
    docker compose start cribl
    echo "cribl: seeded group $CRIBL_GROUP from $src"
}
seed_cribl_group

./scripts/cribl-bootstrap.sh
./scripts/cribl-packs.sh

# Installing a pack has made the Cribl server exit right afterwards, with no error,
# while the container stays up. Make sure the API is answering before finishing.
cribl_up() { curl -sf --cacert "$CRIBL_DIR/local/cribl/auth/homelab-tls/ca.crt" https://127.0.0.1:9000/api/v1/health >/dev/null; }
sleep 10
if ! cribl_up; then
    echo "cribl: API not answering after setup, restarting the container" >&2
    docker compose restart cribl
    for _ in $(seq 1 30); do cribl_up && break; sleep 5; done
    cribl_up || { echo "cribl: still not answering after restart" >&2; exit 1; }
fi
echo "cribl: healthy"

if [[ ! -f "$ARCHIVE_DIR/.splunk-archive" ]]; then
    echo "WARNING: $ARCHIVE_DIR/.splunk-archive not found - nightly config backups and frozen-bucket archiving will fail until the Unraid share is mounted" >&2
fi
echo "done: Splunk https://$(hostname):8000  Cribl https://$(hostname):9000"
