# Secret leak: unmasked credential in indexed events

A credential pattern (private key, cloud/API key, token, URL or key/value password) was found unmasked in indexed data. Cribl masks known patterns, so this is a new format or a source that bypasses masking.

Check which index, sourcetype and host, and find the events with a bounded search that does not print the value: e.g. count by source and a short `substr` of the surrounding field name, never the full `_raw`. Work out which application logged it and whether the credential is real (test fixtures and documentation examples are common). Next steps should name the credential type and location so the owner can rotate it and fix the masking; never include the value.
