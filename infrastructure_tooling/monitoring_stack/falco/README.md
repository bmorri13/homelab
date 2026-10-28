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

## Splunk alerts (`splunk/apps/85_falco`)

Both run every 5 minutes, appear under Triggered Alerts, and are suppressed per host and rule for an hour.

- *Falco - AI agent finding*: any finding from the AI agent rules (below).
- *Falco - runtime finding*: any other finding at Warning or above (OCSF severity medium, high or critical), from the VMs, k3s-01 and the k8saudit rules. Notice-level findings stay searchable in `index=security` without alerting.

## Tuning (`rules.d/homelab_exceptions.yaml`)

Each exception is scoped to a known-good image, process or namespace. The rule still fires for everything else. Record the evidence for every new exception in the file.

| Rule | Exception | Why |
|---|---|---|
| Read sensitive file untrusted | `postgres` image + `pg_isready`; `systemd-sysusers`; `pkexec` running the update notifier | The guardrail-postgres healthcheck reads `/etc/shadow` every 5 s; package installs create users (hosts and CI image builds); ubuntu-desktop-01's GNOME update notifier runs pkexec every 2 minutes |
| Run shell untrusted | `splunk/splunk` image, parent `splunkd`/python; in arc-runners, Steampipe's embedded `postgres` | Splunk runs its own helper scripts; GuardRail CI's Steampipe runs initdb, which spawns `sh` |
| Drop and execute new binary in container | `arc-runners` namespace; BuildKit's `/tmp/qemu-check*/check` | Ephemeral CI containers download tools and apt-install compilers, then run them: opengrep-core about 350 times a minute, gcc/cc1/as hundreds of times per Rust build. All other rules still apply to the runners, apart from the one CI fixture check below. BuildKit's QEMU probe runs once per build inside DinD, where there is no k8s metadata |
| Search Private Keys or Passwords | In `arc-runners`, only `grep -q BEGIN PRIVATE KEY /tmp/tmp.*` | A CI test suite checks its own redaction fixtures this way (20 alerts in 3 days). Any other key search, in the runners or elsewhere, still alerts |
| Clear Log Activities | `dockerd` under `/var/lib/docker/`, `buildkitd` under `/var/lib/buildkit/` | Unpacking image layers truncates `var/log/dpkg.log` inside snapshots (DinD and image builds in arc-runners) |
| Packet socket created in container | `dockerd` in the `docker.io/library/docker` image | The DinD sidecar in arc-runners sets up networking for each CI job's containers |
| Contact K8S API Server From Container | Infrastructure namespaces: argocd, otel-collector, kube-system, external-secrets, cert-manager, arc-systems, toolhive-system, falco, vault; `kubectl proxy`, `thv-proxyrunner` and `vmcp` in toolhive-mcp; the runner hook `node /home/runner/k8s/index.js` in arc-runners | Controllers use the API by design. The kubectl MCP server's proxy reopens its pooled connection hourly, and Falco can't see the requests sent through it. ToolHive's proxy runner manages its MCP pod and vMCP discovers backends. ARC runners in Kubernetes mode run job steps as pods through the hook (1,858 alerts in two days) |

The k8saudit rules (`falco-k8saudit` in `falco_k3s/values.yaml`) have their own exceptions: k3s's control-plane identities are allowed users, and *Pod Created in Kube Namespace* ignores the local-path provisioner's helper pods. The audit log records pods at Metadata level, so that rule's image allow-list can never match, and the exception is by service account.

Application namespaces (apiary, alexprinter, stirling-pdf, uptime-kuma) still alert on API contact. Apiary alerts only when its pod starts, so it is left alerting. Falco can't show what an agent does through the kubectl MCP server; that is in the k3s API audit log (`index=audit sourcetype=kube:apiserver:audit`), with Falco's Kubernetes audit rules running on it (`falco-k8saudit`).

## AI agent rules (`rules.d/ai_agents.yaml`)

Detections for anything an AI agent runs, issue #176. A process counts as an agent's if an ancestor is named `claude`, `codex`, `opencode`, `openclaw` or `hermes`, so the commands an agent runs are covered as well as the agent. Every rule is tagged `ai_agent`: `index=security sourcetype=falco:ocsf "AI agent"`.

| Rule | Priority | Fires when an agent process |
|---|---|---|
| AI agent read credential file | Warning | opens SSH private keys, `.aws/credentials`, `gh` tokens, kubeconfig, Docker config, `.netrc`, `.git-credentials`, another agent's auth file, a `.pem`/`.key`, or `/etc/shadow`. Each tool reading its own credentials (ssh/git the SSH keys, kubectl the kubeconfig, ...) and CA bundles in packages are exempt |
| AI agent printed a .env file | Warning | runs `cat`, `grep`, `head` and similar on a `.env` file. Code that loads `.env` is not matched |
| AI agent piped a download into a shell | Warning | runs `curl`/`wget` piped into `sh`, `bash`, `sudo` or `python` |
| AI agent wrote a persistence file | Warning | writes a shell startup file, crontab, systemd unit, `authorized_keys` or sudoers |
| AI agent outbound connection on unusual port | Notice | connects to a public address on a port other than 22, 53, 80 or 443. UDP to port 0 is excluded: that is getaddrinfo sorting candidate addresses, and nothing is sent |
| AI agent ran a destructive command | Notice | runs `git push --force`, `terraform`/`tofu` apply or destroy, or `kubectl delete` |
| AI agent started a reverse shell | Critical | runs `nc -e`/`-c`, `socat` with `exec:`/`system:`, or a shell using `/dev/tcp` |

The rules are only on the VMs; no agents run on k3s. Agents on Falco hosts today: Hermes and OpenClaw on openclaw-claude-01. coding-box, where Claude Code, Codex and OpenCode run, is deliberately not monitored.

The Splunk alert *Falco - AI agent finding* (app `85_falco`) runs every 5 minutes, appears under Triggered Alerts, and is suppressed per host and rule for an hour.

To test, copy `/bin/bash` to a file named `claude` and run harmless versions of the commands through it, for example `./claude -c 'cat ~/.aws/credentials; true'`. The `; true` matters: bash runs a single command with exec, which drops the `claude` ancestor.

## Next

- **Optional plugins:** `github`.
