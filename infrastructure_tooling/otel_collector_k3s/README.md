# OpenTelemetry Collector on k3s-01

A DaemonSet that sends pod logs, Kubernetes events and Kubernetes metrics from k3s-01 to Cribl's HEC input on security-tooling-01 (`http://192.168.4.11:8088`). Cribl drops noise and picks the Splunk index; see `monitoring_stack/splunk/README.md`.

| Data | Receiver | Splunk sourcetype | Index (set by Cribl) |
|---|---|---|---|
| Pod logs (`/var/log/pods`) | `file_log`, from the `logsCollection` preset | `kube:container` | `k8s`; `apps` for alexprinter, stirling-pdf, uptime-kuma; `audit` for vault and toolhive |
| Kubernetes events | `k8s_events` | `kube:events` | same rules, by event namespace |
| Usage metrics: node, pod, container, volume (CPU, memory working set against requests and limits, filesystem, network) | `kubeletstats`, from the `kubeletMetrics` preset | metric | `metrics` |
| Cluster state, the kube-state-metrics equivalent: pod phase, container restarts and readiness, desired vs ready replicas, node conditions | `k8s_cluster`, from the `clusterMetrics` preset | metric | `metrics` |

Every record carries `k8s.namespace.name`, `k8s.pod.name`, `k8s.container.name`, and the workload name (from the `kubernetesAttributes` preset). Cribl drops the `arc-runners` namespace and `kube-probe` hits.

## Setup

1. Put the Cribl HEC token in Vault: `kv/cribl-hec`, property `token`. The ExternalSecret syncs it into the `cribl-hec` Secret.
2. `kubectl apply -f infrastructure_tooling/otel_collector_k3s/application.yaml`

## Notes

- `alternateConfig` replaces the chart's default config, which has OTLP/Jaeger/Zipkin/Prometheus receivers and a debug exporter. Nothing listens, and there are no hostPorts.
- The chart wires the `kubernetesEvents` preset's receiver only in deployment mode. In daemonset mode it still adds the RBAC, so `k8s_events` is configured by hand. On a single node this doesn't duplicate events; add leader election before running on more nodes.
- Metrics are collected every 60 s, with counters converted to deltas as on the host agents. The `clusterMetrics` preset runs without leader election (`disableLeaderElection`): one node, one collector. Add it back before running on more nodes, or `k8s_cluster` metrics are duplicated. They are shown on the Homelab Operations dashboard (`monitoring_stack/splunk/apps/85_homelab_ops`).
- Checkpoints live in `/var/lib/otelcol` on the node (`storeCheckpoints`), which runs the collector as root.
- The image is `otel/opentelemetry-collector-contrib`, pinned to the same version as the host agents in `monitoring_stack/splunk/otel-agent/`.
- **`k8s_events` stops for good if its watch fails while the API server restarts.** It logs `Watch channel closed unexpectedly` and never reconnects, though pod logs and the audit log keep flowing, because those come from files. This happened when k3s restarted for the audit log on 2026-10-23, and `kube:events` was silent for a day. After restarting k3s by hand, also run `kubectl -n otel-collector rollout restart ds/otel-collector-opentelemetry-collector-agent`; `k3s_audit/install.sh` does this itself. A node reboot recreates the pod, so it's unaffected.
