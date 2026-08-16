"""The miss-rate harness: its classification, its resume, and the corpus it reads.

Not one live call here. What is under test is the part that decides what a run meant — a
harness that mislabels a retrieval failure as a model failure sends every repair to the wrong
place, and that is worse than not measuring, because it comes with a number attached.

The measurement itself is deliberately not run. Eighteen live cross-examination calls against a
twenty-a-day cap would exhaust the quota, and it is written to be one command the day that
lifts.
"""

from __future__ import annotations

import json

import pytest

from scripts.miss_rate import (
    CAUGHT,
    CORPUS,
    CORRECT_SILENCE,
    FALSE_POSITIVE,
    JUDGEMENT_MISS,
    RETRIEVAL_MISS,
    Outcome,
    already_done,
    cases,
    classify,
    load,
    report,
    summarise,
)


class _Finding:
    def __init__(self, domain="access_control", severity="medium", contradiction=True):
        self.domain = domain
        self.severity = severity
        self.contradiction = contradiction


def positive(**over) -> dict:
    return {"id": "SC01", "must_flag": True, "technique": "t", "domain": "d", **over}


def control(**over) -> dict:
    return {"id": "SC06", "must_flag": False, "technique": "t", "domain": "d", **over}


# --- The classification, which is the whole point of the harness ------------------------------


def test_a_flagged_contradiction_is_caught():
    assert classify(positive(), _Finding(), retrieved=True) == CAUGHT


def test_silence_on_a_retrieved_passage_is_the_model():
    """The passage was in the prompt and the model reported nothing. A prompt problem."""
    assert classify(positive(), None, retrieved=True) == JUDGEMENT_MISS


def test_silence_on_a_passage_that_never_arrived_is_retrieval():
    """No prompt change touches this one, and reporting it as a model miss would hide that."""
    assert classify(positive(), None, retrieved=False) == RETRIEVAL_MISS


def test_a_finding_on_the_control_case_is_a_false_positive():
    """A harness that only counted misses would reward a model that flags everything."""
    assert classify(control(), _Finding(), retrieved=True) == FALSE_POSITIVE


def test_silence_on_the_control_case_is_correct():
    assert classify(control(), None, retrieved=True) == CORRECT_SILENCE


def test_a_caught_case_is_caught_however_it_was_retrieved():
    """Retrieval is only asked about when the answer was silence — it explains a miss, not a
    catch. A finding raised without the planted passage is a different question, and one this
    harness deliberately does not confuse itself with."""
    assert classify(positive(), _Finding(), retrieved=False) == CAUGHT


# --- The rates that get published --------------------------------------------------------------


def test_the_rate_counts_only_the_cases_that_should_flag():
    """The control case is measured and reported, and it is not part of the miss rate."""
    outcomes = [
        Outcome("SC01", 1, CAUGHT, retrieved=True),
        Outcome("SC02", 1, JUDGEMENT_MISS, retrieved=True),
        Outcome("SC06", 1, CORRECT_SILENCE, retrieved=True),
    ]
    summary = summarise(outcomes)

    assert summary["attempts"] == 2
    assert summary["caught"] == 1
    assert summary["miss_rate"] == 50.0
    assert summary["control_runs"] == 1


def test_the_two_kinds_of_miss_are_reported_apart():
    outcomes = [
        Outcome("SC01", 1, JUDGEMENT_MISS, retrieved=True),
        Outcome("SC02", 1, RETRIEVAL_MISS, retrieved=False),
    ]
    summary = summarise(outcomes)

    assert summary["judgement_misses"] == 1
    assert summary["retrieval_misses"] == 1
    assert "no repair in common" in report(summary)


def test_a_case_that_failed_every_run_is_unmeasured_rather_than_missed():
    """An unreachable API is not a model that missed something, and the repairs differ."""
    summary = summarise([Outcome("SC01", 1, None, error="429 quota exceeded")])

    assert summary["attempts"] == 0
    assert summary["miss_rate"] is None
    assert summary["cases"][0]["errors"] == 1
    assert "Not yet measured" in report(summary)


def test_the_report_names_the_injection_rate_it_sits_beside():
    assert "injection-corpus detection rate" in report(summarise([]))


# --- Resume, because the quota is what will stop this -------------------------------------------


def test_a_completed_run_is_not_spent_again(tmp_path):
    out = tmp_path / "m.jsonl"
    out.write_text(Outcome("SC01", 1, CAUGHT).as_json() + "\n")

    assert already_done(out) == {("SC01", 1)}


def test_a_failed_run_is_retried(tmp_path):
    """The most likely error is the daily quota, and a quota error is not an answer."""
    out = tmp_path / "m.jsonl"
    out.write_text(Outcome("SC01", 1, None, error="429").as_json() + "\n")

    assert already_done(out) == set()


def test_an_unreadable_line_does_not_lose_the_file(tmp_path):
    out = tmp_path / "m.jsonl"
    out.write_text("{not json\n" + Outcome("SC02", 2, CAUGHT).as_json() + "\n")

    assert [o.case_id for o in load(out)] == ["SC02"]


# --- The corpus itself ---------------------------------------------------------------------------


def test_the_corpus_holds_a_control_case():
    """Without one, the harness measures sensitivity and calls it accuracy."""
    assert any(not case["must_flag"] for case in cases())


def test_every_case_carries_its_own_contradicting_passage():
    for case in cases():
        assert case["claim"].strip()
        assert case["passage"].strip()
        assert case["technique"].strip()


def test_every_positive_case_says_what_a_miss_would_cost():
    """A case that cannot say why it matters is a case that should not be in the corpus."""
    for case in cases():
        if case["must_flag"]:
            assert case["failure_mode_if_missed"].strip()


def test_the_techniques_are_distinct():
    """Six runs of one technique is one measurement repeated, not a corpus."""
    techniques = [case["technique"] for case in cases()]
    assert len(set(techniques)) >= 5


def test_the_corpus_domains_are_rubric_domains():
    from shared.domain import RUBRIC_DOMAINS

    assert all(case["domain"] in RUBRIC_DOMAINS for case in cases())


def test_the_corpus_is_not_part_of_the_review(tmp_path):
    """The calibration must not be able to move because the measurement grew.

    These claims are DataDynamo's and they are deliberately not in its answers or its evidence.
    A case added here changes the miss rate and nothing else; a case added to the pack proper
    would change the Trust Score, the band and the demo with it.
    """
    from scenarios.seed import load_vendor

    pack = load_vendor("datadynamo")
    answered = set(pack["answers"].get("answers") or {})
    assert not answered & {case["id"] for case in cases()}
    assert "subtle" not in json.dumps(pack.get("expected", {})).lower()


def test_the_corpus_lives_in_the_vendor_pack_it_belongs_to():
    assert CORPUS.parent.name == "datadynamo"
    assert CORPUS.is_file()


@pytest.mark.skip(
    reason=(
        "spends eighteen live cross-examination calls against a twenty-a-day cap. This is the "
        "measurement itself; run `python -m scripts.miss_rate` the day the quota lifts."
    )
)
def test_the_measured_miss_rate_is_published():
    from scripts.miss_rate import DEFAULT_OUT, measure

    summary = summarise(measure(3, DEFAULT_OUT))
    assert summary["miss_rate"] is not None
    assert summary["false_positives"] == 0
