#!/usr/bin/env bash
# Host settings k3s-01 needs that Argo CD can't apply. Run as root on k3s-01 from a
# copy of this directory. Idempotent: only changed files are written.
set -euo pipefail
[[ $EUID -eq 0 ]] || { echo "run as root" >&2; exit 1; }
cd "$(dirname "$0")"

put() {  # put <src> <dest> <mode>
    if ! cmp -s "$1" "$2"; then install -D -m "$3" "$1" "$2"; echo "updated $2"; fi
}
put 99-inotify.conf     /etc/sysctl.d/99-inotify.conf 644
put 99-k3s-traefik.conf /etc/sysctl.d/99-k3s-traefik.conf 644
sysctl --system >/dev/null
# k3s watches this directory and re-applies the Traefik chart when the file changes.
put traefik-config.yaml /var/lib/rancher/k3s/server/manifests/traefik-config.yaml 600
