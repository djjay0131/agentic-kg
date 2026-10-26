"""Commit a human-approved review item through KGCS.

The review queue is a *decision* surface, not a second write path. This module
turns one approved ``ReviewItem`` candidate back into the same
``Candidate`` the nightly run produced and hands it to :func:`run_curation`
with the candidate marked human-approved. KGCS still validates it, still
plans the operation with its own ``CurationPlanner``, and ``PlanExecutor`` is
still the only object that mutates canonical state (ADR-0010).

What is deliberately narrow
---------------------------
Only an ``EntityCandidate`` can be committed today. A relation or attribute
assertion whose endpoint is still an alias needs entity resolution, which is
not wired; approving one would either be rejected by the planner or would
require this module to invent an identity — exactly the guessing the review
queue exists to avoid. The caller marks such items ``committable=False`` and
the API refuses them with the blocker instead.

A human approval is not a widened policy
----------------------------------------
``run_curation`` reads eligibility from the deterministic engine's own
decisions: the candidate must be an ``EntityCandidate`` that routed
``LLM_ASSESS``. An approval fills that gap; it cannot reach a candidate the
validator rejected or one the policy already resolved ``AUTO``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agentic_kg.migration.config import MigrationConfig
from agentic_kg.migration.curation._contracts import (
    AttributeAssertionCandidate,
    CurationOperationType,
    EntityCandidate,
    RelationCandidate,
    is_identity_id,
)
from agentic_kg.migration.curation.policy import STRUCTURED_IDENTITY_POLICY


class ReviewCommitError(RuntimeError):
    """An approved item could not be committed through KGCS."""


@dataclass(frozen=True)
class ReviewCommitResult:
    """What the KGCS commit path did for one approved candidate."""

    candidate_id: str
    committed: bool
    epoch: int | None
    identity_ids: tuple[str, ...]
    outcome: str | None
    message: str


def _commit_config() -> MigrationConfig:
    """The config used for a review commit.

    All three flags are forced on because this path is explicitly a human
    approval of an ``LLM_ASSESS`` candidate: it needs KGCS resolution to plan,
    and the adjudication stage is the seam that carries the approval into the
    planner. It never reads the process environment, so an operator's nightly
    ingest flags cannot silently decide whether an approval commits.
    """
    return MigrationConfig(
        use_kgis_ingestion=True,
        use_kgcs_resolution=True,
        use_kgcs_adjudication=True,
    )


def candidate_from_payload(payload: dict[str, Any]) -> Any:
    """Reconstruct the KGCS candidate union from the stored JSON payload."""
    from pydantic import TypeAdapter

    from agentic_kg.migration.curation._contracts import Candidate

    return TypeAdapter(Candidate).validate_python(payload)


def commit_candidate(
    candidate_payload: dict[str, Any],
    *,
    namespace: str,
    canonical_store: Any | None = None,
    config: MigrationConfig | None = None,
) -> ReviewCommitResult:
    """Commit one human-approved candidate through KGCS.

    ``canonical_store`` is an injection seam: tests pass ``MemoryGraphStore``.
    When it is ``None`` the production canonical store is built from the
    environment.
    """
    from agentic_kg.migration.curation.pipeline import run_curation

    candidate = candidate_from_payload(candidate_payload)
    if isinstance(candidate, EntityCandidate):
        pass
    elif isinstance(candidate, (RelationCandidate, AttributeAssertionCandidate)):
        refs = (
            (candidate.subject, candidate.object)
            if isinstance(candidate, RelationCandidate)
            else (candidate.subject,)
        )
        if not all(isinstance(ref, str) and is_identity_id(ref) for ref in refs):
            raise ReviewCommitError(
                "this assertion still has an alias endpoint; entity resolution "
                "is not wired, so KGCS cannot plan it"
            )
    else:
        raise ReviewCommitError(
            "this candidate kind has no committable operation: "
            f"{type(candidate).__name__}"
        )

    if canonical_store is None:
        from agentic_kg.config import get_config
        from agentic_kg.migration.neo4j import (
            SUPPORTED_OPERATIONS,
            canonical_store_from_config,
        )

        neo = get_config().neo4j
        canonical_store = canonical_store_from_config(
            _commit_config(),
            uri=neo.uri,
            auth=(neo.username, neo.password),
            namespace=namespace,
            database=neo.database,
        )
        supported = SUPPORTED_OPERATIONS
    else:
        # An injected store declares its own capabilities through the
        # executor's default set; claiming the Neo4j set for it could turn an
        # unsupported operation into a failed apply.
        supported = None

    result = run_curation(
        [candidate],
        config=config or _commit_config(),
        store=canonical_store,
        confidence_policy=STRUCTURED_IDENTITY_POLICY,
        supported_operations=supported,
        executed_by="agentic_kg.review_queue",
        approved_candidate_ids={candidate.candidate_id},
    )

    outcome = (
        result.execution.outcome.value if result.execution is not None else None
    )
    if not result.committed:
        raise ReviewCommitError(
            "KGCS did not commit the approved candidate "
            f"{candidate.candidate_id}: outcome={outcome!r}"
        )

    identity_ids: list[str] = []
    if result.plan is not None:
        for operation in result.plan.operations:
            if operation.type is not CurationOperationType.CREATE_IDENTITY:
                continue
            payload = operation.payload
            identity_id = (
                payload.get("identity_id")
                if hasattr(payload, "get")
                else getattr(payload, "identity_id", None)
            )
            if identity_id:
                identity_ids.append(str(identity_id))

    return ReviewCommitResult(
        candidate_id=candidate.candidate_id,
        committed=True,
        epoch=result.execution.new_epoch if result.execution is not None else None,
        identity_ids=tuple(identity_ids),
        outcome=outcome,
        message="committed through KGCS",
    )


__all__ = [
    "ReviewCommitError",
    "ReviewCommitResult",
    "candidate_from_payload",
    "commit_candidate",
]
