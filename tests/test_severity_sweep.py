"""The severity sweep's machinery, exercised without spending a live call.

The sweep itself is a live measurement and is deliberately not run here — every claim in two
packs, three times each, against a free tier that allows twenty calls a day. What these tests
cover is everything around the call: that a partial run resumes rather than restarting, that a
failed call is recorded as a failure rather than as an unstable answer, and that the comparison
is over the three fields the Trust Score is computed from rather than over wording.

The resume test is the one that matters. The most likely reason the real sweep stops is the
daily quota, which is also the reason it cannot simply be started again from the beginning.
"""

from __future__ import annotations

from scripts.severity_sweep import (
    STABLE,
    UNMEASURED,
    UNSTABLE,
    Attempt,
    already_done,
    load,
    report,
    signature_of,
    summarise,
)
from shared.domain import Finding


def finding(**overrides) -> Finding:
    return Finding(
        **{
            "finding_id": "f",
            "review_id": "r",
            "domain": "access_control",
            "severity": "high",
            "source": "model",
            "contradiction": True,
            "summary": "one wording",
            **overrides,
        }
    )


def attempt(question_id: str, run: int, signature: str | None, error: str | None = None):
    return Attempt("datadynamo", "r", question_id, run, signature, error)


# --- What counts as the same answer -----------------------------------------------------------


def test_the_signature_is_the_three_fields_the_score_is_computed_from():
    assert signature_of(finding()) == "access_control/high/contradiction"


def test_wording_is_not_compared():
    """Summary text is expected to vary between runs and moves nothing. Comparing it would
    report drift in a field that has no effect on the number."""
    first = signature_of(finding(summary="one wording"))
    second = signature_of(finding(summary="a quite different wording"))

    assert first == second


def test_a_claim_that_produced_no_finding_has_a_signature_too():
    """"No finding" is an answer, and a claim that produces one on Monday and a high on Tuesday
    is the least stable thing the sweep could find."""
    assert signature_of(None) == "none"


# --- Resuming ---------------------------------------------------------------------------------


def test_a_partial_run_is_not_repeated(tmp_path):
    """A sweep interrupted at call 180 of 340 costs 160 calls to finish, not 340."""
    out = tmp_path / "sweep.jsonl"
    out.write_text(
        "\n".join(
            a.as_json()
            for a in (attempt("DP01", 1, "data_protection/low/gap"), attempt("DP01", 2, "none"))
        )
        + "\n"
    )

    assert already_done(out) == {
        ("datadynamo", "DP01", 1),
        ("datadynamo", "DP01", 2),
    }


def test_a_failed_call_is_measured_again_on_a_resume(tmp_path):
    """A failure spent a call and produced no answer. Skipping it on the resume would leave the
    claim permanently unmeasured for the price of having tried once."""
    out = tmp_path / "sweep.jsonl"
    out.write_text(attempt("DP01", 1, None, error="429 quota").as_json() + "\n")

    assert already_done(out) == set()


def test_an_unreadable_line_is_skipped_rather_than_fatal(tmp_path):
    """The file is appended to during a run that may be killed mid-write."""
    out = tmp_path / "sweep.jsonl"
    out.write_text(attempt("DP01", 1, "x/y/gap").as_json() + "\n{ half a line")

    assert len(list(load(out))) == 1


def test_a_file_that_does_not_exist_yet_reads_as_nothing(tmp_path):
    assert list(load(tmp_path / "absent.jsonl")) == []


# --- The verdicts -----------------------------------------------------------------------------


def test_three_identical_answers_are_stable():
    summary = summarise([attempt("DP01", n, "data_protection/low/gap") for n in (1, 2, 3)])

    assert summary["claims"][0]["verdict"] == STABLE
    assert summary["rate"] == 1.0


def test_one_different_answer_is_drift():
    summary = summarise(
        [
            attempt("AC01", 1, "access_control/high/contradiction"),
            attempt("AC01", 2, "access_control/medium/contradiction"),
            attempt("AC01", 3, "access_control/high/contradiction"),
        ]
    )

    assert summary["claims"][0]["verdict"] == UNSTABLE
    assert summary["unstable"] == 1
    assert len(summary["claims"][0]["signatures"]) == 2


def test_a_claim_that_never_answered_is_unmeasured_rather_than_unstable():
    """Different problems, different repairs. A claim nobody could measure is a claim to run
    again; a claim that answered differently is a claim whose anchors need tightening."""
    summary = summarise([attempt("IR01", n, None, error="429 quota") for n in (1, 2, 3)])

    assert summary["claims"][0]["verdict"] == UNMEASURED
    assert summary["unmeasured"] == 1
    assert summary["measured"] == 0


def test_the_rate_is_over_what_was_measured_rather_than_over_what_was_attempted():
    """A rate diluted by calls that never returned would report a quota problem as a stability
    problem."""
    summary = summarise(
        [
            attempt("DP01", 1, "data_protection/low/gap"),
            attempt("DP01", 2, "data_protection/low/gap"),
            attempt("IR01", 1, None, error="429 quota"),
        ]
    )

    assert summary["measured"] == 1
    assert summary["rate"] == 1.0


# --- The report -------------------------------------------------------------------------------


def test_the_report_names_the_drifted_claims_and_the_repair():
    """The repair for drift is tighter anchors, not a lower temperature — temperature is already
    zero, so a wobble at zero is an ambiguity in a definition."""
    summary = summarise(
        [
            attempt("AC01", 1, "access_control/high/contradiction"),
            attempt("AC01", 2, "access_control/medium/gap"),
        ]
    )

    rendered = report(summary, runs=2)

    assert "datadynamo/AC01" in rendered
    assert "temperature" in rendered
    assert "Tighter severity anchors" in rendered


def test_a_clean_sweep_proposes_no_repair():
    rendered = report(summarise([attempt("DP01", n, "x/low/gap") for n in (1, 2)]), runs=2)

    assert "What to do about the drift" not in rendered
    assert "1 of 1 claims stable (100%)" in rendered
