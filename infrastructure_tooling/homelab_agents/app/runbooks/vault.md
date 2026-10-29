# Vault detections (root token, auth/policy changes, audit device, permission-denied bursts, new reader)

Source: `index=audit sourcetype=hashicorp_vault_audit_log`. Tokens and secret values are HMACed; identities show as auth.display_name, auth.policies, auth.metadata.service_account_name / role.

Known activity:
- Root token use: the owner administering Vault from vault-0 (`kubectl -n vault exec vault-0`). Check the Kubernetes audit for an exec into vault-0 by system:admin just before.
- External Secrets (role external-secrets) and Apiary (role apiary) read and write on loops.
- The agent runner (role homelab-agents) reads kv/slack-alerts, kv/cribl-hec and kv/homelab-agents every run.

Check: request.path, request.operation, auth.display_name and remote_address; whether the change shows up as an exec into vault-0 by the owner; and for a new reader, whether a new ExternalSecret or app was deployed (Argo CD events) that explains it. A permission-denied burst from a known client after a policy change is usually a misconfiguration; from an unknown address it's probing.
