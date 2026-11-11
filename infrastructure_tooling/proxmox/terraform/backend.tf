terraform {
  backend "consul" {
    address = "http://consul.bmosan.com:8500"
    path    = "terraform/proxmox/infra"
  }
}
