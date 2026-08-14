# Drawbridge

> The fleet that decides what crosses into the castle.

A governed fleet of agents that runs enterprise vendor security reviews end to end —
intake, questionnaire, evidence analysis, risk scoring, human approval, and continuous
monitoring — built on Google Cloud with ADK 2, Vertex AI Agent Engine, and Model Armor.

**Status:** scaffold. Nothing in this repository is a working product yet.

---

## The problem

TODO — the one-paragraph problem statement (master doc §3.6).

## Demo video

TODO — YouTube link, recorded 28 Aug.

## Architecture

TODO — architecture diagram, the four-layer memory hierarchy, the event backbone,
the five-layer adversarial-content defence.

## 30-minute spin-up

TODO — prerequisites, `make bootstrap`, `make seed`, `make deploy`, `make demo`,
with the exact commands a judge runs on a clean project.

```
make bootstrap   # enable APIs, create topics, buckets, Firestore, IAM, Model Armor templates
make seed        # load the synthetic vendor pack into Firestore and Cloud Storage
make deploy      # build and deploy agents and services
make demo        # run the scripted end-to-end scenario
make teardown    # delete everything except the dashboard
```

## Synthetic data

Every vendor, document and questionnaire answer in this repository is synthetic and was
generated for this project. No real vendor is named, described or implied.

`synthetic-vendors/nimbuswrite/` contains a deliberately planted prompt-injection payload
used to exercise the screening pipeline. Its location, contents and purpose are documented
in [`synthetic-vendors/nimbuswrite/README.md`](synthetic-vendors/nimbuswrite/README.md).
`synthetic-vendors/injection-corpus/` holds the variant set used to measure the defence.

TODO — the published injection-corpus detection table.

## Permission matrix

Nine identities, scoped at collection level. TODO — the table from handbook §3.2,
rendered here as the deliverable it is.

## Teardown

TODO — `make teardown`, what survives, and how to confirm spend.

## Licence

MIT. See [LICENSE](LICENSE).
