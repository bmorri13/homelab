# cc-falco-ocsf

Cribl Stream pack that converts Falco JSON alerts into [OCSF](https://schema.ocsf.io/) 1.9.0 **Detection Findings** (class 2004).

| Falco | OCSF |
|---|---|
| `rule` | `finding_info.title`, `finding_info.analytic.name` (type Rule) |
| `output` | `message`, `finding_info.desc` |
| `priority` | `severity_id`: Emergency/Alert/Critical → Critical, Error → High, Warning → Medium, Notice → Low, Info/Debug → Informational |
| `tags`: `T1555`, `T1059.004`, `mitre_execution`, ... | `finding_info.attacks[]` (technique uid, tactic uid and name, ATT&CK v15) |
| other `tags` (container, filesystem, shell) | `finding_info.types` |
| `proc.*`, `user.*` | `evidences[0].process` (with `file` and `parent_process`) |
| `container.*` | `evidences[0].container` (image uid, name, labels) |
| `fd.name` (a path) | `evidences[0].file` |
| `hostname` | `device.hostname`, `resources[0]` (type Host, role Affected), and the event's `host` |
| everything | `unmapped.output_fields`, `unmapped.tags`, `unmapped.priority` |

Falco only detects, so every finding has `action` Observed and `disposition` Detected. `sourcetype` is set to `falco:ocsf`. The internal field `__falco_alert` lets `hec_in` route the finding to `index=security`.

## Requirements

OCSF requires process PIDs and a container image ID. Falco emits these only when a rule's output mentions them, so the Falco config adds them to every syscall alert (`append_output` with `proc.pid`, `proc.ppid`, `container.image.id`).

Without them, the pack leaves out the process evidence rather than emitting an invalid object, and the details stay under `unmapped`.

## Testing

`data/samples/falco_alert_samples.json` holds 11 real alerts from security-tooling-01. The last four include the appended PID and image fields. All 11 pass the OCSF 1.9.0 validator with no errors or warnings.

Cribl's Code function also rejects identifiers named `process`, so the process object is called `procObj`. The other Cribl syntax limits are listed in the cc-unifi-ocsf README.
