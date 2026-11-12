# Copy to /mnt/user/appdata/consul/config/ on the Unraid server (the compose file
# mounts that directory as -config-dir), then restart the container.
# Without this, anyone who can reach port 8500 can read and overwrite the Terraform
# state. Tokens are created once with the bootstrap in the Proxmox README.
acl {
  enabled                  = true
  default_policy           = "deny"
  enable_token_persistence = true
}
