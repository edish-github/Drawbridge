# Data-scope classification prompt

Used only to map a free-text questionnaire answer onto the enumerated data-scope categories.
It does not pick a tier: the tier is computed from categories by deterministic rules.

## Input

One questionnaire answer, verbatim, already screened.

## Output contract

```json
{"categories": ["customer_data", "production_access", "model_training"], "confidence": 0.0}
```

Categories come from a fixed vocabulary. An answer that supports no category returns an
empty list rather than a guess.

TODO — the fixed category vocabulary, mirrored from the tiering rules.
