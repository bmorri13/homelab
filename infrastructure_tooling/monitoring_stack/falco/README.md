# Falco

Falco 0.45.1 runtime security: kernel syscalls observed through the modern eBPF probe, matched against the default `falco_rules` set. It only detects; nothing is blocked.

Alerts become OCSF Detection Findings in Splunk `index=security`. They carry MITRE ATT&CK techniques from the rule tags and severity from the Falco priority.

```
VM:      falco (systemd) --syslog--> journald --> OTel host agent --+
k3s-01:  falco (DaemonSet) --stdout--> pod log --> OTel DaemonSet --+--> Cribl hec_in --> cc-falco-ocsf --> index=security
```

| Where | How | Config |
|---|---|---|
| security-tooling-01, github-action-runner, openclaw-claude-01 | `install.sh` (deb, systemd `falco-modern-bpf`) | `config.d/homelab.yaml`, `rules.d/*.yaml` |
| k3s-01 | Argo CD app `falco-k3s` (`infrastructure_tooling/falco_k3s`) | the same rules, inlined in `values.yaml` as `customRules` |

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
| Read sensitive file untrusted | `postgres` image, `pg_isready` | The guardrail-postgres healthcheck reads `/etc/shadow` every 5 s |
| Run shell untrusted | `splunk/splunk` image, parent `splunkd`/python | Splunk runs its own helper scripts |
| Drop and execute new binary in container | `arc-runners` namespace | Ephemeral CI containers download tools and apt-install compilers, then run them: opengrep-core about 350 times a minute, gcc/cc1/as hundreds of times per Rust build. All other rules still apply to the runners |
| Clear Log Activities | `dockerd` writing under `/var/lib/docker/` | Unpacking image layers truncates `var/log/dpkg.log` inside snapshots (the DinD sidecar in arc-runners) |
| Packet socket created in container | `dockerd` in the `docker.io/library/docker` image | The DinD sidecar in arc-runners sets up networking for each CI job's containers |
| Contact K8S API Server From Container | Infrastructure namespaces: argocd, otel-collector, kube-system, external-secrets, cert-manager, arc-systems, toolhive-system, falco, vault | Controllers use the API by design |

Application namespaces still alert on API contact. That includes the `toolhive-mcp` kubectl MCP server, where agent-driven cluster access is worth seeing.

## Next

- **Custom AI-agent rules:** issue #176, once the defaults are quiet.
- **Phase 2 hosts:** docker-compose-02/03, splunk-docker-compose-mcp, ubuntu-desktop-01, the Proxmox nodes, and coding-box (which needs the Arch install path).
- **Optional plugins:** `k8saudit` (needs a k3s audit policy, which means a k3s restart) and `github`.
