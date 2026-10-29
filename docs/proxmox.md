# Proxmox Setup

## Fixing Repository Issues Without Subscription

- When you run `apt-get update` on Proxmox without a valid subscription, it often fails because it's trying to access the enterprise repository that requires a subscription. To resolve this, you can disable the enterprise repository and switch to the no-subscription repository:

### Update PVE List
#### Disable the Enterprise Repository:
- Open the file `/etc/apt/sources.list.d/pve-enterprise.list` in your favorite text editor (e.g., nano or vim) and comment out the line by adding a `#` at the beginning:

```bash
# Original line
#deb https://enterprise.proxmox.com/debian/pve bookworm pve-enterprise
```

#### Enable the No-Subscription Repository:
- Next, add the no-subscription repository. Edit `/etc/apt/sources.list.d/pve-enterprise.list` and add the appropriate line:

For Proxmox VE 8.x (running on Debian Bookworm):
```bash
#deb https://enterprise.proxmox.com/debian/pve bookworm pve-enterprise
deb http://download.proxmox.com/debian/pve bookworm pve-no-subscription
```
### Update Ceph List
#### Disable the Enterprise Repository:
- Open the file `/etc/apt/sources.list.d/ceph.list` in your favorite text editor (e.g., nano or vim) and comment out the line by adding a `#` at the beginning:

```bash
# Original line
#deb https://enterprise.proxmox.com/debian/ceph-quincy bookworm enterprise
```

#### Enable the No-Subscription Repository:
- Next, add the no-subscription repository. Edit `/etc/apt/sources.list.d/ceph.list` and add the appropriate line:

For Proxmox VE 8.x (running on Debian Bookworm):
```bash
#deb https://enterprise.proxmox.com/debian/ceph-quincy bookworm enterprise
deb http://download.proxmox.com/debian/ceph-quincy bookworm no-subscription
```

Update the Package List:
Finally, run:
```bash
apt-get update
```

> Note: Using the no-subscription repository means you won't have access to the enterprise support updates, but you'll still receive regular community updates. This setup is common for home labs or non-production environments.

## Adding Valid HTTPS Certs via Cloudflare
- Utlized the video [Secure Proxmox with LetsEncrypt HTTPS Certificates Validated with Cloudflare DNS](https://www.youtube.com/watch?v=2_PhwHOxytM)
- Once completed, you can now access your Proxmox instnace via the DNS name used with a valid cert
    - E.g. `https://proxmox.bmosan.com:8006`


## Setting up Virtual Machines
### Linux
#### Ubuntu
- Uploaded Ubuntu Server ISO and installed, will update the steps later but it is pretty straight forward

### Windows
#### Windows Pro
- Using the (Proxmox - Windows 11 VM (with VirtIO drivers))[https://www.youtube.com/watch?v=eboCDiDpOCs]
> NOTE: This gudie (e.g. needing the extra VirtIO drivers) is needed since Windows requires having the extra drivers to get it installed correctly.

#### Tiny 11 Windows Image Builder
##### Setup
1. Download the latest Windows 11 iso
2. Download the [Tiny11 Builder files](https://github.com/ntdevlabs/tiny11builder)
3. Mount the Windows 11 iso
4. Start up Powershell as Administrator
5. Update execusition policy to unrestricted
```bash
Set-ExecutionPolicy unrestricted
```
6. Run the `tiny11maker.ps1` script
```bash
& "C:\Users\bryan\Documents\tiny_11\tiny11builder-main\tiny11maker.ps1"
```
> NOTE: Hit `Y` to proced if needed
8. Select the Windows iso you wish to use (e.g. Windows 11 Pro - Index `6`)
9. Once finished, reenabled the exection policy
```bash
Set-ExecutionPolicy Restricted
```
> NOTE: Hit `Y` to proced if needed
10. Validatte the execution policy is set correctly again
```bash
Get-ExecutionPolicy -List
```
11. Your iso should be available for use with the name of `tiny11.iso`

> Video Guide: [Tiny11 Builder: Create custom ISO and install Windows 11 without bloatware or Microsoft account](https://www.youtube.com/watch?v=VdKVph3G6hQ)

## Infrastructure Automation

For automated infrastructure provisioning and management on Proxmox, this homelab uses Packer and Terraform for Infrastructure as Code (IaC).

### Packer & Terraform

The `infrastructure_tooling/proxmox/` directory contains comprehensive automation tools for:

- **VM Template Creation** - Packer builds Ubuntu 24.04 LTS templates with cloud-init support
- **Infrastructure Provisioning** - Terraform manages VM lifecycle and configuration
- **State Management** - Remote state storage using Consul running on Unraid
- **Security** - All sensitive credentials are excluded from version control

**Key Features:**
- Automated Ubuntu 24.04 template creation with Packer
- **Parallel template builds** on both Proxmox nodes via GitHub Actions (using ARC runners)
- Multi-VM provisioning with Terraform
- Remote state storage in Consul for team collaboration
- Complete Infrastructure as Code workflow
- Secure credential management with example templates

**GitHub Actions Workflows:**

| Workflow | Trigger | Runner | Description |
|----------|---------|--------|-------------|
| `proxmox-packer-builder.yml` | Manual, Schedule, Push | `arc-k3s-homelab` | Builds templates on both nodes in parallel |
| `terraform-validate-and-plan.yml` | Manual, Push/PR | `self-hosted` | Validates and plans Terraform changes |
| `terraform-apply.yml` | Manual | `self-hosted` | Applies Terraform configuration |

**Quick Start:**
```bash
# Build VM template with Packer (local)
cd infrastructure_tooling/proxmox/packer/ubuntu-server-noble
packer build -var-file="secrets.pkrvars.hcl" ubuntu-2404.pkr.hcl

# Or trigger parallel builds via GitHub Actions
# Go to Actions > Build Proxmox Packer Template > Run workflow

# Deploy VMs with Terraform
cd ../terraform
terraform plan -var-file="secrets.tfvars"
terraform apply -var-file="secrets.tfvars"
```

📖 **Full Documentation:** See the complete setup guide, configuration examples, and security practices in [`infrastructure_tooling/proxmox/README.md`](../infrastructure_tooling/proxmox/README.md)


## Additional Links
- [Don’t run Proxmox without these settings](https://www.youtube.com/watch?v=VAJWUZ3sTSI)
