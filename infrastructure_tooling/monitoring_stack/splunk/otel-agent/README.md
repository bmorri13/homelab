# OTel host agent

The OpenTelemetry Collector (contrib, the version in `VERSION` in `install.sh`) as a systemd service on VMs and Proxmox nodes. It sends to Cribl's HEC input on security-tooling-01 over HTTPS (`https://192.168.4.11:8443`), checking the cert against the homelab CA in `homelab-ca.crt` (a copy of `toolhive_mcp/files/homelab-ca.crt`), and Cribl picks the Splunk index.

| Role | Config | Collects | Splunk index |
|---|---|---|---|
| `base` | `base.yaml` | journald (info and above): sshd, sudo, logind, systemd, kernel, Proxmox services. Host metrics, and the `falco` process's uptime (`process.uptime`, the heartbeat for *HL - Falco not running*) | `infra`; metrics to `metrics` |
| `docker` | `base.yaml` + `docker.yaml` | the above, plus every container's stdout/stderr and per-container metrics | `apps` for containers |
| `pve` | `base.yaml` + `pve.yaml` | the base role, plus Proxmox's own node, guest and storage metrics (Proxmox nodes only) | `metrics` |

Container logs carry `container.name`, `container.image.name`, `compose.project`, and `compose.service`. They come from the Docker API through the `docker_observer` extension, with `include_all_containers: true` and a `port == 0` rule. That combination is the documented way to get one log receiver per container; without it the observer makes one per exposed port and duplicates logs. Docker itself is not reconfigured. Splunk and Cribl container logs are excluded because they already reach Splunk.

## Metrics

All metrics go to the metric index `metrics` (Cribl's `hec_in` passes them through unchanged) at 60 s resolution, with OpenTelemetry semantic-convention names. What is collected follows the USE method (utilization, saturation and errors for every resource), the usual baseline for host monitoring:

| Resource | Utilization | Saturation | Errors |
|---|---|---|---|
| CPU | `system.cpu.utilization` by state | `system.cpu.load_average.*` against `system.cpu.logical.count`; `system.processes.count` (blocked) | — |
| Memory | `system.memory.utilization` (used excludes cache) | `system.paging.utilization`, `system.paging.operations` | — |
| Disk | `system.disk.io`, `system.disk.operations` | `system.disk.io_time` (busy time), `system.disk.pending_operations` | — |
| Filesystem | `system.filesystem.utilization`, `system.filesystem.usage` by state | inodes: `system.filesystem.inodes.usage` | — |
| Network | `system.network.io`, `system.network.packets` | `system.network.connections` | `system.network.errors`, `system.network.dropped` |
| Containers (`docker`) | `container.cpu.utilization`, `container.memory.usage.total` | `container.memory.percent` (of the limit) | `container.restarts` |
| Proxmox (`pve`) | `proxmox_node_*`, `proxmox_vm_cpu`, `proxmox_vm_mem`, `proxmox_storage_used`/`total` | `proxmox_vm_pressure*` (PSI: % of time stalled) | — |

- Counters are converted to per-interval deltas (`cumulativetodelta`), so in Splunk `sum()` over a span is the amount in that span: `| mstats sum(system.network.io) where index=metrics span=5m by host`.
- Excluded to keep series counts bounded: loop, device-mapper and ZFS zvol disks; pseudo, overlay, tmpfs and ISO filesystems; container veths, bridges, Proxmox tap/firewall devices and `tailscale0`; and Proxmox's per-NIC, per-storage and per-device block-stat metric names (the same values come with a dimension).
- **Proxmox:** PVE 9.2's built-in OpenTelemetry metric server (Datacenter → Metric Server, entry `otel-local`, in `/etc/pve/status.cfg`) sends to `127.0.0.1:4318` over http. The setting is cluster-wide, so each node's `pvestatd` pushes to the collector on the same node, and the listener accepts nothing from the network. Proxmox's guest memory counts the guest's page cache as used.
- Every data point also carries `extracted_host`/`extracted_source`/`extracted_sourcetype` dimensions from Cribl's metric conversion; they hold one value per series and can be ignored.
- Dashboards: `apps/85_homelab_ops` (Homelab Operations).

## Install or update

From a copy of this directory on the host, as root:

```bash
# First install: pipe the HEC token on stdin (it is stored in /etc/otelcol-contrib/hec.env, mode 600)
ssh root@security-tooling-01 'grep ^CRIBL_HEC_TOKEN= /opt/splunk-stack/.env' | ssh root@<host> '/root/otel-agent/install.sh docker'

# Later runs refresh configs and upgrade when VERSION in install.sh changes
ssh root@<host> '/root/otel-agent/install.sh base < /dev/null'
```

`install.sh` does the following:

- Checks the `.deb` against a pinned SHA-256.
- Validates the config before restarting, so a bad config never replaces a running one.
- Runs the service as root, which is needed to read the journal, the Docker socket, and `/var/lib/docker/containers`.

### Arch Linux

On an Arch-based host (`ID` or `ID_LIKE` in `/etc/os-release` is `arch`), `install.sh` uses the release tarball `otelcol-contrib_<VERSION>_linux_amd64.tar.gz` for the same `VERSION`, checked against `SHA256_ARCH`. It installs the binary as `/usr/local/bin/otelcol-contrib` (pacman doesn't own `/usr/local`) and writes `/etc/systemd/system/otelcol-contrib.service`, the unit from the `.deb` with that path. It creates no `otelcol-contrib` user: the drop-in runs the service as root on every host. Configs, drop-in, validation and restart are shared with Debian. journald and host metrics need nothing Debian-specific: the receiver runs `journalctl`, which reads `/var/log/journal` on Arch too.

Use the `base` role. From a checkout, on the host:

```bash
# First install: the token on stdin, as above
ssh root@security-tooling-01 'grep ^CRIBL_HEC_TOKEN= /opt/splunk-stack/.env' | sudo infrastructure_tooling/monitoring_stack/splunk/otel-agent/install.sh base

# Later runs
sudo infrastructure_tooling/monitoring_stack/splunk/otel-agent/install.sh base < /dev/null
```

To uninstall (the last line drops the token, the disk queue and the journald cursor):

```bash
sudo systemctl disable --now otelcol-contrib
sudo rm -rf /etc/systemd/system/otelcol-contrib.service /etc/systemd/system/otelcol-contrib.service.d /usr/local/bin/otelcol-contrib
sudo systemctl daemon-reload
sudo rm -rf /etc/otelcol-contrib /var/lib/otelcol-contrib
```

### Upgrading

Renovate watches `VERSION` (GitHub releases of `open-telemetry/opentelemetry-collector-releases`) and lists new releases on the Dependency Dashboard (issue #2), but it opens the PR only when you tick it there: it can't compute the new `SHA256`, and `install.sh` refuses a `.deb` or tarball whose hash doesn't match. After approving, set `SHA256` on the PR branch to the release's `otelcol-contrib_<version>_linux_amd64.deb.sha256` and `SHA256_ARCH` to its `otelcol-contrib_<version>_linux_amd64.tar.gz.sha256`, merge, and re-run `install.sh` on each host. The hash stays hard-coded on purpose: it pins the exact package, whereas fetching the checksum at install time would only catch a corrupt download, not a replaced release (unless the sigstore signature were verified too).

The k3s collector (`../../../otel_collector_k3s`, image tag in its `values.yaml`) is a separate install that Renovate bumps on its own. The two don't need to match: each sends to Cribl independently and neither talks to the other. Keep them within a few releases of each other so the configs, which follow the same conventions, behave alike.

Behaviour notes:

- **First start:** reading begins at the end of the logs (`start_at: end`). No history is replayed.
- **Restarts:** cursors and the send queue live in `/var/lib/otelcol-contrib/storage`, so a restart or a Cribl outage doesn't lose or duplicate logs.
- **Ports:** none are opened. The collector's self-metrics endpoint is disabled because port 8888 was already taken on some hosts.

## Deployed (2026-10-22; metrics 2026-10-28)

| Host | Role |
|---|---|
| proxmox, proxmox2, proxmox3 | pve (base until 2026-10-28) |
| security-tooling-01, docker-compose-02, docker-compose-03, openclaw-claude-01, splunk-docker-compose-mcp | docker |
| github-action-runner, ubuntu-desktop-01, k3s-01 | base (k3s-01 pod logs come from `infrastructure_tooling/otel_collector_k3s`) |

Not yet covered:

- coding-box: excluded deliberately (the user's own agent workstation).
- siem-agent-test-vm: excluded deliberately, because it is a fixed test environment.
