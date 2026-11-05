#!/usr/bin/env bash
# CIS k3s 5.1.5: every namespace's `default` ServiceAccount (k3s exempts
# kube-system) must have automountServiceAccountToken: false, so a pod that
# doesn't name a ServiceAccount gets no API token. Workloads that need the API use
# their own ServiceAccount. Idempotent; run by install.sh. kube-bench's monthly
# run flags a namespace created since.
set -euo pipefail
K="k3s kubectl"
# Same listing as kube-bench's check: the field is unset (<none>) on a new namespace.
$K get serviceaccounts -A --field-selector metadata.name=default --no-headers \
    -o custom-columns=NS:.metadata.namespace,AUTOMOUNT:.automountServiceAccountToken |
while read -r ns automount; do
    [ "$ns" = kube-system ] || [ "$automount" = false ] && continue
    $K -n "$ns" patch serviceaccount default --type merge -p '{"automountServiceAccountToken": false}' >/dev/null
    echo "default ServiceAccount in $ns: automountServiceAccountToken false"
done
