"""Content hashing for versioned records."""

import hashlib
import json

from pydantic import BaseModel


def canonical_json(model: BaseModel) -> str:
    """Key-sorted, compact JSON. JSONB does not preserve object key order, so hashes must not
    depend on it: content loaded back from PostgreSQL must hash identically."""
    return json.dumps(model.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))


def content_sha256(model: BaseModel) -> str:
    return hashlib.sha256(canonical_json(model).encode("utf-8")).hexdigest()
