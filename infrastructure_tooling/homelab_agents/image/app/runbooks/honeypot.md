# Honeypot (OpenCanary) interaction

OpenCanary on 192.168.4.240 runs fake services. Nothing legitimate should connect to it, so any interaction (logtype>=2000) is a scanner, a misconfigured device, or someone exploring the network.

Check `index=security sourcetype=opencanary src_host=<src>` for which ports and services, usernames/passwords tried (describe, don't quote passwords), and timing; identify the source (`index=network` UniFi logs for the IP/MAC, DHCP name); and whether the same source touched other hosts (`index=network src=<src>`). The owner's own scans or a new IoT device probing are common benign causes; repeated logins with credential lists are not.
