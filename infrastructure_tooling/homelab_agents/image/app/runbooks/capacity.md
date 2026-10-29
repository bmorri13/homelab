# Capacity: filesystem, Proxmox storage, CPU, memory

Look at the trend over 24 hours to 7 days with mstats (span=1h): is it growing steadily, a sudden jump, or a short spike? For disks, estimate time to full from the growth rate.

Common causes here: Splunk indexes and the frozen archive on security-tooling-01 (/opt, /mnt/unraid-splunk), Docker images and volumes on docker-compose-0x (`docker system df` is what the owner would run), container logs, k3s images and local-path volumes on k3s-01, Proxmox backups and templates on node storage. For CPU/memory, find the top consumers: per-container (container.cpu.utilization, container.memory.usage.total), per-pod (k8s.pod.*), or Proxmox guest metrics for the VM itself. Splunk searches and Cribl can drive security-tooling-01 CPU.
