# OpenTelemetry Collector on k3s-01

A DaemonSet that sends pod logs and Kubernetes events from k3s-01 to Cribl's HEC input on security-tooling-01 (`http://192.168.4.11:8088`). Cribl drops noise and picks the Splunk index; see `monitoring_stack/splunk/README.md`.

| Data | Receiver | Splunk sourcetype | Index (set by Cribl) |
|---|---|---|---|
| Pod logs (`/var/log/pods`) | `file_log`, from the `logsCollection` preset | `kube:container` | `k8s`; `apps` for alexprinter, stirling-pdf, uptime-kuma; `audit` for vault and toolhive |
| Kubernetes events | `k8s_events` | `kube:events` | same rules, by event namespace |

Every record carries `k8s.namespace.name`, `k8s.pod.name`, `k8s.container.name`, and the workload name (from the `kubernetesAttributes` preset). Cribl drops the `arc-runners` namespace and `kube-probe` hits.

## Setup

1. Put the Cribl HEC token in Vault: `kv/cribl-hec`, property `token`. The ExternalSecret syncs it into the `cribl-hec` Secret.
2. `kubectl apply -f infrastructure_tooling/otel_collector_k3s/application.yaml`

## Notes

- `alternateConfig` replaces the chart's default config, which has OTLP/Jaeger/Zipkin/Prometheus receivers and a debug exporter. Nothing listens, and there are no hostPorts.
- The chart wires the `kubernetesEvents` preset's receiver only in deployment mode. In daemonset mode it still adds the RBAC, so `k8s_events` is configured by hand. On a single node this doesn't duplicate events; add leader election before running on more nodes.
- Checkpoints live in `/var/lib/otelcol` on the node (`storeCheckpoints`), which runs the collector as root.
- The image is `otel/opentelemetry-collector-contrib`, pinned to the same version as the host agents in `monitoring_stack/splunk/otel-agent/`.
