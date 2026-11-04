#!/usr/bin/env bash
# Move k3s-01 to the release in k3s-version. Run as root on k3s-01 from a copy of
# this directory, after taking a Proxmox snapshot of the VM (README.md): k3s can't
# be downgraded, so the snapshot is the rollback.
#
# Replaces the k3s binary and restarts k3s. Running containers keep running; the API
# is down for a minute or so, and the bundled components (Traefik, CoreDNS,
# metrics-server, local-path) then roll to the versions this release ships.
set -euo pipefail
[[ $EUID -eq 0 ]] || { echo "run as root" >&2; exit 1; }
cd "$(dirname "$0")"
source <(grep -E '^K3S_(VERSION|SHA256)=' k3s-version)

current=$(k3s --version | awk 'NR==1{print $3}')
minor() { echo "$1" | sed -E 's/^v1\.([0-9]+)\..*/\1/'; }
if [[ $current == "$K3S_VERSION" ]]; then echo "already on $K3S_VERSION"; exit 0; fi
step=$(( $(minor "$K3S_VERSION") - $(minor "$current") ))
if (( step < 0 || step > 1 )); then
    echo "$current -> $K3S_VERSION: Kubernetes only supports moving one minor version at a time" >&2
    exit 1
fi

./install.sh   # host settings first, so the new release starts with them

tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
curl -fsSL -o "$tmp/k3s" "https://github.com/k3s-io/k3s/releases/download/${K3S_VERSION/+/%2B}/k3s"
echo "$K3S_SHA256  $tmp/k3s" | sha256sum -c --quiet -
chmod 755 "$tmp/k3s"

cp -p /usr/local/bin/k3s "/usr/local/bin/k3s.$current"
install -m 755 "$tmp/k3s" /usr/local/bin/k3s
echo "restarting k3s on $K3S_VERSION"
systemctl restart k3s

for _ in $(seq 1 60); do
    k3s kubectl get --raw /readyz >/dev/null 2>&1 && break
    sleep 5
done
k3s kubectl get --raw /readyz >/dev/null
# The node keeps reporting the old version (and Ready) until the new kubelet updates it.
for _ in $(seq 1 60); do
    [[ $(k3s kubectl get node "$(hostname)" -o jsonpath='{.status.nodeInfo.kubeletVersion}') == "$K3S_VERSION" ]] && break
    sleep 5
done
k3s kubectl wait node/"$(hostname)" --for=jsonpath='{.status.nodeInfo.kubeletVersion}'="$K3S_VERSION" --timeout=10s
k3s kubectl wait node/"$(hostname)" --for=condition=Ready --timeout=300s
k3s kubectl get node -o wide
echo "k3s is up on $K3S_VERSION; the previous binary is /usr/local/bin/k3s.$current"
