# Falco

Falco 0.45.1 runtime security: kernel syscalls observed through the modern eBPF probe, matched against the default `falco_rules` set. It only detects; nothing is blocked.

Alerts become OCSF Detection Findings in Splunk `index=security`. They carry MITRE ATT&CK techniques from the rule tags and severity from the Falco priority.

```
VM:      falco (systemd) --syslog--> journald --> OTel host agent --+
k3s-01:  falco (DaemonSet) --stdout--> pod log --> OTel DaemonSet --+--> Cribl hec_in --> cc-falco-ocsf --> index=security
```

| Where | How | Config |
|---|---|---|
| VMs: security-tooling-01, github-action-runner, openclaw-claude-01, docker-compose-02, docker-compose-03, splunk-docker-compose-mcp, ubuntu-desktop-01 | `install.sh` (deb, systemd `falco-modern-bpf`) | `config.d/homelab.yaml`, `rules.d/*.yaml` |
| Proxmox nodes: proxmox, proxmox2, proxmox3 (Debian 13) | `install.sh`, as on the VMs | the same |
| k3s-01 | Argo CD app `falco-k3s` (`infrastructure_tooling/falco_k3s`) | the same rules, inlined in `values.yaml` as `customRules` |
| k3s-01 API audit | `falco-k8saudit` Deployment in the same app, fed by the apiserver audit webhook ([`k3s_audit`](../../k3s_audit/README.md)) | the pinned `k8saudit-rules` set |

## Install or update a host

```bash
scp -r infrastructure_tooling/monitoring_stack/falco root@<host>:/root/falco-install
ssh root@<host> /root/falco-install/install.sh
```

`install.sh` sets up the host as follows:

- **Package:** adds Falco's signed apt repository, then installs `falco=0.45.1` and holds it at that version.
- **Driver:** `modern_ebpf`, so there is no kernel module. It needs kernel 5.8+ with BTF, which every homelab host has.
- **Updates:** `falcoctl` artifact auto-updates are off, so the rules in effect are the ones in this repo.
- **Validation:** runs `falco --dry-run` before restarting the service.

## Configuration choices (`config.d/homelab.yaml`)

- **`priority: notice`.** The packaged default is `debug`.
- **Alerts go through syslog, not stdout.** Under systemd, Falco's stdout was block-buffered, and alerts never reached journald. They show up immediately in the foreground and in a container, where the k3s chart keeps stdout.
- **`webserver.enabled: false`.** Its default `0.0.0.0:8765` collided with rea-bridge on security-tooling-01, and nothing polls it. Falco opens no ports on the VMs.
- **`append_output` adds `proc.pid`, `proc.ppid` and `container.image.id`** to every syscall alert. OCSF requires process PIDs and an image ID, and Falco only emits them when a rule's output mentions them.

## Tuning (`rules.d/homelab_exceptions.yaml`)

Each exception is scoped to a known-good image, process or namespace. The rule still fires for everything else. Record the evidence for every new exception in the file.

| Rule | Exception | Why |
|---|---|---|
| Read sensitive file untrusted | `postgres` image + `pg_isready`; `systemd-sysusers`; `pkexec` running the update notifier | The guardrail-postgres healthcheck reads `/etc/shadow` every 5 s; package installs create users (hosts and CI image builds); ubuntu-desktop-01's GNOME update notifier runs pkexec every 2 minutes |
| Run shell untrusted | `splunk/splunk` image, parent `splunkd`/python; in arc-runners, Steampipe's embedded `postgres` | Splunk runs its own helper scripts; GuardRail CI's Steampipe runs initdb, which spawns `sh` |
| Drop and execute new binary in container | `arc-runners` namespace; BuildKit's `/tmp/qemu-check*/check` | Ephemeral CI containers download tools and apt-install compilers, then run them: opengrep-core about 350 times a minute, gcc/cc1/as hundreds of times per Rust build. All other rules still apply to the runners. BuildKit's QEMU probe runs once per build inside DinD, where there is no k8s metadata |
| Clear Log Activities | `dockerd` under `/var/lib/docker/`, `buildkitd` under `/var/lib/buildkit/` | Unpacking image layers truncates `var/log/dpkg.log` inside snapshots (DinD and image builds in arc-runners) |
| Packet socket created in container | `dockerd` in the `docker.io/library/docker` image | The DinD sidecar in arc-runners sets up networking for each CI job's containers |
| Contact K8S API Server From Container | Infrastructure namespaces: argocd, otel-collector, kube-system, external-secrets, cert-manager, arc-systems, toolhive-system, falco, vault; `kubectl proxy` in toolhive-mcp | Controllers use the API by design. The kubectl MCP server's proxy reopens its pooled connection hourly, and Falco can't see the requests sent through it |

Application namespaces (apiary, alexprinter, stirling-pdf, uptime-kuma) still alert on API contact. Apiary alerts only when its pod starts, so it is left alerting. Falco can't show what an agent does through the kubectl MCP server; that is in the k3s API audit log (`index=audit sourcetype=kube:apiserver:audit`), with Falco's Kubernetes audit rules running on it (`falco-k8saudit`).

## Next

- **Custom AI-agent rules:** issue #176, once the defaults are quiet.
- **coding-box:** needs an Arch install path (no deb).
- **Optional plugins:** `github`.
