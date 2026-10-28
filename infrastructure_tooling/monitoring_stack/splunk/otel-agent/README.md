# OTel host agent

The OpenTelemetry Collector (contrib, 0.163.2) as a systemd service on VMs and Proxmox nodes. It sends to Cribl's HEC input on security-tooling-01 (`http://192.168.4.11:8088`), and Cribl picks the Splunk index.

| Role | Config | Collects | Splunk index |
|---|---|---|---|
| `base` | `base.yaml` | journald (info and above): sshd, sudo, logind, systemd, kernel, Proxmox services. Host metrics | `infra`; metrics to `metrics` |
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
