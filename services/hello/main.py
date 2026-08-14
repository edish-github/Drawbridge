"""Smallest possible Cloud Run service: a public root and a health endpoint.

This exists to prove the platform works — that a container builds, deploys, gets a public URL
and answers, before any product code depends on it. It holds no state, calls no model, reads
no secret and touches no other service, so a failure here is a platform failure and nothing
else.

Failure semantics: unhandled exceptions return 500 with no body; there is nothing here whose
internals are worth leaking. The health endpoint reports only that the process is serving —
it deliberately checks no dependency, because a readiness probe that fails on a downstream
outage takes a healthy container out of rotation for someone else's problem.
"""

from __future__ import annotations

import os

from fastapi import FastAPI

app = FastAPI(title="drawbridge-hello", docs_url=None, redoc_url=None)


@app.get("/")
def root() -> dict[str, str]:
    """Return the service identity. Public, cached, and reaches no model."""
    return {
        "service": "drawbridge-hello",
        "message": "Drawbridge platform check: Cloud Run is serving.",
        "region": os.environ.get("REGION", "unset"),
        "revision": os.environ.get("K_REVISION", "local"),
    }


@app.get("/healthz")
def healthz() -> dict[str, str]:
    """Liveness and readiness. Checks nothing downstream, by design."""
    return {"status": "ok"}
