"""Canonical taxonomy hash for staleness audit (AC-15).

Each ingested ``Paper`` carries a ``taxonomy_hash`` property: the sha256
of the canonically-serialized taxonomy used by the extractor for that
batch. A query against the current hash lists papers ingested under a
stale taxonomy — candidates for re-ingestion via the AC-13 purge path.

"Canonical" here means:

- keys are sorted alphabetically inside every node
- ``children: None`` and missing ``children`` collapse to ``children: []``
- only schema-meaningful fields are included; whitespace, comments, and
  YAML formatting choices are erased before hashing

The intent is that cosmetic file edits (re-indent, add comment) do NOT
produce a new hash; semantic changes (added node, renamed node) do.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any


def _canonicalize_node(node: dict[str, Any]) -> dict[str, Any]:
    """Strip None children and recursively normalize child order/shape."""
    cleaned: dict[str, Any] = {}
    for key in sorted(node.keys()):
        if key == "children":
            children = node[key] or []
            cleaned["children"] = [_canonicalize_node(c) for c in children]
        else:
            cleaned[key] = node[key]
    # Ensure children is present even when omitted in source — so omitted
    # vs. empty vs. None all collapse to the same canonical form.
    cleaned.setdefault("children", [])
    return cleaned


def canonical_taxonomy_hash(parsed: list[dict[str, Any]]) -> str:
    """Compute the canonical sha256 hex digest of a parsed taxonomy.

    Args:
        parsed: Output of ``parse_taxonomy``. Empty list is valid.

    Returns:
        64-character sha256 hex digest.
    """
    canonical = [_canonicalize_node(root) for root in parsed]
    serialized = json.dumps(canonical, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()
