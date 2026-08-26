"""The audit binder as a service.

``render.py`` builds the document and has done since the beginning; this wraps it in an endpoint
so an operator can obtain one from the product rather than from a terminal. It adds exactly two
things: a tenant scope, and a check that the person asking belongs to it.

**It renders from a template and never from a model.** That claim is asserted by an import graph
in the test suite rather than promised here, because a document that could be steered by the
content it reports on is worse than no document — and the content it reports on is written by the
party being assessed.

Failure semantics: an unknown review and a review in another organisation return the same 404. A
render failure returns 500 and writes nothing; a partially rendered binder is not an audit record.
"""

from __future__ import annotations

import logging
import os

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import HTMLResponse

from shared import tenancy
from shared.identity import principal_in, verify_token

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("drawbridge.binder")

app = FastAPI(title="drawbridge-binder", docs_url=None, redoc_url=None)


@app.get("/healthz")
def health() -> dict:
    return {"status": "ok", "service": "binder"}


@app.get("/binders/{org_id}/{review_id}", response_class=HTMLResponse)
def binder(org_id: str, review_id: str, authorization: str = Header(default="")) -> HTMLResponse:
    """Render one review's audit binder.

    The caller is verified here rather than upstream. A binder is the most sensitive artefact the
    product produces — it contains the vendor's evidence, the reasoning, and the name of whoever
    accepted the risk — so the service that renders one checks membership itself rather than
    trusting whoever proxied the request.
    """
    principal = verify_token(authorization.removeprefix("Bearer ").strip())
    if principal is None:
        raise HTTPException(401, "Sign in again.")

    with tenancy.acting_for(org_id):
        scoped = principal_in(principal, org_id)
        if not scoped.is_member:
            # Same answer as a review that does not exist. A response that distinguished them
            # would confirm the existence of another organisation's review.
            raise HTTPException(404, "No such review.")

        if not tenancy.collection("reviews").document(review_id).get().exists:
            raise HTTPException(404, "No such review.")

        from services.binder.render import render

        try:
            html = render(review_id)
        except Exception as exc:  # noqa: BLE001 — a partial binder is not an audit record
            log.exception("binder render failed for %s/%s", org_id, review_id)
            raise HTTPException(500, "The binder could not be rendered.") from exc

    return HTMLResponse(
        content=html,
        headers={
            "content-disposition": f'attachment; filename="{review_id}-audit-binder.html"',
            # An audit record is a point-in-time document. Caching one would serve a stale
            # version of something whose whole value is being the record as at now.
            "cache-control": "no-store",
        },
    )


if __name__ == "__main__":  # pragma: no cover
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8082)))
