#!/usr/bin/env bash
# Install kube-bench on k3s-01 and run it monthly against the CIS k3s benchmark.
# It runs on the host, not as a pod: the k3s profile reads `journalctl -u k3s` and
# files under /var/lib/rancher, which a container can't see properly.
# Idempotent. Run as root on k3s-01 from a copy of this directory.
set -euo pipefail
[[ $EUID -eq 0 ]] || { echo "run as root" >&2; exit 1; }
cd "$(dirname "$0")"

# Renovate tracks VERSION but can't compute SHA256, so its PR waits for approval on
# the Dependency Dashboard. To upgrade: approve it there, then on the PR branch set
# SHA256 to the linux_amd64.tar.gz line of the release's checksums file, and merge.
VERSION=0.16.0
SHA256=82dbc7e598740dc9344d41f8ad0b8210d57c4c00bdb2c5f1d8a69a2b98baddcf   # kube-bench_${VERSION}_checksums.txt
DEST=/usr/local/lib/kube-bench

if [[ "$(cat "$DEST/VERSION" 2>/dev/null)" != "$VERSION" ]]; then
    tmp=$(mktemp -d)
    trap 'rm -rf "$tmp"' EXIT
    curl -fsSLo "$tmp/kb.tgz" "https://github.com/aquasecurity/kube-bench/releases/download/v${VERSION}/kube-bench_${VERSION}_linux_amd64.tar.gz"
    echo "$SHA256  $tmp/kb.tgz" | sha256sum -c --quiet
    tar xzf "$tmp/kb.tgz" -C "$tmp"
    rm -rf "$DEST"
    install -d -m 755 "$DEST"
    install -m 755 "$tmp/kube-bench" "$DEST/kube-bench"
    cp -r "$tmp/cfg" "$DEST/cfg"
    echo "$VERSION" > "$DEST/VERSION"
    echo "kube-bench $VERSION installed"
fi

install -m 644 kube-bench.service kube-bench.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now kube-bench.timer
systemctl list-timers kube-bench.timer --no-pager
