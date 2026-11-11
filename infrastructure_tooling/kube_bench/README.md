# kube-bench on k3s-01

Checks k3s-01 against the CIS Kubernetes Benchmark, k3s profile (`k3s-cis-1.9`): API server, controller and scheduler flags, kubelet settings, file permissions and ownership, and RBAC and service-account policy. It reports configuration drift that the audit log and Falco can't see, because nothing *happens*: a setting is simply weaker than it should be.

It runs **on the host**, monthly (1st of the month, 07:00 UTC) through a systemd timer. Not as a pod: the k3s profile reads `journalctl -u k3s` and files under `/var/lib/rancher/k3s`, which a container can't see properly (most checks come back as warnings there).

- `install.sh` downloads the pinned release, checks its SHA-256 against the release's checksums file, and installs it to `/usr/local/lib/kube-bench` with `kube-bench.service` and `kube-bench.timer`. Run as root on k3s-01 from a copy of this directory. To upgrade, change `VERSION` and `SHA256`. Renovate lists new releases on the Dependency Dashboard (issue #2) and opens the PR when you tick it there; push the new `SHA256` to the PR branch before merging.
- Output: one line per check to the journal (identifier `kube-bench`), shipped by k3s-01's OTel agent. Cribl's `hec_in` sends it to `index=security` as `sourcetype=kube-bench`; `monitoring_stack/splunk/apps/85_k8s_posture` extracts `status` (PASS, FAIL, WARN, INFO), `check_id` and `check`.
- *Kubernetes - CIS benchmark failures* (`85_homelab_ops`) posts the FAIL list to Slack after each monthly run.
- Run it now (e.g. after a k3s upgrade): `systemctl start kube-bench && journalctl -u kube-bench -n 120 --no-pager`.

First run, 2026-11-04 (k3s v1.31.5): 58 PASS, 5 FAIL, 53 WARN, 14 INFO. Most WARN are manual-review checks. 1.2.26 (`--etcd-cafile`) and the etcd section don't apply: k3s-01 uses SQLite, not etcd.

It lives on the host, not in Argo CD (like `../k3s_audit`), so re-run `install.sh` after rebuilding k3s-01.
