#!/usr/bin/env bash
# Install or update the OTel collector on a Debian/Ubuntu host (VMs, Proxmox nodes), or
# on Arch Linux (coding-box) from the release tarball.
#   install.sh base     journald + host metrics
#   install.sh docker   base + container logs and metrics
#   install.sh pve      base + Proxmox's own node, guest and storage metrics (Proxmox nodes)
# Run as root from a copy of this directory. On first install the Cribl HEC token is
# read from stdin (one line, CRIBL_HEC_TOKEN=<token>) and stored in
# /etc/otelcol-contrib/hec.env, root-only. It is never passed as an argument.
# Re-running with the same version only refreshes the configs.
set -euo pipefail

# Renovate tracks VERSION but can't compute the hashes, so its PR waits for approval
# on the Dependency Dashboard. To upgrade: approve it there, then on the PR branch set
# SHA256 to the contents of otelcol-contrib_<version>_linux_amd64.deb.sha256 and
# SHA256_ARCH to otelcol-contrib_<version>_linux_amd64.tar.gz.sha256 from the release
# (check each against the downloaded file), and merge. A stale hash fails the install.
VERSION=0.163.2
SHA256=5a175c93c5a560368c1bdbf0ec25fdc06e7b7d852f424bf7dbc36da6fd495db7
SHA256_ARCH=cc19dd6a1b85ca694ef16655688e597f693d0e24d2baecdd50e2db597819eeff
ROLE="${1:?usage: install.sh base|docker|pve}"
[[ "$ROLE" == base || "$ROLE" == docker || "$ROLE" == pve ]] || { echo "role must be base, docker or pve" >&2; exit 1; }
[[ $EUID -eq 0 ]] || { echo "run as root" >&2; exit 1; }
cd "$(dirname "$0")"
ETC=/etc/otelcol-contrib

RELEASE="https://github.com/open-telemetry/opentelemetry-collector-releases/releases/download/v${VERSION}"
# A subshell, so os-release's own VERSION doesn't replace ours.
# shellcheck source=/dev/null
os=$(. /etc/os-release; echo " $ID ${ID_LIKE:-} ")
if [[ "$os" == *" arch "* ]]; then
    # No package: the binary from the release tarball, and the unit the .deb ships
    # (with the binary in /usr/local/bin, which pacman doesn't own).
    BIN=/usr/local/bin/otelcol-contrib
    installed=$("$BIN" --version 2>/dev/null | awk '{print $NF}' || true)
    if [[ "$installed" != "$VERSION" ]]; then
        tmp=$(mktemp -d)
        curl -fsSL -o "$tmp/otelcol-contrib.tar.gz" "$RELEASE/otelcol-contrib_${VERSION}_linux_amd64.tar.gz"
        echo "$SHA256_ARCH  $tmp/otelcol-contrib.tar.gz" | sha256sum -c - >/dev/null || { echo "tarball SHA-256 mismatch: update SHA256_ARCH for $VERSION" >&2; rm -rf "$tmp"; exit 1; }
        tar -xzf "$tmp/otelcol-contrib.tar.gz" -C "$tmp" otelcol-contrib
        install -m 755 "$tmp/otelcol-contrib" "$BIN"
        rm -rf "$tmp"
        echo "otelcol-contrib ${installed:-none} -> $VERSION"
    fi
    mkdir -p "$ETC"
    # User/Group come from the drop-in below, as on Debian, so no otelcol-contrib user.
    cat > /etc/systemd/system/otelcol-contrib.service <<EOF
# Managed by otel-agent/install.sh: the unit from the otelcol-contrib .deb.
[Unit]
Description=OpenTelemetry Collector Contrib
After=network.target

[Service]
EnvironmentFile=$ETC/otelcol-contrib.conf
ExecStart=$BIN \$OTELCOL_OPTIONS
ExecReload=/bin/kill -HUP \$MAINPID
KillMode=mixed
Restart=on-failure
Type=simple

[Install]
WantedBy=multi-user.target
EOF
else
    BIN=/usr/bin/otelcol-contrib
    installed=$(dpkg-query -W -f='${Version}' otelcol-contrib 2>/dev/null || true)
    if [[ "$installed" != "$VERSION" ]]; then
        deb=$(mktemp --suffix=.deb)
        curl -fsSL -o "$deb" "$RELEASE/otelcol-contrib_${VERSION}_linux_amd64.deb"
        echo "$SHA256  $deb" | sha256sum -c - >/dev/null
        # Keep our otelcol-contrib.conf on upgrade instead of prompting.
        dpkg --force-confold -i "$deb" >/dev/null
        rm -f "$deb"
        echo "otelcol-contrib ${installed:-none} -> $VERSION"
    fi
fi

if [[ ! -s "$ETC/hec.env" ]]; then
    IFS= read -r line || true
    [[ "$line" == CRIBL_HEC_TOKEN=?* ]] || { echo "pipe CRIBL_HEC_TOKEN=<token> on stdin for the first install" >&2; exit 1; }
    (umask 077; printf '%s\n' "$line" > "$ETC/hec.env")
fi
chmod 600 "$ETC/hec.env"

# The homelab CA that signed Cribl's HEC cert (scripts/tls-certs.sh); public.
install -m 644 homelab-ca.crt "$ETC/homelab-ca.crt"
install -m 644 base.yaml "$ETC/base.yaml"
opts="--config=$ETC/base.yaml"
rm -f "$ETC/docker.yaml" "$ETC/pve.yaml"
if [[ "$ROLE" != base ]]; then
    install -m 644 "$ROLE.yaml" "$ETC/$ROLE.yaml"
    opts="$opts --config=$ETC/$ROLE.yaml"
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
"$BIN" validate $opts

systemctl daemon-reload
systemctl enable otelcol-contrib >/dev/null 2>&1
systemctl restart otelcol-contrib
sleep 3
systemctl is-active --quiet otelcol-contrib || { journalctl -u otelcol-contrib -n 30 --no-pager >&2; exit 1; }
echo "otelcol-contrib $VERSION running, role $ROLE"
