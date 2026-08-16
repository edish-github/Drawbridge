# Drawbridge

> The fleet that decides what crosses into the castle.

A governed fleet of agents that runs enterprise vendor security reviews end to end —
intake, questionnaire, evidence analysis, risk scoring, human approval, and continuous
monitoring — built on Google Cloud with ADK 2, Vertex AI Agent Engine, and Model Armor.

**Status:** the whole review runs, locally, in one free command. What needs a Google Cloud
project — Agent Engine, Model Armor, IAM identity binding — is listed item by item in
[`cloud_gaps.md`](cloud_gaps.md) with what is unproven and what changes when it stops being.

---

## The problem

A vendor security review is a company asking another company two hundred questions and then
reading whatever comes back. The questions are standard, the evidence is a SOC 2 and a
certificate and a subprocessor list, and the work is reconciling one against the other: the
questionnaire says multi-factor authentication is enforced everywhere, and exception note 3.2
of the vendor's own audit report says it is not enforced for administrative access. That
reconciliation is the job, it takes an analyst six to twelve weeks per vendor, and it is done
by hand because the answers arrive as prose and the evidence arrives as PDFs.

The obvious thing to point a language model at is also the worst thing to point one at: every
input is a document written by the party being assessed, who has a commercial interest in the
outcome and now knows an agent is reading it. **Drawbridge is built the other way round.**
Untrusted content is screened before any model sees it, no agent can send an email or reach the
network except through a gateway that enforces three named policies, the Trust Score is
arithmetic in Python over severities rather than a number a model chose, and no review reaches
a decision without a person. What the models do is the part that is genuinely judgement:
reading a table, weighing a claim against a passage, and grading how badly a control failed.

## Demo video

TODO — YouTube link, recorded 28 August.

## Documentation

[`docs/`](docs/README.md) — [Getting started](docs/getting-started.md) ·
[Architecture](docs/architecture.md) · [Security](docs/security.md) ·
[Live demo](docs/live-demo.md) · [Diagrams](docs/diagrams/README.md)

## Architecture

Five agents on one event backbone, and every arrow between them is a Pub/Sub topic:

```
intake → Orchestrator → Questionnaire → [G2 contact gate] → vendor
                                              ↓
                             Model Armor screening boundary
                              ↓                        ↓
                        clean bucket              quarantine
                              ↓                        ↓
                          Evidence            Adversarial Conduct
                              ↓                        ↓
                          Risk Scorer ←──────────────┘
                              ↓
                      [G1 decision gate] → decided → binder → Watchdog
```

Four things carry the design, and each is enforced somewhere a test can point at:

**The screening boundary.** Nothing that a vendor wrote reaches a model without a signed,
verdict-bearing stamp. Policy **P2** enforces it in `shared/routing.py` — in the router rather
than at the tool gateway, because the router is the only place every model call passes through.

**The gateway.** Every outbound effect goes through `gateway.call_tool`. **P1** requires a human
approval token before first contact with a vendor; **P3** bounds outbound fetch to an
allowlist. An agent with unbounded email and unbounded fetch is a phishing service and an SSRF
surface.

**Exactly-once effects.** Every side effect is claimed before it happens under
`review_id:plan_vN:step_id`. A worker killed between the claim and the effect leaves a claim
nobody can verify, and the replay **refuses** rather than resending. `make demo-crash` walks it.

**Four memory layers.** Session state, the Firestore ledger, durable per-vendor memory, and the
retrieval index over screened chunks. The third is what makes a second review open already
knowing what the first one found.

## 30-minute spin-up

Nothing below needs a Google Cloud project. This is the path a judge runs first, and it is free.

```
brew install --cask google-cloud-sdk     # or the platform equivalent; provides the emulators
python -m venv .venv && source .venv/bin/activate
pip install -e .

cp .env.example .env                     # set GEMINI_API_KEY; leave RUNTIME_MODE=local
make emulators                           # Firestore with rules loaded, Pub/Sub, twelve topics
make reset                               # seed the synthetic vendors and the approved register
make demo-fixtures VENDOR=datadynamo     # the whole review, seventeen asserted beats
make dashboard                           # second terminal: localhost:3000
```

`make test` runs the suite against the emulators; `make binder REVIEW=<id>` renders the audit
binder for the review that just ran.

On a real project, the same review runs on the deployed fleet:

```
make bootstrap   # enable APIs, create topics, buckets, Firestore, IAM, Model Armor templates
make seed        # load the synthetic vendor pack into Firestore and Cloud Storage
make deploy      # build and deploy agents and services
make demo        # run the scripted end-to-end scenario against the live models
make teardown    # delete everything except the dashboard
```

`make bootstrap-dry` prints all 69 commands and executes none. The rehearsal is worth running
first: it found two real defects before any project existed — a variable that never reached the
Firestore step, and a dry run that overwrote a tracked file.

## Running locally

No Google Cloud project, no billing account and no credentials. Local mode runs the kernel and
the agents against the Firestore and Pub/Sub emulators with a Gemini API key, which is the same
required-technology clause satisfied through a different endpoint.

```
cp .env.example .env      # set GEMINI_API_KEY; leave RUNTIME_MODE=local
make emulators            # Firestore with rules loaded, Pub/Sub, and the twelve topics
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

Seventeen beats, asserted rather than hoped for: intake → Tier 2 with a stated reason → plan
checkpointed → questionnaire built → **P1 refuses** → contact gate → approval → one email →
replies parsed incrementally → **the vendor's own answer re-tiers the review to Tier 1** → the
additional questions go out and nothing is asked twice → **four vague answers are re-asked,
quoting the vendor back** → coverage reaches 93% → evidence retrieved and cross-examined →
**a fourth party nobody here has reviewed is named on the timeline** → Trust Score 64,
conditional, with a per-domain breakdown → memo → **decision gate** → approval → decided →
audit binder rendered → **the Watchdog sweeps and opens a linked re-review** on the certificate
the review recorded as expired.

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

## The fourth-party chain

The only place an agent queries an internal system rather than reading a vendor's document. The
model extracts the subprocessor table; a set difference against
[`approved_vendors`](infra/iam/permission-matrix.yaml) — this organisation's own register, with
readers and no writer — decides what it means. Four findings, all `source=rule`: a company
receiving customer data that nobody here reviewed, one whose review has lapsed, one the vendor's
list says there is no agreement with, and customer data reaching a region the intake form ruled
out.

It is a demo beat rather than a row in a table. When the diff finds a company receiving customer
data that this organisation has never reviewed, `fourth_party_gap` fires and the timeline names
it, what it receives and the fact that no review of it exists — before the gate, not in row four
of a findings list after it:

```
Sendline Notifications receives customer data through DataDynamo Logistics in EU (Dublin)
— Delivery notification email and SMS — and this organisation has never reviewed it.
It is not on the approved-vendor register.
```

NimbusWrite's model provider is the same shape and lands harder: it receives your customers'
text, and you have never reviewed it or signed anything with it. The chain renders on the review
timeline and in binder section 4, as a nested list with the unknown nodes marked.

The restraint is the part worth reading the code for. Four finding types across four
subprocessors could produce sixteen findings about one vendor, which is how a feature becomes
the tab nobody opens. Every gate is on whether the subprocessor processes customer data; the
agreement and residency findings are one per vendor rather than one per company, because the
control that failed is flow-down and it fails once however many companies it fails for; and
`processes_customer_data` and `dpa_claimed` are three-valued, so a document that is silent about
agreements is a document following a convention rather than a vendor with a finding.

## What a label claims, and what it proves

Every finding carries `rule` or `model`. A finding that turns on a date carries a second label
for the date itself, because the first one was quietly claiming more than it proved:

| | |
|---|---|
| `date extracted` | a model read it off the page — a certificate expiry, a report period end |
| `date declared` | a person or an internal system stated it — the register's own status flag |
| `date computed` | this system derived it — a register review worked out to have lapsed |

`rule` says the comparison is arithmetic the code performed. It says nothing about the input,
and on an expired certificate the input is a date a model read out of a PDF. Both labels print
side by side in binder section 4, so an auditor can see which half of the finding is machinery
and which half is a reading.

## The Trust Score

0–100, higher is safer, and the arithmetic is printed in the binder so it can be re-done by
hand. Each domain starts at its weight and loses 2, 5 or 10 points per finding by severity.
**There is no contradiction multiplier** — the severity anchors already price a contradicted
claim, so multiplying charges the same fact twice, and *no fact is priced twice* is a sentence
that survives questioning in a way *we set it to 1.5* is not.

Three synthetic vendors land in three bands, and none of them sits on a boundary:

| Vendor | Score | Band | Margin |
|---|---|---|---|
| CleanCloud | 91 | approve (≥80) | 11 above |
| DataDynamo | 64 | conditional (60–79) | 4 above |
| NimbusWrite | 38 | escalate (<60) | 21 below |

`tests/test_calibration.py` asserts the bands **and** that each score sits at least four points
inside its band at every boundary, so a later change to a finding set fails a test rather than
silently reclassifying a vendor. What is scored is what was asked: a review renormalises over
its plan's domain set, because scoring a freight company on AI-specific controls nobody put a
question to would hand it ten free points.

NimbusWrite's arithmetic alone reaches 63 — conditional. It escalates because the Adversarial
Conduct modifier takes 25 points and forces the band, which is the whole story in one number: the
arithmetic said conditional, and the vendor telling us who they are outvoted it.

## The second review

```
make demo-second
```

Runs DataDynamo end to end, closes it with a named person attaching two conditions, then opens a
second review six months later that already knows the outcome, the band, the conditions, the
certificate that expired, the contact, and how usable every answer was.

Two things change because of it. **Scrutiny never falls**: the tier starts where the last review
ended, so a second intake form that is more modest than the first buys nothing. And **43
questions are asked instead of 54** — the eleven carried are the only domain that came back
clean, because a domain that produced a finding is asked again in full.

Nothing prose-shaped carries. The conditions are sentences a person typed, so they live on the
approval record and the dossier holds the pointer: durable memory is recalled into a planning
prompt before any screening has run in the new review, which is the one place content from an
earlier review could reach a model without passing a detector in this one.

## Kill it mid-send

```
make demo-crash
```

Three real processes. The first is driven to the instant the questionnaire has left the
building and sent SIGKILL — uncatchable, so no handler runs and nothing tidies up. The kill
lands in the narrowest window there is: after the email was sent and before its idempotency
claim closed, which from the outside is indistinguishable from a worker that died a millisecond
earlier and sent nothing.

The second worker replays the send and **refuses** it, because a claim it cannot verify is not a
claim it may repeat. A named person then confirms the email did go out, and the third worker
replays it again, logs `idempotency SKIP`, banks the checkpoint and finishes the whole review.
The vendor receives one questionnaire per plan version and none from the crash.

That refusal is the honest answer to *why might a resumable agent order two laptops*.

## Permission matrix

Ten identities, scoped at collection level, generated into Firestore security rules from
[`infra/iam/permission-matrix.yaml`](infra/iam/permission-matrix.yaml) by `make rules`. One
source, two enforcement points: the table below and the ruleset the emulator loads cannot drift,
and a test fails if the code names a collection the matrix does not.

```
make rules      # regenerate infra/firestore/firestore.rules
make emulators  # start the emulator with them loaded
pytest tests/test_iam_boundaries.py
```

**230 rows, all 23 collections and all 10 identities, checked against the running emulator** —
44 permitted writes and 186 denials, each one a real `PermissionDenied` from a real rules
evaluation rather than a claim in a document. What the emulator cannot check is IAM identity:
that the Evidence agent genuinely *runs as* `sa-evidence` is a binding between a revision and a
service account, and those tests stay skipped with that as their stated reason rather than a
blanket "needs a project".

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

`synthetic-vendors/nimbuswrite/` contains a deliberately planted prompt-injection payload used
to exercise the screening pipeline. It is one sentence of white-on-white text in a document's
body asking the reviewer to record the vendor as pre-approved — mild, obviously synthetic, and
carrying nothing that would function against any system but this one. Its location, contents
and purpose are documented in
[`synthetic-vendors/nimbuswrite/README.md`](synthetic-vendors/nimbuswrite/README.md).

[`synthetic-vendors/injection-corpus/`](synthetic-vendors/injection-corpus/README.md) holds the
twelve-variant set used to **measure** the defence rather than assert it — visual concealment,
persona framing, authority spoofing, encoding, homoglyphs, fragmentation, metadata channels,
multimodal, alternate vectors, structured fields, and one aimed at the output rather than the
input. Every payload is mild and clearly labelled; only publicly documented technique classes
are used. Variant 1 is built and is the same fixture as the NimbusWrite payload; the other
eleven are specified and scheduled.

**The detection rate is not published yet, and the reason is stated rather than omitted:** it
needs the real Model Armor service, which needs a project. `tests/test_armor_flow.py` holds the
eleven assertions it will produce, skipped with that as their reason.

## The one exception that is real, and the one that is not

Two things this repository does not claim.

**Extraction has been measured against a document nobody here wrote.** The synthetic packs were
written by the person who wrote the extractor and they parse suspiciously well, which is a fact
about the fixtures. [`fixtures/public/`](fixtures/public/EXTRACTION-NOTES.md) points at a real
published security audit — fetched, never vendored — and records what the pipeline did to it.
It found that **71% of a real 63-page report never reaches the model**, that the heading-aware
chunker finds zero headings in a typeset PDF, and that the conclusion sits in a transmittal
letter rather than anywhere an extractor looks. Nothing about that document is ever scored, and
a test asserts the path does not exist.

**The miss rate is not measured yet.** The suite can prove over-flagging does not happen. It
cannot prove under-flagging does not happen, because a test for a missed contradiction needs
one already known to be missed. [`scripts/miss_rate.py`](scripts/miss_rate.py) is the honest
alternative: six deliberately subtle contradictions — a scope qualifier, an exception in an
appendix, a claim true-when-written, a control described as planned, and a control case that
should raise nothing — run against the real cross-examiner, with misses attributed to retrieval
or to judgement, because the two have no repair in common. It is written and deliberately not
run: eighteen calls against a twenty-a-day cap. Its number belongs next to the detection rate
above, and publishing one without the other would be publishing the half there was more
confidence about.

## Teardown

```
PROJECT_ID=<id> make teardown
```

Deletes every Agent Engine deployment, every Cloud Run service except the dashboard, all topics
and subscriptions, the quarantine, clean and binder buckets **and their contents**, both Model
Armor templates, and the fleet service accounts. It prints the list and asks before doing any of
it.

The dashboard survives on purpose: the submission carries a hosted URL that has to load while
judging is in progress. **It never deletes the project and never touches billing** — that is a
manual decision after judging closes, and confirming spend means reading the billing console,
which this repository has no credential for and no business holding.

Local mode has no teardown because it creates nothing outside `.emulators/` and
`.local-storage/`. `make emulators-stop` and deleting those two directories is the whole of it.

## Licence

MIT. See [LICENSE](LICENSE).
