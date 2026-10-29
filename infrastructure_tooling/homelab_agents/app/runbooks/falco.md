# Falco findings (runtime and AI agent rules)

Source: `index=security sourcetype=falco:ocsf`. finding_info.title = rule, device.hostname = host, evidences{}.process.cmd_line / evidences{}.process.parent_process.cmd_line, container fields for k3s pods.

Known noise already excepted (so a hit is outside those exceptions): ARC runner CI work in arc-runners, dockerd/buildkitd in DinD, Steampipe postgres, pg_isready, the GNOME update notifier, ToolHive proxies contacting the API. On k3s-01, k8saudit rules (Attach/Exec Pod, K8s Secret Get) mostly reflect the owner's kubectl work; correlate with the Kubernetes audit. "Remove Bulk Data from Disk" from an agent's own `shred -u` cleanup is known. AI agent rules fire on processes under claude/codex/opencode/openclaw/hermes; the real agent on Falco hosts is Hermes on openclaw-claude-01.

Check the full process lineage (parent command lines), the container/pod, what else that process tree did in the same minute, and whether it is a CI job, a deploy, or an interactive session. A reverse shell, credential file read or persistence write by an agent needs the exact command quoted (without secrets).
