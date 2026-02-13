terraform {
  required_providers {
    proxmox = {
      source  = "bpg/proxmox"
      version = "0.95.0"
    }
  }
}

provider "proxmox" {
  endpoint  = var.proxmox_api_url
  api_token = "${var.proxmox_api_token_id}=${var.proxmox_api_token_secret}"
  insecure  = true
}

# Splunk Docker Compose MCP VM
module "splunk_docker_compose_mcp" {
  source = "./modules/proxmox-vm"

  vm_name        = "splunk-docker-compose-mcp"
  description    = "Terraform-managed Ubuntu 24.04 VM for Splunk, use ubuntu@<ip-address> to login"
  tags           = ["terraform", "ubuntu", "splunk"]
  target_node    = coalesce(var.target_node_splunk, var.target_node)
  template_vm_id = 9002
  cores          = 8
  memory         = 4096
  disk_size      = "300"

  # Shared configuration
  disk_storage   = var.disk_storage
  network_bridge = var.network_bridge
  dns_servers    = var.dns_servers
  dns_domain     = var.dns_domain
  vm_username    = "ubuntu"
  vm_password    = var.vm_password
}

# GitHub Action Runner VM
module "github_action_runner" {
  source = "./modules/proxmox-vm"

  vm_name        = "github-action-runner"
  description    = "Terraform-managed Ubuntu 24.04 VM for GitHub Actions, use ubuntu@<ip-address> to login"
  tags           = ["terraform", "ubuntu", "github-runner"]
  target_node    = coalesce(var.target_node_github_runner, var.target_node)
  template_vm_id = 9002
  cores          = 2
  memory         = 4092
  disk_size      = "50"

  # Shared configuration
  disk_storage   = var.disk_storage
  network_bridge = var.network_bridge
  dns_servers    = var.dns_servers
  dns_domain     = var.dns_domain
  vm_username    = "ubuntu"
  vm_password    = var.vm_password
}

# Monitoring Docker Compose VM
module "monitoring_docker_compose_01" {
  source = "./modules/proxmox-vm"

  vm_name        = "monitoring-docker-compose-01"
  description    = "Terraform-managed Ubuntu 24.04 VM for monitoring, use ubuntu@<ip-address> to login"
  tags           = ["terraform", "ubuntu", "monitoring"]
  target_node    = coalesce(var.target_node_monitoring_01, var.target_node)
  template_vm_id = 9000
  cores          = 2
  memory         = 4092
  disk_size      = "250"

  # Shared configuration
  disk_storage   = var.disk_storage
  network_bridge = var.network_bridge
  dns_servers    = var.dns_servers
  dns_domain     = var.dns_domain
  vm_username    = "ubuntu"
  vm_password    = var.vm_password
}

# Docker Compose 02 VM
module "monitoring_docker_compose_02" {
  source = "./modules/proxmox-vm"

  vm_name        = "docker-compose-02"
  description    = "Terraform-managed Ubuntu 24.04 VM for docker-compose, use ubuntu@<ip-address> to login"
  tags           = ["terraform", "ubuntu", "docker-compose"]
  target_node    = coalesce(var.target_node_docker_compose_02, var.target_node)
  template_vm_id = 9000
  cores          = 2
  memory         = 4092
  disk_size      = "500"

  # Shared configuration
  disk_storage   = var.disk_storage
  network_bridge = var.network_bridge
  dns_servers    = var.dns_servers
  dns_domain     = var.dns_domain
  vm_username    = "ubuntu"
  vm_password    = var.vm_password
}

# Monitoring Docker Compose VM on proxmox2
module "docker_compose_03" {
  source = "./modules/proxmox-vm"

  vm_name        = "docker-compose-03"
  description    = "Terraform-managed Ubuntu 24.04 VM for monitoring on proxmox2, use ubuntu@<ip-address> to login"
  tags           = ["terraform", "ubuntu", "monitoring"]
  target_node    = "proxmox2"
  template_vm_id = 9001
  cores          = 2
  memory         = 4092
  disk_size      = "250"

  # Shared configuration
  disk_storage   = var.disk_storage
  network_bridge = var.network_bridge
  dns_servers    = var.dns_servers
  dns_domain     = var.dns_domain
  vm_username    = "ubuntu"
  vm_password    = var.vm_password
}

# Claude OpenClaw VM on proxmox2
module "claude_openclaw" {
  source = "./modules/proxmox-vm"

  vm_name        = "claude-openclaw"
  description    = "Terraform-managed Ubuntu 24.04 VM for Claude OpenClaw"
  tags           = ["terraform", "ubuntu", "claude-openclaw"]
  target_node    = "proxmox2"
  template_vm_id = 9001
  cores          = 2
  memory         = 4092
  disk_size      = "250"

  # Shared configuration
  disk_storage   = var.disk_storage
  network_bridge = var.network_bridge
  dns_servers    = var.dns_servers
  dns_domain     = var.dns_domain
  vm_username    = "ubuntu"
  vm_password    = var.vm_password
}

# output "vm_ip_addresses" {
#   description = "IP addresses of the created VMs"
#   value = {
#     splunk_docker_compose_mcp = module.splunk_docker_compose_mcp.ipv4_addresses
#     github_action_runner      = module.github_action_runner.ipv4_addresses
#   }
# }
