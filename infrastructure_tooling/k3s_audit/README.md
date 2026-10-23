# k3s API audit log

k3s-01's apiserver records Kubernetes API requests under `audit-policy.yaml` and sends each event to two places:

```
kube-apiserver --+--> /var/log/kubernetes/audit/audit.log --> OTel DaemonSet --> Cribl --> index=audit (kube:apiserver:audit)
                 +--> webhook 10.43.200.10:9765 --> falco-k8saudit (k8saudit-rules) --> Cribl --> index=security (falco:ocsf)
```

- **The log** answers "who did what in the cluster": people, CI, and application workloads, including AI agents using the toolhive-mcp kubectl MCP server. Syscall Falco can't see this, because it only sees a pooled connection to the API.
- **The webhook** feeds Falco's Kubernetes audit rules: exec into a pod, a privileged or hostPath pod, ClusterRole changes, access to Secrets by unexpected users, and similar.

## What is recorded (`audit-policy.yaml`)

Rules apply in order, and the first match wins.

| Requests | Level |
|---|---|
| Health, metrics and API discovery | not recorded |
| Leases, events, endpoints, endpointslices | not recorded |
| Control-plane components (`system:kube-controller-manager`, scheduler, kube-proxy, k3s controller) | not recorded |
| The kubelet reading and reporting status | not recorded |
| Reads by infrastructure service accounts (the namespaces Falco also exempts) | not recorded |
| Anything on Secrets, ConfigMaps, ServiceAccount tokens, Token/Access reviews | `Metadata`: who, what, when, result; never the body |
| Other creates, updates, patches, deletes (including `pods/exec`) | `Request`: includes the submitted object |
| Everything else, such as reads by people and application workloads | `Metadata` |

The log rotates at 100 MB, keeping 3 files for 7 days.

## Install or update

```bash
scp -r infrastructure_tooling/k3s_audit root@k3s-01:/root/k3s-audit
ssh root@k3s-01 /root/k3s-audit/install.sh
```

`install.sh` copies the policy, the webhook kubeconfig, and a k3s config drop-in (`/etc/rancher/k3s/config.yaml.d/audit.yaml`), then restarts k3s, but only if a file changed.

- **During the restart:** running pods keep running, but the API is unavailable for about 30 seconds.
- **Rollback:** if k3s isn't ready within 3 minutes, the script removes the drop-in and restarts k3s without auditing.

Deploy the `falco-k3s` and `otel-collector-k3s` Argo CD apps first, so the webhook Service and the log mount already exist. If Falco is down, the apiserver retries a batch and then drops it; API requests are never blocked waiting for the webhook.
