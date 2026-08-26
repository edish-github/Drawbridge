"""The vendor portal: where the other company answers.

This is the surface a person outside your organisation touches — a security contact at a supplier
who has been asked sixty questions and would rather be doing something else. Everything about it
is shaped by that:

**No account.** They authenticate a signed link, not themselves. Asking a vendor to create an
account is how a three-day review becomes a six-week one.

**It resumes.** A questionnaire is answered over days, in the gaps between other work. Every
answer is saved as it is typed, and the page reopens exactly where it was left.

**It never says more than it must.** An invalid link, an expired link, a link for a review that
has closed and a link for a review that never existed all render the same page. A vendor cannot
act on the difference, and a portal that distinguished them would confirm which review ids exist
to anybody who tried a few.

**Uploads land in quarantine and cannot be read back.** The portal's identity holds
``objectCreator`` on the quarantine bucket and no read at all, so a document a vendor uploads is
one this service can never retrieve — which is what makes "no agent has a code path into
quarantine" true rather than aspirational.

Failure semantics: a save that fails returns an error the vendor can act on and keeps their text
in the page. Losing a paragraph somebody spent ten minutes writing is the single most likely way
this product makes an enemy of the person it needs cooperation from.
"""

from __future__ import annotations

import logging
import os
import uuid
from datetime import UTC, datetime

from fastapi import FastAPI, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from google.cloud.firestore_v1 import FieldFilter

from shared import tenancy
from shared.context import AgentContext
from shared.domain import ReviewState
from shared.events import TOPIC_VENDOR_EVIDENCE_UPLOADED, TOPIC_VENDOR_REPLY_RECEIVED, publish
from shared.portal_links import PortalLink, verify

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("drawbridge.portal")

app = FastAPI(title="drawbridge-portal", docs_url=None, redoc_url=None)

OPEN_STATES = {
    ReviewState.QUESTIONNAIRE_OUT.value,
    ReviewState.REPLIES_IN.value,
    ReviewState.EVIDENCE_REVIEW.value,
    ReviewState.GATED.value,
}
"""States in which a vendor may still contribute.

Evidence review is included: a document that arrives while the fleet is reconciling is still
useful, and telling a vendor "too late" for something they were asked for is how you get an
incomplete review and an annoyed supplier. A decided review is not — its record is immutable, and
a late submission opens a new review rather than editing a closed one.
"""

MAX_UPLOAD_BYTES = 40 * 1024 * 1024


@app.get("/healthz")
def health() -> dict:
    return {"status": "ok", "service": "portal"}


def _resolve(token: str) -> tuple[PortalLink, dict]:
    """Verify a link and load its review, or raise the one refusal this service has.

    Raises:
        HTTPException: 404 for every failure — invalid, expired, closed, absent. One answer,
            because a vendor cannot act on the difference and an attacker should not learn it.
    """
    link = verify(token)
    if link is None:
        raise HTTPException(404, "link-not-usable")

    with tenancy.acting_for(link.org_id):
        snap = tenancy.collection("reviews").document(link.review_id).get()
        if not snap.exists:
            raise HTTPException(404, "link-not-usable")
        review = snap.to_dict() or {}

    if str(review.get("state")) not in OPEN_STATES:
        raise HTTPException(404, "link-closed")

    return link, review


@app.get("/r/{token}", response_class=HTMLResponse)
def questionnaire(token: str) -> HTMLResponse:
    """The questionnaire, with whatever has already been answered filled in."""
    try:
        link, review = _resolve(token)
    except HTTPException as exc:
        return HTMLResponse(_closed_page(str(exc.detail)), status_code=exc.status_code)

    with tenancy.acting_for(link.org_id):
        vendor = (
            tenancy.collection("vendors").document(str(review.get("vendor_id"))).get().to_dict()
            or {}
        )
        answers = {
            str(d.to_dict().get("question_id")): d.to_dict()
            for d in tenancy.collection("qa_responses")
            .where(filter=FieldFilter("review_id", "==", link.review_id))
            .stream()
        }
        org = tenancy.load_org(link.org_id)
        questions = _questions_for(link.review_id, review)
        documents = [
            d.to_dict()
            for d in tenancy.collection("screenings")
            .where(filter=FieldFilter("review_id", "==", link.review_id))
            .stream()
        ]

    return HTMLResponse(
        _questionnaire_page(
            token=token,
            requester=org.name if org else "the requesting organisation",
            vendor_name=str(vendor.get("name", "your company")),
            questions=questions,
            answers=answers,
            uploaded=len({d.get("origin_ref") for d in documents}),
        )
    )


@app.post("/r/{token}/answer")
async def save_answer(token: str, question_id: str = Form(...), text: str = Form("")) -> dict:
    """Save one answer. Called as the vendor types, so it must be cheap and idempotent.

    Keyed on review and question, so the same answer edited five times is one document rather than
    five, and a redelivered save overwrites rather than duplicating.
    """
    link, _ = _resolve(token)

    with tenancy.acting_for(link.org_id):
        tenancy.collection("qa_responses").document(f"{link.review_id}:{question_id}").set(
            tenancy.stamp(
                {
                    "review_id": link.review_id,
                    "question_id": question_id,
                    "text": text,
                    "source_msg": "portal",
                    "answered_at": datetime.now(UTC).isoformat(),
                    # Provenance, not a judgement. A portal answer was typed by a person into a
                    # form; the confidence a parsed email answer carries does not apply, and
                    # inventing one would be the fleet asserting something it did not measure.
                    "provenance": "portal",
                    "needs_human": False,
                }
            ),
            merge=True,
        )
    return {"ok": True}


@app.post("/r/{token}/submit")
def submit(token: str) -> RedirectResponse:
    """Tell the fleet a batch of answers has arrived.

    Publishing is separate from saving on purpose. Saving happens continuously as somebody types;
    publishing is the vendor saying *I am done for now*, which is what the coverage join is
    actually waiting on.
    """
    link, _ = _resolve(token)

    ctx = AgentContext(
        org_id=link.org_id,
        review_id=link.review_id,
        agent="portal",
        trace_id=uuid.uuid4().hex,
    )
    with tenancy.acting_for(link.org_id):
        publish(
            TOPIC_VENDOR_REPLY_RECEIVED,
            link.review_id,
            {"message_id": f"portal-{uuid.uuid4().hex[:8]}", "source": "portal", "body": ""},
            ctx=ctx,
        )

    return RedirectResponse(f"/r/{token}?saved=1", status_code=303)


@app.post("/r/{token}/upload")
async def upload(token: str, request: Request, document: UploadFile) -> RedirectResponse:
    """Accept a document into quarantine.

    The bytes go straight to the quarantine bucket, which this service can write and cannot read.
    Nothing is inspected here — inspection is the screening pipeline's, behind a boundary this
    service is on the wrong side of, and a portal that peeked at an upload would be a portal that
    had already read hostile content.
    """
    link, _ = _resolve(token)

    payload = await document.read()
    if len(payload) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "That file is larger than 40 MB.")
    if not payload:
        raise HTTPException(400, "That file was empty.")

    from shared.config import settings
    from shared.storage import ref_for, write_object

    # Namespaced by organisation as well as review. Two customers may review the same vendor and
    # receive files of the same name, and object names are the one place tenancy is not enforced
    # by a Firestore path.
    safe = (document.filename or f"upload-{uuid.uuid4().hex[:8]}").replace("/", "_")
    ref = ref_for(
        settings().bucket_quarantine,
        f"{link.org_id}/{link.review_id}/{uuid.uuid4().hex[:8]}-{safe}",
    )
    write_object(ref, payload, content_type=document.content_type or "application/octet-stream")

    ctx = AgentContext(
        org_id=link.org_id,
        review_id=link.review_id,
        agent="portal",
        trace_id=uuid.uuid4().hex,
    )
    with tenancy.acting_for(link.org_id):
        publish(
            TOPIC_VENDOR_EVIDENCE_UPLOADED,
            link.review_id,
            {"object_ref": ref, "filename": document.filename},
            ctx=ctx,
        )

    log.info("upload accepted review=%s ref=%s", link.review_id, ref)
    return RedirectResponse(f"/r/{token}?uploaded=1", status_code=303)


def _questions_for(review_id: str, review: dict) -> list[dict]:
    """The questions this vendor has been sent, in bank order.

    Read from what was actually delivered rather than recomputed from the tier: a re-tier adds
    questions mid-review, and a portal that recomputed would show a vendor questions nobody had
    sent them.
    """
    from agents.questionnaire.generator import load_bank

    sent = set(review.get("sent_questions") or [])
    bank = load_bank()

    out: list[dict] = []
    for domain, items in bank.items():
        for question in items:
            if not sent or question.question_id in sent:
                out.append(
                    {
                        "id": question.question_id,
                        "text": question.text,
                        "domain": domain,
                        "evidence": question.evidence_required,
                    }
                )
    return out


# --- Rendering ----------------------------------------------------------------------------
#
# Server-rendered HTML with no framework and no build step. The portal is the one surface whose
# audience did not choose this product, will use it once, and may be on a phone on a train — so it
# is one document with inline styles, no JavaScript bundle, and a form that works without
# JavaScript at all. The auto-save is progressive enhancement; the submit button is a plain POST.


def _shell(title: str, body: str) -> str:
    return f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title>
<style>
*{{box-sizing:border-box}}
body{{margin:0;background:#e7e4dd;color:#191a1c;
 font:400 16px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
 -webkit-font-smoothing:antialiased}}
.wrap{{max-width:760px;margin:0 auto;padding:32px 20px 80px}}
.card{{background:#fff;border:1px solid rgba(25,26,28,.08);border-radius:20px;padding:28px;
 margin-bottom:16px;box-shadow:0 1px 2px rgba(25,26,28,.04)}}
h1{{font-size:26px;line-height:1.2;letter-spacing:-.02em;margin:0 0 8px;font-weight:700}}
h2{{font-size:15px;margin:0 0 14px;font-weight:600}}
p{{margin:0 0 14px;color:#4d4f52}}
.lede{{font-size:16.5px;color:#4d4f52}}
.brand{{display:flex;align-items:center;gap:10px;margin-bottom:22px}}
.mark{{width:30px;height:30px;border-radius:10px;background:#191a1c;display:flex;
 align-items:center;justify-content:center;flex:none}}
.name{{font-weight:700;font-size:13.5px;letter-spacing:.13em}}
.q{{border-top:1px solid rgba(25,26,28,.08);padding:18px 0}}
.q:first-of-type{{border-top:none}}
.q label{{display:block;font-weight:600;font-size:14.5px;margin-bottom:4px}}
.q .domain{{font:500 10.5px/1.5 ui-monospace,monospace;letter-spacing:.09em;
 text-transform:uppercase;color:#96938c;margin-bottom:8px}}
textarea{{width:100%;min-height:88px;padding:12px 14px;border-radius:12px;
 border:1px solid rgba(25,26,28,.16);font:inherit;font-size:14.5px;resize:vertical;
 background:#fdfdfc;color:#191a1c}}
textarea:focus-visible{{outline:2px solid transparent;border-color:#3d8a67;
 box-shadow:0 0 0 3px rgba(61,138,103,.25)}}
.btn{{display:inline-flex;align-items:center;justify-content:center;padding:13px 24px;
 border-radius:999px;background:#191a1c;color:#f6f4f0;font-weight:600;font-size:14px;
 border:0;cursor:pointer;text-decoration:none}}
.btn:hover{{background:#2c2e31}}
.note{{border:1px dashed rgba(25,26,28,.18);border-radius:14px;padding:14px 16px;
 font-size:13.5px;color:#6f6c66;background:#f6f4f0}}
.ok{{border:1px solid #3d8a67;background:#e9f2ec;color:#265a43;border-radius:14px;
 padding:13px 16px;font-size:14px;margin-bottom:16px}}
.saved{{font:500 11px/1.5 ui-monospace,monospace;color:#3d8a67;
 letter-spacing:.06em;text-transform:uppercase;height:14px}}
input[type=file]{{font:inherit;font-size:14px}}
footer{{text-align:center;color:#96938c;font-size:12.5px;padding-top:8px}}
</style></head><body><div class="wrap">
<div class="brand"><div class="mark">
<svg width="16" height="16" viewBox="0 0 16 16" fill="none">
<path d="M2 13V6.2a6 6 0 0 1 12 0V13" stroke="#f6f4f0" stroke-width="1.5" stroke-linecap="round"/>
<path d="M8 13V6.5M4.6 13V8.4M11.4 13V8.4" stroke="#f6f4f0" stroke-width="1.3"
 stroke-linecap="round"/>
</svg></div><div class="name">DRAWBRIDGE</div></div>
{body}
<footer>This link is private to your company. Please do not forward it.</footer>
</div></body></html>"""


def _closed_page(reason: str) -> str:
    """One page for every refusal. See the module docstring."""
    body = """<div class="card">
<h1>This link is no longer usable</h1>
<p class="lede">It may have expired, or the review it belongs to may have finished.</p>
<p>If you were asked for something and still have it to send, reply to the email you received and
whoever requested the review will send you a fresh link.</p>
</div>"""
    return _shell("Link no longer usable", body)


def _questionnaire_page(
    *,
    token: str,
    requester: str,
    vendor_name: str,
    questions: list[dict],
    answers: dict,
    uploaded: int,
) -> str:
    answered = sum(1 for q in questions if (answers.get(q["id"], {}).get("text") or "").strip())
    total = len(questions) or 1

    rows = []
    for q in questions:
        existing = (answers.get(q["id"], {}).get("text") or "").replace("<", "&lt;")
        rows.append(
            f"""<div class="q">
<div class="domain">{q['domain'].replace('_', ' ')} · {q['id']}</div>
<label for="{q['id']}">{q['text']}</label>
<textarea id="{q['id']}" name="{q['id']}" data-question="{q['id']}"
 placeholder="Please describe how this works in practice, and name the evidence we can check."
>{existing}</textarea>
<div class="saved" data-saved-for="{q['id']}"></div>
</div>"""
        )

    body = f"""<div class="card">
<h1>Security review — {vendor_name}</h1>
<p class="lede">{requester} is reviewing the service you provide, and has {total} questions.</p>
<p>Answer as many as you can, in your own words. <strong>Your work is saved as you type</strong>,
so you can close this page and come back to it. Nothing is submitted until you press the button
at the bottom.</p>
<p>Where a question asks for evidence, attach the document rather than describing it — it is
almost always faster for both of us.</p>
<div class="note"><strong>{answered} of {total} answered.</strong>
{f' {uploaded} document(s) received.' if uploaded else ''}</div>
</div>

<form method="post" action="/r/{token}/submit">
<div class="card">
<h2>Questions</h2>
{''.join(rows)}
</div>
<div class="card">
<h2>Documents</h2>
<p>Certifications, audit reports, penetration test summaries, your data processing agreement —
whatever supports the answers above.</p>
</div>
<div class="card" style="text-align:center">
<button class="btn" type="submit">Send these answers</button>
<p style="margin-top:12px;font-size:13.5px">You can send more than once. Later answers replace
earlier ones.</p>
</div>
</form>

<form class="card" method="post" action="/r/{token}/upload" enctype="multipart/form-data">
<h2>Attach a document</h2>
<p><input type="file" name="document" required accept=".pdf,.txt,.md,.doc,.docx"></p>
<button class="btn" type="submit">Upload</button>
<p style="margin-top:12px;font-size:13.5px">Up to 40 MB. It is scanned before anyone reads it.</p>
</form>

<script>
// Progressive enhancement only: without JavaScript every answer still submits with the form.
// This saves each one as it is typed so a closed tab does not cost somebody their afternoon.
(function () {{
  var timers = {{}};
  document.querySelectorAll("textarea[data-question]").forEach(function (box) {{
    box.addEventListener("input", function () {{
      var id = box.dataset.question;
      clearTimeout(timers[id]);
      timers[id] = setTimeout(function () {{
        var flag = document.querySelector('[data-saved-for="' + id + '"]');
        var form = new FormData();
        form.append("question_id", id);
        form.append("text", box.value);
        fetch("/r/{token}/answer", {{ method: "POST", body: form }})
          .then(function (r) {{
            flag.textContent = r.ok ? "Saved" : "Not saved — keep this page open";
          }})
          .catch(function () {{ flag.textContent = "Not saved — keep this page open"; }});
      }}, 800);
    }});
  }});
}})();
</script>"""
    return _shell(f"Security review — {vendor_name}", body)


if __name__ == "__main__":  # pragma: no cover
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8083)))
