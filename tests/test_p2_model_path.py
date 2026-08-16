"""P2 on the model path: no external content reaches a model without a verified stamp.

The claim is in the architecture document, in the diagrams and in the video narration, and for
most of this project's life it was enforced nowhere — cross-examination called the router
directly and the policy lived at the effect gate. These tests are what make it true.

The refusal is the interesting case, so most of what follows asserts that something did *not*
happen. The seeded documents the whole local demo runs on are exactly the content P2 exists to
refuse, and they are refused: the allowance that lets a fixture run proceed is a declaration on
the artefact, not a hole in the policy.
"""

from __future__ import annotations

import os

import pytest

from shared.armor import (
    CRITICAL_FILTERS,
    EXECUTION_SKIPPED,
    EXECUTION_SUCCESS,
    MATCH_FOUND,
    NO_MATCH_FOUND,
    SEEDED_TEMPLATE,
    ScreenResult,
    record_screening,
    sign_stamp,
    stamp_for,
    stamps_for,
)
from shared.context import AgentContext
from shared.gateway import ALLOW_UNSCREENED_ENV, PolicyViolation, unscreened_fixtures_allowed
from shared.routing import EXTERNAL_INPUT_TASKS, enforce_p2
from tests.conftest import emulator_required

REAL_TEMPLATE = "drawbridge-untrusted"


def ctx(review_id: str) -> AgentContext:
    return AgentContext(review_id=review_id, agent="test", trace_id="t")


def verdict(
    *, template: str = REAL_TEMPLATE, executed: bool = True, sanitised: bool = False, ref: str = "r"
) -> ScreenResult:
    return ScreenResult(
        clean=not sanitised,
        template=template,
        template_version="3",
        filters={
            f: (MATCH_FOUND if sanitised and f == "pi_and_jailbreak" else NO_MATCH_FOUND)
            for f in CRITICAL_FILTERS
        },
        execution={
            f: (EXECUTION_SUCCESS if executed else EXECUTION_SKIPPED) for f in CRITICAL_FILTERS
        },
        sanitised=sanitised,
        origin_ref=ref,
    )


def stamp(**kwargs) -> str:
    return sign_stamp(verdict(**kwargs).model_dump(mode="json"))


@pytest.fixture
def strict(monkeypatch):
    """Run with the fixture allowance off, which is how cloud always runs."""
    monkeypatch.delenv(ALLOW_UNSCREENED_ENV, raising=False)


# --- The tasks the policy covers ---------------------------------------------------------


def test_every_task_consuming_vendor_content_is_covered():
    """A task added later that reads a vendor document and is not in this set is a hole."""
    for task in ("cross_examine", "extract_controls", "parse_reply", "relevance", "risk_memo"):
        assert task in EXTERNAL_INPUT_TASKS


def test_planning_is_deliberately_not_covered():
    """The intake form is written by the buying organisation, not by the vendor. It is
    untrustworthy in a different way and the planner already treats it as a claim."""
    assert "plan_review" not in EXTERNAL_INPUT_TASKS


def test_a_task_outside_the_set_needs_no_stamp(review_id):
    enforce_p2("plan_review", ctx(review_id), None)


# --- The refusals ---------------------------------------------------------------------------


@pytest.mark.parametrize("task", sorted(EXTERNAL_INPUT_TASKS))
def test_no_stamp_is_refused_for_every_covered_task(task, review_id, strict):
    with pytest.raises(PolicyViolation) as exc:
        enforce_p2(task, ctx(review_id), None)

    assert exc.value.policy == "P2"


def test_an_empty_stamp_list_is_refused_like_no_stamp_at_all(review_id, strict):
    """A caller that passed [] has not screened its sources; it has skipped naming them."""
    with pytest.raises(PolicyViolation, match="P2"):
        enforce_p2("cross_examine", ctx(review_id), [])


def test_a_stamp_whose_body_was_edited_after_signing_is_refused(review_id, strict):
    """Otherwise P2 checks that content carries a verdict rather than what the verdict said.

    The edit turns an inadmissible claim into an admissible one, so a policy that parsed the
    payload without verifying the tag would let this through — which is the whole point.
    """
    from shared.gateway import verify_stamp

    honest = stamp(sanitised=True)
    tampered = honest.replace('"sanitised": true', '"sanitised": false')

    assert tampered != honest, "the fixture must actually change the claim"
    assert verify_stamp(tampered) is None

    with pytest.raises(PolicyViolation, match="P2"):
        enforce_p2("risk_memo", ctx(review_id), [tampered])


def test_a_skipped_detector_is_treated_as_unscreened(review_id, strict):
    """A detector that never ran is not a detector that found nothing."""
    with pytest.raises(PolicyViolation, match="P2"):
        enforce_p2("cross_examine", ctx(review_id), [stamp(executed=False)])


def test_one_bad_source_refuses_the_whole_call(review_id, strict):
    """A prompt is one prompt. A clean document beside an inadmissible one does not launder it."""
    with pytest.raises(PolicyViolation, match="P2"):
        enforce_p2("cross_examine", ctx(review_id), [stamp(), stamp(executed=False)])


def test_a_fully_screened_source_passes(review_id, strict):
    enforce_p2("cross_examine", ctx(review_id), [stamp()])


# --- Sanitised content is inadmissible to the memo -----------------------------------------


def test_a_sanitised_document_reaches_the_evidence_agent(review_id, strict):
    """The legitimate content of a document that tried something is still reviewed."""
    enforce_p2("cross_examine", ctx(review_id), [stamp(sanitised=True)])


def test_a_sanitised_document_does_not_reach_the_memo(review_id, strict):
    """A sanitised document is by definition one that tried something, and the memo is the
    artefact a CISO acts on."""
    with pytest.raises(PolicyViolation, match="P2"):
        enforce_p2("risk_memo", ctx(review_id), [stamp(sanitised=True)])


# --- The fixture allowance ------------------------------------------------------------------


def test_a_seeded_stamp_is_refused_by_default(review_id, strict):
    """The seeded path the whole local demo runs on is unscreened, and P2 says so."""
    with pytest.raises(PolicyViolation, match="P2"):
        enforce_p2("cross_examine", ctx(review_id), [stamp(template=SEEDED_TEMPLATE)])


@emulator_required
def test_the_allowance_admits_a_declared_fixture_and_marks_the_review(review_id, db, monkeypatch):
    monkeypatch.setenv(ALLOW_UNSCREENED_ENV, "1")
    db.collection("reviews").document(review_id).set({"review_id": review_id})

    enforce_p2("cross_examine", ctx(review_id), [stamp(template=SEEDED_TEMPLATE)])

    assert db.collection("reviews").document(review_id).get().to_dict()["unscreened_fixtures"]


def test_the_allowance_does_not_cover_a_source_with_no_stamp(review_id, monkeypatch):
    """It admits content that declared what it is. It never admits content nobody labelled."""
    monkeypatch.setenv(ALLOW_UNSCREENED_ENV, "1")

    with pytest.raises(PolicyViolation, match="P2"):
        enforce_p2("cross_examine", ctx(review_id), None)


def test_the_allowance_does_not_cover_a_real_verdict_that_failed(review_id, monkeypatch):
    """A skipped detector on the real service is a broken control, not a fixture."""
    monkeypatch.setenv(ALLOW_UNSCREENED_ENV, "1")

    with pytest.raises(PolicyViolation, match="P2"):
        enforce_p2("cross_examine", ctx(review_id), [stamp(executed=False)])


def test_cloud_mode_ignores_the_allowance_entirely(monkeypatch):
    """An escape hatch an environment variable can open in production is not an escape hatch."""
    from shared import config

    monkeypatch.setenv(ALLOW_UNSCREENED_ENV, "1")
    monkeypatch.setattr(config, "settings", lambda: _CloudSettings())

    assert unscreened_fixtures_allowed() is False


class _CloudSettings:
    is_local = False
    is_cloud = True


# --- Stamps come from the ledger the binder is rendered from --------------------------------


@emulator_required
def test_a_stamp_is_resolved_from_the_screening_ledger(review_id):
    record_screening(review_id, verdict(ref="gs://clean/x/soc2-report.txt"))

    assert stamp_for(review_id, "gs://clean/x/soc2-report.txt")


@emulator_required
def test_a_clean_reference_resolves_to_the_verdict_recorded_against_quarantine(review_id):
    """Promotion names the quarantine object; the caller holds the clean one. Same document."""
    record_screening(review_id, verdict(ref="gs://quarantine/acme/soc2-report.pdf"))

    assert stamp_for(review_id, f"gs://clean/{review_id}/soc2-report.txt")


@emulator_required
def test_an_unscreened_reference_resolves_to_nothing(review_id):
    assert stamp_for(review_id, "gs://clean/x/never-screened.txt") is None
    assert stamps_for(review_id, ["gs://clean/x/never-screened.txt"]) == []


@emulator_required
def test_the_environment_the_suite_runs_in_declares_itself():
    """The suite runs on fixtures, and it says so rather than passing because nothing checks."""
    assert os.environ.get(ALLOW_UNSCREENED_ENV) == "1"
