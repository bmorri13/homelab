# UniFi configuration changed

Source: `index=audit` UniFi admin/config events (about one change a day is normal: the owner tuning WiFi, firewall or CyberSecure).

Check who made the change (admin name, source), what changed (firewall policy, logging/SIEM settings, IDS/IPS, networks, WLANs), and whether it weakens anything: logging or IDS turned off, a firewall allow rule from the internet, a new port forward, a new admin. Changes by the owner from the LAN are expected_activity unless they weaken security, in which case say what they weakened.
