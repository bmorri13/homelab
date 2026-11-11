# Detection search failing

An HL - detection (app 85_detections) errored when the scheduler ran it, so it detected nothing while it failed. The item's object is the detection name and detail has the latest error.

Check `index=_internal sourcetype=scheduler savedsearch_name="<object>"` for when the failures started, how many runs failed, and whether any run since succeeded; `index=_internal sourcetype=splunkd log_level=ERROR` around the first failure for the full message; and the search's own text with the knowledge-object tools. Common causes: a macro, lookup or field it uses was changed or removed by a recent change to the Splunk apps, a missing index or sourcetype, or a syntax error in a newly added detection. Say what the detection would have missed while it failed (its index and time range), and whether it has recovered.

Skills: to find what in the search causes the error, invoke `search-and-dashboard-troubleshooter`.
