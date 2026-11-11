# OpenVAS

[OpenVAS](https://github.com/greenbone) (Greenbone Community Edition) scans the network for vulnerabilities. This directory runs it as one container, `immauss/openvas`, which bundles the scanner, gvmd, PostgreSQL, Redis and the web UI (GSA) in one image.

**Not deployed.** OpenVAS isn't running anywhere today. It is meant to run on security-tooling-01, next to the Splunk logging box, and to be started when a scan is needed.

| | |
|---|---|
| Image | `immauss/openvas`, pinned by tag and digest in `docker-compose.yml` (Renovate bumps both) |
| Web UI | port 9392, bound to `127.0.0.1` unless `GVM_BIND_ADDR` says otherwise |
| Data | Docker volume `openvas_data` at `/data`: database, feeds and scan results |
| Login | `GVM_ADMIN_USER` (default `admin`) and `GVM_ADMIN_PASSWORD` (required) from `.env` |

## Resources

The immauss docs give no memory or disk figures; see [upstream](https://immauss.github.io/openvas/). Greenbone's own [Community Containers](https://greenbone.github.io/docs/latest/22.4/container/index.html#hardware-requirements) docs list 2 CPU cores, 4 GB RAM and 20 GB of storage as the minimum, and 4 cores, 8 GB and 60 GB as recommended. The image runs the same components, so use those as a guide.

The first start loads the feeds and rebuilds the database. The immauss docs say it can be scanning in 15 to 20 minutes with enough resources.

## Start and stop

```bash
cp .env-example .env   # then set GVM_ADMIN_PASSWORD
docker compose up -d
docker compose logs -f openvas   # wait for the feed load to finish
```

The web UI is then at `http://127.0.0.1:9392` on the host (plain HTTP, one reason it stays on loopback). From another machine, use an SSH tunnel (`ssh -L 9392:127.0.0.1:9392 <host>`), or set `GVM_BIND_ADDR` in `.env` to one of the host's own addresses and recreate the container. Docker publishes ports past the host firewall, so pick an address on a network you trust.

```bash
docker compose down        # stop; the volume and scan results stay
docker compose down -v     # stop and delete the volume (feeds reload on the next start)
```

Scans touch the OpenCanary honeypot; see [Expected noise](../../opencanary/README.md#expected-noise).
