# siem-software-factory POC. The targets without "remote-" run on security-tooling-01
# as root in /opt/splunk-stack/siem-software-factory (deploy.sh, or `make push`,
# puts this directory there). The remote-* targets run them from a workstation over
# Tailscale SSH. Every target is safe to re-run.
HOST       ?= root@security-tooling-01
REMOTE_DIR ?= /opt/splunk-stack/siem-software-factory
MAX        ?= 100
PY         := python3 scripts

.PHONY: help unit check prereq setup plan rotate collect replay test test-1000 report clean clean-all \
        push remote-% pull

help:
	@sed -n 's/^## //p' $(MAKEFILE_LIST)

## unit        offline tests (no services needed; CI-safe)
unit:
	python3 -m unittest discover -s tests -v
## prereq      check Splunk, Cribl, Worker -> Splunk TLS, mounts, source data (before setup)
prereq:
	$(PY)/check.py --prereq
## check       prereq + validate the POC configuration (read-only)
check:
	$(PY)/check.py
## plan        show what setup would create or change
plan:
	$(PY)/setup.py --plan
## setup       create or update the POC resources in Splunk and Cribl (idempotent)
setup:
	$(PY)/setup.py
## rotate      mint new Splunk tokens and store them again
rotate:
	$(PY)/setup.py --rotate
## collect     run the collector once and save a dataset (MAX=100, at most 1000)
collect:
	$(PY)/collect.py --max $(MAX)
## replay      replay a dataset to index=devlogs (DATASET=<id>, default the newest)
replay:
	$(PY)/replay.py $(DATASET)
## test        capture MAX events, replay them, run tests A-D, write reports/<id>/
test:
	$(PY)/run_tests.py --max $(MAX)
## test-1000   the same with 1000 events
test-1000:
	$(PY)/run_tests.py --max 1000
## report      print the newest test report
report:
	@cat reports/latest.md
## clean       remove the POC's Splunk and Cribl resources (keeps datasets). CONFIRM=yes to apply
clean:
	$(PY)/cleanup.py $(if $(filter yes,$(CONFIRM)),--yes)
## clean-all   the same, and delete the saved datasets too. CONFIRM=yes to apply
clean-all:
	$(PY)/cleanup.py --purge-samples $(if $(filter yes,$(CONFIRM)),--yes)

## push        (workstation) copy this directory to the host
push:
	tar --exclude './samples/*/*' --exclude './reports/*' --exclude '__pycache__' -cf - . | \
	    tailscale ssh $(HOST) 'mkdir -p $(REMOTE_DIR) && tar -xf - -C $(REMOTE_DIR)'
## remote-X    (workstation) push, then run target X on the host, e.g. make remote-test MAX=1000
remote-%: push
	tailscale ssh $(HOST) 'make -C $(REMOTE_DIR) $* MAX=$(MAX) DATASET=$(DATASET) CONFIRM=$(CONFIRM)'
## pull        (workstation) copy dataset DATASET=<category>/<id> into samples/ (git-ignored)
pull:
	@test -n "$(DATASET)" || { echo "set DATASET=<category>/<id>"; exit 1; }
	mkdir -p samples/$(DATASET)
	tailscale ssh $(HOST) 'tar -C /srv/siem-factory/samples/$(DATASET) -cf - .' | tar -xf - -C samples/$(DATASET)
	cd samples/$(DATASET) && sha256sum -c SHA256SUMS
