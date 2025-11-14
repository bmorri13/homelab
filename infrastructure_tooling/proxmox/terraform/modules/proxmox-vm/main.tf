resource "proxmox_virtual_environment_vm" "vm" {
  name        = var.vm_name
  description = var.description
  tags        = var.tags
  node_name   = var.target_node

  # Use template created by Packer
  clone {
    vm_id        = var.template_vm_id
    full         = true
    datastore_id = var.disk_storage
    retries      = 3
  }

  agent {
    enabled = true
  }

  cpu {
    cores = var.cores
    type  = "host"
  }

  memory {
    dedicated = var.memory
  }

  network_device {
    bridge = var.network_bridge
  }

  # Disk configuration to resize the template's disk
  disk {
    interface    = "scsi0" # Must match the template's disk interface
    size         = var.disk_size
    file_format  = "raw" # Match template's format
    datastore_id = var.disk_storage
  }

  operating_system {
    type = "l26"
  }

  # Cloud-init configuration for the VM
  initialization {
    ip_config {
      ipv4 {
        address = "dhcp"
      }
    }

    # DNS servers configuration
    dns {
      servers = var.dns_servers
      domain  = var.dns_domain
    }

    user_account {
      keys     = var.ssh_public_keys != "" ? [var.ssh_public_keys] : []
      username = var.vm_username
      password = var.vm_password
    }

    datastore_id = var.disk_storage
  }

  # Ensure VM starts on creation
  started = true
  on_boot = true

  # Set timeout for operations
  timeout_create = "30m"

  # Ignore changes to MAC addresses and IPv6 addresses as they are managed by Proxmox/Docker
  lifecycle {
    ignore_changes = [
      mac_addresses,
      ipv6_addresses,
    ]
  }
}