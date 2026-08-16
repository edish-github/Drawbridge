# Amendments

Where the code and the planning documents in `research/` disagree, and which one is right.

`research/` is read-only and stays that way. This file is the record of drift, so a reader
coming from the handbook knows which claims survived contact with an implementation. Diagrams
are regenerated in Phase 3; they are not edited here.

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
