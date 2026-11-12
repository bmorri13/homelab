# DNS detections (possible tunnelling, NXDOMAIN burst, zone transfer or ANY query)

Source: `index=network sourcetype=adguard:querylog`, AdGuard Home's query log. AdGuard only logs: nothing is blocked, so a query in the log was answered. Fields: `QH` (name), `QT` (type), `IP` (client, the finding's `src`), `Answer` (base64 DNS reply), `Cached`, `Upstream`. Names ending in `.tail<id>.ts.net` are clients appending the tailnet search domain, not separate lookups. `object` is the registered domain (or the first few domains or names).

Check:
- All of the client's DNS around the time: `index=network sourcetype=adguard:querylog IP="<src>" earliest=-1h latest=+1h | stats count values(QT) dc(QH) by QH | sort - count`, and whether it has done the same before (look back 7 days). A pattern that repeats daily from one device for weeks is usually software, not an intrusion.
- Tunnelling: look at the sample names. Random-looking labels, base32/hex/base64 text, or a counter in the name under one domain, from one device, look like data carried in DNS. A vendor's reputation or telemetry domain (antivirus, CDN, DNSBL) doing the same is benign: say which domain and suggest adding it to the `hl_dns_chatty_domain` macro in `85_detections`. Who owns the domain (well-known vendor or a fresh, unknown one) matters most.
- NXDOMAIN burst: are the failing domains random-looking strings (DGA) or real names that went away (an app pointed at a dead service, a typo, a misconfigured search domain)? A device that keeps trying random domains is likely infected.
- Zone transfer or ANY: which names were asked for, and from which device. `dig`, `nslookup` or `host -a` run by the owner looks like this; a device that isn't someone's workstation asking for zone transfers is reconnaissance.
- In every case, what the client reached next: other traffic from `src` in `index=network` around the same time.

Don't name devices, addresses or the network layout beyond what the finding shows.

Skills: if a field looks missing or wrong (no `Answer`, `IP` empty), invoke `field-extraction-and-cim-mapping`; to work on the search itself, `search-and-dashboard-troubleshooter`.
