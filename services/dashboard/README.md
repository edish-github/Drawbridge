# The operator console

Eleven screens over the review ledger. Runs as `sa-dashboard`, reads Firestore directly from
server components, and **holds no write path** — there is no API route in this app and no call
that mutates anything, asserted by `tests/test_console.py` against the source tree.

```bash
make dashboard      # regenerates the console's generated files, then starts it on :3000
```

## The screens

| Route | What it answers |
|---|---|
| `/` | What should I do now? |
| `/queue` | Which reviews are parked, and on what |
| `/vendors` | What is the portfolio's trust posture |
| `/vendors/[id]` | Everything about one vendor, across every review it has had |
| `/reviews/[id]` | What happened in this review, in order, and why |
| `/reviews/[id]/gate` | What am I being asked to accept, and on what evidence |
| `/reviews/[id]/graph` | What was supposed to happen, and which of it did |
| `/monitoring` | What has the Watchdog seen since the reviews closed |
| `/evidence` | What did each detector say about each document |
| `/findings` | Which conclusions were arithmetic and which were judgement |
| `/binders` | What an auditor receives |
| `/agents` | What can each agent touch, and what can it structurally not |
| `/activity` | Everything the fleet and its humans did |
| `/settings` | The policy the fleet runs on, read-only |

## Generated files

The console is TypeScript and the fleet is Python, so two things reach the screen through
generated JSON rather than being declared twice:

```
lib/graph.json     the review graph      · scripts/graph_dump.py  --format json
lib/policy.json    rubric, matrix, P1–P3 · scripts/policy_dump.py
```

`make console` rewrites both. `tests/test_console.py` regenerates and diffs, so a stale copy
fails the suite rather than quietly showing last week's rubric on screen.

`lib/graph.ts` reimplements the graph projection loop, because there is no API to call it
through. The duplication is bounded — the *rules* live in the generated file and only the
resolving is written twice — and `tests/test_graph_ui.py` asserts both sides handle the same
observation forms.

## Design

The palette, the type and the geometry come from the design canvas in `Drawbridge.html`,
unpacked and translated rather than approximated. Two faces, self-hosted from `public/fonts`
so the console needs no network beyond the emulator it reads:

- **Plus Jakarta Sans** — anything a person wrote
- **JetBrains Mono** — anything a machine produced: review ids, idempotency keys, hashes, state
  names, trace ids

That split is doing work. Half the argument this console makes is that the numbers on screen
were computed rather than written, and mono reads as real output rather than as copy somebody
typed.

The binder shares this palette; the architecture diagrams keep the older slate one. Those are
documentation *about* the system rather than output *from* it, and the split is recorded above
the binder's stylesheet.

## Server components everywhere except one

`app/nav.tsx` is the only client component, and only because it needs `usePathname` to know
which leaf is lit. Every expansion on every screen is a `<details>` element rather than a hook,
so the console still expands in a screenshot, in print, and on the machine where hydration
failed five minutes before recording.
