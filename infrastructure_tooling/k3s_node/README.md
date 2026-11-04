# k3s-01 node

What k3s-01 needs on the host itself, which Argo CD can't apply, and how k3s is upgraded.

| File | Installed to | Why |
|---|---|---|
| `99-inotify.conf` | `/etc/sysctl.d/` | Falco and other pods that watch files fail to start at the default inotify limit |
| `99-k3s-traefik.conf` | `/etc/sysctl.d/` | Traefik runs as non-root on the host network and must bind 80 and 443 |
| `traefik-config.yaml` | `/var/lib/rancher/k3s/server/manifests/` | Traefik on the host network, no Service (there is no load balancer) |
| `k3s-version` | | The k3s release and its binary's SHA-256, for `upgrade-k3s.sh` |

The API audit settings are separate: `../k3s_audit`.

On a rebuilt node, run `install.sh` and `../k3s_audit/install.sh` (see its README) before the Argo CD apps sync. Copy the directory over and run as root:

```bash
scp -r infrastructure_tooling/k3s_node root@k3s-01:/root/
ssh root@k3s-01 /root/k3s_node/install.sh
```

## Upgrading k3s

Kubernetes moves one minor version at a time, and k3s can't be downgraded, so each step is: check compatibility, snapshot the VM, upgrade, verify.

1. **Check what the new minor needs.** Read the k3s release notes for the bundled component versions, and check that everything installed supports the new Kubernetes version:
   - Argo CD: `docs/operator-manual/tested-kubernetes-versions.md`.
   - cert-manager: its supported releases table.
   - External Secrets: its stability table.
   - Vault: its chart.
   - The ToolHive, ARC, Falco, Trivy and OTel charts.
   - Clients still calling deprecated APIs: `k3s kubectl get --raw /metrics | grep apiserver_requested_deprecated_apis`.
2. **Set `k3s-version`** to the latest patch of the next minor. Take the SHA-256 for `k3s` from that release's `sha256sum-amd64.txt`. Open a PR.
3. **Snapshot the VM** (k3s-01 is VM 103 on proxmox3; `local-lvm` is thin LVM):
   ```bash
   ssh root@proxmox3 qm snapshot 103 pre-k3s-<version> --description "before k3s <version>"
   ```
4. **Upgrade** after the PR is merged:
   ```bash
   scp -r infrastructure_tooling/k3s_node root@k3s-01:/root/
   ssh root@k3s-01 /root/k3s_node/upgrade-k3s.sh
   ```
   The API is down for about a minute. Ingress (Authentik, Vault, the MCP gateway, Splunk and Cribl logins) drops while Traefik rolls.
5. **Verify:**
   - The node is Ready on the new version.
   - Every Argo CD app is Synced and Healthy.
   - The Traefik pod is running.
   - `https://splunk.bmosan.com` and `https://cribl.bmosan.com` reach Authentik.
   - The MCP gateway tools answer.
   - `index=audit` and the k3s OTel collector are still sending.
   - AdGuard answers DNS on 192.168.4.98.
6. **Delete the snapshot** once it has run cleanly for a day (`qm delsnapshot 103 <name>`). A thin-LVM snapshot grows with every write.

Rollback: `qm rollback 103 <name>` on proxmox3. This returns the whole VM to the snapshot, including Vault, Authentik and AdGuard data written since.

### Path from 1.31 (November 2026)

| Step | k3s | Traefik | Also needed |
|---|---|---|---|
| 1 | 1.32.13 | 3.6 (chart 39), from 2.11 | Nothing else: Argo CD 3.4, cert-manager 1.17 and the rest support 1.32. **Done 2026-11-04.** |
| 2 | 1.33 | chart 40 | cert-manager 1.17 is the last that stops at 1.33: upgrade it to 1.20+ before step 3 |
| 3 | 1.34 | chart 40 | Argo CD 3.4 → 3.6 here (3.6 is tested on 1.34+) |
| 4 | 1.35, 1.36 | chart 40 | External Secrets 0.18 and Vault 1.18 need upgrading on the way |

Traefik 3 changes for this cluster:
- The Ingress routes, `ipAllowList` and forwardAuth middlewares, and ServersTransports already use the `traefik.io` API that v3 reads.
- Selectorless Services (`authentik/cribl-leader`, `authentik/splunk-web`) work through the EndpointSlices Kubernetes mirrors from their Endpoints.
- Traefik's internal port moves from 9000 to 8080 on the host.
