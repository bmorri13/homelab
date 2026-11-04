# Detection validation (Atomic Red Team style)

Proves the `HL - ...` detections and Falco still fire, end to end: a test does the thing, its log reaches Splunk through Cribl, the scheduled search runs, and a finding lands in `index=security`. Without it, a broken search, a dropped log source or a changed Cribl route goes unnoticed until a real attack. On 2026-11-04 the three Authentik detections failed every run for an hour before a manual test caught it.

Each test is a harmless, self-cleaning version of an ATT&CK technique, in the spirit of [Atomic Red Team](https://github.com/redcanaryco/atomic-red-team) (MIT): the technique is the same, the payload is replaced by something that leaves nothing behind. The tests are small shell scripts rather than the Invoke-AtomicRedTeam framework, so nothing extra is installed on the hosts.

Its first run (2026-11-04) found a real gap: *HL - Sensitive command run over SSH or sudo* had never fired for a local `sudo`. A local sudo has no `src`, and the search's `stats ... by host user src` silently dropped every such row, so it only ever caught commands over Tailscale SSH. Fixed in the same change (`src` defaults to `local`).

## Where it runs

Only on two places, so production machines are never touched:

- **ubuntu-desktop-01** (host tests): a low-stakes desktop VM that already has the OTel agent and Falco. `host/` installs a script and a weekly systemd timer.
- **The `art-validation` namespace on k3s-01** (Kubernetes tests): a CronJob whose service account `art-runner` may do exactly these tests in that namespace and nothing else (`k8s/rbac.yaml`). Restricted Pod Security. The runner is the official `registry.k8s.io/kubectl` image at the cluster's version, with a static busybox shell copied in by an init container (the kubectl image has no shell).

Both run weekly, Sunday 06:00 UTC.

## Tests

| Where | ATT&CK | What it does | Expected finding |
|---|---|---|---|
| host | T1003.008 | Reads `/etc/shadow` with a copy of `cat` under `/usr/local/lib/art-validation/work` | Falco *Read sensitive file untrusted* |
| host | T1136.001 | `sudo useradd` a system account `art-validation` (no home, no shell), deleted at the end | *HL - Sensitive command run over SSH or sudo* |
| host | T1053.003 | Installs a crontab for that account containing only a comment, removed at the end | same |
| host | T1070.003 | `history -c` in a throwaway shell through sudo | same |
| k8s | T1610 | Creates a pod by hand (busybox, `sleep 600`, non-root) | *HL - Kubernetes workload created by hand* |
| k8s | T1609 | `kubectl exec` into it | *HL - Kubernetes exec, attach or port-forward into a pod* |
| k8s | T1552.007 | Creates and reads a dummy secret | *HL - Kubernetes secrets read by a person or application* |
| k8s | T1528 | Mints a 10-minute token for its own service account (discarded) | *HL - Kubernetes service account token minted* |
| k8s | T1098.006 | Binds the read-only `view` role to its own service account in its namespace, then deletes the binding | *HL - Kubernetes RBAC role or binding changed* |
| k8s | T1613 | Lists nodes six times, which it may not (403) | *HL - Kubernetes anonymous or denied API requests* |

Everything the tests create is named `art-validation`.

## How a result is checked

Before each test the runner logs a marker, `ART-VALIDATION run=<id> expect="<detection>" test=<technique>`: to the journal on ubuntu-desktop-01 (`index=infra`) or the pod log (`index=k8s`). *Splunk - detection validation failed* (`monitoring_stack/splunk/apps/85_homelab_ops`) runs hourly, takes the markers 40 to 100 minutes old (so each run is checked exactly once, after two runs of each detection), and posts to Slack for every expected detection with no finding containing `art-validation` since.

The findings themselves still land in `index=security`, but the security agent skips findings that contain `art-validation` and come from these two hosts (`homelab_agents/image/app/queues.py`), so test runs don't reach Slack as incidents.

Detections suppress repeats for 1 to 24 hours. A second run within that time finds its findings suppressed and is reported as failed; leave a day between manual runs.

## Running it by hand

```bash
# ubuntu-desktop-01
systemctl start art-validation && journalctl -u art-validation -n 20 --no-pager
# k3s-01
kubectl -n art-validation create job --from=cronjob/art-validation art-validation-manual-$(date +%s)
```

## Install

- **Kubernetes:** `kubectl apply -f infrastructure_tooling/art_validation/application.yaml` (Argo CD, applied once).
- **Host:** copy `host/` to ubuntu-desktop-01 and run `host/install.sh` as root.

## Adding a test

Add it to `host/art-validation` or `k8s/art-validation.sh` with a `mark "<exact detection name>" <technique>` line before it. It must be harmless, clean up after itself (the `cleanup` trap runs even on failure), and put `art-validation` somewhere in what the detection reports. For a Kubernetes test, give `art-runner` the one permission it needs in `k8s/rbac.yaml`.
