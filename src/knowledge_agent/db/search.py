"""Hybrid retrieval over the closed-incidents table.

Table contract (see sql/schema.sql):
  snow_closed_incidents(
    incident_number PK, short_description, description, resolution_notes,
    root_cause, category, subcategory, assignment_group, priority,
    state, opened_at, resolved_at, search_text, search_tsv,
    embedding vector(<dims>), metadata jsonb
  )

Retrieval = vector cosine similarity blended with Postgres full-text rank.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from pgvector.psycopg import register_vector

from knowledge_agent.config import Settings
from knowledge_agent.db.connection import get_pool
from knowledge_agent.db.embeddings import embed_query

log = logging.getLogger(__name__)

_REGISTERED = False


def _ensure_vector(conn) -> None:
    global _REGISTERED
    if not _REGISTERED:
        register_vector(conn)
        _REGISTERED = True


SEARCH_SQL = """
SELECT
  incident_number,
  short_description,
  LEFT(COALESCE(description, ''), 1200)      AS description_excerpt,
  LEFT(COALESCE(resolution_notes, ''), 1200) AS resolution_excerpt,
  category, subcategory, assignment_group, priority,
  resolved_at,
  1 - (embedding <=> %(qv)s::vector) AS semantic_score,
  COALESCE(ts_rank(search_tsv, plainto_tsquery('english', %(kw)s)), 0) AS keyword_score
FROM snow_closed_incidents
WHERE
  state = 'Closed'
  AND (%(category)s IS NULL OR category = %(category)s)
  AND (%(assignment_group)s IS NULL OR assignment_group = %(assignment_group)s)
  AND (
    embedding <=> %(qv)s::vector < 2.0  -- cosine distance always < 2; keeps HNSW index usable
    OR search_tsv @@ plainto_tsquery('english', %(kw)s)
  )
ORDER BY
  ((1 - %(kw_weight)s) * (1 - (embedding <=> %(qv)s::vector))
   + %(kw_weight)s * COALESCE(ts_rank(search_tsv, plainto_tsquery('english', %(kw)s)), 0) / 5.0
  ) DESC
LIMIT %(top_k)s;
"""

DETAIL_SQL = """
SELECT
  incident_number, short_description, description,
  category, subcategory, assignment_group,
  priority, state, opened_at, resolved_at,
  resolved_by, resolution_notes, root_cause, work_notes_summary,
  metadata
FROM snow_closed_incidents
WHERE incident_number = %(incident_number)s;
"""


def hybrid_search(
    query: str,
    settings: Settings,
    top_k: Optional[int] = None,
    category: Optional[str] = None,
    assignment_group: Optional[str] = None,
    min_similarity: Optional[float] = None,
) -> list[dict[str, Any]]:
    k = max(1, min(top_k or settings.default_top_k, settings.max_top_k))
    threshold = settings.min_similarity if min_similarity is None else min_similarity

    vector = embed_query(
        query,
        model_id=settings.embedding_model_id,
        region=settings.embedding_region,
        expected_dims=settings.embedding_dims,
    )

    pool = get_pool(settings)
    with pool.connection() as conn:
        _ensure_vector(conn)
        with conn.cursor() as cur:
            cur.execute(
                SEARCH_SQL,
                {
                    "qv": vector,
                    "kw": query[:1000],
                    "category": category,
                    "assignment_group": assignment_group,
                    "kw_weight": settings.keyword_weight,
                    "top_k": k,
                },
            )
            cols = [d[0] for d in cur.description]
            rows = [dict(zip(cols, r)) for r in cur.fetchall()]

    # Python-side threshold + combined score (keeps SQL simple & index-friendly)
    results = []
    for r in rows:
        sem = float(r.get("semantic_score") or 0.0)
        kw = float(r.get("keyword_score") or 0.0)
        combined = (1 - settings.keyword_weight) * sem + settings.keyword_weight * min(kw / 5.0, 1.0)
        r["combined_score"] = round(combined, 4)
        r["semantic_score"] = round(sem, 4)
        r["keyword_score"] = round(kw, 4)
        if r.get("resolved_at") is not None:
            r["resolved_at"] = str(r["resolved_at"])
        if sem >= threshold:
            results.append(r)
    return results


def get_incident(incident_number: str, settings: Settings) -> Optional[dict[str, Any]]:
    pool = get_pool(settings)
    with pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(DETAIL_SQL, {"incident_number": incident_number.strip().upper()})
            row = cur.fetchone()
            if not row:
                return None
            cols = [d[0] for d in cur.description]
            rec = dict(zip(cols, row))
    for k in ("opened_at", "resolved_at"):
        if rec.get(k) is not None:
            rec[k] = str(rec[k])
    return rec
