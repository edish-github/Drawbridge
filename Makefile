.PHONY: help bootstrap bootstrap-dry rules emulators emulators-stop seed reset run-local dev-ui open-review \
        deploy demo demo-fixtures demo-crash demo-second binder dashboard teardown test lint probe \
        graph replay console corpus

PYTHON ?= python

help:           ## list available targets
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "%-12s %s\n", $$1, $$2}'

bootstrap:      ## enable APIs, create topics, buckets, firestore, IAM, armor templates
	./infra/bootstrap.sh

# The first real run should not also be the first run. Prints every gcloud command the real
# bootstrap would issue, in order, without a project and without touching anything.
bootstrap-dry:  ## rehearse the bootstrap: print every command, execute none
	DRY_RUN=1 ./infra/bootstrap.sh

# The rules are generated from the permission matrix rather than hand-written, so the table in
# the README and the ruleset the emulator enforces cannot drift apart. Regenerating before the
# emulator starts is what makes a matrix edit take effect in the suite on the next run.
rules:          ## regenerate infra/firestore/firestore.rules from the permission matrix
	@set -a; [ -f .env ] && . ./.env; set +a; \
		$(PYTHON) -m infra.firestore.generate_rules \
			--project $${PROJECT_ID:-drawbridge-local}

emulators: rules ## start the Firestore and Pub/Sub emulators for local mode
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

# Pillar three, as a beat rather than a claim. Two real processes: the first is SIGKILLed with
# the questionnaire already sent and the checkpoint not yet written, the second finishes the
# review. The vendor is emailed once across both.
demo-crash:     ## kill the worker mid-send, restart it, and finish the review
	$(UNSCREENED) $(PYTHON) -m scenarios.crash_demo --vendor $(or $(VENDOR),datadynamo)

# Layer three of the memory hierarchy, made visible. Runs one review to a decision, then opens
# a second review of the same vendor and shows what it already knew and what changed because of
# it. Without this beat, durable memory is a store nothing visibly reads.
demo-second:    ## review the same vendor twice and show what the second one already knew
	$(UNSCREENED) $(PYTHON) -m scenarios.second_review --vendor $(or $(VENDOR),datadynamo)

# The graph is data, so the picture and the dashboard's copy of it are outputs rather than
# artefacts somebody maintains. Regenerates docs/diagrams/src/23-review-graph.mmd and
# services/dashboard/lib/graph.json; tests/test_graph_ui.py fails if the committed copies are
# stale, and scripts/check_contracts.py --check graph fails if the graph stopped describing the
# code. Rendering the .mmd to SVG and PNG is the mermaid loop in docs/diagrams/README.md.
graph:          ## regenerate the review graph: diagram 23 source and the dashboard's copy
	$(PYTHON) -m scripts.graph_dump --format mermaid
	$(PYTHON) -m scripts.graph_dump --format json
	@$(PYTHON) -m scripts.graph_dump --format text

# The console is TypeScript and the fleet is Python, so the graph and the policy reach the screen
# through generated files rather than being declared twice. tests/test_console.py regenerates and
# diffs, so a stale copy fails the suite rather than quietly showing last week's rubric.
console:        ## regenerate what the operator console reads: the graph and the policy snapshot
	$(PYTHON) -m scripts.graph_dump --format json
	$(PYTHON) -m scripts.policy_dump

# One review's actual path through the graph, reconstructed from the ledger. Reads only records
# written for other reasons, so it works on reviews that ran before the projection existed.
replay:         ## project one review onto the graph (REVIEW=<id>)
	$(PYTHON) -m scripts.graph_dump --review $(REVIEW)

corpus:         ## run the injection corpus and write the measured detection table
	$(UNSCREENED) $(PYTHON) -m scripts.corpus_run

binder:         ## render a review's audit binder to HTML (REVIEW=<id>)
	$(PYTHON) -m services.binder.render --review-id $(REVIEW)

# The emulator partitions on project id, so the dashboard has to read under the same one the
# fleet writes under. .env is the single place that is configured, so the target reads it rather
# than restating a default that would drift.
dashboard: emulators console ## the operator console at localhost:3000
	@set -a; [ -f .env ] && . ./.env; set +a; \
		export FIRESTORE_EMULATOR_HOST=$${FIRESTORE_EMULATOR_HOST:-localhost:8080}; \
		cd services/dashboard && npm install --silent --no-audit --no-fund && npm run dev

teardown:       ## delete everything except the dashboard service
	./infra/teardown.sh

test:           ## run the test suite
	pytest -q

# Two kinds of static check, and the second is the one that catches design drift rather than
# style. check_contracts diffs the things that are written down in more than one place —
# topics, the rubric, the question bank, the permission matrix, and the review graph against
# the code that executes it.
lint:           ## static checks: style, then the cross-file contracts
	ruff check .
	$(PYTHON) -m scripts.check_contracts

probe:          ## capability probe, writes infra/CAPABILITY-REPORT.md
	$(PYTHON) -m scripts.probe_geap
