# Getting started

Nothing on this page needs a Google Cloud project, a billing account, or a credential beyond a
free Gemini API key. The whole review runs against the Firestore and Pub/Sub emulators, and the
default demo makes no model call at all.

## Prerequisites

- Python 3.11
- The Google Cloud SDK, for the Firestore and Pub/Sub emulators — `brew install --cask
  google-cloud-sdk`, or the platform equivalent. No project is created and no API is enabled.
- Node 20, only if you want the dashboard.

## Fifteen minutes to a finished review

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e .

cp .env.example .env                     # set GEMINI_API_KEY; leave RUNTIME_MODE=local
make emulators                           # Firestore with rules loaded, Pub/Sub, twelve topics
make reset                               # seed the synthetic vendors and the approved register
make demo-fixtures VENDOR=datadynamo     # the whole review, seventeen asserted beats
```

The run prints each beat as it fires and fails loudly if one does not. It ends with a review in
`DECIDED`, a Trust Score of 64, an audit binder on disk, and a Watchdog sweep that opened a
linked re-review on the certificate the review found expired.

```bash
make dashboard                           # second terminal: localhost:3000
make binder REVIEW=<id>                  # the eight-section audit binder as HTML
make test                                # the suite, against the emulators
```

## Why the default demo makes no model call

`demo-fixtures` answers every routed task from the vendor pack, so it is free, deterministic and
runs in CI. That is not a shortcut around the model — it is a consequence of arithmetic. The
Gemini API free tier caps `gemini-3.5-flash` at **20 requests per day**, and a full Tier 1
review makes roughly thirty: one plan, one parse per reply batch, one extraction per document,
one cross-examination per claim, one memo.

`make demo` runs the identical script against the live models and needs quota. Both produce the
same beats; they disagree about the model's judgement and about nothing else.

## Why local mode has to declare itself

Both demo targets set `DRAWBRIDGE_ALLOW_UNSCREENED=1`, and that is not a convenience flag.

Local mode has no Model Armor. The demo runs on evidence seeded straight into the clean bucket,
and **policy P2 refuses it** — no external content reaches a model without a verified,
verdict-bearing stamp:

```
P2 REJECTED · local-seed v0 · verdict_not_trustworthy · task=parse_reply
review=... parked — P2: local-seed 0 · no match inadmissible to parse_reply
```

The flag admits content that **declared what it is**, and nothing else. A source with no stamp
is refused whatever the flag is set to, and cloud mode ignores the variable entirely. Every
review it produces carries `unscreened_fixtures=true`, the worker prints a banner, and the
binder says so on its cover.

Run the demo without the flag to watch the refusal. That refusal is the feature.

## Walking one review by hand

```bash
make run-local                           # the worker: pull events, dispatch, acknowledge
make open-review VENDOR=nimbuswrite      # second terminal
```

The worker tiers the review, checkpoints its plan, builds the questionnaire — and stops. The
gateway refuses the send under **P1** because no human has approved first contact:

```
P1 REJECTED · ref=trust@nimbuswrite.example
review=... parked at the contact gate — P1: outbound email requires a human approval token
```

Release it the way the approvals service will:

```bash
python -m scripts.issue_token --review-id <id> --identity you@example.com
```

The questionnaire is then delivered exactly once, and stays delivered exactly once however many
times the event is redelivered.

## On a real project

```bash
make bootstrap-dry    # print all 69 provisioning commands, execute none
make bootstrap        # enable APIs, create topics, buckets, Firestore, IAM, Armor templates
make seed
make deploy
make demo
make teardown         # everything except the dashboard
```

Run the rehearsal first. It found two real defects before any project existed: a variable that
never reached the Firestore index step, and a dry run that overwrote a tracked file.

## Reproducing the measurements

```bash
python -m fixtures.public.fetch          # the real audit report the extractor is measured on
python -m scripts.cloud_checklist        # the eight cloud assumptions, cheapest first
python -m scripts.severity_sweep         # severity stability — needs quota
python -m scripts.miss_rate              # the cross-examiner's miss rate — needs quota
```

The last two are deliberately not run. Both are resumable and file-backed, because the thing
most likely to stop them is the daily cap, which is also the reason they cannot simply be
started again from the beginning.

## Synthetic data

Every vendor, document and questionnaire answer in this repository is synthetic and was
generated for this project. No real vendor is named, described or implied.

The one real document is a published government security audit, fetched rather than vendored,
used to measure extraction and never scored. See
[`fixtures/public/`](../fixtures/public/EXTRACTION-NOTES.md).
