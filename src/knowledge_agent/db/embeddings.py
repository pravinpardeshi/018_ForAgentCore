"""Bedrock embedding client. MUST match the ingestion pipeline's model + dims."""

from __future__ import annotations

import json
import logging
from functools import lru_cache

import boto3

log = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _bedrock_client(region: str):
    return boto3.client("bedrock-runtime", region_name=region)


def embed_query(text: str, model_id: str, region: str, expected_dims: int) -> list[float]:
    """Embed a single query string via Amazon Bedrock. Raises on dim mismatch."""
    text = (text or "").strip()
    if not text:
        raise ValueError("embed_query: text must be non-empty")

    client = _bedrock_client(region)

    if "titan-embed" in model_id:
        body = json.dumps({"inputText": text})
    elif "cohere.embed" in model_id:
        body = json.dumps({"texts": [text], "input_type": "search_query"})
    else:  # best-effort generic path
        body = json.dumps({"inputText": text})

    resp = client.invoke_model(modelId=model_id, body=body.encode("utf-8"))
    payload = json.loads(resp["body"].read())

    if "embedding" in payload:
        vector = payload["embedding"]
    elif "embeddings" in payload:
        vector = payload["embeddings"][0]
    elif "embeddingsByType" in payload:  # Cohere v3 style
        vector = payload["embeddingsByType"]["float"][0]
    else:
        raise RuntimeError(f"Unrecognised embedding response keys: {list(payload.keys())}")

    if len(vector) != expected_dims:
        raise RuntimeError(
            f"Embedding dim mismatch: got {len(vector)}, expected {expected_dims}. "
            f"Query model {model_id} must match the ingestion pipeline model."
        )
    return vector
