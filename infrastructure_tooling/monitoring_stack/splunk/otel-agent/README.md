# OTel host agent

The OpenTelemetry Collector (contrib, 0.163.2) as a systemd service on VMs and Proxmox nodes. It sends to Cribl's HEC input on security-tooling-01 (`http://192.168.4.11:8088`), and Cribl picks the Splunk index.

| Role | Config | Collects | Splunk index |
|---|---|---|---|
| `base` | `base.yaml` | journald (info and above): sshd, sudo, logind, systemd, kernel, Proxmox services | `infra` |
| `docker` | `base.yaml` + `docker.yaml` | the above, plus every container's stdout/stderr | `apps` for containers |

Container logs carry `container.name`, `container.image.name`, `compose.project`, and `compose.service`. They come from the Docker API through the `docker_observer` extension, with `include_all_containers: true` and a `port == 0` rule. That combination is the documented way to get one log receiver per container; without it the observer makes one per exposed port and duplicates logs. Docker itself is not reconfigured. Splunk and Cribl container logs are excluded because they already reach Splunk.

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

## Deployed (2026-10-22)

| Host | Role |
|---|---|
| proxmox, proxmox2, proxmox3 | base |
| security-tooling-01, docker-compose-02, docker-compose-03, openclaw-claude-01, splunk-docker-compose-mcp | docker |
| github-action-runner, ubuntu-desktop-01, k3s-01 | base (k3s-01 pod logs come from `infrastructure_tooling/otel_collector_k3s`) |

Not yet covered:

- coding-box: Arch Linux, which needs the tarball rather than the `.deb`.
- siem-agent-test-vm: excluded deliberately, because it is a fixed test environment.
