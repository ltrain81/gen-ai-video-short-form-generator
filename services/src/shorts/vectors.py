"""S3 Vectors: one index per tenant, 512-d cosine (Marengo Embed 3.0)."""
from __future__ import annotations

import re
from functools import lru_cache
from typing import Iterable

import boto3

from .config import get_config

DIMENSION = 512
NON_FILTERABLE = ["text"]


@lru_cache(maxsize=1)
def client():
    return boto3.client("s3vectors")


def index_name(tenant_id: str) -> str:
    name = "t-" + re.sub(r"[^a-z0-9-]", "-", tenant_id.lower())
    return name[:63]


def ensure_index(tenant_id: str) -> str:
    name = index_name(tenant_id)
    bucket = get_config().vector_bucket
    try:
        client().get_index(vectorBucketName=bucket, indexName=name)
    except client().exceptions.NotFoundException:
        try:
            client().create_index(
                vectorBucketName=bucket, indexName=name, dataType="float32", dimension=DIMENSION,
                distanceMetric="cosine",
                metadataConfiguration={"nonFilterableMetadataKeys": NON_FILTERABLE},
            )
        except client().exceptions.ConflictException:
            pass
    return name


def put(tenant_id: str, vectors: Iterable[dict]) -> int:
    """vectors: [{"key": str, "embedding": [float], "metadata": {...}}]"""
    name = ensure_index(tenant_id)
    batch, count = [], 0
    for v in vectors:
        batch.append({"key": v["key"], "data": {"float32": [float(x) for x in v["embedding"]]},
                      "metadata": v.get("metadata", {})})
        if len(batch) == 500:
            client().put_vectors(vectorBucketName=get_config().vector_bucket, indexName=name, vectors=batch)
            count += len(batch)
            batch = []
    if batch:
        client().put_vectors(vectorBucketName=get_config().vector_bucket, indexName=name, vectors=batch)
        count += len(batch)
    return count


def query(tenant_id: str, embedding: list[float], top_k: int = 50, filter: dict | None = None) -> list[dict]:
    args = {
        "vectorBucketName": get_config().vector_bucket,
        "indexName": index_name(tenant_id),
        "queryVector": {"float32": [float(x) for x in embedding]},
        "topK": min(top_k, 100),
        "returnMetadata": True,
        "returnDistance": True,
    }
    if filter:
        args["filter"] = filter
    try:
        return client().query_vectors(**args).get("vectors", [])
    except client().exceptions.NotFoundException:
        return []


def delete_keys(tenant_id: str, keys: list[str]) -> None:
    for i in range(0, len(keys), 500):
        client().delete_vectors(vectorBucketName=get_config().vector_bucket,
                                indexName=index_name(tenant_id), keys=keys[i:i + 500])
