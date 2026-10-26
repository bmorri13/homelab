# cc-unifi-ocsf

Cribl Stream pack that converts UniFi Network SIEM syslog into [OCSF](https://schema.ocsf.io/) 1.9.0 JSON.

It handles the two formats a UniFi gateway sends:

- **CEF** (Traffic Logging > Activity Logging > SIEM Server): `CEF:0|Ubiquiti|UniFi Network|...` with `UNIFI*` extension keys.
- **Kernel firewall lines**: `[WAN_LOCAL-D-2147483647] DESCR="..." SRC=... DST=... PROTO=...`.

Other events pass through unchanged.

## Event mapping

| UniFi event | OCSF class | Notes |
|---|---|---|
| IDS/IPS "Threat Detected", "Threat Detected and Blocked" | Detection Finding (2004) | Signature in `finding_info.analytic` (`uid` is the Suricata SID). Endpoints are in `evidences[0]`. The home-network side of the flow is `resources[0]` (type Host, role Affected), which the OCSF-CIM add-on maps to CIM Alerts `dest`. Detected-only events get disposition "Detected". |
| Zone-based firewall CEF (`UNIFIpolicyType`, `UNIFIsrcZone`, `UNIFIdstZone`), kernel firewall lines | Network Activity (4001) | Activity Refuse (blocked) or Traffic (allowed), with `firewall_rule`. |
| WiFi or wired client connected, disconnected, roamed | Network Activity (4001) | Activity Open, Close, or Other (`activity_name` "Roam"). The client is `src_endpoint`; the AP is `dst_endpoint`. |
| Admin login or "accessed UniFi Network" | Authentication (3002) | |
| Config changes (`UNIFIcategory=Audit`, "Config Modified") | Device Config State Change (5019) | The admin is `actor.user.name`. |
| Anything else (internet down, device offline, updates, latency) | Base Event (0) | The UniFi event name is `activity_name`. |

Every event carries `metadata` (product, version, CEF event code), `device` (the UniFi gateway or controller), `severity_id`, and `time` from `UNIFIutcTime`. `UNIFI*` keys the mapping does not use are kept under `unmapped`.

## Output

- `_raw` is the OCSF event as JSON, and the parsed syslog fields are removed.
- `sourcetype` is set to `ubiquiti:unifi:ocsf`, and `_time` is set from the OCSF `time`.
- `__ocsf_class_uid` is set as an internal field, so the calling pipeline can pick an index without parsing `_raw`.

The pack does not set `index`. In this stack, the `syslog_network` pipeline does that: classes 3002 and 5019 go to `audit`, and everything else goes to `network`.

## Using it

Call it from a pipeline with a Chain function (`processor: pack:cc-unifi-ocsf`), or route to it directly. `scripts/cribl-packs.sh` installs it through the Cribl API. Bump `version` in `package.json` to roll out a change.

## Implementation notes

The logic is one Code function. Cribl's Code function checker is stricter than Node, and rejects:

- `??`, `?.`, and object spread
- anything that leaves an empty syntax-tree node, such as `for (;;)` without an initializer or array destructuring holes
- any identifier named `proto`

Cribl's syslog parser reads `CEF:` as the RFC 3164 tag, so the pack parses `_raw` rather than `message`.

## Testing

`data/samples/unifi_cef_samples.json` holds 22 events:

- real events from this network (sanitized)
- public UniFi CEF examples
- three synthetic events: a zone-firewall block, a kernel firewall line, and an admin login

All 22 convert in Cribl 4.20.3, and every output passes the OCSF 1.9.0 validator (`https://schema.ocsf.io/api/v2/validate`) with no errors or warnings.
