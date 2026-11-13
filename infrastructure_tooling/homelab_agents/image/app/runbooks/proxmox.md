# Proxmox guest destroyed

Source: `index=infra sourcetype=linux:journald host=proxmox*`, pvedaemon task lines (`<user> starting task UPID:...:qmdestroy:<vmid>:...`).

Known: Packer (`root@pam!packer`, the GitHub workflow that rebuilds templates every Monday at 12:00 UTC) deletes its own templates 9000-9006 and is not reported. The terraform-apply workflow uses the same Proxmox token as Packer, so a VM Terraform replaces (a change to `proxmox/terraform/main.tf`) shows up as that token on a VM id outside 9000-9006. The owner removes test VMs by hand.

Check: who (`user`, a person `user@realm` or a token `user@realm!token`), which guest (`object`), and the matching `end task` line (`OK` or an error). Was it a template or a VM that runs something (Terraform's `main.tf` lists the VMs; a host that stopped sending telemetry at the same time was running)? For the Packer token on another id, look for a terraform-apply run at the same time and a commit to `main.tf` that explains it. For a person, look for their Proxmox login (pvedaemon/pveproxy) and from where. Destruction by an unknown token, or of several guests in a row, is an incident.
