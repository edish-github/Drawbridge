.PHONY: help bootstrap seed run-local deploy demo teardown test lint probe

PYTHON ?= python

help:           ## list available targets
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "%-12s %s\n", $$1, $$2}'

bootstrap:      ## enable APIs, create topics, buckets, firestore, IAM, armor templates
	./infra/bootstrap.sh

seed:           ## load synthetic vendors into Firestore + Storage
	$(PYTHON) -m scenarios.seed

run-local:      ## ADK dev UI against the agent packages
	adk web agents/

deploy:         ## build + push + deploy agents and services
	./infra/deploy/deploy_all.sh

demo:           ## run the scripted end-to-end demo scenario
	$(PYTHON) -m scenarios.demo_runner --vendor nimbuswrite --compress 240

teardown:       ## delete everything except the dashboard service
	./infra/teardown.sh

test:           ## run the test suite
	pytest -q

lint:           ## static checks
	ruff check .

probe:          ## capability probe, writes infra/CAPABILITY-REPORT.md
	$(PYTHON) -m scripts.probe_geap
