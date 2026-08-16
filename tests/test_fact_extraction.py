"""Extraction reads the whole document, one query per fact.

It used to read the first 24,000 characters. On the synthetic packs — a few thousand characters
each — that was indistinguishable from reading everything, so no test in this repository ever
exercised the cap. On a real 63-page audit it discarded 71% of the document, and the reason the
loss was invisible is that a truncated prompt returns a confident answer about the part it was
given rather than an error about the part it was not.

These tests pin the three things that make the replacement better rather than merely different:
each fact retrieves its own passages, a fact is never answered from another document, and the
degradation is a ladder with a log line at every step rather than a silent fallback.
"""

from __future__ import annotations

import pytest

from agents.evidence.extractors import (
    FACT_QUERIES,
    FACT_TOP_K,
    MAX_DOCUMENT_CHARS,
    retrieve_facts,
)
from shared.armor import _short
from shared.context import AgentContext
from shared.domain import EvidenceChunk
from tests.conftest import emulator_required


def ctx(review_id: str) -> AgentContext:
    return AgentContext(review_id=review_id, agent="evidence", trace_id="t")


def chunk(review_id: str, doc_ref: str, index: int, text: str) -> EvidenceChunk:
    """Mirrors ``armor.index_chunks``: the id carries a hash of the ref, not the ref, because a
    document reference contains slashes and a Firestore document id may not."""
    from shared.armor import _short

    return EvidenceChunk(
        chunk_id=f"{review_id}:{_short(doc_ref)}:{index:03d}",
        review_id=review_id,
        doc_ref=doc_ref,
        page=1,
        text=text,
        embedding=[float(index), 1.0, 0.0],
    )


def write(db, *chunks: EvidenceChunk) -> None:
    from shared.armor import CHUNK_COLLECTION

    for c in chunks:
        db.collection(CHUNK_COLLECTION).document(c.chunk_id).set(c.model_dump(mode="json"))


@pytest.fixture
def retrieval_calls(monkeypatch):
    """Record every query retrieval was asked, without embedding anything."""
    from agents.evidence import extractors

    calls: list[tuple[str, str | None, int]] = []
    store: dict[str, list[EvidenceChunk]] = {}

    def fake(query, review_id, _ctx, *, k=None, doc_ref=None):
        calls.append((query, doc_ref, k))
        return store.get(query, [])[:k]

    monkeypatch.setattr(
        "agents.evidence.retrieval.retrieve_for_claim", fake, raising=True
    )
    monkeypatch.setattr(extractors, "read_clean_document", lambda ref: ("PREFIX BODY", "d.txt"))
    return calls, store


# --- One query per fact, and that is the whole point ---------------------------------------------


def test_every_fact_gets_its_own_query(retrieval_calls):
    """A single merged query returns what is moderately relevant to everything and decisively
    relevant to nothing, which is a worse prefix rather than a better search."""
    calls, store = retrieval_calls
    store.update({q: [chunk("rv", "d", 1, "x")] for q in FACT_QUERIES.values()})

    retrieve_facts("d", "rv", ctx("rv"))

    assert [q for q, _, _ in calls] == list(FACT_QUERIES.values())
    assert len(set(q for q, _, _ in calls)) == len(FACT_QUERIES)


def test_the_queries_are_the_words_a_document_would_use():
    """The embedding is of the query text, so a query has to read like the document rather than
    like the schema. A report says "the audit covered the period"; nothing says
    "report_period_end"."""
    for field, query in FACT_QUERIES.items():
        assert query != field
        assert "_" not in query, field
        assert len(query.split()) >= 8, field


def test_the_exceptions_query_names_appendices():
    """The one place a real report puts what its body sections do not carry, and the one place
    a prefix never reaches."""
    assert "appendix" in FACT_QUERIES["exceptions"]


def test_every_query_is_scoped_to_the_one_document(retrieval_calls):
    """"Who is the auditor" asked across a review answers from whichever document names one
    most confidently. Attributing the SOC 2's auditor to the ISO certificate is a wrong fact,
    which is worse than a missing one."""
    calls, store = retrieval_calls
    store.update({q: [chunk("rv", "soc2", 1, "x")] for q in FACT_QUERIES.values()})

    retrieve_facts("soc2", "rv", ctx("rv"))

    assert {doc for _, doc, _ in calls} == {"soc2"}
    assert {k for _, _, k in calls} == {FACT_TOP_K}


# --- What reaches the prompt ---------------------------------------------------------------------


def test_a_passage_answering_two_queries_is_read_once(retrieval_calls):
    _, store = retrieval_calls
    shared_chunk = chunk("rv", "d", 7, "the period and the exceptions, together")
    store[FACT_QUERIES["period"]] = [shared_chunk]
    store[FACT_QUERIES["exceptions"]] = [shared_chunk]

    body, count = retrieve_facts("d", "rv", ctx("rv"))

    assert count == 1
    assert body.count("the period and the exceptions") == 1


def test_passages_reach_the_prompt_in_document_order(retrieval_calls):
    """A model reading passages out of order reconstructs a chronology the document does not
    have — which matters most for exactly the field this is extracting: dates."""
    _, store = retrieval_calls
    store[FACT_QUERIES["period"]] = [chunk("rv", "d", 9, "LATER")]
    store[FACT_QUERIES["auditor"]] = [chunk("rv", "d", 2, "EARLIER")]

    body, _ = retrieve_facts("d", "rv", ctx("rv"))

    assert body.index("EARLIER") < body.index("LATER")


def test_the_prompt_carries_the_chunk_id_of_every_passage(retrieval_calls):
    """Extraction is upstream of every deterministic check, so where a fact came from has to be
    recoverable from the prompt that produced it."""
    _, store = retrieval_calls
    store[FACT_QUERIES["expiry"]] = [chunk("rv", "d", 3, "Valid until 14 March 2025")]

    body, _ = retrieve_facts("d", "rv", ctx("rv"))

    assert f"[rv:{_short('d')}:003]" in body


# --- The degradation ladder, and every rung says so -----------------------------------------------


def test_a_failed_query_falls_back_to_the_whole_document_not_to_a_prefix(
    retrieval_calls, monkeypatch, caplog
):
    """A failed *query* embedding is not a missing document. Every chunk of it is the whole
    document and is still uncapped, so a prefix here would discard text that is right there."""
    import logging

    _, _store = retrieval_calls
    everything = [chunk("rv", "d", i, f"part {i}") for i in range(1, 4)]
    monkeypatch.setattr(
        "agents.evidence.retrieval.chunks_for_document",
        lambda review_id, doc_ref: everything,
        raising=True,
    )

    with caplog.at_level(logging.WARNING):
        body, count = retrieve_facts("d", "rv", ctx("rv"))

    assert count == 3
    assert "part 3" in body
    assert "PREFIX BODY" not in body
    assert "no query matched" in caplog.text


def test_a_document_with_no_chunks_at_all_falls_back_to_a_bounded_prefix(
    retrieval_calls, monkeypatch, caplog
):
    """A scanned PDF, or an embedding outage at ingress. Part of a document beats none of it,
    and this is the one case where a field past the cap reads as absent when it is not."""
    import logging

    monkeypatch.setattr(
        "agents.evidence.retrieval.chunks_for_document",
        lambda review_id, doc_ref: [],
        raising=True,
    )

    with caplog.at_level(logging.WARNING):
        body, count = retrieve_facts("d", "rv", ctx("rv"))

    assert count == 0
    assert body == "PREFIX BODY"
    assert "no chunks at all" in caplog.text
    assert "read as absent when they are not" in caplog.text


def test_the_cap_is_the_only_thing_left_that_truncates():
    """MAX_DOCUMENT_CHARS survives for one path per module and no others.

    An AST walk rather than a grep over the source, because the module docstrings discuss the
    cap at length and a text search cannot tell an explanation from a slice. This counts the
    places the code actually truncates something.
    """
    import ast
    import inspect

    from agents.evidence import extractors, subprocessors

    sites = []
    for module in (extractors, subprocessors):
        tree = ast.parse(inspect.getsource(module))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Subscript) or not isinstance(node.slice, ast.Slice):
                continue
            upper = node.slice.upper
            if isinstance(upper, ast.Name) and upper.id == "MAX_DOCUMENT_CHARS":
                sites.append((module.__name__, node.lineno))

    # One per module, and each is the never-indexed fallback: a document with no chunks at all.
    assert len(sites) == 2, f"{sites} — extraction should retrieve, not truncate"
    assert {name for name, _ in sites} == {
        "agents.evidence.extractors",
        "agents.evidence.subprocessors",
    }
    assert MAX_DOCUMENT_CHARS == 24_000


# --- Against the real chunk store -----------------------------------------------------------------


@emulator_required
def test_retrieval_never_crosses_a_document_boundary(review_id, db):
    """The property under test is the pre-filter, so it runs against the real store rather than
    against a stub that could only ever confirm the stub."""
    from agents.evidence.retrieval import knn_search

    write(
        db,
        chunk(review_id, "clean/soc2.txt", 1, "Kestrel Assurance Partners issued the opinion"),
        chunk(review_id, "clean/iso.txt", 1, "Certificate valid until 14 March 2025"),
    )

    got = knn_search(review_id, [1.0, 1.0, 0.0], 10, doc_ref="clean/iso.txt")

    assert {c.doc_ref for c in got} == {"clean/iso.txt"}


@emulator_required
def test_an_unscoped_search_still_sees_the_whole_review(review_id, db):
    """Cross-examination reconciles a claim against everything the vendor sent, so the scoping
    is a parameter rather than a new default."""
    from agents.evidence.retrieval import knn_search

    write(
        db,
        chunk(review_id, "clean/a.txt", 1, "alpha"),
        chunk(review_id, "clean/b.txt", 1, "beta"),
    )

    assert len({c.doc_ref for c in knn_search(review_id, [1.0, 1.0, 0.0], 10)}) == 2


@emulator_required
def test_chunks_for_document_returns_one_document_in_order(review_id, db):
    from agents.evidence.retrieval import chunks_for_document

    write(
        db,
        chunk(review_id, "clean/a.txt", 2, "second"),
        chunk(review_id, "clean/a.txt", 1, "first"),
        chunk(review_id, "clean/b.txt", 1, "other"),
    )

    got = chunks_for_document(review_id, "clean/a.txt")

    assert [c.text for c in got] == ["first", "second"]
