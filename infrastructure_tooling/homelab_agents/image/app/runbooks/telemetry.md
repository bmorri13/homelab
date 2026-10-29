# Host stopped sending telemetry

A host that sent metrics in the last 24 hours went quiet for 15 minutes. Usual causes: the VM was shut down or rebooted (Proxmox shows it stopped), the OTel collector stopped, or the network/Tailscale path broke. An attacker stopping telemetry looks the same.

Check `| mstats latest(proxmox_vm_uptime) where index=metrics earliest=-1h by name` for the VM's state, whether logs from the host (`index=infra host=<host>`) stopped at the same time or only metrics did, the last journald lines before it went quiet (shutdown, otelcol errors, OOM), and whether it has come back.
