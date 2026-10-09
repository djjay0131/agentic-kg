"""The **shared** upstream conformance suite, run against the Neo4j adapter.

``GraphMutationStoreContract`` is imported from
``kg_contracts.testing.contract`` and subclassed unchanged: not one assertion is
restated locally. That is deliberate and is §9.0 obligation 3 — "any criterion
naming an upstream rule must exercise the upstream code rather than a local
restatement of it". A re-implemented copy of these seven tests would stay green
through an upstream change to the rule it claims to enforce.

The class name must start with ``Test`` for pytest to collect it, and it must
not define ``__init__`` for the same reason. The suite calls ``make_store()``
once per test method and expects a pristine store each time (see the conftest
note on namespace-per-store).

This satisfies mapping-spec **AC-2**, first clause.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest
from kg_contracts.testing.contract import GraphMutationStoreContract

pytestmark = pytest.mark.integration


class TestNeo4jGraphMutationStoreContract(GraphMutationStoreContract):
    """``Neo4jCanonicalGraphStore`` against the shared suite, unmodified."""

    @pytest.fixture(autouse=True)
    def _bind_factory(self, make_canonical_store: Callable[..., object]) -> None:
        # The suite's make_store() takes no arguments, so the fixture is bound
        # onto the instance instead of being a parameter.
        self._make_canonical_store = make_canonical_store

    def make_store(self):  # type: ignore[no-untyped-def]
        return self._make_canonical_store()
