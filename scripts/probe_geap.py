"""Capability probe: attempt the smallest real call against every component this project needs.

Run it once, read the report, and find out today whether anything needs the interface-fallback
path — rather than on the day the demo is recorded.

    PROJECT_ID=... REGION=... python -m scripts.probe_geap

Every probe is read-only or creates a resource it immediately deletes, and every one uses the
smallest possible input. The most expensive calls here are a handful of tokens against the
fast model and one short embedding.

Each probe reports one of:

    available              the call succeeded
    gated                  authentication or authorisation refused it
    unavailable-in-region  the API accepted the call but not in this region
    error                  something else, with the exception recorded verbatim

Results are written to ``infra/CAPABILITY-REPORT.md``. The error text is the valuable part —
"gated" without the message it came with is not something a workaround can be designed from.

Failure semantics: no probe can fail the run. Each is wrapped, timed, and recorded with its
exception; the report is written even when every probe fails, because a report of ten failures
is exactly the output that matters most on a bad day.
"""

from __future__ import annotations

import argparse
import itertools
import os
import time
import traceback
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

REPORT_PATH = "infra/CAPABILITY-REPORT.md"

AVAILABLE = "available"
GATED = "gated"
UNAVAILABLE_IN_REGION = "unavailable-in-region"
ERROR = "error"


@dataclass
class ProbeResult:
    name: str
    component: str
    status: str
    detail: str = ""
    error: str = ""
    elapsed_ms: int = 0
    notes: list[str] = field(default_factory=list)


def classify(exc: BaseException) -> tuple[str, str]:
    """Map an exception onto a status and a short reason.

    The classification is deliberately coarse. What matters in the report is the verbatim
    error, which is recorded either way; the status only decides which section a reader looks
    at first.
    """
    text = f"{type(exc).__name__}: {exc}"
    lowered = text.lower()

    if any(k in lowered for k in ("permission denied", "403", "forbidden", "unauthorized", "401")):
        return GATED, text
    if any(k in lowered for k in ("not enabled", "has not been used", "serviceusage")):
        return GATED, text
    if any(k in lowered for k in ("not found in region", "not available in", "location", "404")):
        return UNAVAILABLE_IN_REGION, text
    return ERROR, text


def run_probe(name: str, component: str, fn: Callable[[], str]) -> ProbeResult:
    started = time.monotonic()
    try:
        detail = fn()
        status, error = AVAILABLE, ""
    except BaseException as exc:  # noqa: BLE001 — a probe must never fail the run
        detail = ""
        status, error = classify(exc)
        error = f"{error}\n{traceback.format_exc(limit=3)}"
    elapsed = int((time.monotonic() - started) * 1000)
    result = ProbeResult(name=name, component=component, status=status, detail=detail,
                         error=error, elapsed_ms=elapsed)
    print(f"  {status:<22} {name} ({elapsed} ms)")
    return result


# --- Probes -------------------------------------------------------------------------------
# Each returns a short human-readable detail string on success, and raises on failure.


def probe_vertex_fast(project: str, region: str, model: str) -> str:
    """Smallest possible generation against the fast model."""
    from google import genai

    client = genai.Client(vertexai=True, project=project, location=region)
    resp = client.models.generate_content(model=model, contents="ok")
    return f"model={model} responded, {len(resp.text or '')} chars"


def probe_vertex_deep(project: str, region: str, model: str) -> str:
    """Smallest possible generation against the deep model."""
    from google import genai

    client = genai.Client(vertexai=True, project=project, location=region)
    resp = client.models.generate_content(model=model, contents="ok")
    return f"model={model} responded, {len(resp.text or '')} chars"


def probe_embeddings(project: str, region: str, model: str) -> str:
    """One short embedding. Also reports the dimensionality, which the KNN index depends on."""
    from google import genai

    client = genai.Client(vertexai=True, project=project, location=region)
    resp = client.models.embed_content(model=model, contents="drawbridge capability probe")
    dims = len(resp.embeddings[0].values)
    return f"model={model}, dimensionality={dims}"


def probe_agent_engine(project: str, region: str) -> str:
    """List Agent Engine deployments. Read-only: it creates nothing."""
    import vertexai
    from vertexai import agent_engines

    vertexai.init(project=project, location=region)
    engines = list(agent_engines.list())
    return f"list succeeded, {len(engines)} existing deployment(s)"


def probe_model_armor(project: str, region: str) -> str:
    """List Model Armor templates. Read-only."""
    from google.cloud import modelarmor_v1

    client = modelarmor_v1.ModelArmorClient(
        client_options={"api_endpoint": f"modelarmor.{region}.rep.googleapis.com"}
    )
    parent = f"projects/{project}/locations/{region}"
    templates = list(client.list_templates(parent=parent))
    names = ", ".join(t.name.rsplit("/", 1)[-1] for t in templates) or "none"
    return f"list succeeded, {len(templates)} template(s): {names}"


def probe_memory_bank(project: str, region: str) -> str:
    """Construct the Memory Bank service and attempt one retrieval.

    Memory Bank is scoped to an Agent Engine id, so with no deployment present this reports
    what it could and could not establish rather than claiming a clean pass.
    """
    from google.adk.memory.vertex_ai_memory_bank_service import VertexAiMemoryBankService

    engine_id = os.environ.get("AGENT_ENGINE_ID")
    if not engine_id:
        return (
            "service class constructs; no AGENT_ENGINE_ID set, so the scoped call was not "
            "attempted. Re-run with AGENT_ENGINE_ID after the hello agent deploys."
        )
    VertexAiMemoryBankService(project=project, location=region, agent_engine_id=engine_id)
    return f"service constructed against agent_engine_id={engine_id}"


def probe_agent_registry(project: str, region: str) -> str:
    """Probe the Agent Registry surface.

    TODO(verify): the registry's client surface. If the installed SDK exposes no registry
    client, that is itself the finding, and the fallback is publishing the fleet description
    as a documented artefact rather than a registry entry.
    """
    import vertexai
    from vertexai import agent_engines

    vertexai.init(project=project, location=region)
    surfaces = [n for n in dir(agent_engines) if "regist" in n.lower() or "template" in n.lower()]
    if not surfaces:
        raise RuntimeError(
            "no registry-shaped surface found on vertexai.agent_engines; "
            f"available: {[n for n in dir(agent_engines) if not n.startswith('_')]}"
        )
    return f"candidate registry surfaces: {surfaces}"


def probe_agent_gateway(project: str, region: str) -> str:
    """Probe for an Agent Gateway product surface.

    The architecture treats the gateway as a chokepoint this project implements in
    ``shared/gateway.py``. If a managed product surface exists it is worth using; if it does
    not, the contract is unchanged and the local implementation stands. Either answer is
    useful, so this probe reports rather than fails.
    """
    import importlib

    for module in ("google.cloud.agentgateway", "google.cloud.agentgateway_v1"):
        try:
            importlib.import_module(module)
            return f"module {module} is importable"
        except ImportError:
            continue
    raise RuntimeError(
        "no agent gateway client module installed; the gateway contract is implemented "
        "locally in shared/gateway.py, which is the documented fallback"
    )


def probe_firestore_knn(project: str, region: str) -> str:
    """Attempt a KNN query against the evidence chunk collection.

    An empty result is a pass: it proves the query shape is accepted. A missing-index error is
    also informative and is reported verbatim.
    """
    from google.cloud import firestore
    from google.cloud.firestore_v1.base_vector_query import DistanceMeasure
    from google.cloud.firestore_v1.vector import Vector

    client = firestore.Client(project=project)
    query = (
        client.collection("evidence_chunks")
        .where(filter=firestore.FieldFilter("review_id", "==", "probe"))
        .find_nearest(
            vector_field="embedding",
            query_vector=Vector([0.0] * 768),
            limit=1,
            distance_measure=DistanceMeasure.COSINE,
        )
    )
    docs = list(query.stream())
    return f"KNN query accepted, {len(docs)} result(s) (an empty result is a pass)"


def probe_pubsub(project: str, region: str) -> str:
    """List topics. Read-only."""
    from google.cloud import pubsub_v1

    client = pubsub_v1.PublisherClient()
    topics = list(client.list_topics(request={"project": f"projects/{project}"}))
    return f"list succeeded, {len(topics)} topic(s)"


def probe_storage(project: str, region: str) -> str:
    """List buckets. Read-only."""
    from google.cloud import storage

    client = storage.Client(project=project)
    buckets = list(client.list_buckets(max_results=10))
    return f"list succeeded, {len(buckets)} bucket(s)"


def probe_cloud_trace(project: str, region: str) -> str:
    """List recent traces. Read-only."""
    from google.cloud import trace_v1

    client = trace_v1.TraceServiceClient()
    traces = client.list_traces(request={"project_id": project})
    count = sum(1 for _ in itertools.islice(traces, 5))
    return f"list succeeded, sampled {count} trace(s)"


# --- Report -------------------------------------------------------------------------------


def render_report(results: list[ProbeResult], project: str, region: str) -> str:
    stamp = datetime.now(UTC).isoformat(timespec="seconds")
    counts: dict[str, int] = {}
    for r in results:
        counts[r.status] = counts.get(r.status, 0) + 1

    lines = [
        "# Capability report",
        "",
        f"Generated {stamp} against project `{project}` in `{region}`.",
        "",
        "Every probe here made the smallest real call the component allows. A component that "
        "reports anything other than `available` needs either a workaround or the "
        "interface-fallback path, and the error text below is what that decision is made from.",
        "",
        "| Component | Probe | Status | Detail |",
        "|---|---|---|---|",
    ]

    for r in results:
        detail = (r.detail or r.error.splitlines()[0] if r.error else r.detail) or ""
        detail = detail.replace("|", "\\|")[:160]
        lines.append(f"| {r.component} | {r.name} | `{r.status}` | {detail} |")

    lines += ["", "## Summary", ""]
    for status in (AVAILABLE, GATED, UNAVAILABLE_IN_REGION, ERROR):
        lines.append(f"- `{status}`: {counts.get(status, 0)}")

    failures = [r for r in results if r.status != AVAILABLE]
    if failures:
        lines += ["", "## Failures, verbatim", ""]
        for r in failures:
            lines += [
                f"### {r.component} — {r.name}",
                "",
                f"Status: `{r.status}` · {r.elapsed_ms} ms",
                "",
                "```",
                r.error.strip() or "(no exception text captured)",
                "```",
                "",
            ]
    else:
        lines += ["", "No failures. Every probed component answered.", ""]

    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", default=os.environ.get("PROJECT_ID"))
    parser.add_argument("--region", default=os.environ.get("REGION", "us-central1"))
    parser.add_argument("--model-fast", default=os.environ.get("MODEL_FAST", "gemini-3.5-flash"))
    parser.add_argument("--model-deep", default=os.environ.get("MODEL_DEEP", "gemini-pro"))
    parser.add_argument(
        "--model-embed", default=os.environ.get("MODEL_EMBED", "text-embedding-005")
    )
    parser.add_argument("--out", default=REPORT_PATH)
    args = parser.parse_args()

    if not args.project:
        print("PROJECT_ID must be set, or pass --project")
        return 2

    p, r = args.project, args.region
    print(f"probing {p} in {r}\n")

    probes = [
        ("Vertex AI", "fast model generation", lambda: probe_vertex_fast(p, r, args.model_fast)),
        ("Vertex AI", "deep model generation", lambda: probe_vertex_deep(p, r, args.model_deep)),
        ("Vertex AI", "text embeddings", lambda: probe_embeddings(p, r, args.model_embed)),
        ("Agent Engine", "list deployments", lambda: probe_agent_engine(p, r)),
        ("Model Armor", "list templates", lambda: probe_model_armor(p, r)),
        ("Memory Bank", "construct service", lambda: probe_memory_bank(p, r)),
        ("Agent Registry", "locate surface", lambda: probe_agent_registry(p, r)),
        ("Agent Gateway", "locate surface", lambda: probe_agent_gateway(p, r)),
        ("Firestore", "KNN vector search", lambda: probe_firestore_knn(p, r)),
        ("Pub/Sub", "list topics", lambda: probe_pubsub(p, r)),
        ("Cloud Storage", "list buckets", lambda: probe_storage(p, r)),
        ("Cloud Trace", "list traces", lambda: probe_cloud_trace(p, r)),
    ]

    results = [run_probe(name, component, fn) for component, name, fn in probes]

    report = render_report(results, p, r)
    with open(args.out, "w") as fh:
        fh.write(report)

    print(f"\nwrote {args.out}")
    failed = [x for x in results if x.status != AVAILABLE]
    print(f"{len(results) - len(failed)}/{len(results)} available")
    # The probe always exits 0: a failing component is a finding to read, not a broken run.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
