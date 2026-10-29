# Docker containers restarting

Docker hosts: docker-compose-02, docker-compose-03 (also security-tooling-01's Splunk/Cribl stack). Container logs are in `index=apps` (container.name, host). Find the error before each restart, the restart cadence, and memory (container.memory.percent near 100 means OOM). On docker-compose-03, recreating a compose network can lose Docker's FORWARD rules (k3s kube-router clobbers them), so containers lose egress and cloudflared tunnels fail.
