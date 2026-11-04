# Trivy Operator on k3s-01

Scans what is actually **running** on k3s-01, continuously, and keeps the results as Kubernetes objects:

| Report | What it finds |
|---|---|
| VulnerabilityReport | HIGH and CRITICAL CVEs in every running container's image, including images that aren't in this repo (k3s system images, chart sub-images) |
| ExposedSecretReport | Keys, tokens and passwords baked into an image's filesystem |
| ConfigAuditReport | Risky workload settings: root, privileged, no limits, writable root filesystem, host access |
| RbacAssessmentReport / ClusterRbacAssessmentReport | Over-broad roles: wildcards, secret access, pod exec, escalation |

Renovate keeps versions current but never says which CVEs are running; guardrail scans the repo, not the cluster. This fills that gap.

## How it runs

- Upstream chart `trivy-operator` 0.38.1 (operator 0.36.1, Trivy 0.76.1; the chart has no digest pinning, so images are pinned by tag), namespace `trivy-system` under the `restricted` Pod Security level, scan jobs included.
- One built-in Trivy server holds the vulnerability database for every scan (5 Gi local-path), instead of each scan job downloading it. Two scan jobs at a time; every workload is rescanned daily (`scannerReportTTL: 24h`).
- Only HIGH and CRITICAL vulnerabilities are recorded: what is worth acting on, and it keeps each report small enough to be one Splunk event.
- Node checks (`infraAssessmentScannerEnabled`) and compliance reports are off: they need a privileged node collector, and `../kube_bench` runs the CIS k3s benchmark on the host instead.
- The report CRDs were applied with `kubectl apply --server-side` before the first sync, so the collector's report reader never starts against missing types. Argo CD owns them now (`ServerSideApply`).

## Into Splunk

The k3s OTel collector (`../otel_collector_k3s`, receiver `k8sobjects/trivy`, read-only ClusterRole on these report types) copies every report every 12 hours to Cribl, which sends it to `index=security` as `sourcetype=trivy:<kind>` (`trivy:vulnerabilityreport`, `trivy:exposedsecretreport`, ...). `monitoring_stack/splunk/apps/85_k8s_posture` parses the JSON. Alerts in `85_homelab_ops`, posted to Slack:

- *Trivy - critical vulnerabilities in running images*: weekly digest (Monday 13:00 UTC).
- *Trivy - secret found in a running image*: within 6 hours of a report, then once a week per image. Secret values are masked by Trivy and never logged.

Look at a workload's reports directly: `kubectl get vulnerabilityreports,exposedsecretreports,configauditreports -A`.

## Outside Git

- **Argo CD Application**, applied once: `kubectl apply -f infrastructure_tooling/trivy_operator/application.yaml`.
