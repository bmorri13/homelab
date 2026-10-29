# Login detections (new SSH source/host, Proxmox login failures, UniFi admin logins)

Sources: `index=infra sourcetype=linux_secure` (Tailscale SSH: ts_user, src; sudo; Proxmox pvedaemon auth failures), `index=audit` UniFi admin events.

Known: VMs are reached with Tailscale SSH (100.x sources). tag:openclaw is the OpenClaw/Hermes agent doing health checks. Terraform and Packer use API tokens, so a Proxmox password failure is a person or something guessing.

Check: is the source a known Tailscale device of the owner (same source used for other logins in 30 days)? Did the login succeed afterwards, and what commands followed (`eventtype=homelab_tailscale_ssh_command`)? For Proxmox failures: the user named, the source IP (rhost), how many attempts, and whether a success from the same source followed. For UniFi: source IP and whether it's on the LAN or a known remote-access path.
