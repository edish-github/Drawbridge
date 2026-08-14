# Planning prompt

Short and structured. Receives the vendor facts and the tiering policy, returns a JSON plan:
a list of step names with parameters.

## Tiering policy, given explicitly rather than inferred

- **Tier 1** — the vendor processes customer data, has production system access, or is an AI
  service handling company text.
- **Tier 2** — the vendor handles internal, non-customer data.
- **Tier 3** — everything else.
- When evidence is ambiguous, tier **up** and state why.

## Output contract

```json
{
  "tier": 1,
  "domains": ["data_protection", "access_control", "..."],
  "steps": [{"step_id": "questionnaire_send:v1", "kind": "questionnaire", "params": {}}]
}
```

Step ids are deterministic from workflow position. They are never timestamps or uuids,
because the idempotency key is derived from them.

TODO — the filled prompt, once the tiering policy fields are fixed against the intake schema.
