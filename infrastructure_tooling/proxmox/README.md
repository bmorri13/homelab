# Proxmox Infrastructure Automation

This directory contains infrastructure automation tools for managing Proxmox VE environments using Packer and Terraform.

## Directory Structure

```
proxmox/
├── consul/                     # Consul setup for remote state storage
├── packer/                     # VM template creation
│   └── ubuntu-server-noble/    # Ubuntu 24.04 template configuration
└── terraform/                  # VM provisioning and management
```

## Overview

### Packer
The `packer` directory contains configuration files for creating VM templates on Proxmox VE. The current setup builds an Ubuntu 24.04 LTS server template with cloud-init support.

**Key Files:**
- `ubuntu-2404.pkr.hcl` - Main Packer configuration for Ubuntu 24.04
- `secrets.pkrvars.hcl` - Sensitive variables (Proxmox API token, etc.)
- `variables.pkrvars.hcl` - General configuration variables
- `http/user-data` & `http/meta-data` - Cloud-init configuration files

### Terraform
The `terraform` directory manages VM provisioning using templates created by Packer. It uses the Proxmox provider to create and configure virtual machines.

**Key Files:**
- `main.tf` - VM resource definitions and provider configuration
- `variables.tf` - Variable declarations
- `secrets.tfvars` - Sensitive variables (API tokens, credentials)
- `terraform.tfvars` - General configuration values
- `backend.tf` - Consul backend configuration for remote state

## Prerequisites

1. **Proxmox VE** environment with API access
2. **Packer** installed locally ([Download](https://www.packer.io/downloads))
3. **Terraform** installed locally ([Download](https://www.terraform.io/downloads))
4. **Consul** running for Terraform state storage (see Consul Setup section)

## GitHub Actions Automation

This repository includes automated workflows for building Packer templates and deploying Terraform infrastructure via GitHub Actions.

### Workflows

#### 1. Proxmox Packer Builder
**File:** `.github/workflows/proxmox-packer-builder.yml`

Builds Ubuntu 24.04 VM templates with automatic deletion of existing templates.

**Features:**
- Validates Packer templates on PR/push
- Builds VM templates with VM ID 9000 (default)
- Automatically deletes existing VM/template before building
- Dynamic VM naming with build date: `ubuntu-2404-YYYYMMDD`
- Dynamically generates password hash from GitHub secrets
- Installs Docker 27.5.1 in the template
- Uploads Packer logs as artifacts

**Triggers:**
- Manual (workflow_dispatch)
- Push to main (when packer files change)
- Pull requests to main (when packer files change)

**Required GitHub Secrets (prod environment):**
- `PROXMOX_URL` - Proxmox API URL (e.g., `https://proxmox.example.com:8006`)
- `PROXMOX_USERNAME` - Proxmox API token ID (e.g., `root@pam!packer`)
- `PROXMOX_TOKEN` - Proxmox API token secret
- `PROXMOX_NODE` - Proxmox node name (e.g., `proxmox`)
- `SSH_PASSWORD` - Password for ubuntu user

#### 2. Terraform Validate and Plan
**File:** `.github/workflows/terraform-validate-and-plan.yml`

Validates and plans Terraform changes before applying.

**Features:**
- Terraform format check
- Validates Terraform configuration
- Generates execution plan
- Uploads plan as artifact
- Shows plan summary in GitHub Actions

**Triggers:**
- Manual (workflow_dispatch)
- Push to main (when terraform files change)
- Pull requests to main (when terraform files change)

**Required GitHub Secrets (prod environment):**
- `PROXMOX_URL`, `PROXMOX_USERNAME`, `PROXMOX_TOKEN`, `PROXMOX_NODE`
- `SSH_PASSWORD` - VM password
- `SSH_PUBLIC_KEYS` - SSH public keys for VM access
- `CONSUL_HTTP_TOKEN` - Token for Consul backend access

#### 3. Terraform Apply
**File:** `.github/workflows/terraform-apply.yml`

Applies Terraform configuration to provision VMs.

**Features:**
- Runs plan before apply
- Optional auto-approve via workflow input
- Creates terraform.tfvars dynamically from secrets
- Cleans up sensitive files after execution

**Triggers:**
- Manual only (workflow_dispatch)

**Workflow Inputs:**
- `auto_approve` - Skip manual approval (default: false)

**Required GitHub Secrets (prod environment):**
- Same as Terraform Validate and Plan workflow

### Setting Up GitHub Secrets

Navigate to your repository settings and add the following secrets to the `prod` environment:

1. Go to Settings → Environments → prod → Environment secrets
2. Add the following secrets:
   - `PROXMOX_URL`
   - `PROXMOX_USERNAME`
   - `PROXMOX_TOKEN`
   - `PROXMOX_NODE`
   - `SSH_PASSWORD`
   - `SSH_PUBLIC_KEYS`
   - `CONSUL_HTTP_TOKEN`

## Running Locally

### 1. Packer - Build VM Template

Navigate to the packer directory and build the Ubuntu template:

```bash
cd packer/ubuntu-server-noble
packer build -var-file="secrets.pkrvars.hcl" ubuntu-2404.pkr.hcl
```

**Note:** Ensure your `secrets.pkrvars.hcl` file contains the required Proxmox API token and other sensitive variables.

### 2. Terraform - Provision VMs

Navigate to the terraform directory and run:

```bash
cd terraform

# Initialize Terraform (first time only)
terraform init

# Plan the deployment
terraform plan -var-file="secrets.tfvars"

# Apply the configuration
terraform apply -var-file="secrets.tfvars"
```

### 3. Destroy Resources (if needed)

```bash
terraform destroy -var-file="secrets.tfvars"
```

## Consul Setup for Remote State

The Terraform configuration uses Consul as a remote backend to store state files. Consul is running on an Unraid server using the Docker Compose Manager app.

**Consul Configuration:**
- **Address:** `http://consul.bmosan.com:8500`
- **State Path:** `terraform/proxmox/infra`
- **Docker Compose File:** `consul/unraid-docker-compose.yml`

To set up Consul on Unraid:
1. Install the Docker Compose Manager app from Community Applications
2. Use the provided `unraid-docker-compose.yml` file to deploy Consul
3. Access the Consul UI at `http://consul.bmosan.com:8500`

## Configuration Files

### Setting Up Secret Variables

**Important:** All sensitive configuration files are excluded from version control via `.gitignore`. You need to create local copies from the provided example files:

1. **Packer Configuration:**
   ```bash
   cd packer/ubuntu-server-noble
   cp secrets.pkrvars.hcl.example secrets.pkrvars.hcl
   cp credentials.json.example credentials.json
   cp variables.pkrvars.hcl.example variables.pkrvars.hcl
   # Edit the files with your actual credentials and configuration
   ```

2. **Terraform Configuration:**
   ```bash
   cd terraform
   cp secrets.tfvars.example secrets.tfvars
   cp terraform.tfvars.example terraform.tfvars
   # Edit both files with your actual credentials and VM specifications
   ```

### Required Secret Variables

**Packer (`secrets.pkrvars.hcl`):**
```hcl
proxmox_token = "your-proxmox-api-token"
ssh_password  = "your-secure-password"
```

**Packer (`variables.pkrvars.hcl`):**
```hcl
# Proxmox connection details
proxmox_url      = "https://your-proxmox-server.example.com:8006/api2/json"
proxmox_username = "root@pam!packer"
proxmox_node     = "your-proxmox-node-name"

# VM template configuration
vm_id        = "9000"
iso_file     = "local:iso/ubuntu-24.04.2-live-server-amd64.iso"
ssh_username = "ubuntu"
```

**Packer (`credentials.json`):**
```json
{
  "proxmox_api_url": "https://your-proxmox-server.example.com:8006/api2/json",
  "proxmox_api_token_id": "root@pam!packer",
  "proxmox_api_token_secret": "your-proxmox-api-token-secret"
}
```

**Terraform (`secrets.tfvars`):**
```hcl
proxmox_api_token_id     = "root@pam!terraform"
proxmox_api_token_secret = "your-secret-token"
vm_password              = "your-secure-vm-password"
```

**Terraform (`terraform.tfvars`):**
```hcl
# Proxmox API connection details
proxmox_api_url = "https://your-proxmox-server.example.com:8006/api2/json"

# Proxmox node settings
target_node = "your-proxmox-node-name"

# Network and storage configuration
network_bridge = "vmbr0"
disk_storage   = "local-lvm"
gateway        = "192.168.1.1"
dns_servers    = ["8.8.8.8", "8.8.4.4"]

# SSH public keys for VM access
ssh_public_keys = "ssh-rsa AAAAB3NzaC1yc2EAAAA... your-public-key-here"

# VM definitions with custom specifications
vms = {
  "vm-name-1" = {
    template_vm_id = 9000
    cores          = 4
    memory         = 4096
    disk_size      = "100"
  }
}
```

### VM Configuration

The Terraform configuration supports multiple VM definitions through the `vms` variable. Each VM can be customized with different specifications (CPU, memory, disk, network) as defined in `terraform.tfvars`.

## Notes

- VM templates created by Packer are assigned ID 9000 by default (customizable via workflow input)
- Template names include build date: `ubuntu-2404-YYYYMMDD`
- VMs created by Terraform use cloud-init for initial configuration
- Default login for created VMs: `ubuntu@<ip-address>` with password from secrets
- All VMs are tagged with "terraform" and "ubuntu" for easy identification
- The Terraform state is stored remotely in Consul for team collaboration and state persistence
- GitHub Actions workflows automatically manage template lifecycle (delete old, build new)
- Self-hosted GitHub Actions runner required (Linux, X64) for Packer builds
