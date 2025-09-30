variable "vm_name" {
  description = "Name of the VM"
  type        = string
}

variable "description" {
  description = "Description of the VM"
  type        = string
  default     = "Terraform-managed Ubuntu VM"
}

variable "tags" {
  description = "Tags to apply to the VM"
  type        = list(string)
  default     = ["terraform"]
}

variable "target_node" {
  description = "Proxmox node to create the VM on"
  type        = string
}

variable "template_vm_id" {
  description = "ID of the VM template to clone from"
  type        = number
}

variable "cores" {
  description = "Number of CPU cores"
  type        = number
  default     = 2
}

variable "memory" {
  description = "Amount of memory in MB"
  type        = number
  default     = 2048
}

variable "disk_size" {
  description = "Disk size (e.g., '50G', '100G')"
  type        = string
  default     = "50"
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

variable "vm_username" {
  description = "Username for the VM"
  type        = string
  default     = "ubuntu"
}

variable "vm_password" {
  description = "Password for the VM user"
  type        = string
  sensitive   = true
  default     = null
}

variable "ssh_public_keys" {
  description = "SSH public key(s) to add to the VM"
  type        = string
  default     = ""
}