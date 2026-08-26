"""Pass two: retrieval. No model call beyond the embedding of the query.

The document was chunked, embedded and indexed at ingress, after the clean-stamp. For each
questionnaire claim, the top-k relevant passages are pulled from the Firestore KNN index,
pre-filtered on ``review_id`` so retrieval can never surface one review's evidence inside
another.

Why this is load-bearing rather than decorative: a Tier-1 SOC 2 runs sixty to a hundred pages,
and the hero finding depends on locating one exception note inside it. Retrieval turns that
from luck into a query, makes ``Finding.evidence_ref`` an actual pointer rather than a
best-effort quotation, and cuts the most expensive call in the system at the same time — six
passages instead of a whole report is what protects the per-review cost figure while
improving the finding.

**Two backends behind one function.** In cloud mode the query goes to the Firestore KNN index.
In local mode there is no vector index, so similarity is computed in Python over the chunks
belonging to this review. That is not a downgrade worth hiding: a single review holds tens of
chunks, cosine over tens of vectors is microseconds, and the *result* is the same ranking the
index would produce. What differs is that the review filter is applied by a query rather than
by the index, which is why the local path filters before it ranks rather than after — a
post-filter would let another review's passages consume the k slots, which is the exact failure
the composite index exists to prevent.

Verified against ``google-cloud-firestore`` 2.28.1: ``CollectionReference.find_nearest(
vector_field, query_vector, limit, distance_measure, distance_result_field=None,
distance_threshold=None)`` with ``Vector`` and ``DistanceMeasure`` from
``google.cloud.firestore_v1``. TODO(verify): whether the review-scoped pre-filter composes as
``collection.where(...).find_nearest(...)`` on a composite vector index, or requires the
filter fields declared in the index definition in a specific order.

Failure semantics: an unavailable embedding call or KNN index logs a degraded-mode warning
and returns an empty list, and the caller falls back to whole-document context. Retrieval is
an optional control and is never on the critical path. A query returning zero chunks is a
gap, not an error — the cross-examination prompt is required to label it as one.
"""

from __future__ import annotations

import logging
import math

from shared import tenancy as tenant
from shared.armor import CHUNK_COLLECTION
from shared.config import settings
from shared.domain import EvidenceChunk

log = logging.getLogger("drawbridge.retrieval")


def knn_search(
    review_id: str, query_embedding: list[float], k: int, *, doc_ref: str | None = None
) -> list[EvidenceChunk]:
    """Return the ``k`` nearest chunks within this review's evidence.

    The ``review_id`` filter is applied by the index, not by post-filtering the results —
    post-filtering would let another review's passages consume the k slots.

    Args:
        doc_ref: narrow the search to one document. Cross-examination wants the whole review,
            because a claim is reconciled against everything the vendor sent. Extraction wants
            one document, because "who is the auditor" asked across a review would answer from
            whichever document names an auditor most confidently, and attributing the SOC 2's
            auditor to the ISO certificate is a wrong fact rather than a missing one.

    Raises:
        Nothing. An unavailable index logs a degraded-mode warning and returns an empty list.
    """
    if not query_embedding:
        return []

    try:
        if settings().is_cloud:
            return _knn_indexed(review_id, query_embedding, k, doc_ref=doc_ref)
        return _knn_bruteforce(review_id, query_embedding, k, doc_ref=doc_ref)
    except Exception as exc:  # noqa: BLE001 — retrieval is optional and never blocks
        log.warning("degraded mode: retrieval unavailable for review=%s: %s", review_id, exc)
        return []


def _knn_indexed(
    review_id: str, query_embedding: list[float], k: int, *, doc_ref: str | None = None
) -> list[EvidenceChunk]:
    """Query the Firestore KNN index, pre-filtered to this review and optionally one document."""
    from google.cloud.firestore_v1 import FieldFilter
    from google.cloud.firestore_v1.base_vector_query import DistanceMeasure
    from google.cloud.firestore_v1.vector import Vector

    query = (
        tenant.collection(CHUNK_COLLECTION)
        .where(filter=FieldFilter("review_id", "==", review_id))
    )
    if doc_ref:
        query = query.where(filter=FieldFilter("doc_ref", "==", doc_ref))

    snapshots = query.find_nearest(
        vector_field="embedding",
        query_vector=Vector(query_embedding),
        limit=k,
        distance_measure=DistanceMeasure.COSINE,
    ).get()
    return [EvidenceChunk.model_validate(s.to_dict()) for s in snapshots]


def _knn_bruteforce(
    review_id: str, query_embedding: list[float], k: int, *, doc_ref: str | None = None
) -> list[EvidenceChunk]:
    """Rank this review's chunks by cosine similarity in Python.

    The review filter is a query, so the candidate set is already scoped before anything is
    ranked. Nothing here can see another review's evidence, which is the property the composite
    index provides in cloud mode.
    """
    from google.cloud.firestore_v1 import FieldFilter

    query = (
        tenant.collection(CHUNK_COLLECTION)
        .where(filter=FieldFilter("review_id", "==", review_id))
    )
    if doc_ref:
        query = query.where(filter=FieldFilter("doc_ref", "==", doc_ref))

    scored: list[tuple[float, EvidenceChunk]] = []
    for doc in query.stream():
        chunk = EvidenceChunk.model_validate(doc.to_dict())
        if not chunk.embedding:
            continue
        scored.append((cosine(query_embedding, chunk.embedding), chunk))

    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [chunk for _, chunk in scored[:k]]


def retrieve_for_claim(
    claim: str, review_id: str, ctx, *, k: int | None = None, doc_ref: str | None = None
):
    """Embed a query and return the passages relevant to it.

    The one entry point cross-examination and extraction both use, so the embedding call and
    the search stay together and a caller cannot accidentally search with an unembedded query.
    """
    from shared.routing import embed

    limit = k if k is not None else settings().vector_top_k
    vector = embed(claim, ctx)
    if not vector:
        log.warning(
            "degraded mode: no query embedding for review=%s, falling back to whole-document "
            "context",
            review_id,
        )
        return []
    return knn_search(review_id, vector, limit, doc_ref=doc_ref)


def chunks_for_document(review_id: str, doc_ref: str) -> list[EvidenceChunk]:
    """Return every chunk of one document, in order.

    The fallback when a document was never indexed — a scanned PDF, an embedding outage — so
    extraction still has something to read rather than silently returning nothing for every
    fact. Ordered by chunk id, which is the order the document was written in.
    """
    return [chunk for chunk in all_chunks(review_id) if chunk.doc_ref == doc_ref]


def cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity between two vectors. Returns 0.0 for a zero vector."""
    if len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return dot / norm if norm else 0.0


def chunk_document(
    text: str, doc_ref: str, review_id: str, chunk_tokens: int
) -> list[EvidenceChunk]:
    """Split a stamped document into chunks carrying their page numbers.

    The page number travels with the chunk because the binder prints retrieval provenance
    beside every cited passage. Embeddings are attached by ``shared.armor.index_chunks``, which
    is the only place a chunk is written — this function is the shape, not the write.
    """
    from shared.armor import chunk_text

    return [
        EvidenceChunk(
            chunk_id=f"{review_id}:{doc_ref}:{index:03d}",
            review_id=review_id,
            doc_ref=doc_ref,
            page=1,
            text=body,
            embedding=[],
        )
        for index, body in enumerate(chunk_text(text, chunk_tokens))
    ]


def resolve_chunk(chunk_id: str) -> EvidenceChunk | None:
    """Resolve a ``Finding.evidence_ref`` back to the chunk it cited.

    Returns ``None`` when the chunk no longer exists, which the binder renders as a broken
    provenance link rather than omitting the finding.
    """
    snap = tenant.collection(CHUNK_COLLECTION).document(chunk_id).get()
    if not snap.exists:
        return None
    return EvidenceChunk.model_validate(snap.to_dict())


def all_chunks(review_id: str) -> list[EvidenceChunk]:
    """Return every chunk for a review, ordered by chunk id.

    The degraded-mode fallback: when retrieval is unavailable, cross-examination runs against
    whole-document context and labels everything it finds as a gap.
    """
    from google.cloud.firestore_v1 import FieldFilter

    docs = (
        tenant.collection(CHUNK_COLLECTION)
        .where(filter=FieldFilter("review_id", "==", review_id))
        .stream()
    )
    return sorted(
        (EvidenceChunk.model_validate(d.to_dict()) for d in docs), key=lambda c: c.chunk_id
    )
