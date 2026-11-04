# Vault

Vault on k3s-01, from the upstream chart (`application.yaml`):
- Standalone, with file storage on a local-path volume.
- One Shamir unseal key. Any restart of the pod leaves Vault sealed until the key is entered.
- The UI and API are at `https://vault.bmosan.com` (tailnet only, `../vault_ingress`).

Clients:
- External Secrets (kubernetes auth role `external-secrets`).
- apiary and the homelab agents (kubernetes auth roles of the same names).
- People (userpass).
- The audit device writes to stdout, which is shipped to Splunk `index=audit`.

## Upgrading

Vault upgrades in place, but it doesn't promise that an older version can read the data afterwards, so the data backup is the rollback.

1. **Read the notes.** Read `docs/updates/important-changes` for every version crossed, in HashiCorp's docs, and check the chart's CHANGELOG.
2. **Merge, apply and sync.** Change `targetRevision` in a PR, merge it, then `kubectl apply -f infrastructure_tooling/vault/application.yaml` and sync `vault` in Argo CD. Vault keeps running on the old version (updateStrategy `OnDelete`).
3. **Back up the data.** This takes a crash-consistent copy while Vault is running, which is fine for file storage at this size. Also take a Proxmox snapshot of VM 103 to be safe.
   ```bash
   ssh root@k3s-01 'd=$(ls -d /var/lib/rancher/k3s/storage/pvc-*_vault_data-vault-0); tar --numeric-owner -czf /root/vault-data-$(date +%Y%m%d-%H%M).tgz -C "$d" .'
   ```
4. **Restart into the new version, with the unseal key at hand:**
   ```bash
   ssh root@k3s-01 'k3s kubectl -n vault delete pod vault-0 && k3s kubectl -n vault wait pod/vault-0 --for=jsonpath="{.status.phase}"=Running --timeout=300s'
   ssh root@k3s-01 k3s kubectl -n vault exec -it vault-0 -- vault operator unseal   # paste the key at the prompt
   ```
   While Vault is sealed, secret refreshes and Vault logins fail. Secrets already written stay in place.
5. **Verify:**
   - `vault status` shows the new version, unsealed.
   - The `vault-backend` ClusterSecretStore is Ready, and the ExternalSecrets resync (force one with the `force-sync` annotation).
   - apiary and the agents log in.
   - Audit events still reach `index=audit`.

Rollback: stop Vault (`kubectl -n vault scale sts vault --replicas=0`), restore the tarball into the volume directory with `tar --numeric-owner -xzf`, set `targetRevision` back, sync, scale back to 1 and unseal.
