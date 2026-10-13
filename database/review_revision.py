"""Stable optimistic revision tokens for motorcycle detail review state."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping


_REVIEW_REVISION_FIELDS = (
    "human_outcome",
    "reviewed_by",
    "reviewed_at",
    "reviewer_notes",
    "updated_at",
)


def motorcycle_detail_review_revision(row: Mapping[str, Any]) -> str:
    """Hash the persisted state fields that a concurrent review can replace."""
    payload = {key: row.get(key) for key in _REVIEW_REVISION_FIELDS}
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
