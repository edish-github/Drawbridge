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

## Running locally

No Google Cloud project, no billing account and no credentials. Local mode runs the kernel and
the agents against the Firestore and Pub/Sub emulators with a Gemini API key, which is the same
required-technology clause satisfied through a different endpoint.

```
cp .env.example .env      # set GEMINI_API_KEY; leave RUNTIME_MODE=local
make emulators            # Firestore, Pub/Sub, and the eleven topics with their subscriptions
make run-local            # the worker: pull events, dispatch to agents, acknowledge
```

Then, in a second terminal, walk a review from intake to first contact:

```
make open-review VENDOR=nimbuswrite
```

The worker tiers the review, checkpoints its plan, builds the questionnaire — and stops. The
gateway refuses the send under policy P1 because no human has approved first contact, the review
parks in `GATED` with `gate_scope=contact`, and the refusal is logged as a named policy:

```
P1 REJECTED · ref=trust@nimbuswrite.example
review=... parked at the contact gate — P1: outbound email requires a human approval token
```

Release it the way the approvals service will:

```
python -m scripts.issue_token --review-id <id> --identity you@example.com
```

The questionnaire is then delivered exactly once, and stays delivered exactly once however many
times the event is redelivered — the send is claimed under
`<review_id>:plan_v1:questionnaire_send:v1`, and the checkpoint and the idempotency key both
refuse to repeat it.

`make dev-ui` runs the ADK development UI against the agent packages for inspecting one agent
interactively; it does not consume the event backbone.

## The whole review, in one command

```
make emulators
make reset                          # clears prior runs; `make seed` alone keeps them
make demo-fixtures VENDOR=datadynamo
make dashboard                      # in a second terminal: localhost:3000
```

Fourteen beats, asserted rather than hoped for: intake → Tier 2 with a stated reason → plan
checkpointed → questionnaire built → **P1 refuses** → contact gate → approval → one email →
replies parsed incrementally → **the vendor's own answer re-tiers the review to Tier 1** → the
additional questions go out and nothing is asked twice → coverage reaches 93% → evidence
retrieved and cross-examined → Trust Score 60, conditional, with a per-domain breakdown → memo →
**decision gate** → approval → decided → audit binder rendered.

`demo-fixtures` answers every model call from the vendor pack, so it is free, deterministic and
runs in CI. `make demo` runs the same script against the live models and needs API quota — the
Gemini API free tier caps `gemini-3.5-flash` at 20 requests per day and a full review makes
about thirty.

Both targets set `DRAWBRIDGE_ALLOW_UNSCREENED=1`, and that is not a convenience. Local mode has
no Model Armor, so the demo runs on evidence seeded straight into the clean bucket, and **policy
P2 refuses it** — no external content reaches a model without a verified, verdict-bearing
stamp:

```
P2 REJECTED · local-seed v0 · verdict_not_trustworthy · task=parse_reply
review=... parked — P2: local-seed 0 · no match inadmissible to parse_reply
```

The flag admits content that declared what it is, and nothing else: a source with no stamp is
refused whatever it is set to, and cloud mode ignores the variable entirely. Every review it
produces carries `unscreened_fixtures=true`, the worker prints a banner, and the binder says so
on its cover. Run the demo without it to watch the refusal.

Replay either one: same Trust Score, same findings, same re-tier, idempotent skips logged.

## The audit binder

```
make binder REVIEW=<id>
```

Eight sections per the handbook's Appendix D — timeline with tier changes, questionnaire with
parse provenance, evidence inventory with per-document per-filter verdicts and the template that
produced them, findings with retrieval provenance and a `rule` or `model` label on each, the
score arithmetic against 100, human decisions, the reasoning trace, and post-approval
monitoring. HTML with a print stylesheet, rendered in about fifty milliseconds.

**Rendered by a template, never by a model**, and the cover says so — asserted by an import
graph, because a document that could be steered by the content it reports on is worse than no
document.

## The dashboard

```
make dashboard
```

Read-only over `reviews`, `dashboard_events`, `decisions` and `scores`. Three screens: the
queue filtered to what needs a person, the review timeline with every entry expandable to the
agent, goal, decision, trace id and idempotency key, and the gate card carrying the memo, the
findings with their provenance labels and the per-domain arithmetic.

The decision controls are inert by design. The dashboard holds no signing key and there is no
write path in it — if a surface every operator can reach could mint an approval, the gateway
refusing to sign would be decorative. The card names the command that issues the token instead.

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
