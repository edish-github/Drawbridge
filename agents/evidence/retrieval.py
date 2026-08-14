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

from shared.domain import EvidenceChunk


def knn_search(review_id: str, query_embedding: list[float], k: int) -> list[EvidenceChunk]:
    """Return the ``k`` nearest chunks within this review's evidence.

    The ``review_id`` filter is applied by the index, not by post-filtering the results —
    post-filtering would let another review's passages consume the k slots.
    """
    raise NotImplementedError


def chunk_document(
    text: str, doc_ref: str, review_id: str, chunk_tokens: int
) -> list[EvidenceChunk]:
    """Split a stamped document into overlapping chunks carrying their page numbers.

    The page number travels with the chunk because the binder prints retrieval provenance
    beside every cited passage.
    """
    raise NotImplementedError


def resolve_chunk(chunk_id: str) -> EvidenceChunk | None:
    """Resolve a ``Finding.evidence_ref`` back to the chunk it cited.

    Returns ``None`` when the chunk no longer exists, which the binder renders as a broken
    provenance link rather than omitting the finding.
    """
    raise NotImplementedError
