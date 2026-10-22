#!/usr/bin/env bash
# Install or update Falco on a Debian/Ubuntu host from the signed Falco apt repo.
# Run as root from a copy of this directory. Re-running refreshes config and rules.
#   - modern_ebpf driver (no kernel module), falcoctl artifact auto-updates off so
#     the rules in effect are always the ones in this repo
#   - config.d/homelab.yaml and rules.d/*.yaml copied to /etc/falco
#   - validated with --dry-run before the service is restarted
set -euo pipefail

VERSION=0.45.1
[[ $EUID -eq 0 ]] || { echo "run as root" >&2; exit 1; }
cd "$(dirname "$0")"

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
