# Splunk configuration or user changed outside a deploy

Sources: `index=_configtracker` (`data.path`, `data.action`, `data.changes{}.stanza`, `data.changes{}.properties{}.name`) and `index=_audit` (`create_user`, `edit_user`, `delete_user`, `edit_roles`, `info=succeeded`).

Known: deploys (`deploy.sh`) only write each app's `default/`, so a change under `local/`, a user's saved searches, or a security-relevant file in `etc/system/local` was made in Splunk Web, over REST or on the host. The owner sets the decoy macros in `85_detections/local/macros.conf` by hand, and creates users by hand now and then (`splunk-mcp-token.sh` creates `mcp-gateway`).

Check: which file, stanza and properties (`detail`; values are left out on purpose, don't go looking for secrets in them). What it does: an `HL - ` saved search disabled (`disabled`, `enableSched`), its search or schedule rewritten, an alert action removed, a macro widened so a detection matches less, a role given more capabilities or indexes, an output or input added, authentication changed. Who: `index=_audit` around the same time for the user, the REST endpoint and their login (`HL - Splunk local login`). A change that weakens detection, or by an account other than the owner's, is an incident.
