# Amendments

Where the code and the planning documents in `research/` disagree, and which one is right.

`research/` is read-only and stays that way. This file is the record of drift, so a reader
coming from the handbook knows which claims survived contact with an implementation.

**Read [`docs/architecture.md`](docs/architecture.md) for the current system.** Every amendment
below that changed the design is folded into that document as ordinary description rather than
as a correction, because a reader arriving at the docs should see what the thing is, not the
original plan plus a list of edits. This file is the audit trail behind it: what moved, and why.
The diagram set in [`docs/diagrams/`](docs/diagrams/README.md) has been regenerated against the
same amendments, and its README records which nodes were deleted for describing something that
was never built.

| | Amendment | In the architecture doc |
|---|---|---|
| A01 | `index_chunks` belongs to the Evidence agent | Four memory layers |
| A03 | Cost ceiling is $1.00 | — measured, not structural |
| A04 | Embeddings are 3072-dimensional | Four memory layers |
| A05 | The planned model ids return 404 | Nothing reaches a model except through one function |
| A06 | Anything can park a review; only the Orchestrator advances one | Who may move a review |
| A08 | No contradiction multiplier | Scoring |
| A09 | Scored over the plan's domain set | Scoring |
| A10 | P1 gates first contact, not every message | [Security](docs/security.md) — three policies |
| A11 | P2 is enforced in the router | Nothing reaches a model except through one function |
| A12 | Chases and follow-ups are composed from the bank | [Security](docs/security.md) — what the model never decides |
| A13 | A crashed effect is confirmed, never released | Exactly-once effects |
| A14 | The Watchdog screens what it fetches | [Security](docs/security.md) — three policies |
| A15 | Conditions live in the ledger, not durable memory | Four memory layers |
| A16 | A second review carries a domain, not a question | The second review |
| A17 | `date_source` attributes the input a date finding turns on | Scoring — what a provenance label claims |

A02 and A07 are facts about the fixtures and the free tier rather than about the design, and
they live in [`docs/getting-started.md`](docs/getting-started.md).

---

## A01 · `index_chunks` moved from the screening service to the Evidence agent

**Documents affected:** Amendment 02's edit index, diagram 01 (component boundaries), diagram
04 (screening pipeline). All three place chunking and embedding inside the screening service.

**What the code does:** `shared.armor.index_chunks` still enforces the ordering constraint —
only clean-bucket content is ever chunked, and a reference outside it is refused — but it is
called by the Evidence agent, not by `screen_and_promote`.

**Why:** chunking calls `routing.embed`, embedding is a generative model call, and the
screening identity is declared `vertex_ai: false` with *never: any generative model call*.
Leaving the call in the promotion path would have made the published permission matrix false,
silently, and only detectably once real IAM was applied. The constraint the documents care
about is preserved; the identity that executes it changed.

## A02 · The NimbusWrite payload ships as a PDF, not as Markdown

**Documents affected:** anywhere the planted payload is described as living in
`evidence/security-overview.md`.

**What the code does:** extraction sniffs format from content and handles both. The Markdown
fixture with its inline white-on-white `<span>` remains, and the shipped adversarial document
is a PDF carrying the same text in its text layer with a white fill colour.

**Why:** a real vendor upload is a PDF. Testing concealment only against Markdown would have
tested the HTML unwrapper and nothing else.

## A03 · Cost ceiling is 1.00, not 0.50

**Documents affected:** every mention of a $0.50 per-review ceiling.

**What the code does:** `COST_CEILING_PER_REVIEW_USD=1.00`, enforced.

**Why:** measured per-call costs put a full Tier 1 review at $0.30–0.60, which straddles the
old ceiling. A ceiling a normal review can cross is an alert about the ceiling rather than
about the review. Provisional until a full pack has been measured end to end.

## A04 · Embeddings are 3072 dimensions, not 768

**Documents affected:** the retrieval design and the Firestore index definition wherever 768
appears.

**What the code does:** `infra/firestore/indexes.yaml` declares 3072, measured against the live
API.

**Why:** `text-embedding-005` does not exist on this API surface. `gemini-embedding-001` does,
and returns 3072. The dimension is fixed at index creation, so getting this wrong would have
meant dropping and rebuilding the index.

## A05 · The model ids in `research/` return 404

**Documents affected:** every model id named in the planning documents.

**What the code does:** `MODEL_FAST=gemini-3.5-flash`, `MODEL_DEEP=gemini-3.7-flash`,
`MODEL_EMBED=gemini-embedding-001`.

**Why:** `gemini-pro` and `text-embedding-005` do not resolve, and Pro models are quota-limited
to zero on the Gemini API free tier. The deep model is settled against Vertex AI when
`probe_geap.py` runs. The claim that survives either outcome is unchanged: the deep model is
spent in exactly two places, cross-examination and the memo.

## A07 · The Gemini API free tier caps `gemini-3.5-flash` at 20 requests per day

**Documents affected:** anywhere a full review is described as runnable on the free tier.

**What the code does:** `scenarios/fixtures.py` answers every routed task from the vendor pack,
so `--fixtures-only` runs the whole chain with no model call at all. `make demo-fixtures` is
free and deterministic; `make demo` uses the live models and needs quota.

**Why:** measured, not assumed — a full Tier 2 review makes roughly 30 model calls (one plan,
one parse per reply batch, one extraction per document, one cross-examination per claim, one
memo). The daily cap is 20. A live end-to-end run is therefore impossible on the free tier
regardless of cost, which makes billing a prerequisite for measuring the per-review figure
rather than a nicety.

## A08 · The contradiction multiplier is removed; severity carries it alone

**Documents affected:** Appendix B and diagram 10, wherever a contradiction multiplier appears.

**What the code does:** `rubric.yaml` declares no `contradiction_multiplier`, and `load_rubric`
raises if one reappears. A contradiction costs exactly what its severity costs. The
`contradiction` flag stays on the finding and drives the binder, the badge and the finding text.

**Why:** the severity anchors the Evidence agent judges against already define *high* as "a
control the vendor claims is in place is contradicted by their own evidence". Multiplying again
charges the same fact twice. "No fact is priced twice" is defensible under questioning in a way
that "we set it to 1.5" is not. The measured consequence: DataDynamo moves from escalate to
conditional, which is what the three-vendor calibration needs.

## A09 · The score is computed over the domains the review asked about

**Documents affected:** anywhere the tier profile is described as the scoring domain set.

**What the code does:** `compute_score` takes the domain list from the review's checkpointed
plan and renormalises over it. `planner.domains_for` drops `ai_specific` for a vendor that is
not an AI service, so it is neither asked about nor scored.

**Why:** the Tier 1 profile includes `ai_specific`, and DataDynamo is a freight company. Scoring
the nominal profile awarded it ten out of ten in a domain nobody put a question to — a free ten
points, and a number that cannot be defended out loud. What is scored is now what was asked.

## A10 · P1 gates first contact, not every message

**Documents affected:** anywhere P1 is described as requiring a token per outbound email.

**What the code does:** `verify_approval_token` passes without a token when a spend record
already exists for this review and this recipient. A different address on the same review is
unauthorised and still needs its own approval.

**Why:** G2 is *first* outbound contact, and the policy text already said "no outbound email to a
**new contact**". The chase rounds, the targeted follow-up and the additional questions a
re-tier produces are the same authorised conversation; re-approving each would make the gate
noise rather than a control. The narrowness is the point — a vendor contact that changed
mid-review is the case the check protects against, and it is still caught.

## A11 · P2 is enforced in the router, not at the tool gateway

**Documents affected:** §6.4 and the diagrams, which place all three policies at
`gateway.call_tool`.

**What the code does:** `routing.generate` applies P2 to every task in
`EXTERNAL_INPUT_TASKS`, verifying a stamp per named source before the model is reached, and
parks the review on a refusal. P1 and P3 stay at the tool gateway.

**Why:** `call_tool` is for effects on the world and a model call is not one, so routing prompts
through it to reach the policy would blur a boundary that is currently clean. Since nothing
reaches a model except through `generate`, the router is the only place the claim can be made
true. Before this, *"no external content reaches a model without a verified stamp"* was in the
architecture document, in the diagrams and in the narration, and was enforced nowhere.

## A12 · Chases and follow-ups are composed from the bank, not by a model

**Documents affected:** the Questionnaire agent's description, which lists composing chases and
follow-ups among the fast model's jobs.

**What the code does:** `chaser.py` and `followup.py` build their messages from the question
bank and the review's own state. The vendor's answer is quoted verbatim in a re-ask; nothing is
paraphrased and no model call is made on either path.

**Why:** the bank already states the evidence each question requires, in the words the review
was designed around, so a model would turn something exact into something approximate. It would
do it by putting the vendor's own text into a prompt and mailing the result to a human, which is
the tool-poisoning shape — spent for no gain. Both messages are also free and deterministic,
which matters on a quota that caps the fast model at twenty requests a day.

## A13 · A crashed step is confirmed by a person, not released for a retry

**Documents affected:** anywhere reconciliation is described only as "surfaced for human
confirmation", with no path onward.

**What the code does:** `idempotency.confirm` closes a claimed-but-incomplete key on a named
person's word that the effect happened. `once` now records a `claim` payload describing what the
step was about to do, and confirmation promotes it to the result — so a step whose process died
before it could record what it did is replayable without the caller re-deriving it. There is
deliberately no "it did not happen, run it again": releasing a claim for an effect that might
have occurred is the operation that sends the second email.

**Why:** the guard already refused to repeat a crashed effect, which was correct and left the
review stuck. `make demo-crash` walks the whole path — refusal, confirmation, replay, skip — and
building it is what surfaced that the refusal had no exit.

## A14 · The Watchdog screens what it fetches

**Documents affected:** the P3 description, wherever the feed allowlist is presented as the
control over fetched content.

**What the code does:** `sources.fetch_feed_signals` screens every fetched body through
`screen_text` before it is parsed into signals, and the `relevance` task is in
`EXTERNAL_INPUT_TASKS`, so P2 verifies the stamp before the model is reached.

**Why:** P3 bounds *where* the fleet fetches from and says nothing about what comes back. A
compromised advisory page or an article quoting an attacker's own text is tool poisoning — the
third threat Model Armor names, and the only one no other path in this system covers, because
every other untrusted input arrives from the vendor. In local mode the stub is untrusted by
construction, so fetched content is refused and the sweep runs on expiry math alone.

## A15 · Conditions live in the ledger, not in durable memory

**Documents affected:** the memory-hierarchy description, wherever the conditions attached to a
conditional approval are listed among the things durable memory carries between reviews.

**What the code does:** ``Approval.conditions`` holds them in the approvals collection. The
dossier records an ``approval_condition`` note saying conditions were attached and naming the
review; ``recall._conditions`` resolves the text from that review's approval record.

**Why:** a condition is a sentence a person typed, and the structured-write guard rejects it at
thirteen words — correctly. The guard was not widened. Durable memory is recalled into a
planning prompt *before any screening has run in the new review*, which makes it the one place
content from an earlier review could reach a model without passing a detector in this one, and
the fix for something that does not fit is a note type that does, not a looser rule.

## A16 · A second review carries a domain, not a question

**Documents affected:** anywhere the memory payoff is described as "questions already answered
are not asked again".

**What the code does:** a question is carried only when its answer was rated ``usable``, its text
is unchanged, **and the prior review recorded no finding in its domain**. A vendor carrying a
conduct flag carries nothing at all.

**Why:** the first two conditions alone carried fifty of DataDynamo's fifty-four questions, which
is a rubber stamp rather than a review. The domain gate is what makes it a saving rather than a
shortcut: the finding is the reason the review happened, and asking around it would be the worst
possible economy. Measured: 43 asked instead of 54, and the eleven carried are the only domain
that came back clean.

## A17 · A rule finding attributes the date it turns on, separately from its conclusion

**Documents affected:** anywhere `source="rule"` is presented as the whole of a finding's
provenance.

**What the code does:** `Finding.date_source` carries `extracted`, `declared` or `computed` on
any finding that turns on a date, alongside `source`. A certificate expiry is `rule` and
`extracted`; a register review worked out to have lapsed is `rule` and `computed`; the same
register row with a hand-set status flag is `rule` and `declared`. Findings that turn on no date
carry nothing. The binder prints both labels side by side and explains the difference.

**Why:** `rule` describes the *conclusion* — a comparison the code performed — and says nothing
about the input. On a date comparison the input is usually a date a model read off a page, so a
single label read as a stronger claim than the evidence supported, and it read that way in the
binder, in front of an auditor. The arithmetic is still the code's; only the attribution changed.
The distinction between computed and declared is the one an auditor actually asks for: *did we
work this out, or did somebody mark it?* — and a hand-set flag is only as current as the last
person who touched it.

## A06 · Review-state ownership is narrowed to forward transitions

**Documents affected:** anywhere the Orchestrator is described as the only component that
writes review state.

**What the code does:** the Orchestrator owns the plan and every forward transition. Any
component may park a review — into `GATED` or `NEEDS_HUMAN` — through `shared.state.park`,
which validates the transition, writes the ledger event and raises the dashboard card.
`tests/test_state_ownership.py` asserts that nothing outside `agents/orchestrator/` advances a
review.

**Why:** the cost ceiling, screening and the contact gate all need to stop a review from where
they are. Routing a park through the Orchestrator would mean a lost message leaves a review
claiming to be in flight after it has already stopped — a worse inconsistency than the one it
would fix. Anything can stop a review; only the Orchestrator can advance one.
