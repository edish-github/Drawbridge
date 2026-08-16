# What is unproven, and what changes when it stops being unproven

Everything in this repository runs against emulators and the Gemini API. Ten things do not, and
each of them is an assumption about an API surface nobody has been able to check without a
billing account. This file is the list, written from the code rather than from prediction:
for each one, what is unproven, **the one function that changes**, the test that unskips, and
what happens if the component turns out not to be available at all.

The reason it is a table rather than prose is that the day the project exists these are
discovered serially, one traceback at a time. `python -m scripts.cloud_checklist` runs them in
order — cheapest and most consequential first — and writes the answers to
`infra/CLOUD-CHECKLIST.md`.

**Ten items. Eight are a one-function change. Two are not, and they are named as such.**

---

## The eight that are one function

### 1 · Model ids resolve in the target region

| | |
|---|---|
| **Unproven** | That `MODEL_FAST` and `MODEL_DEEP` exist on Vertex AI in `REGION`. They resolve on the Gemini API today; the planning documents' ids returned 404, which is why nobody is assuming this one. |
| **One function** | `shared.config.Settings` field defaults, plus `.env`. No code path branches on the id. |
| **Test that unskips** | Nothing skipped — every test uses fixtures or the Gemini API. The checklist's step 1 is the proof. |
| **If unavailable** | Substitute the nearest available id. The claim that survives either way is unchanged: the deep model is spent in exactly two places, cross-examination and the memo. |

### 2 · Embedding dimension

| | |
|---|---|
| **Unproven** | That `gemini-embedding-001` returns 3072 on Vertex AI as it does on the Gemini API. Fixed at index creation and expensive to get wrong: a mismatch means dropping and rebuilding the index. |
| **One function** | `infra/firestore/indexes.yaml` — one number, read by `create_indexes.sh`. |
| **Test that unskips** | None; `tests/test_cross_exam.py` already runs against the real dimension locally. |
| **If unavailable** | Any dimension works as long as the index and the model agree. Checklist step 2 compares the two directly rather than asserting either. |

### 3 · KNN pre-filter composition

| | |
|---|---|
| **Unproven** | Whether `collection.where(review_id).find_nearest(...)` composes on a composite vector index, or whether the filter fields must be declared in the index in a particular order. |
| **One function** | `agents.evidence.retrieval._knn_indexed`. |
| **Test that unskips** | None skipped — `test_retrieval_is_scoped_to_one_review` covers the local path and asserts the property the index provides. |
| **If unavailable** | The local brute-force path already ranks correctly over a review's chunks; a review holds tens of chunks and cosine over tens of vectors is microseconds. Losing the index costs latency at portfolio scale, not correctness. **This is the one gap with a working fallback already running in production code.** |

### 4 · Model Armor response field names

| | |
|---|---|
| **Unproven** | The exact field names for per-filter match state and execution state on the sanitize response, and whether the template version is returned there or must be read from `get_template`. |
| **One function** | `shared.armor._from_sdk_response`. Everything downstream reads `ScreenResult`, which this function is the only producer of. |
| **Test that unskips** | `tests/test_armor_flow.py` — 11 tests, including the injection-corpus detection rate and the false-positive control. |
| **If unavailable** | Nothing else works. Model Armor is a required technology for the track and the screening boundary is the project's thesis. There is no fallback and none should be built. |

### 5 · RAI filter type strings

| | |
|---|---|
| **Unproven** | The exact `filterType` strings the responsible-AI flag accepts on `gcloud model-armor templates create`, and whether per-filter confidence is settable. |
| **One function** | `infra/model_armor/create_templates.sh`, one flag. |
| **Test that unskips** | None directly; it gates item 4. |
| **If unavailable** | Create the template with the three critical filters and omit RAI. The rubric already maps `armor_responsible_ai` to a null domain — logged, never scored — so its absence changes no number. |

### 6 · Memory Bank payload shape

| | |
|---|---|
| **Unproven** | Whether `add_memory` accepts a structured fact payload or requires a session-shaped wrapper. |
| **One function** | `shared.memory._write_to_memory_bank`. Isolated this milestone; before it, the write was inline and there was no cloud call site at all. |
| **Test that unskips** | None. `tests/test_second_review.py` covers the guard and the recall, both of which run before the backend is chosen. |
| **If unavailable** | The Firestore copy already answers every question `recall_dossier` asks — it is a structural query, not a semantic search. Losing Memory Bank costs the semantic layer and the architecture's third tier being the named service; it costs no behaviour. `_write_to_memory_bank` degrades rather than raising for exactly this reason. |

### 7 · Asymmetric approval-token verification

| | |
|---|---|
| **Unproven** | The signing and verification call shapes against the project's KMS key pair. Local mode compares an opaque id against a Firestore record, which is honest about what it proves and proves less than the architecture claims. |
| **One function** | `shared.gateway.verify_approval_token` for the public half, `shared.armor.sign_stamp` for the private. |
| **Test that unskips** | `tests/test_token_forgery.py` gains the forgery cases that need a real signature; the replay, scope and single-use cases already pass locally. |
| **If unavailable** | Cloud mode currently returns `False` for everything, which leaves the contact gate closed rather than open — the correct direction to be wrong in. |

### 8 · The rate table

| | |
|---|---|
| **Unproven** | The per-million-token rates in `shared.routing.RATES_USD_PER_MTOK`, taken from a published pricing page. The cost ceiling and the per-review cost figure both rest on them. |
| **One function** | One dictionary. |
| **Test that unskips** | None. `COST_CEILING_PER_REVIEW_USD` is enforced against whatever the table says. |
| **If unavailable** | The rates are read from the billing console after one full review, and the ceiling is re-set from a measurement rather than an estimate. Until then the ceiling is documented as provisional in `CHANGELOG-amendments.md` A03. |

---

## The two that are not one function

### 9 · IAM identity binding

| | |
|---|---|
| **Unproven** | That the Evidence agent genuinely *runs as* `sa-evidence`. The emulator evaluates the generated security rules — 230 rows, all 23 collections, all 10 identities — but it never checks that the caller presenting a token is the principal it claims to be. |
| **Why not one function** | It is not code. It is the binding between a Cloud Run revision, or an Agent Engine deployment, and a service account, applied by `infra/iam/apply_iam.sh` and asserted by nothing local. Every collection-level row is proven; the identity half of every row is not. |
| **Test that unskips** | `test_the_evidence_agent_runs_as_sa_evidence`, plus the Storage, Secret Manager and Vertex AI rows that have no emulator at all. |
| **If unavailable** | It cannot be. The permission matrix is a graded deliverable and a published claim; half-proven is the honest current state and it is stated as such in the README. |

### 10 · Agent Engine deployment and the Agent Registry

| | |
|---|---|
| **Unproven** | The streaming query method name on the returned `AgentEngine` handle, and whether the installed ADK exposes a registry client surface at all. |
| **Why not one function** | Deployment is a build, a push, a twenty-minute wait and a handle whose surface decides how `infra/deploy/deploy_all.sh` invokes it. The registry may not exist as a client API, in which case the answer is a console screenshot rather than a code change. |
| **Test that unskips** | None. There is no local Agent Engine and nothing pretends there is. |
| **If unavailable** | The fleet runs on Cloud Run against the same event backbone — `shared.subscriber` is transport-agnostic and the worker is the same worker. Agent Engine is the deployment target the track prefers, not a dependency of the design. |

---

## What is already proven without a project

Worth stating alongside, because the list above reads as a lot until it is set against what
does not need it:

- The whole review, intake to a decision to an audit binder, in sixteen asserted beats.
- Policies P1, P2 and P3, including P2 in the router with a stamp verified per named source.
- The permission matrix at collection level: 230 rows against real rules evaluation.
- Exactly-once side effects across a real SIGKILL, and the confirmation path out of a crash.
- The Trust Score arithmetic, and three vendors landing in three bands with margin.
- The fourth-party diff against an internal register, and the second review that opens knowing
  what the first one found.
