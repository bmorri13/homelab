# Isolated network device tried to reach a trusted network

Source: `index=network sourcetype=ubiquiti:unifi:ocsf`, firewall blocks logged by the policy "Log isolated network block" (src_endpoint.ip / hostname / mac, dst_endpoint.ip and port). The connection was blocked; the question is why the device tried.

Check the source device (hostname, MAC, the UniFi client name; whether it is new or has been seen for weeks), which targets and ports it tried, how many and over what time, and whether it has done this before (look back 7-30 days). One or two ports to a single address on a schedule usually means a device looking for a service it was paired with or a misconfigured address; many hosts or ports in a short time is a scan and suggests the device is compromised. Also check whether it reached anything that was allowed (`index=network src=<src>`). Don't describe the network layout beyond what the finding shows.
