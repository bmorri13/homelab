#!/usr/bin/env bash
# Turn on Kubernetes API auditing on the k3s server. Run as root on k3s-01 from a
# copy of this directory. Re-running refreshes the policy and restarts k3s only if
# something changed. Running workloads keep running during the restart; only the
# API is unavailable for about 30 seconds.
#
# If k3s does not come back healthy with the new settings, the drop-in is removed
# and k3s is restarted without auditing, so a bad policy can't leave the API down.
set -euo pipefail
[[ $EUID -eq 0 ]] || { echo "run as root" >&2; exit 1; }
cd "$(dirname "$0")"

changed=0
put() {  # put <src> <dest>
    if ! cmp -s "$1" "$2"; then install -D -m 600 "$1" "$2"; changed=1; fi
}
put audit-policy.yaml        /etc/rancher/k3s/audit-policy.yaml
put audit-webhook.kubeconfig /etc/rancher/k3s/audit-webhook.kubeconfig
put audit.yaml               /etc/rancher/k3s/config.yaml.d/audit.yaml
install -d -m 700 /var/log/kubernetes/audit

if [[ $changed -eq 0 ]]; then
    echo "audit config unchanged, k3s not restarted"
    exit 0
fi

wait_ready() {
    for _ in $(seq 1 36); do
        k3s kubectl get --raw /readyz >/dev/null 2>&1 && return 0
        sleep 5
    done
    return 1
}

echo "restarting k3s with audit config"
systemctl restart k3s
if ! wait_ready; then
    echo "k3s not ready with audit config; rolling back" >&2
    journalctl -u k3s -n 40 --no-pager | grep -i -E "audit|error" >&2 || true
    rm -f /etc/rancher/k3s/config.yaml.d/audit.yaml
    systemctl restart k3s
    wait_ready && echo "rolled back: k3s is up without auditing" >&2
    exit 1
fi
# The OTel collector's k8s_events receiver doesn't recover when its watch fails
# during an API restart (it logs "Watch channel closed unexpectedly" and stops),
# so Kubernetes events stop reaching Splunk until the collector restarts.
k3s kubectl -n otel-collector rollout restart ds/otel-collector-opentelemetry-collector-agent >/dev/null 2>&1 &&
    echo "restarted the otel collector (k8s_events)" ||
    echo "could not restart the otel collector; restart it so Kubernetes events resume" >&2
sleep 5
if [[ -s /var/log/kubernetes/audit/audit.log ]]; then
    echo "k3s ready; audit log is being written"
else
    echo "k3s ready, but the audit log is still empty" >&2
fi
