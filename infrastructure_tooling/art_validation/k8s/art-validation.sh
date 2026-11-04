#!/bin/sh
# Runs under busybox sh (cronjob.yaml): keep it POSIX sh plus pipefail.
# Atomic Red Team style detection validation, Kubernetes part. Runs in the namespace
# art-validation as the service account art-runner, which may do exactly these tests
# and nothing else (rbac.yaml). Before each test it prints a marker
#   ART-VALIDATION run=<id> expect="<detection>" test=<id>
# (index=k8s); "Splunk - detection validation failed" checks the findings later.
set -uo pipefail
RUN="${ART_RUN_ID:-$(date -u +%Y%m%dT%H%M%SZ)-k8s}"
NS=art-validation
K="kubectl -n $NS"
mark() { echo "ART-VALIDATION run=$RUN expect=\"$1\" test=$2"; }

cleanup() {
    $K delete pod art-validation-target --ignore-not-found --wait=false >/dev/null
    $K delete secret art-validation-secret --ignore-not-found >/dev/null
    $K delete rolebinding art-validation-rb --ignore-not-found >/dev/null
}
trap cleanup EXIT

# T1610 Deploy Container: a pod created directly, not through Argo CD.
mark "HL - Kubernetes workload created by hand" T1610
$K run art-validation-target --image="$TARGET_IMAGE" --restart=Never --overrides='{
  "spec": {"securityContext": {"runAsNonRoot": true, "runAsUser": 65534, "seccompProfile": {"type": "RuntimeDefault"}},
           "containers": [{"name": "art-validation-target", "image": "'"$TARGET_IMAGE"'", "command": ["sleep", "600"],
             "securityContext": {"allowPrivilegeEscalation": false, "capabilities": {"drop": ["ALL"]}}}]}}'
$K wait --for=condition=Ready pod/art-validation-target --timeout=120s

# T1609 Container Administration Command: exec into the pod.
mark "HL - Kubernetes exec, attach or port-forward into a pod" T1609
$K exec art-validation-target -- true

# T1552.007 Unsecured Credentials: Container API. Read a (dummy) secret.
mark "HL - Kubernetes secrets read by a person or application" T1552.007
$K create secret generic art-validation-secret --from-literal=note=art-validation-dummy >/dev/null
$K get secret art-validation-secret -o name

# T1528 Steal Application Access Token: mint a short-lived service account token.
mark "HL - Kubernetes service account token minted" T1528
$K create token art-runner --duration=10m >/dev/null && echo "token minted (discarded)"

# T1098.006 Account Manipulation: Additional Container Cluster Roles. Bind the
# read-only view role to this namespace's own service account, then remove it.
mark "HL - Kubernetes RBAC role or binding changed" T1098.006
$K create rolebinding art-validation-rb --clusterrole=view --serviceaccount=$NS:art-runner

# T1613 Container and Resource Discovery: list nodes, which this account may not;
# the detection wants 5 or more denied requests.
mark "HL - Kubernetes anonymous or denied API requests" T1613
for _ in 1 2 3 4 5 6; do kubectl get nodes >/dev/null 2>&1; done
echo "6 denied node listings"

echo "[$RUN] done"
