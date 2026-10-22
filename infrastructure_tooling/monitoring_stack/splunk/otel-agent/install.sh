#!/usr/bin/env bash
# Install or update the OTel collector on a Debian/Ubuntu host (VMs, Proxmox nodes).
#   install.sh base     journald only
#   install.sh docker   journald + container logs
# Run as root from a copy of this directory. On first install the Cribl HEC token is
# read from stdin (one line, CRIBL_HEC_TOKEN=<token>) and stored in
# /etc/otelcol-contrib/hec.env, root-only. It is never passed as an argument.
# Re-running with the same version only refreshes the configs.
set -euo pipefail

VERSION=0.163.2
SHA256=5a175c93c5a560368c1bdbf0ec25fdc06e7b7d852f424bf7dbc36da6fd495db7
ROLE="${1:?usage: install.sh base|docker}"
[[ "$ROLE" == base || "$ROLE" == docker ]] || { echo "role must be base or docker" >&2; exit 1; }
[[ $EUID -eq 0 ]] || { echo "run as root" >&2; exit 1; }
cd "$(dirname "$0")"
ETC=/etc/otelcol-contrib

installed=$(dpkg-query -W -f='${Version}' otelcol-contrib 2>/dev/null || true)
if [[ "$installed" != "$VERSION" ]]; then
    deb=$(mktemp --suffix=.deb)
    curl -fsSL -o "$deb" "https://github.com/open-telemetry/opentelemetry-collector-releases/releases/download/v${VERSION}/otelcol-contrib_${VERSION}_linux_amd64.deb"
    echo "$SHA256  $deb" | sha256sum -c - >/dev/null
    # Keep our otelcol-contrib.conf on upgrade instead of prompting.
    dpkg --force-confold -i "$deb" >/dev/null
    rm -f "$deb"
    echo "otelcol-contrib ${installed:-none} -> $VERSION"
fi

if [[ ! -s "$ETC/hec.env" ]]; then
    IFS= read -r line || true
    [[ "$line" == CRIBL_HEC_TOKEN=?* ]] || { echo "pipe CRIBL_HEC_TOKEN=<token> on stdin for the first install" >&2; exit 1; }
    (umask 077; printf '%s\n' "$line" > "$ETC/hec.env")
fi
chmod 600 "$ETC/hec.env"

install -m 644 base.yaml "$ETC/base.yaml"
opts="--config=$ETC/base.yaml"
if [[ "$ROLE" == docker ]]; then
    install -m 644 docker.yaml "$ETC/docker.yaml"
    opts="$opts --config=$ETC/docker.yaml"
else
    rm -f "$ETC/docker.yaml"
fi
printf '# Managed by otel-agent/install.sh (role: %s)\nOTELCOL_OPTIONS="%s"\n' "$ROLE" "$opts" > "$ETC/otelcol-contrib.conf"

# Root is needed to read the journal, the Docker socket, and /var/lib/docker/containers.
mkdir -p /etc/systemd/system/otelcol-contrib.service.d
cat > /etc/systemd/system/otelcol-contrib.service.d/homelab.conf <<'EOF'
[Service]
User=root
Group=root
EnvironmentFile=/etc/otelcol-contrib/hec.env
EOF

# Validate before restarting so a bad config never replaces a running one.
set -a; . "$ETC/hec.env"; set +a
# shellcheck disable=SC2086
/usr/bin/otelcol-contrib validate $opts

systemctl daemon-reload
systemctl enable otelcol-contrib >/dev/null 2>&1
systemctl restart otelcol-contrib
sleep 3
systemctl is-active --quiet otelcol-contrib || { journalctl -u otelcol-contrib -n 30 --no-pager >&2; exit 1; }
echo "otelcol-contrib $VERSION running, role $ROLE"
