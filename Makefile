.PHONY: help bootstrap emulators emulators-stop seed reset run-local dev-ui open-review deploy \
        demo demo-fixtures binder dashboard teardown test lint probe

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

reset: emulators ## clear every review's working state, then reseed (local emulator only)
	$(PYTHON) -m scenarios.seed --reset

run-local: emulators ## run the worker: pull events, dispatch to agents, acknowledge
	$(PYTHON) -m scripts.run_local

dev-ui:         ## ADK dev UI against the agent packages, for inspecting one agent
	adk web agents/

open-review:    ## open a review for a synthetic vendor (VENDOR=nimbuswrite)
	$(PYTHON) -m scripts.open_review --vendor $(or $(VENDOR),nimbuswrite)

deploy:         ## build + push + deploy agents and services
	./infra/deploy/deploy_all.sh

# Both demo targets run on seeded clean-bucket fixtures, which no detector inspected. P2
# refuses them by default and that refusal is correct, so the demo declares what it is running
# on rather than the policy being softened to let it through. Every review produced this way
# carries unscreened_fixtures=true and the binder prints it on the cover.
UNSCREENED = DRAWBRIDGE_ALLOW_UNSCREENED=1

demo:           ## run the end-to-end scenario against the live models (needs quota)
	$(UNSCREENED) $(PYTHON) -m scenarios.demo_runner --vendor $(or $(VENDOR),datadynamo)

demo-fixtures:  ## run the same scenario with fixture answers: free, deterministic, no model call
	$(UNSCREENED) $(PYTHON) -m scenarios.demo_runner --vendor $(or $(VENDOR),datadynamo) \
		--fixtures-only

binder:         ## render a review's audit binder to HTML (REVIEW=<id>)
	$(PYTHON) -m services.binder.render --review-id $(REVIEW)

# The emulator partitions on project id, so the dashboard has to read under the same one the
# fleet writes under. .env is the single place that is configured, so the target reads it rather
# than restating a default that would drift.
dashboard: emulators ## read-only operator view at localhost:3000
	@set -a; [ -f .env ] && . ./.env; set +a; \
		export FIRESTORE_EMULATOR_HOST=$${FIRESTORE_EMULATOR_HOST:-localhost:8080}; \
		cd services/dashboard && npm install --silent --no-audit --no-fund && npm run dev

teardown:       ## delete everything except the dashboard service
	./infra/teardown.sh

test:           ## run the test suite
	pytest -q

lint:           ## static checks
	ruff check .

probe:          ## capability probe, writes infra/CAPABILITY-REPORT.md
	$(PYTHON) -m scripts.probe_geap
