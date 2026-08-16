# Drawbridge — documentation

> The fleet that decides what crosses into the castle.

A governed fleet of agents that runs enterprise vendor security reviews end to end. These pages
describe **the system that exists**, not the system that was planned. Where the two differ, the
difference is recorded in [`CHANGELOG-amendments.md`](../CHANGELOG-amendments.md) and the
current behaviour is what is written here.

| | |
|---|---|
| [Getting started](getting-started.md) | The free path. No project, no billing, one command. |
| [Architecture](architecture.md) | Five agents, one event backbone, four memory layers, and where every constraint is enforced. |
| [Security](security.md) | The screening boundary, three gateway policies, two human gates, ten identities, and what is measured versus asserted. |
| [Live demo](live-demo.md) | The four beats, what each one proves, and how to reproduce them. |
| [Diagrams](diagrams/README.md) | Twenty-three Mermaid sources with PNG and SVG exports. |

## What this is

A vendor security review is a company asking another company two hundred questions and reading
whatever comes back. The work is reconciling the answers against the evidence: the questionnaire
says multi-factor authentication is enforced everywhere, and exception note 3.2 of the vendor's
own audit report says it is not enforced for administrative access. That takes an analyst six to
twelve weeks per vendor.

The obvious thing to point a language model at is also the worst thing to point one at. Every
input is a document written by the party being assessed, who has a commercial interest in the
outcome and now knows an agent is reading it.

**So the design is inverted.** Untrusted content is screened before any model sees it. No agent
sends an email or reaches the network except through a gateway enforcing three named policies.
The Trust Score is arithmetic in Python over model-assigned severities, not a number a model
chose. No review reaches a decision without a named person. What the models are left holding is
the part that is genuinely judgement.

## Four claims, and where each is proven

| Claim | Proven by |
|---|---|
| A vendor cannot steer the review by writing instructions into their evidence | `make corpus` — twelve variants, every outcome measured — and the screening boundary in [Security](security.md) |
| The score cannot be gamed by the thing being scored | An import graph: `agents/risk_scorer/scoring.py` cannot reach `shared.routing` |
| A killed worker never sends a second email and never loses work | `make demo-crash`, three real processes and a real SIGKILL |
| A review that ran before makes the next one shorter without making it laxer | `make demo-second`, 43 questions instead of 54, tier never falls |

## What is not proven yet

Stated here rather than left for a reader to discover:

- **The Model Armor detection rate.** All twelve corpus variants are built and the harness runs,
  but against the local stub, which is a regex over the corpus's own technique classes. `make
  corpus` prints the floor and labels it as a stub result; the real number needs the service.
- **Severity stability.** The Trust Score is arithmetic over model-assigned severities, and the
  evidence for their stability is three runs of one claim. `scripts/severity_sweep.py` measures
  it; it needs quota.
- **The cross-examiner's miss rate.** `scripts/miss_rate.py` measures it against six deliberately
  subtle contradictions; same constraint.
- **IAM identity binding.** 230 permission-matrix rows are enforced by real Firestore rules
  evaluation. That `sa-evidence` genuinely *runs as* `sa-evidence` is a deployment binding and
  is not.

[`cloud_gaps.md`](../cloud_gaps.md) lists all ten blocked items with the one function that
changes for each.
