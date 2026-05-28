"""B3 alias deny-list loader.

Loads the structured YAML fixture into a ``frozenset[str]`` of lowercased
terms at import time. See ``b3_deny_list.yml`` for the governance contract.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, Union

import yaml

DEFAULT_DENY_LIST_PATH = Path(__file__).parent / "b3_deny_list.yml"


def load_deny_list(path: Union[str, Path]) -> frozenset[str]:
    """Parse a deny-list YAML file into a frozenset of lowercased terms.

    Args:
        path: Filesystem path to the YAML fixture.

    Returns:
        Frozenset of lowercased term strings.

    Raises:
        FileNotFoundError: If ``path`` does not exist.
        ValueError: If the YAML root is missing ``deny_list``, or any entry
            is missing the ``term`` field, or any term is not a string.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Deny-list fixture not found: {path}")

    raw = yaml.safe_load(path.read_text()) or {}
    if "deny_list" not in raw:
        raise ValueError(
            f"Deny-list fixture root must contain 'deny_list' key: {path}"
        )

    entries = raw["deny_list"] or []
    terms: list[str] = []
    for idx, entry in enumerate(entries):
        if not isinstance(entry, dict) or "term" not in entry:
            raise ValueError(
                f"Deny-list entry {idx} missing required 'term' field"
            )
        term = entry["term"]
        if not isinstance(term, str):
            raise ValueError(
                f"Deny-list entry {idx} 'term' must be a string, got "
                f"{type(term).__name__}"
            )
        terms.append(term.lower())

    return frozenset(terms)


# Module-level default loaded once at import time. Callers can override by
# passing a custom path to ``link_problems_to_concepts(... alias_deny_list=...)``.
DEFAULT_ALIAS_DENY_LIST: frozenset[str] = load_deny_list(DEFAULT_DENY_LIST_PATH)


def merged_deny_list(extras: Iterable[str] = ()) -> frozenset[str]:
    """Convenience: return the default deny-list plus extra terms (lowercased)."""
    return DEFAULT_ALIAS_DENY_LIST | {t.lower() for t in extras}
