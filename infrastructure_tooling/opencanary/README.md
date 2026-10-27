# OpenCanary honeypot

[OpenCanary](https://github.com/thinkst/opencanary) (Thinkst, BSD licensed) runs on docker-compose-02 as a fake NAS called `nas-backup-01`. Nothing legitimate uses it, so any connection is worth a look: a scan, a compromised host exploring the LAN, or a misconfigured client.

| | |
|---|---|
| Address | `192.168.4.240` on proxmox-net (VLAN 4), outside the DHCP range (.6–.100) |
| MAC | `02:42:c0:a8:04:f0` (fixed, locally administered) |
| Host | docker-compose-02 (VM 101), `/opt/opencanary` |
| Image | `thinkst/opencanary:0.9.10`, pinned by digest |
| Services | FTP 21, SSH 22, Telnet 23, HTTP 80 (NAS login page), TFTP 69/udp, SNMP 161/udp, MSSQL 1433, MySQL 3306, RDP 3389, VNC 5900, Redis 6379, Git 9418 |

The container has its own LAN address through a Docker macvlan network, so it looks like a separate machine. A macvlan container can't be reached from its own host: test it from another machine, not from docker-compose-02. The VM's Proxmox NIC has the firewall off, so the extra MAC address is allowed on `vmbr0`.

The container runs read-only, with all capabilities dropped except those needed to bind low ports and then switch to `nobody`.

## Logs and alerts

1. OpenCanary writes one JSON line per event to stdout.
2. docker-compose-02's OTel agent ships the container's log to Cribl.
3. Cribl's `hec_in` pipeline sends lines starting `{"dst_host"` to `index=security` with `sourcetype=opencanary`. Twisted's startup lines stay in `index=apps`.

The Splunk app `85_opencanary` (in `monitoring_stack/splunk/apps`):
- **Event names:** a lookup turns `logtype` into a name, such as `SSH login attempt` or `Redis command`.
- **CIM:** maps the events to Intrusion Detection, with `src`, `dest`, `dest_port`, `signature`, `user` and `severity` (`high` for login attempts).
- **Alert:** *Honeypot - connection to OpenCanary* runs every 5 minutes, appears under Triggered Alerts, and is suppressed per source for an hour.

Passwords that someone types into the honeypot are masked by Cribl's `splunk_out`, like any other `password` value. Only the usernames are kept.

## Deploy or change

```bash
rsync -a docker-compose.yml opencanary.conf root@docker-compose-02:/opt/opencanary/
ssh root@docker-compose-02 'cd /opt/opencanary && docker compose up -d --force-recreate'
```

To test, run from any other LAN host:

```bash
curl -s -o /dev/null -d 'username=test&password=test' http://192.168.4.240/index.html
```

The event reaches `index=security sourcetype=opencanary` within about a minute.

## Expected noise

Vulnerability scans (OpenVAS/Greenbone) and UniFi's own device discovery will touch the canary. If one becomes routine, add its address to `ip.ignorelist` in `opencanary.conf` rather than turning the alert off.
