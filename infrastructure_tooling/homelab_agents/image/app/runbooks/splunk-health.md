# Splunk scheduled searches skipped or failing

`index=_internal sourcetype=scheduler` has status, reason, savedsearch_name, app, run_time. Skipped with "maximum number of concurrent" means the scheduler is overloaded at that minute (several searches on the same cron); failed means the search errored (see `index=_internal sourcetype=splunkd log_level=ERROR` and the search's own text with the knowledge-object tools). The HL detections (85_detections) and the alerts in 85_falco, 85_opencanary, 85_secret_leaks feed the security agent, so if they skip, findings are missed: say which detections were affected.

Skills: skipped or delegated runs → invoke `dashboard-report-alert-performance-advisor`; failed runs → `search-and-dashboard-troubleshooter`; one search that takes too long to fit its schedule → `search-performance-optimizer`.
