"""Rollback: a compensating `CurationPlan`, applied through the same executor.

Rollback in this architecture is never deletion (governance principle 5). A
committed plan is undone by ``kgcs.executor.compensate.Compensator``, which
emits a *new* ``CurationPlan`` of inverse operations in reverse order, and that
plan goes through the same :class:`PlanExecutor` and the same
``GraphMutationStore`` as the forward one. There is no second write path, and
this module adds none.

**Partial rollback is reported, never implied.** ``CREATE_IDENTITY`` and
``PROMOTE_ONTOLOGY_TERM`` have no inverse in the v1 ``CurationOperationType``
vocabulary — there is no "un-create identity" — so a plan made only of
``CREATE_IDENTITY`` operations compensates to **nothing at all**:
``CompensationResult.plan`` is ``None`` and every operation lands in
``non_compensable``. That is exactly the shape of the plan this repo's curation
path currently emits (see ``policy.py``), so on today's pipeline *rollback is
not available*, and :class:`RollbackResult.fully_reversed` says so rather than
an ``execution=None`` being read as "nothing to undo".

``against_snapshot`` is required by ``Compensator.compensate`` and must be the
epoch the original plan committed at, which is ``ExecutionRecord.new_epoch``.
Carrying the source plan's own snapshot guard instead would be stale by
construction — the original plan's commit is what invalidated it (KGCS
ADR-0018) — so :func:`roll_back` takes the forward :class:`CurationRunResult`
and reads the epoch off it rather than letting a caller supply one.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from agentic_kg.migration.curation._contracts import (
    CompensationResult,
    Compensator,
    CurationOperation,
    CurationOperationType,
    DerivedIdFactory,
    ExecutionAuditSink,
    ExecutionRecord,
    FixedClock,
    GraphMutationStore,
    IdFactory,
    PlanExecutor,
)
from agentic_kg.migration.curation.pipeline import CurationRunResult
from agentic_kg.migration.curation.policy import RUN_INSTANT


class NotRollbackable(RuntimeError):
    """The forward run never committed, so there is nothing to compensate."""


@dataclass(frozen=True)
class RollbackResult:
    """What compensating one committed run produced, and what it could not undo."""

    compensation: CompensationResult
    execution: ExecutionRecord | None
    against_snapshot: int

    @property
    def non_compensable(self) -> tuple[CurationOperation, ...]:
        return self.compensation.non_compensable

    @property
    def fully_reversed(self) -> bool:
        """True iff every forward operation had an inverse AND it committed.

        Both halves are required. ``CompensationResult.fully_compensable`` says
        the *plan* covered everything; it says nothing about whether the store
        accepted it. A caller that checked only the first would report a
        rollback that came back ``UNSUPPORTED_OPERATION`` as a completed one.
        """
        return (
            self.compensation.fully_compensable
            and self.execution is not None
            and self.execution.committed
        )


def roll_back(
    result: CurationRunResult,
    *,
    store: GraphMutationStore,
    id_factory: IdFactory | None = None,
    instant: datetime | None = None,
    supported_operations: frozenset[CurationOperationType] | None = None,
    execution_audit_sink: ExecutionAuditSink | None = None,
    executed_by: str = "agentic-kg.migration.curation/compensate",
) -> RollbackResult:
    """Compensate a committed run and apply the compensating plan.

    Raises:
        NotRollbackable: ``result`` did not commit. Compensating a plan that
            never reached the graph would emit inverse operations against state
            that was never created.
    """
    if not result.committed or result.execution is None:
        raise NotRollbackable(
            "the forward run did not commit, so there is nothing to compensate. "
            f"execution={None if result.execution is None else result.execution.outcome}."
        )
    plan = result.plan
    if plan is None:  # pragma: no cover - committed implies a plan
        raise NotRollbackable("the forward run committed without a plan")

    epoch = result.execution.new_epoch
    if epoch is None:  # pragma: no cover - a committed apply carries an epoch
        raise NotRollbackable("the committed execution record carries no epoch")

    ids = id_factory or DerivedIdFactory()
    compensation = Compensator(id_factory=ids).compensate(plan, against_snapshot=epoch)

    execution: ExecutionRecord | None = None
    if compensation.plan is not None:
        executor = PlanExecutor(
            store,
            id_factory=ids,
            clock=FixedClock(instant or RUN_INSTANT),
            supported_operations=supported_operations,
            audit_sink=execution_audit_sink,
            executed_by=executed_by,
        )
        execution = executor.execute(compensation.plan, is_compensation=True)

    return RollbackResult(
        compensation=compensation, execution=execution, against_snapshot=epoch
    )


__all__ = ["NotRollbackable", "RollbackResult", "roll_back"]
