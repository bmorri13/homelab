# Proxmox Infrastructure Automation

This directory contains infrastructure automation tools for managing Proxmox VE environments using Packer and Terraform.

## Directory Structure

```
proxmox/
├── consul/                     # Consul setup for remote state storage
├── packer/                     # VM template creation
│   ├── ubuntu-server-noble/    # Ubuntu 24.04 server template configuration
│   └── ubuntu-desktop-noble/   # Ubuntu 24.04 desktop template configuration
└── terraform/                  # VM provisioning and management
```

## Overview

### Packer
The `packer` directory contains configuration files for creating VM templates on Proxmox VE. The current setup builds Ubuntu 24.04 LTS server and desktop templates with cloud-init support.

**Key Files:**
- `ubuntu-server-noble/ubuntu-2404.pkr.hcl` - Main Packer configuration for Ubuntu 24.04 server
- `ubuntu-desktop-noble/ubuntu-2404-desktop.pkr.hcl` - Main Packer configuration for Ubuntu 24.04 desktop
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

Builds Ubuntu 24.04 VM templates on multiple Proxmox cluster nodes in parallel.

**Features:**
- Validates Packer templates on PR/push
- Builds VM templates on multiple nodes in parallel
- Node-specific VM IDs (see the table below)
- Automatically deletes existing VM/template before building
- Dynamic VM naming with build date: `ubuntu-2404-YYYYMMDD`
- Dynamically generates password hash from GitHub secrets
- Installs Docker 27.5.1 in the template
- Uploads Packer logs as artifacts (per-node)

**Multi-Node Configuration:**

| Template | Node | VM ID |
|----------|------|-------|
| Server | proxmox | 9000 |
| Server | proxmox2 | 9001 |
| Server | proxmox3 | 9005 |
| Desktop | proxmox2 | 9004 |
| Desktop | proxmox3 | 9006 |

**Triggers:**
- Manual (workflow_dispatch)
- Scheduled (Every Monday at 12:00 UTC)
- Push to main (when packer files change)
- Pull requests to main (when packer files change)

**Required GitHub Secrets (prod environment):**
- `PROXMOX_URL` - Proxmox API URL (e.g., `https://proxmox.example.com:8006/api2/json`)
- `PROXMOX_USERNAME` - Proxmox API token ID (e.g., `root@pam!packer`)
- `PROXMOX_TOKEN` - Proxmox API token secret
- `SSH_PASSWORD` - Password for ubuntu user
- `TAILSCALE_AUTH_KEY` - Tailscale auth key stored in the template for auto-connect on first boot
- `TAILSCALE_TAGS` - Optional. Tailscale ACL tags to advertise (default: `tag:proxmox-bmo`)

#### 2. Terraform Validate and Plan
**File:** `.github/workflows/terraform-validate-and-plan.yml`

Validates and plans Terraform changes before applying.

**Features:**
- Terraform format check
- Validates Terraform configuration
- Generates execution plan
- Shows plan summary in GitHub Actions

**Triggers:**
- Manual (workflow_dispatch)
- Push to main (when terraform files change)
- Pull requests to main (when terraform files change)

**Required GitHub Secrets (prod environment):**
- `PROXMOX_URL`, `PROXMOX_USERNAME`, `PROXMOX_TOKEN`
- `PROXMOX_NODE` - Target Proxmox node for Terraform deployments
- `SSH_PASSWORD` - VM password
- `CONSUL_HTTP_TOKEN` - Token for Consul backend access
- `TARGET_NODE_SPLUNK`, `TARGET_NODE_GITHUB_RUNNER`, `TARGET_NODE_DOCKER_COMPOSE_02` - Optional. Per-VM node overrides (default: `PROXMOX_NODE`)

#### 3. Terraform Apply
**File:** `.github/workflows/terraform-apply.yml`

Applies Terraform configuration to provision VMs.

**Features:**
- Runs plan before apply, then applies that saved plan (no interactive approval step)
- Fails before apply when the plan updates, replaces or deletes more than `max_changes` resources
- One run at a time (`terraform-proxmox` concurrency group)
- Creates terraform.tfvars dynamically from secrets
- Cleans up sensitive files after execution

**Triggers:**
- Manual only (workflow_dispatch)

**Workflow Inputs:**
- `max_changes` - Max resources the plan may update, replace or delete (default: 1). Creates and no-ops do not count. Raise it only after reading the plan; the apply runner is itself a managed VM

**Required GitHub Secrets (prod environment):**
- Same as Terraform Validate and Plan workflow

### Setting Up GitHub Secrets

Navigate to your repository settings and add the following secrets to the `prod` environment:

1. Go to Settings → Environments → prod → Environment secrets
2. Add the following secrets:
   - `PROXMOX_URL` - Proxmox API URL
   - `PROXMOX_USERNAME` - Proxmox API token ID
   - `PROXMOX_TOKEN` - Proxmox API token secret
   - `PROXMOX_NODE` - Target node for Terraform deployments (Packer uses matrix strategy)
   - `SSH_PASSWORD` - Password for VM users
   - `CONSUL_HTTP_TOKEN` - Token for Consul backend access
   - `TAILSCALE_AUTH_KEY` - Tailscale auth key for the Packer templates
   - `TAILSCALE_TAGS` - Optional. Tailscale ACL tags (default: `tag:proxmox-bmo`)
   - `TARGET_NODE_SPLUNK`, `TARGET_NODE_GITHUB_RUNNER`, `TARGET_NODE_DOCKER_COMPOSE_02` - Optional. Per-VM node overrides for Terraform

## Running Locally

### 1. Packer - Build VM Template

Navigate to the packer directory and build the Ubuntu template:

```bash
cd packer/ubuntu-server-noble
packer build -var-file="secrets.pkrvars.hcl" -var-file="variables.pkrvars.hcl" ubuntu-2404.pkr.hcl
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
proxmox_token      = "your-proxmox-api-token"
ssh_password       = "your-secure-password"
tailscale_auth_key = "tskey-auth-your-key-here"
```

**Packer (`variables.pkrvars.hcl`):**
```hcl
# Proxmox connection details
proxmox_url      = "https://your-proxmox-server.example.com:8006/api2/json"
proxmox_username = "root@pam!packer"
proxmox_node     = "your-proxmox-node-name"

# VM template configuration
vm_id        = "9000"
iso_file     = "local:iso/ubuntu-24.04.3-live-server-amd64.iso"
ssh_username = "ubuntu"
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
dns_servers    = ["8.8.8.8", "8.8.4.4"]
```

### VM Configuration

Each VM is a `module` block in `main.tf` with its own specifications (node, template, CPU, memory, disk). Add or resize VMs there.

## Notes

- Template IDs are fixed per node in the workflow matrix (see the table above)
- Template names include build date: `ubuntu-2404-YYYYMMDD`
- VMs created by Terraform use cloud-init for initial configuration
- Default login for created VMs: `ubuntu@<ip-address>` with password from secrets
- All VMs are tagged with "terraform" and "ubuntu" for easy identification
- The Terraform state is stored remotely in Consul for team collaboration and state persistence
- GitHub Actions workflows automatically manage template lifecycle (delete old, build new)
- Packer builds run on the ARC runner set `arc-k3s-homelab`; Terraform runs on the `github-action-runner` VM (`[self-hosted, Linux, X64]`)
