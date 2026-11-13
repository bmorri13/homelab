#!/usr/bin/env bash
# Install or update Falco on a Debian/Ubuntu host from the signed Falco apt repo, or on
# Arch Linux (coding-box) from Falco's release tarball and a copy of the package's unit.
# Run as root from a copy of this directory. Re-running refreshes config and rules.
#   - modern_ebpf driver (no kernel module), falcoctl artifact auto-updates off so
#     the rules in effect are always the ones in this repo
#   - config.d/homelab.yaml and rules.d/*.yaml copied to /etc/falco
#   - validated with --dry-run before the service is restarted
set -euo pipefail

VERSION=0.45.2
# Arch only: SHA-256 of falco-<VERSION>-x86_64.tar.gz from download.falco.org/packages/bin/x86_64/.
# Falco publishes no .sha256 for it, only a .asc signature: on a version bump, check the
# .asc with Falco's package key (falco.org/repo/falcosecurity-packages.asc), then put
# the tarball's sha256sum here. A stale hash fails the install.
SHA256_ARCH=b2e9961e51601095765c6c644329673e6af2f06c02357d0e77da0f680e4910da
[[ $EUID -eq 0 ]] || { echo "run as root" >&2; exit 1; }
cd "$(dirname "$0")"

# A subshell, so os-release's own VERSION doesn't replace ours.
# shellcheck source=/dev/null
os=$(. /etc/os-release; echo " $ID ${ID_LIKE:-} ")
if [[ "$os" == *" arch "* ]]; then
    # The tarball holds what the package installs, under usr/ and etc/. Taken: falco,
    # its container plugin and its config. Left out: falcoctl (kept off on Debian too)
    # and the kernel module sources (modern_ebpf needs no module).
    # Without -c /dev/null, --version loads our config and logs with timestamps.
    installed=$(falco -c /dev/null -o json_output=false --version 2>/dev/null | sed -n 's/^Falco version: //p' || true)
    if [[ "$installed" != "$VERSION" ]]; then
        tmp=$(mktemp -d)
        curl -fsSL -o "$tmp/falco.tar.gz" "https://download.falco.org/packages/bin/x86_64/falco-${VERSION}-x86_64.tar.gz"
        echo "$SHA256_ARCH  $tmp/falco.tar.gz" | sha256sum -c - >/dev/null || { echo "tarball SHA-256 mismatch: update SHA256_ARCH for $VERSION" >&2; rm -rf "$tmp"; exit 1; }
        tar -xzf "$tmp/falco.tar.gz" -C "$tmp"
        src="$tmp/falco-${VERSION}-x86_64"
        install -m 755 "$src/usr/bin/falco" /usr/bin/falco
        install -d /usr/share/falco/plugins /etc/falco/config.d /etc/falco/rules.d
        install -m 644 "$src"/usr/share/falco/plugins/*.so /usr/share/falco/plugins/
        # The packaged config is replaced on upgrade, as dpkg does for unmodified
        # conffiles; homelab settings live in config.d and rules.d.
        install -m 644 "$src/etc/falco/falco.yaml" "$src/etc/falco/falco_rules.yaml" /etc/falco/
        install -m 644 "$src"/etc/falco/config.d/*.yaml /etc/falco/config.d/
        [[ -e /etc/falco/falco_rules.local.yaml ]] || install -m 644 "$src/etc/falco/falco_rules.local.yaml" /etc/falco/
        rm -rf "$tmp"
        echo "falco ${installed:-none} -> $VERSION"
    fi
    # The package's falco-modern-bpf.service (scripts/systemd in the Falco repo, same
    # tag), without the falcoctl-artifact-follow ordering. StandardOutput=null as
    # upstream: alerts reach journald through syslog (config.d/homelab.yaml).
    cat > /etc/systemd/system/falco-modern-bpf.service <<'EOF'
# Managed by falco/install.sh: the falco-modern-bpf.service from the Falco package.
[Unit]
Description=Falco: Container Native Runtime Security with modern ebpf
Documentation=https://falco.org/docs/

[Service]
Type=simple
User=root
ExecStart=/usr/bin/falco -o engine.kind=modern_ebpf
ExecReload=kill -1 $MAINPID
UMask=0077
TimeoutSec=30
RestartSec=15s
Restart=on-failure
PrivateTmp=true
NoNewPrivileges=yes
ProtectHome=read-only
ProtectSystem=full
ProtectKernelTunables=true
RestrictRealtime=true
RestrictAddressFamilies=~AF_PACKET
StandardOutput=null

[Install]
WantedBy=multi-user.target
Alias=falco.service
EOF
    systemctl daemon-reload
else
    KEYRING=/usr/share/keyrings/falco-archive-keyring.gpg
    LIST=/etc/apt/sources.list.d/falcosecurity.list
    if [[ ! -s "$KEYRING" ]]; then
        curl -fsSL https://falco.org/repo/falcosecurity-packages.asc | gpg --dearmor -o "$KEYRING" --yes
    fi
    echo "deb [signed-by=$KEYRING] https://download.falco.org/packages/deb stable main" > "$LIST"

    installed=$(dpkg-query -W -f='${Version}' falco 2>/dev/null || true)
    if [[ "$installed" != "$VERSION" ]]; then
        apt-get update -qq -o Dir::Etc::sourcelist="$LIST" -o Dir::Etc::sourceparts=- >/dev/null
        apt-mark unhold falco >/dev/null 2>&1 || true
        DEBIAN_FRONTEND=noninteractive FALCO_FRONTEND=noninteractive \
            FALCO_DRIVER_CHOICE=modern_ebpf FALCOCTL_ENABLED=no \
            apt-get install -y -qq "falco=$VERSION" >/dev/null
        apt-mark hold falco >/dev/null
        echo "falco ${installed:-none} -> $VERSION"
    fi
fi

install -m 644 config.d/homelab.yaml /etc/falco/config.d/homelab.yaml
mkdir -p /etc/falco/rules.d
for f in rules.d/*.yaml; do install -m 644 "$f" "/etc/falco/rules.d/$(basename "$f")"; done

# Validate config and rules before touching the running service.
falco --dry-run -o engine.kind=modern_ebpf >/tmp/falco-dryrun.log 2>&1 || { cat /tmp/falco-dryrun.log >&2; exit 1; }

systemctl enable falco-modern-bpf >/dev/null 2>&1
systemctl restart falco-modern-bpf
sleep 8
systemctl is-active --quiet falco-modern-bpf || { journalctl -u falco-modern-bpf -n 30 --no-pager >&2; exit 1; }
echo "falco $VERSION running (modern_ebpf)"
