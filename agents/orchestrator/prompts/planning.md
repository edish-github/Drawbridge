# Planning prompt

The rendered prompt lives in `agents/orchestrator/planner.py` as `PLANNING_PROMPT`, because it
is filled from the vendor record and the step vocabulary and would go stale here the moment
either changed. This file is the design note behind it.

## Tiering policy, given explicitly rather than inferred

- **Tier 1** — the vendor processes customer data, has production system access, or is an AI
  service handling company text.
- **Tier 2** — the vendor handles internal, non-customer data.
- **Tier 3** — everything else.
- When evidence is ambiguous, tier **up** and state why.

## The floor

Deterministic rules over the enumerated intake fields — declared data categories, declared
system access, whether the vendor is an AI service — produce a tier before the model is asked
anything. That tier is a **floor**, and it is stated in the prompt. The model may return a lower
tier number, which is stricter; a higher one is rejected and the floor stands.

The asymmetry is the point. The intake form is written by the person who wants the contract
signed, and the free-text description is passed to the model only so its stated reason reads in
the requester's own terms. Nothing written as prose is allowed to reduce scrutiny.

## Output contract

`shared.domain.ReviewPlan`, attached as the structured-output schema:

```json
{
  "tier": 1,
  "reason": "An AI service processing customer text is Tier 1 from the intake form onward.",
  "steps": ["questionnaire_send", "evidence_extract", "cross_examine", "score", "memo"],
  "needs_human": false
}
```

Steps are bare names from `STEP_VOCABULARY`. Two reasons they carry no parameters: parameters
come from the tier and the plan version, which the model has no view of; and an open object in a
response schema compiles to `additionalProperties`, which Vertex AI accepts and the Gemini
Developer API rejects. `planner.params_for` fills them in.

Step ids are deterministic from workflow position. They are never timestamps or uuids,
because the idempotency key is derived from them.

`needs_human` is the defined non-compliance path: work with no name in the vocabulary is
returned as a reason rather than as an invented step name that nothing downstream can execute.
