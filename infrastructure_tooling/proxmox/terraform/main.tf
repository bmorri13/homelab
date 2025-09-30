terraform {
  required_providers {
    proxmox = {
      source  = "bpg/proxmox"
      version = "0.84.1"
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
  target_node    = var.target_node
  template_vm_id = 9002
  cores          = 8
  memory         = 4092
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
  target_node    = var.target_node
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

# testr VM
module "test_instance" {
  source = "./modules/proxmox-vm"

  vm_name        = "test-instance"
  description    = "Terraform-managed Ubuntu 24.04 VM for testing, use ubuntu@<ip-address> to login"
  tags           = ["terraform", "ubuntu", "testing"]
  target_node    = var.target_node
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

# output "vm_ip_addresses" {
#   description = "IP addresses of the created VMs"
#   value = {
#     splunk_docker_compose_mcp = module.splunk_docker_compose_mcp.ipv4_addresses
#     github_action_runner      = module.github_action_runner.ipv4_addresses
#   }
# }