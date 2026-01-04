variable "proxmox_api_url" {
  description = "Proxmox API URL"
  type        = string
}

variable "proxmox_api_token_id" {
  description = "Proxmox API token ID"
  type        = string
}

variable "proxmox_api_token_secret" {
  description = "Proxmox API token secret"
  type        = string
  sensitive   = true
}

variable "target_node" {
  description = "Proxmox node to create the VM on"
  type        = string
}

variable "disk_storage" {
  description = "Storage location for the VM disk"
  type        = string
  default     = "local-lvm"
}

variable "network_bridge" {
  description = "Network bridge to use"
  type        = string
  default     = "vmbr0"
}

variable "gateway" {
  description = "Network gateway IP address"
  type        = string
  default     = "192.168.1.1"
}

variable "dns_servers" {
  description = "List of DNS servers"
  type        = list(string)
  default     = ["8.8.8.8", "8.8.4.4"]
}

variable "dns_domain" {
  description = "DNS domain name"
  type        = string
  default     = "home"
}

variable "ssh_public_keys" {
  description = "SSH public keys to add to the VM"
  type        = string
  default     = ""
}

variable "vm_password" {
  description = "Password for the Ubuntu user (overrides template password)"
  type        = string
  sensitive   = true
  default     = null # If null, keeps the password from the template
}

# Per-VM node overrides (optional - falls back to target_node if not set)
variable "target_node_splunk" {
  description = "Proxmox node for Splunk VM (overrides target_node)"
  type        = string
  default     = null
}

variable "target_node_github_runner" {
  description = "Proxmox node for GitHub runner VM (overrides target_node)"
  type        = string
  default     = null
}

variable "target_node_monitoring_01" {
  description = "Proxmox node for monitoring VM (overrides target_node)"
  type        = string
  default     = null
}

variable "target_node_docker_compose_02" {
  description = "Proxmox node for docker-compose-02 VM (overrides target_node)"
  type        = string
  default     = null
}