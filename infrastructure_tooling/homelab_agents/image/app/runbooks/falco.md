# Falco findings (runtime and AI agent rules)

Source: `index=security sourcetype=falco:ocsf`. finding_info.title = rule, device.hostname = host, evidences{}.process.cmd_line / evidences{}.process.parent_process.cmd_line, container fields for k3s pods. finding_info.attacks{} carries ATT&CK v20 techniques (version 20) and, for AI agent rules, MITRE ATLAS techniques (AML.T####, version atlas-5.6.0). The rule files are in infrastructure_tooling/monitoring_stack/falco/rules.d; hosts and k3s-01 load the same set.

Known noise already excepted (so a hit is outside those exceptions): ARC runner CI work in arc-runners, dockerd/buildkitd in DinD, Steampipe postgres, pg_isready, the GNOME update notifier, ToolHive proxies contacting the API, pvedaemon workers reading /etc/pam.d on the Proxmox nodes, the cointhieves DinD sidecar and local-path helper pods being privileged. On k3s-01, k8saudit rules (Attach/Exec Pod, K8s Secret Get) mostly reflect the owner's kubectl work; correlate with the Kubernetes audit. "Remove Bulk Data from Disk" from an agent's own `shred -u` cleanup is known. AI agent rules fire on processes under claude/codex/opencode/openclaw/hermes: Hermes and OpenClaw on openclaw-claude-01, Claude Code/Codex/OpenCode on coding-box, and the homelab agent CronJobs (toolhive-clients) on k3s-01, which run claude.

Check the full process lineage (parent command lines), the container/pod, what else that process tree did in the same minute, and whether it is a CI job, a deploy, or an interactive session. A reverse shell, credential file read or persistence write by an agent needs the exact command quoted (without secrets).

## AI agent rules (all tagged ai_agent, all page)

- AI agent read credential file: an agent process opened an SSH key, cloud/cluster/Vault/Terraform credential, registry token (.npmrc, .pypirc), shell history, another process's /proc/<pid>/environ, a service-account token, another agent's auth file or a private key, outside the tool that owns it. Check whether the content could have reached the agent's context (cat/grep vs. a tool reading it), and whether the file was a test dummy.
- AI agent printed a .env file: the agent's own read tool, or cat/grep/sed/jq/cp/an editor/an inline interpreter one-liner, opened a .env file. Templates (.env.example) are excluded. Check whether the file holds live secrets; if so they are now in the transcript.
- AI agent piped a download into a shell: curl/wget output piped into, or passed to, a shell or interpreter, or saved and executed in the same command. Get the URL, fetch it safely for review, and check what ran next.
- AI agent wrote a persistence file: shell startup files, cron/at, systemd units, autostart, ld.so.preload, init scripts, SSH config/authorized_keys, passwd/shadow/group, PAM, sudoers, udev rules, git hooks. Read the file as written now and compare with the previous version.
- AI agent changed agent configuration: Claude Code settings/hooks/.mcp.json/managed settings, ~/.claude.json written by anything but claude, Codex config.toml/hooks.json, OpenCode config or plugins, Hermes config.yaml, OpenClaw openclaw.json. Expected now and then from the owner (Codex writes config.toml when a project is trusted or the model changed). Look for new MCP servers, hooks, allowed commands or changed endpoints.
- AI agent outbound connection on unusual port: public address, port other than 22/53/80/443. Identify the destination and the tool.
- AI agent ran a destructive command: force push or remote branch delete, terraform/tofu apply or destroy, kubectl delete, helm uninstall, docker prune/rm -f, qm/pct destroy, Vault or Argo CD delete, gh repo delete, mkfs, dd onto a block device, shred outside /tmp, rm -r of a top-level or home directory. Confirm the owner asked for it and what was affected.
- AI agent started a reverse shell / AI agent redirected standard streams to a network connection: netcat/socat with a program, /dev/tcp, fifo plus network tool, an interpreter one-liner, or any process under an agent duplicating a socket onto stdin/stdout/stderr. Treat as an incident: remote address, process tree, kill it.
- AI agent uploaded a file or data: curl with a file body or upload, wget --post-file, gh gist create, rclone to a remote, scp/rsync to a remote host, netcat fed from a file. Agents post files to APIs sometimes; check the destination and what the file held.
- AI agent stopped or changed a security tool: systemctl stop/disable/mask, pkill/killall or kill by pidof of falco, otelcol(-contrib) or auditd, or writes to /etc/falco, /etc/otelcol-contrib, /etc/audit. Check the tool is running again and diff its config. Re-running falco/install.sh under an agent with sudo also fires this.
- AI agent used sudo or changed user: sudo/su/doas/pkexec/run0 under an agent. Check the command it ran with the new privileges and whether the owner approved it.

## Homelab host rules

- Tunnelling or proxy tool executed: chisel, SoftEther (vpnclient/vpnserver/...), tor, frp, ngrok, gost, ligolo, iox, rathole, bore, or a cloudflared quick tunnel (`tunnel --url`, not the named-tunnel containers). Find who started it, the remote end, and which local port it exposes.

## Upstream rules on trial (indexed, do not page)

Enabled from Falco's incubating and sandbox sets; listed in the 85_falco macro falco_trial_rules and left out of "Falco - runtime finding" until tuned, so a triage queue only sees them when asked. Expected noise while on trial: package upgrades setting setuid bits, art-validation's weekly crontab test (Schedule Cron Jobs), dig/nc/tcpdump run by hand on hosts, privileged or socket-mounting compose containers on the Docker hosts.

- Modify Shell Configuration File, Schedule Cron Jobs, Adding ssh keys to authorized_keys: persistence by any process; check who wrote it.
- Set Setuid or Setgid bit: a chmod adding setuid/setgid outside package installs.
- Delete or rename shell history: history file removed, renamed or truncated.
- Read environment variable from /proc files: a container process read another process's environ.
- Backdoored library loaded into SSHD (CVE-2024-3094): sshd loaded liblzma 5.6.0/5.6.1. Incident.
- Launch Suspicious Network Tool on Host / Netcat/Socat Remote Code Execution on Host: nc, nmap, tcpdump, socat and similar on a host; the second one with a program attached.
- Launch Privileged Container / Launch Sensitive Mount Container: a new privileged container or one mounting /proc, the container socket, /, /etc or /root from the host.
- Known Cryptominer Process Executed / Detect crypto miners using the Stratum protocol: miner binary or stratum URL. Incident.
- Network Tool Executed During NPM Package Install: a network tool run from an npm install script in a container (supply chain).
- Web Server Spawned Shell / Reverse Shell from Web Server: a web server process started a shell or a reverse-shell one-liner.
