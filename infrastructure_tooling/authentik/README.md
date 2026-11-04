# Authentik

`https://auth.bmosan.com`: the homelab's identity provider, on k3s-01 and reachable only from the tailnet. It gives Splunk Web and the Cribl UI one login with a required second factor:

| App | How | Why |
|---|---|---|
| Splunk Web | SAML (Splunk's own SSO) | The Developer license includes `SAMLAuth`. |
| Cribl UI | Traefik forward auth in front of the Leader (`cribl.bmosan.com`) | Cribl's Free license has no external login (`remote_auth: 0`), so Authentik guards the route and Cribl's own login still follows. |

This chart deploys Authentik and the routes for both apps.

## What's here

- **Upstream chart** `authentik` 2026.8.5, image pinned by digest. One server and one worker, run as non-root under the `restricted` Pod Security level.
- **PostgreSQL** (`templates/postgres.yaml`): the official image, one replica, 5 Gi on local-path. A NetworkPolicy lets only Authentik's pods reach it. The upstream chart's PostgreSQL option is the Bitnami chart, which no longer has free images.
- **No Redis.** Authentik dropped it in 2025.10.
- **No Kubernetes API access** (`serviceAccount.create: false`). The Cribl forward auth uses the outpost embedded in the server, so Authentik never deploys outposts of its own.
- **Blueprints** (`blueprints/*.yaml`, mounted from a ConfigMap): Authentik configuration kept in Git. The worker applies each one when it changes and re-applies it every hour, so UI edits to these objects revert.
  - `session.yaml`: a login lasts 5 hours from sign-in (Authentik's default ends it when the browser closes), so one password and second factor cover Splunk Web and the Cribl UI gate for that time. The Cribl gate's token is 5 hours too (`cribl.yaml`). Cribl's own login still follows: its Free license can't take Authentik's identity.
  - `require-mfa.yaml`: the default login flow's MFA stage requires TOTP or WebAuthn, and makes a user without either enrol one before the login completes.
  - `splunk.yaml`: the Splunk SAML provider and application, its `role`, `mail` and `realName` attributes, and the entitlement `splunk-admin` for `authentik Admins` (Splunk's side: `monitoring_stack/splunk/apps/00_homelab_auth`).
  - `cribl.yaml`: a forward-auth proxy provider for `cribl.bmosan.com`, the Cribl application (open to `authentik Admins` only), and the provider bound to the embedded outpost.
- **Ingress**: Traefik, cert-manager DNS-01, `tailnet-only` middleware (403 to anything outside the tailnet ranges).
- **Splunk Web** (`templates/splunk.yaml`): `https://splunk.bmosan.com`, tailnet-only, to Splunk Web at `https://192.168.4.11:8000` (homelab-CA cert, name `splunk-homelab`). No forward auth: Splunk itself sends the login here.
- **Cribl UI** (`templates/cribl.yaml`): `https://cribl.bmosan.com`, tailnet-only. Traefik sends each request to the embedded outpost (`authentik-forward-auth` middleware) and, once Authentik allows it, on to the Leader at `https://192.168.4.11:9000`, a Service without a selector plus its Endpoints. Traefik checks the Leader's cert against the homelab CA (`files/homelab-ca.crt`) for the name `cribl-homelab`. `/outpost.goauthentik.io` on the same host goes to Authentik without forward auth, for the login start and callback. Traefik runs on the host network and can't resolve cluster DNS names, so the outpost is reached through the Service `authentik-forward-auth` at a fixed `10.43.200.11`. The Leader's firewall (`monitoring_stack/splunk/scripts/mgmt-firewall.sh`) accepts port 9000 only from k3s-01.

## Outside Git

- **Vault `kv/authentik`**: `secret_key`, `postgres_password`, `bootstrap_password`. Generated inside the Vault pod so no value is printed or leaves Vault:

  ```bash
  kubectl -n vault exec -i vault-0 -- sh -c 'export VAULT_TOKEN="$(cat)"; r() { head -c "$1" /dev/urandom | base64 | tr -d "\n/+="; }; vault kv put kv/authentik secret_key="$(r 60)" postgres_password="$(r 32)" bootstrap_password="$(r 24)" >/dev/null && vault kv metadata get kv/authentik | grep -E "^(current_version|created_time)"' \
    < <(kubectl -n default get secret vault-token -o jsonpath='{.data.token}' | base64 -d)
  ```

  `secret_key` must never change after the first start: it signs sessions and derives IDs. `bootstrap_password` is read only on the very first start, as the password of the built-in `akadmin` user. Read it in the Vault UI to log in the first time.
- **Cloudflare DNS**: `A auth.bmosan.com`, `A cribl.bmosan.com` and `A splunk.bmosan.com → 100.124.194.87` (k3s-01's tailnet IP), DNS-only, like `vault`, `mcp` and `apiary`.
- **SAML signing keypair** `splunk-saml-signing`: a self-signed RSA 4096 certificate, valid 100 years, generated inside Authentik so the private key never left it. Its public cert is in `monitoring_stack/splunk/auth/authentik-saml.crt`. It has to be self-signed: Authentik's own `CertificateBuilder` (and the UI's Generate button) issue certificates from Authentik's internal CA, which Splunk rejects ("unable to get local issuer certificate", logged as "failed to verify signature with cert"). To regenerate it:

  ```bash
  kubectl -n authentik exec -i deploy/authentik-worker -- ak shell -c "
  import datetime
  from cryptography import x509
  from cryptography.x509.oid import NameOID
  from cryptography.hazmat.primitives import hashes, serialization
  from cryptography.hazmat.primitives.asymmetric import rsa
  from authentik.crypto.models import CertificateKeyPair
  key = rsa.generate_private_key(public_exponent=65537, key_size=4096)
  name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'splunk-saml-signing'), x509.NameAttribute(NameOID.ORGANIZATION_NAME, 'bmosan homelab')])
  now = datetime.datetime.now(datetime.timezone.utc)
  cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
      .serial_number(x509.random_serial_number()).not_valid_before(now - datetime.timedelta(minutes=5))
      .not_valid_after(now + datetime.timedelta(days=36500))
      .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
      .sign(key, hashes.SHA256()))
  kp, _ = CertificateKeyPair.objects.get_or_create(name='splunk-saml-signing')
  kp.certificate_data = cert.public_bytes(serialization.Encoding.PEM).decode()
  kp.key_data = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
  kp.save()
  print(kp.certificate_data)"
  ```

  Then commit the printed certificate as `auth/authentik-saml.crt` and redeploy the Splunk stack.
- **Argo CD Application**: `kubectl apply -f infrastructure_tooling/authentik/application.yaml`. Argo CD doesn't manage the Application itself, so re-apply it after changing that file.

## First login

1. Log in at `https://auth.bmosan.com` as `akadmin` with `bootstrap_password`.
2. Authentik asks you to enrol a second factor (TOTP app or passkey) before the login completes, as `require-mfa.yaml` requires.
3. Create your own user, add it to the `authentik Admins` group, log in as that user (enrolling its own second factor), then deactivate `akadmin`.

## Break-glass

`akadmin` is deactivated. If you're locked out, this prints a one-time admin login link for it (valid one year; treat it like a password):

```bash
kubectl -n authentik exec deploy/authentik-worker -- ak create_recovery_key 1 akadmin
```

## Logs

Authentik's pod logs reach Splunk through the k3s OTel collector (`k8s.namespace.name=authentik`). Alerts on failed logins and MFA failures come with the Splunk change.
