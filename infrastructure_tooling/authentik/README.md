# Authentik

`https://auth.bmosan.com`: the homelab's identity provider, on k3s-01 and reachable only from the tailnet. It gives Splunk Web and the Cribl UI one login with a required second factor:

| App | How | Why |
|---|---|---|
| Splunk Web | SAML (Splunk's own SSO) | The Developer license includes `SAMLAuth`. |
| Cribl UI | Traefik forward auth in front of the Leader (`cribl.bmosan.com`) | Cribl's Free license has no external login (`remote_auth: 0`), so Authentik guards the route and Cribl's own login still follows. |

This chart deploys Authentik itself. The Splunk and Cribl wiring come in later changes.

## What's here

- **Upstream chart** `authentik` 2026.8.5, image pinned by digest. One server and one worker, run as non-root under the `restricted` Pod Security level.
- **PostgreSQL** (`templates/postgres.yaml`): the official image, one replica, 5 Gi on local-path. A NetworkPolicy lets only Authentik's pods reach it. The upstream chart's PostgreSQL option is the Bitnami chart, which no longer has free images.
- **No Redis.** Authentik dropped it in 2025.10.
- **No Kubernetes API access** (`serviceAccount.create: false`). The Cribl forward auth uses the outpost embedded in the server, so Authentik never deploys outposts of its own.
- **Blueprints** (`blueprints/*.yaml`, mounted from a ConfigMap): Authentik configuration kept in Git. The worker applies each one when it changes and re-applies it every hour, so UI edits to these objects revert.
  - `require-mfa.yaml`: the default login flow's MFA stage requires TOTP or WebAuthn, and makes a user without either enrol one before the login completes.
- **Ingress**: Traefik, cert-manager DNS-01, `tailnet-only` middleware (403 to anything outside the tailnet ranges).

## Outside Git

- **Vault `kv/authentik`**: `secret_key`, `postgres_password`, `bootstrap_password`. Generated inside the Vault pod so no value is printed or leaves Vault:

  ```bash
  kubectl -n vault exec -i vault-0 -- sh -c 'export VAULT_TOKEN="$(cat)"; r() { head -c "$1" /dev/urandom | base64 | tr -d "\n/+="; }; vault kv put kv/authentik secret_key="$(r 60)" postgres_password="$(r 32)" bootstrap_password="$(r 24)" >/dev/null && vault kv metadata get kv/authentik | grep -E "^(current_version|created_time)"' \
    < <(kubectl -n default get secret vault-token -o jsonpath='{.data.token}' | base64 -d)
  ```

  `secret_key` must never change after the first start: it signs sessions and derives IDs. `bootstrap_password` is read only on the very first start, as the password of the built-in `akadmin` user. Read it in the Vault UI to log in the first time.
- **Cloudflare DNS**: `A auth.bmosan.com → 100.124.194.87` (k3s-01's tailnet IP), DNS-only, like `vault`, `mcp` and `apiary`.
- **Argo CD Application**, applied once: `kubectl apply -f infrastructure_tooling/authentik/application.yaml`.

## First login

1. Log in at `https://auth.bmosan.com` as `akadmin` with `bootstrap_password`.
2. Authentik asks you to enrol a second factor (TOTP app or passkey) before the login completes, as `require-mfa.yaml` requires.
3. Create your own user, add it to the `authentik Admins` group, log in as that user (enrolling its own second factor), then deactivate `akadmin`.

## Logs

Authentik's pod logs reach Splunk through the k3s OTel collector (`k8s.namespace.name=authentik`). Alerts on failed logins and MFA failures come with the Splunk change.
