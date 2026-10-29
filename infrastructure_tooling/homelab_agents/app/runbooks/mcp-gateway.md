# MCP gateway: write tool, denial, or new client

Source: `index=audit` k8s namespace toolhive-mcp, vMCP audit events (subjects.user_id = client ServiceAccount, target.name = tool, outcome).

Known clients: macbook-air, windows-desktop, linux-vm (people's machines), agent-sec and agent-ops (these agents). A denial from an agent means it tried a tool outside its allowlist, which is worth reporting but usually the model guessing. Denials right after a gateway change are often the owner testing.

Check which client, which tool, how many calls, and the outcome. Write-capable tools (Cribl pipeline config, notebooks, UniFi or firewall changes) called by anyone are worth a person's look; say exactly what was called with what arguments if the audit shows them. A new client name that isn't in the list above is serious.
