# Diagrams

Twenty-four Mermaid sources with PNG and SVG exports. Every diagram here describes something
that exists. Where a node described a component that was planned and not built, the node was
**deleted** rather than greyed out — a diagram that hedges is a diagram nobody can check.

```
src/*.mmd    Mermaid source. GitHub renders these inline; so does the docs site.
png/*.png    Retina PNG on white, 2000px. For anything that rejects SVG.
svg/*.svg    Transparent vector. Used by the docs pages.
```

## Regenerating

```bash
npm install -g @mermaid-js/mermaid-cli
npx puppeteer browsers install chrome     # if no local Chrome

cd docs/diagrams
for f in src/*.mmd; do
  n=$(basename "$f" .mmd)
  mmdc -i "$f" -o "svg/$n.svg" -c mermaid-config.json -b transparent
  mmdc -i "$f" -o "png/$n.png" -c mermaid-config.json -b white -s 2 -w 2000
done
```

`mermaid-config.json` holds the shared theme, and every diagram in the set renders clean with it.

**One parser trap, since it cost an afternoon.** A semicolon inside a `sequenceDiagram` note
terminates the statement, so the note parses as far as the semicolon and the remainder fails.
Diagram 17 carried one and had never rendered. Semicolons inside quoted flowchart labels are
fine; inside sequence notes they are not.

## The set

| # | Diagram | Where it is used |
|---|---|---|
| 01 | System architecture | Architecture — canonical |
| 02 | The fleet on one page | Overview, Getting started |
| 03 | Review lifecycle sequence | Architecture |
| 04 | Adversarial content defence | Security |
| 05 | Kill and resume | Architecture, Live demo |
| 06 | Memory hierarchy | Architecture |
| 07 | Event backbone | Architecture |
| 08 | Review state machine | Architecture |
| 09 | Zero-trust permissions | Security |
| 10 | Risk scoring pipeline | Architecture |
| 11 | Audit binder composition | Architecture |
| 12 | Technology stack | Overview |
| 13 | Firestore data model | Architecture |
| 14 | Deployment and cost | Getting started |
| 15 | Build timeline | — |
| 16 | Synthetic vendor pack | Getting started |
| 17 | Approval-token lifecycle | Security |
| 18 | Personas and adoption | Overview |
| 19 | Questionnaire loop | Architecture |
| 20 | Failure semantics | Architecture, Security |
| 21 | Tests, claims and demo beats | Live demo |
| 22 | Demo shot map | working document |
| 23 | The review graph | Architecture — **generated** |
| 24 | The fourth-party chain | Architecture, Security |

**23 is generated, and it is the only one in this set that is.** It was absent for most of the
build, and the reason given was right at the time: it had been specified as a dump of an ADK
graph workflow, the Orchestrator is not one, there was no `scripts/graph_dump.py`, and a
placeholder for a picture the repository cannot produce is the kind of thing this set exists to
not have.

What changed is not the execution model. There is still no graph runner and no process holds a
review's position. What changed is that the topology is declared as data in `shared/graph.py`, so
there is something to dump — and because it is generated from the same object
`scripts/check_contracts.py --check graph` diffs against the subscriber table, the event
contract, the transition table and the permission matrix, this is the one diagram here that
cannot quietly stop being true.

```bash
make graph        # rewrites src/23-review-graph.mmd, then render it with the loop above
make replay REVIEW=<id>   # the same graph, coloured by what one review actually did
```

**Twelve park edges are summarised rather than drawn**, and that is a cartographic decision
rather than a tidying one. Every node with a failure policy of `park` has an edge into
*Parked · needs a person*; drawing them puts a dozen identical lines across every band and spends
the reader's attention on the one property that is uniform. The note on the diagram states the
property instead, where it is easier to check than a dozen arrows are — a claim that every node
parks is falsified by one node that cannot. `--failure-edges` draws them for the times the
question is specifically about the failure topology.

## What changed in this pass

Corrected against the code rather than against the plan:

| Diagram | Was | Is |
|---|---|---|
| 01 | Vendor Portal, Gemma 3 scrubber, public read-only surface, Agent Registry | deleted — README-only or unbuilt |
| 01 | 11 topics, 6 identities | 12 topics, 10 identities |
| 01, 06, 12 | `text-embedding-005`, 768 dimensions | `gemini-embedding-001`, 3072 |
| 01, 10, 12 | Gemini Pro | `gemini-3.7-flash` — Pro is quota-limited to zero on the free tier |
| 01 | "the Agent Gateway is the only exit" | P1 and P3 at the gateway, P2 in the router |
| 01, 09 | chunking and embedding inside screening | inside the Evidence agent — screening may make no model call |
| 09 | six identities, three service identities, "one denied action per agent" | ten identities, four service identities, 230 asserted rows |
| 10 | "penalties subtract by severity **and by contradiction**" | no multiplier — no fact is priced twice |
| 10 | scored over the tier profile | scored over the domains the plan asked about |
| 10 | *why 71?* | *why 64?* |
| 14 | $0.50 per-review ceiling | $1.00, measured |
| 17 | never rendered — a semicolon in a sequence note | renders |
| 24 | generic placeholders, marked *(Target)*, no chain data | the real DataDynamo chain, the four finding types and their gates, and the beat |

| 23 | absent — "there is no graph to dump" | generated from `shared/graph.py` by `make graph` |
| 01, 02 | agents as five boxes | unchanged; the node-level view is 23, and the two are different questions |

The sources these were derived from live outside this repository and are unchanged; this
directory is the copy that tracks the code.
