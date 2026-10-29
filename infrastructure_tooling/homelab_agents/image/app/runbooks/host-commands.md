# Sensitive command over SSH or sudo

Source: `index=infra sourcetype=linux_secure` (sudo lines, Tailscale SSH command eventtype homelab_tailscale_ssh_command with command and ts_user).

Flags: stopping security tooling (falco, otelcol, tailscaled), touching authorized_keys or cron, writing PAM/sudoers/sshd_config, clearing logs (journalctl --rotate/--vacuum), user/group changes.

Check the full session around it: who (ts_user / sudo user), from where, the other commands in the same session, and whether it lines up with maintenance (an install script, a package upgrade, a deploy). The owner and their AI agents run admin commands routinely; unexplained persistence or tooling stopped without a restart is suspicious.
