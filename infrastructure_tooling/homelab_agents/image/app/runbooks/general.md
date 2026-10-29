# General

No specific runbook for this item. Establish who or what caused it (user, source, host, process), what else that actor did around the same time, whether it has happened before, and whether a deployment, CI job or the owner's own session explains it.

Skills: if the detection itself looks wrong (it matched events that don't show what its name says), invoke `search-and-dashboard-troubleshooter`; if a field it relies on (user, host, src) is empty or wrong in the raw events, `field-extraction-and-cim-mapping`.
