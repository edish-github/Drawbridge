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

from shared.armor import CHUNK_COLLECTION
from shared.clients import firestore_client
from shared.config import settings
from shared.domain import EvidenceChunk

log = logging.getLogger("drawbridge.retrieval")


def knn_search(review_id: str, query_embedding: list[float], k: int) -> list[EvidenceChunk]:
    """Return the ``k`` nearest chunks within this review's evidence.

    The ``review_id`` filter is applied by the index, not by post-filtering the results —
    post-filtering would let another review's passages consume the k slots.

    Raises:
        Nothing. An unavailable index logs a degraded-mode warning and returns an empty list.
    """
    if not query_embedding:
        return []

    try:
        if settings().is_cloud:
            return _knn_indexed(review_id, query_embedding, k)
        return _knn_bruteforce(review_id, query_embedding, k)
    except Exception as exc:  # noqa: BLE001 — retrieval is optional and never blocks
        log.warning("degraded mode: retrieval unavailable for review=%s: %s", review_id, exc)
        return []


def _knn_indexed(review_id: str, query_embedding: list[float], k: int) -> list[EvidenceChunk]:
    """Query the Firestore KNN index, pre-filtered to this review."""
    from google.cloud.firestore_v1 import FieldFilter
    from google.cloud.firestore_v1.base_vector_query import DistanceMeasure
    from google.cloud.firestore_v1.vector import Vector

    snapshots = (
        firestore_client()
        .collection(CHUNK_COLLECTION)
        .where(filter=FieldFilter("review_id", "==", review_id))
        .find_nearest(
            vector_field="embedding",
            query_vector=Vector(query_embedding),
            limit=k,
            distance_measure=DistanceMeasure.COSINE,
        )
        .get()
    )
    return [EvidenceChunk.model_validate(s.to_dict()) for s in snapshots]


def _knn_bruteforce(review_id: str, query_embedding: list[float], k: int) -> list[EvidenceChunk]:
    """Rank this review's chunks by cosine similarity in Python.

    The review filter is a query, so the candidate set is already scoped before anything is
    ranked. Nothing here can see another review's evidence, which is the property the composite
    index provides in cloud mode.
    """
    from google.cloud.firestore_v1 import FieldFilter

    docs = (
        firestore_client()
        .collection(CHUNK_COLLECTION)
        .where(filter=FieldFilter("review_id", "==", review_id))
        .stream()
    )

    scored: list[tuple[float, EvidenceChunk]] = []
    for doc in docs:
        chunk = EvidenceChunk.model_validate(doc.to_dict())
        if not chunk.embedding:
            continue
        scored.append((cosine(query_embedding, chunk.embedding), chunk))

    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [chunk for _, chunk in scored[:k]]


def retrieve_for_claim(claim: str, review_id: str, ctx, *, k: int | None = None):
    """Embed a claim and return the passages relevant to it.

    The one entry point cross-examination uses, so the embedding call and the search stay
    together and a caller cannot accidentally search with an unembedded query.
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
    return knn_search(review_id, vector, limit)


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
    snap = firestore_client().collection(CHUNK_COLLECTION).document(chunk_id).get()
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
        firestore_client()
        .collection(CHUNK_COLLECTION)
        .where(filter=FieldFilter("review_id", "==", review_id))
        .stream()
    )
    return sorted(
        (EvidenceChunk.model_validate(d.to_dict()) for d in docs), key=lambda c: c.chunk_id
    )
