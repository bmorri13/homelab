# Falco

Falco runtime security: kernel syscalls observed through the modern eBPF probe, matched against Falco's stable rules, a selection of its incubating and sandbox rules, and the homelab rules in `rules.d`. It only detects; nothing is blocked.

Alerts become OCSF Detection Findings in Splunk `index=security`. They carry MITRE ATT&CK techniques (and MITRE ATLAS techniques for the AI agent rules) from the rule tags and severity from the Falco priority.

```
VM:      falco (systemd) --syslog--> journald --> OTel host agent --+
k3s-01:  falco (DaemonSet) --stdout--> pod log --> OTel DaemonSet --+--> Cribl hec_in --> cc-falco-ocsf --> index=security
```

| Where | How | Config |
|---|---|---|
| VMs: security-tooling-01, github-action-runner, openclaw-claude-01, docker-compose-02, docker-compose-03, splunk-docker-compose-mcp, ubuntu-desktop-01 | `install.sh` (deb, systemd `falco-modern-bpf`) | `config.d/*.yaml`, `rules.d/*.yaml` |
| Proxmox nodes: proxmox, proxmox2, proxmox3 (Debian 13) | `install.sh`, as on the VMs | the same |
| k3s-01 | Argo CD app `falco-k3s` (`infrastructure_tooling/falco_k3s`) | the same `rules.d/*.yaml` and `config.d/homelab-rules.yaml`, through `templates/homelab-rules.yaml` (`rules.d` and `config.d` there are symlinks to this directory); output settings in `values.yaml` |
| k3s-01 API audit | `falco-k8saudit` Deployment in the same app, fed by the apiserver audit webhook ([`k3s_audit`](../../k3s_audit/README.md)) | the pinned `k8saudit-rules` set, with overrides in `values.yaml` (`customRules`) |

## Install or update a host

```bash
rsync -a --delete infrastructure_tooling/monitoring_stack/falco/ root@<host>:/root/falco-install/
ssh root@<host> /root/falco-install/install.sh
```

The host needs `rsync` (`apt-get install rsync`). Don't use `scp -r` here: once `/root/falco-install` exists, it copies into `/root/falco-install/falco` and the old `install.sh` and rules run.

`install.sh` sets up the host as follows:

- **Package:** adds Falco's signed apt repository, then installs `falco=$VERSION` (pinned in `install.sh`) and holds it at that version. The repository is GPG-signed, but the Arch path below pins a tarball hash, so Renovate's PR for a new Falco release waits for approval on the Dependency Dashboard (issue #2): approve it, set `SHA256_ARCH` on the PR branch (steps beside it in `install.sh`), and merge. Merging changes nothing until `install.sh` is re-run on each host.
- **Driver:** `modern_ebpf`, so there is no kernel module. It needs kernel 5.8+ with BTF, which every homelab host has.
- **Updates:** `falcoctl` artifact auto-updates are off, so the rules in effect are the ones in this repo.
- **Rules:** copies `config.d/*.yaml` and `rules.d/*.yaml` to `/etc/falco`. `/etc/falco/rules.d` is synced: a file removed or renamed here is removed from the host.
- **Validation:** runs `falco --dry-run` before restarting the service.

### Arch Linux

On an Arch-based host (`ID` or `ID_LIKE` in `/etc/os-release` is `arch`), `install.sh` takes Falco from the release tarball instead (`falco-$VERSION-x86_64.tar.gz` from `download.falco.org/packages/bin/x86_64/`), checked against `SHA256_ARCH`. Falco publishes no checksum file for it, only a GPG signature (`.asc`) made with the same key as the apt repository; the pinned hash was taken from a tarball whose signature verified. It installs what the package would, minus falcoctl and the kernel module sources:

- `/usr/bin/falco`, `/usr/share/falco/plugins/libcontainer.so` (Falco looks for plugins in that directory), and `/etc/falco/{falco.yaml,falco_rules.yaml,config.d/falco.container_plugin.yaml}`, replaced on each upgrade.
- `/etc/systemd/system/falco-modern-bpf.service`: the package's unit (`scripts/systemd/falco-modern-bpf.service` at the same tag) without the `falcoctl-artifact-follow` ordering. Same name, same `ExecStart`, same hardening, `StandardOutput=null`, so alerts reach journald through syslog exactly as on the Debian hosts.

The rest (config, rules, dry run, enable and restart) is shared. From a checkout, on the host:

```bash
sudo infrastructure_tooling/monitoring_stack/falco/install.sh
```

To uninstall:

```bash
sudo systemctl disable --now falco-modern-bpf
sudo rm -f /etc/systemd/system/falco-modern-bpf.service /usr/bin/falco
sudo rm -rf /usr/share/falco /etc/falco
sudo systemctl daemon-reload
```

## Rules

### Layout and load order

Falco reads a rules directory in file-name order, and a list or macro must be defined before a rule uses it, while an override must come after the rule it changes. So the files are split by purpose, with numeric prefixes that make the order explicit. One file per rule would make the order depend on rule names.

| Order | File | What |
|---|---|---|
| first | `/etc/falco/falco_rules.yaml` | Falco's stable rules (25), shipped with the package (falco-rules 5.2.0 in Falco 0.45.2; on k3s-01 falcoctl installs the same version) |
| 10 | `rules.d/10-falco-incubating-rules.yaml` | upstream incubating rules, vendored unchanged ([below](#upstream-rules)) |
| 11 | `rules.d/11-falco-sandbox-rules.yaml` | upstream sandbox rules, vendored unchanged |
| 20 | `rules.d/20-homelab-macros.yaml` | lists and macros shared by the homelab rules (`ai_agent_procs`, `ai_agent_proc`, ...) |
| 30 | `rules.d/30-ai-agents.yaml` | [AI agent rules](#ai-agent-rules-rulesd30-ai-agentsyaml) |
| 40 | `rules.d/40-host.yaml` | homelab host rules for any process |
| 80 | `rules.d/80-upstream-exceptions.yaml` | [tuning](#tuning-rulesd80-upstream-exceptionsyaml) of upstream rules, loaded after every rule it changes |

`config.d/homelab-rules.yaml` sets `rule_matching: all` and the rule selection. With Falco's default (`first`), the first matching rule hides every later rule for the same event, so an AI agent rule could be silenced by a stable rule on the same file open, or a trial rule could silence one that pages. `all` evaluates every enabled rule (Falco notes a possible CPU cost; check `falco` CPU on a busy host after rollout).

The k8saudit overrides load in a different Falco instance (`falco-k8saudit`) and stay in `falco_k3s/values.yaml`. The syscall files are not mounted there, and the k8saudit overrides are not mounted in the DaemonSet.

The layout follows Falco's guidance: rules files load in the order given and a directory's files alphabetically (`rules_files` in the packaged `falco.yaml`), [overrides](https://falco.org/docs/concepts/rules/overriding/) append to a rule after it is loaded, the [style guide](https://falco.org/docs/concepts/rules/style-guide/) (key order, `evt.type` filter first, output as a sentence then `key=%field`, tags with host/container, `mitre_<tactic>` and a technique), and [controlling rules](https://falco.org/docs/concepts/rules/controlling-rules/) (the config's `rules:` selectors). The homelab rules carry no maturity tag on purpose: the selection turns rules off by maturity tag.

### Upstream rules

`upstream-rules.sh` vendors `falco-incubating_rules.yaml` and `falco-sandbox_rules.yaml` from [falcosecurity/rules](https://github.com/falcosecurity/rules) at a pinned tag (`falco-incubating-rules-6.0.1`, `falco-sandbox-rules-6.2.0`, the newest compatible with Falco's engine 0.65.0) and checks each against the SHA-256 beside the version. The files are committed unchanged, so Falco loads them offline and the rules in effect are the ones in this repo; CI re-downloads them and fails if the copies differ.

Every rule in those files is loaded, then `config.d/homelab-rules.yaml` disables the `maturity_incubating` and `maturity_sandbox` tags and enables these by name:

- Incubating: Modify Shell Configuration File, Schedule Cron Jobs, Adding ssh keys to authorized_keys, Launch Privileged Container, Set Setuid or Setgid bit, Delete or rename shell history, Read environment variable from /proc files, Backdoored library loaded into SSHD (CVE-2024-3094), Launch Suspicious Network Tool on Host.
- Sandbox: Netcat/Socat Remote Code Execution on Host, Launch Sensitive Mount Container, Known Cryptominer Process Executed, Detect crypto miners using the Stratum protocol, Network Tool Executed During NPM Package Install, Web Server Spawned Shell, Reverse Shell from Web Server.

The stable rules and the homelab rules are untouched by the selection. The vendored files redefine `python_package_managers` without `uv`, which would change the stable rules too; `80-upstream-exceptions.yaml` appends it back.

To enable another upstream rule, add an `enable` entry with its exact name, add it to the trial macro below, and run `validate-rules.sh`. To move to a new upstream release, approve Renovate's PR on the Dependency Dashboard, run `./upstream-rules.sh` on the branch, review `git diff rules.d`, set the SHA-256 it printed, and run `validate-rules.sh`.

### Trial period for new upstream rules

Newly enabled upstream rules are indexed like every other finding, but kept out of the *Falco - runtime finding* alert (and so out of the triage agents' queue and Slack) by the macro `falco_trial_rules` in `splunk/apps/85_falco/default/macros.conf`. To graduate a rule: look at its findings over the trial (`index=security sourcetype=falco:ocsf finding_info.title="<rule>"`), add exceptions for the known noise to `80-upstream-exceptions.yaml`, then remove its name from the macro. The homelab rules page from the start.

### Adding a rule or an exception

- A homelab rule goes in `30-ai-agents.yaml` (anything about agent processes, tagged `ai_agent`) or `40-host.yaml`. Keep the rule's key order (`rule`, `desc`, `condition`, `output`, `priority`, `tags`), start the condition with the event type (`spawned_process`, `open_read`, ...), write the output as a sentence then `key=%field` pairs, and tag it `host`/`container`, a type tag, `mitre_<tactic>`, the ATT&CK v20 technique and, for agent rules, the ATLAS v5.6.0 technique (`AML.T####`) if one applies. Shared lists and macros go in `20-homelab-macros.yaml`. Don't rename rules: Splunk searches, the runbook and the detection validation refer to them.
- An exception to an upstream rule goes in `80-upstream-exceptions.yaml` as an `override: {condition: append}` of `and not (...)`, scoped to a process, image, namespace or path, with the evidence in a comment. Upstream rules define no `exceptions` blocks (the style guide keeps them out of upstream rules), so a condition append is the one mechanism that works for all of them. Never edit the vendored files.
- Falco's `regex` operator is RE2 in POSIX mode and must match the whole string: use `[[:space:]]` instead of `\s`, start with `.*` to match mid-string, and bracket special characters (`[|]`, `[(]`). Each quoted value is limited to 256 characters; split longer patterns.

### Validating

```bash
infrastructure_tooling/monitoring_stack/falco/validate-rules.sh
```

It downloads the Falco release pinned in `install.sh` (checked against `SHA256_ARCH`) and, without root: loads the stable rules and `rules.d` in load order and fails on any error or warning; runs a dry run with the rule selection and checks the enabled rules are exactly the stable ones, the selected upstream ones and every homelab rule; and validates the k8saudit overrides against the k8saudit plugin's rules. The *Falco rules* workflow (`.github/workflows/falco-rules.yml`) runs it, plus `upstream-rules.sh`, on every change here or in `falco_k3s`.

### k3s-01

The `falco-k3s` chart renders `rules.d/*.yaml` into the ConfigMap `falco-homelab-rules` (mounted at `/etc/falco/rules.d`) and `config.d/homelab-rules.yaml` into `falco-homelab-config` (mounted at `/etc/falco/homelab.d`, listed in `config_files`). Rendering fails if the symlinks don't resolve. A change to the rules updates the ConfigMaps but not the pod template, so restart the DaemonSet after Argo CD syncs it: `kubectl -n falco rollout restart daemonset/falco`.

## Configuration choices (`config.d/homelab.yaml`)

- **`priority: notice`.** The packaged default is `debug`.
- **Alerts go through syslog, not stdout.** Under systemd, Falco's stdout was block-buffered, and alerts never reached journald. They show up immediately in the foreground and in a container, where the k3s chart keeps stdout.
- **`webserver.enabled: false`.** Its default `0.0.0.0:8765` collided with rea-bridge on security-tooling-01, and nothing polls it. Falco opens no ports on the VMs.
- **`append_output` adds `proc.pid`, `proc.ppid` and `container.image.id`** to every syscall alert. OCSF requires process PIDs and an image ID, and Falco only emits them when a rule's output mentions them.

## Splunk alerts (`splunk/apps/85_falco`)

Both run every 5 minutes, appear under Triggered Alerts, and are suppressed per host and rule for an hour.

- *Falco - AI agent finding*: any finding from the AI agent rules (below).
- *Falco - runtime finding*: any other finding at Warning or above (OCSF severity medium, high or critical), from the VMs, k3s-01 and the k8saudit rules, except the upstream rules on trial (macro `falco_trial_rules`). Notice-level findings stay searchable in `index=security` without alerting.

## Tuning (`rules.d/80-upstream-exceptions.yaml`)

Each exception is scoped to a known-good image, process or namespace. The rule still fires for everything else. Record the evidence for every new exception in the file.

| Rule | Exception | Why |
|---|---|---|
| Read sensitive file untrusted | `postgres` image + `pg_isready`; `systemd-sysusers`; `pkexec` running the update notifier; `pvedaemon worke` reading `/etc/pam.d/` | The guardrail-postgres healthcheck reads `/etc/shadow` every 5 s; package installs create users (hosts and CI image builds); ubuntu-desktop-01's GNOME update notifier runs pkexec every 2 minutes; Proxmox's pvedaemon workers read PAM config for every API or web login (about 40 a week on one node) |
| Run shell untrusted | `splunk/splunk` image, parent `splunkd`/python; in arc-runners, Steampipe's embedded `postgres` | Splunk runs its own helper scripts; GuardRail CI's Steampipe runs initdb, which spawns `sh` |
| Drop and execute new binary in container | `arc-runners` namespace; BuildKit's `/tmp/qemu-check*/check` | Ephemeral CI containers download tools and apt-install compilers, then run them: opengrep-core about 350 times a minute, gcc/cc1/as hundreds of times per Rust build. All other rules still apply to the runners, apart from the one CI fixture check below. BuildKit's QEMU probe runs once per build inside DinD, where there is no k8s metadata |
| Search Private Keys or Passwords | In `arc-runners`, only `grep -q BEGIN PRIVATE KEY /tmp/tmp.*` | A CI test suite checks its own redaction fixtures this way (20 alerts in 3 days). Any other key search, in the runners or elsewhere, still alerts |
| Clear Log Activities | `dockerd` under `/var/lib/docker/`, `buildkitd` under `/var/lib/buildkit/` | Unpacking image layers truncates `var/log/dpkg.log` inside snapshots (DinD and image builds in arc-runners) |
| Packet socket created in container | `dockerd` in the `docker.io/library/docker` image | The DinD sidecar in arc-runners sets up networking for each CI job's containers |
| Launch Privileged Container | In arc-runners, the `docker.io/library/docker` image; in kube-system, local-path `helper-pod-*` pods (`mirrored-library-busybox`) | The cointhieves runner set's DinD sidecar is privileged and starts with every CI job; k3s's local-path provisioner runs a privileged helper pod per volume. Falco's own DaemonSet is allowed by the rule itself |
| Contact K8S API Server From Container | Infrastructure namespaces: argocd, otel-collector, kube-system, external-secrets, cert-manager, arc-systems, toolhive-system, falco, vault; `kubectl proxy`, `thv-proxyrunner` and `vmcp` in toolhive-mcp; the runner hook `node /home/runner/k8s/index.js` in arc-runners | Controllers use the API by design. The kubectl MCP server's proxy reopens its pooled connection hourly, and Falco can't see the requests sent through it. ToolHive's proxy runner manages its MCP pod and vMCP discovers backends. ARC runners in Kubernetes mode run job steps as pods through the hook (1,858 alerts in two days) |

The k8saudit rules (`falco-k8saudit` in `falco_k3s/values.yaml`) have their own exceptions: k3s's control-plane identities are allowed users, and *Pod Created in Kube Namespace* ignores the local-path provisioner's helper pods. The audit log records pods at Metadata level, so that rule's image allow-list can never match, and the exception is by service account. *Attach/Exec Pod* ignores the ARC homelab runner set's service account exec'ing into its own `-workflow` job pods (Kubernetes container mode runs every job step that way).

Application namespaces (apiary, alexprinter) still alert on API contact. Apiary alerts only when its pod starts, so it is left alerting. Falco can't show what an agent does through the kubectl MCP server; that is in the k3s API audit log (`index=audit sourcetype=kube:apiserver:audit`), with Falco's Kubernetes audit rules running on it (`falco-k8saudit`).

## AI agent rules (`rules.d/30-ai-agents.yaml`)

Detections for anything an AI agent runs, issue #176. A process counts as an agent's if an ancestor is named `claude`, `codex`, `opencode`, `openclaw` (or its gateway, `openclaw-gatewa`) or `hermes`, so the commands an agent runs are covered as well as the agent. Every rule is tagged `ai_agent`: `index=security sourcetype=falco:ocsf finding_info.types{}=ai_agent`. Where one applies, a rule also carries a MITRE ATLAS v5.6.0 technique, which the pack maps to `finding_info.attacks[]` with `version` `atlas-5.6.0`.

| Rule | Priority | ATLAS | Fires when an agent process |
|---|---|---|---|
| AI agent read credential file | Warning | AML.T0055 | opens SSH private keys, AWS/gcloud/Azure credentials, `gh` tokens, kubeconfig or `/etc/rancher/k3s/k3s.yaml`, Docker config, `.netrc`, `.git-credentials`, `.vault-token`, `terraform.tfstate*`/`*.tfvars`, `.npmrc`, `.pypirc`, Tailscale state, shell history, another process's `/proc/<pid>/environ`, a service-account token, another agent's auth file, a `.pem`/`.key`, or `/etc/shadow`. Each tool reading its own credentials (ssh/git the SSH keys, kubectl the kubeconfig, terraform the state, npm the `.npmrc`, kubectl/helm the in-cluster token, each agent its own auth file, ...) and CA bundles anywhere are exempt |
| AI agent printed a .env file | Warning | AML.T0055 | reads a `.env` file with the agent's own file tools, or with a tool that prints, searches, copies or edits it (`cat`, `grep`, `sed`, `awk`, `jq`, `cp`, `dd`, editors, ...), or an inline interpreter one-liner (`python -c`, `node -e`). Dev servers and scripts loading `.env` through dotenv, and templates (`.env.example`, `.env.sample`, `.env.template`, `.env.dist`), are not matched |
| AI agent piped a download into a shell | Warning | AML.T0053 | pipes `curl`/`wget` into a shell or interpreter (optionally through `sudo` or `env`), runs `sh <(curl ...)` or `sh -c "$(curl ...)"`, or downloads a file and runs or `chmod +x`es it in the same command |
| AI agent wrote a persistence file | Warning | | writes or moves into place a shell startup file (bash, zsh, fish, `/etc/profile.d`), cron or at job, systemd unit (system or user), autostart entry, `/etc/ld.so.preload`, `/etc/rc.local`, init script, SSH server/client config, `authorized_keys(2)`, passwd/shadow/group, PAM, sudoers, udev rule, or a git hook (not git's `.sample` templates, pre-commit or lefthook) |
| AI agent changed agent configuration | Warning | AML.T0081 | writes Claude Code settings, hooks, `.mcp.json` or managed settings (`~/.claude.json` and `settings.local.json` only when not written by claude itself), Codex `config.toml`/`hooks.json`, OpenCode config or plugins, Hermes `config.yaml`, OpenClaw `openclaw.json`. Transcripts, sessions, memory and caches are not matched |
| AI agent outbound connection on unusual port | Notice | | connects to a public address on a port other than 22, 53, 80 or 443. Private, CGNAT, loopback, link-local and IPv6 ULA (Tailscale) ranges are excluded, as is UDP to port 0 (getaddrinfo sorting candidate addresses; nothing is sent) |
| AI agent ran a destructive command | Notice | AML.T0101 | runs a force push or remote branch delete, `terraform`/`tofu` apply or destroy, `kubectl delete`, `helm uninstall`, `docker` prune or `rm -f`, `qm`/`pct destroy`, a Vault or Argo CD delete, `gh repo delete`, `mkfs`, `dd` onto a block device, `shred` outside `/tmp`, or `rm -r` of a top-level or home directory. The verb is matched after any options (`kubectl -n x delete`, `git -C dir push --force`); `terraform plan -destroy` is not matched |
| AI agent started a reverse shell | Critical | AML.T0072 | runs `nc`/`ncat` with a program, `socat` with `exec:`/`system:`, a shell using `/dev/tcp` or a fifo with a network tool, or a Python/Perl/Ruby/PHP/awk/Node socket-to-shell one-liner |
| AI agent redirected standard streams to a network connection | Critical | AML.T0072 | duplicates a network socket onto stdin, stdout or stderr (`dup`/`dup2`/`dup3`), whatever the language, on hosts and in containers |
| AI agent uploaded a file or data | Notice | AML.T0086 | sends a file with `curl` (`-d @f`, `--data-binary @f`, `-F x=@f`, `-T`), `wget --post-file`, `gh gist create`, `rclone` to a remote, `scp`/`rsync` to a remote host, or netcat fed from a file. Uploads to localhost are not matched |
| AI agent stopped or changed a security tool | Warning | | stops, disables, masks or kills Falco, the OTel collector or auditd, or writes `/etc/falco`, `/etc/otelcol-contrib` or `/etc/audit` |
| AI agent used sudo or changed user | Notice | AML.T0053 | runs `sudo`, `su`, `doas`, `pkexec` or `run0` |

The rules run on every Falco host and on k3s-01, where the homelab agent CronJobs (`toolhive-clients`) run the Claude Agent SDK and its bundled `claude`. Agents on Falco hosts: Hermes and OpenClaw on openclaw-claude-01; coding-box, where Claude Code, Codex and OpenCode run, installs through the Arch path.

The Splunk alert *Falco - AI agent finding* (app `85_falco`) runs every 5 minutes, appears under Triggered Alerts, and is suppressed per host and rule for an hour.

To test, copy `/bin/bash` to a file named `claude` and run harmless versions of the commands through it, for example `./claude -c 'cat /tmp/falco-test/.aws/credentials; true'` after creating that dummy file. The `; true` matters: bash runs a single command with exec, which drops the `claude` ancestor. The PR that introduced this layout lists a test command for each rule.

## Host rules (`rules.d/40-host.yaml`)

| Rule | Priority | Fires when |
|---|---|---|
| Tunnelling or proxy tool executed | Warning | chisel, SoftEther (`vpnclient`, `vpnbridge`, `vpnserver`, `vpncmd`), `tor`, `frpc`/`frps`, `ngrok`, `gost`, `ligolo-agent`/`ligolo-proxy`, `iox`, `rathole` or `bore` starts, or `cloudflared tunnel --url` (a quick tunnel; the named-tunnel containers run `tunnel run` with a token and are not matched) |

## Next

- **Optional plugins:** `github`.
