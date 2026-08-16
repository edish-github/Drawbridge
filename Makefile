.PHONY: help bootstrap emulators emulators-stop seed run-local dev-ui open-review deploy demo \
        demo-fixtures teardown test lint probe

PYTHON ?= python

help:           ## list available targets
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "%-12s %s\n", $$1, $$2}'

bootstrap:      ## enable APIs, create topics, buckets, firestore, IAM, armor templates
	./infra/bootstrap.sh

emulators:      ## start the Firestore and Pub/Sub emulators for local mode
	./scripts/emulators.sh

emulators-stop: ## stop the emulators
	@for p in .emulators/*.pid; do \
		[ -f "$$p" ] || continue; \
		kill $$(cat $$p) 2>/dev/null && echo "stopped $$(basename $$p .pid)"; \
		rm -f $$p; \
	done

seed: emulators ## load synthetic vendors into Firestore and object storage
	$(PYTHON) -m scenarios.seed

run-local: emulators ## run the worker: pull events, dispatch to agents, acknowledge
	$(PYTHON) -m scripts.run_local

dev-ui:         ## ADK dev UI against the agent packages, for inspecting one agent
	adk web agents/

open-review:    ## open a review for a synthetic vendor (VENDOR=nimbuswrite)
	$(PYTHON) -m scripts.open_review --vendor $(or $(VENDOR),nimbuswrite)

deploy:         ## build + push + deploy agents and services
	./infra/deploy/deploy_all.sh

demo:           ## run the end-to-end scenario against the live models (needs quota)
	$(PYTHON) -m scenarios.demo_runner --vendor $(or $(VENDOR),datadynamo)

demo-fixtures:  ## run the same scenario with fixture answers: free, deterministic, no model call
	$(PYTHON) -m scenarios.demo_runner --vendor $(or $(VENDOR),datadynamo) --fixtures-only

teardown:       ## delete everything except the dashboard service
	./infra/teardown.sh

test:           ## run the test suite
	pytest -q

lint:           ## static checks
	ruff check .

probe:          ## capability probe, writes infra/CAPABILITY-REPORT.md
	$(PYTHON) -m scripts.probe_geap
