"""The curation run: candidates -> KGCS -> CurationPlan -> executor -> canonical graph.

This module is the second half of the only legal write path::

    source -> KGIS -> Candidate + Evidence -> ledger
           -> KGCS -> deterministic validation / policy -> CurationPlan
           -> PlanExecutor -> canonical graph -> published epoch

and it is deliberately the *whole* of this subpackage's contact with a
``GraphMutationStore``. The store reaches exactly one expression here — the
``PlanExecutor(...)`` constructor — and nothing this module returns holds a
reference to it. Applications submit candidates; the executor alone mutates
canonical state (ADR-0010, governance principle 1).
``test_no_application_write_surface.py`` asserts both halves by AST walk.

**Config is injected (ADR-0004 decision 4, AC-15c).** Nothing here calls
``get_migration_config()``; the flag arrives as a ``MigrationConfig`` argument.
``test_config_injection.py`` enforces it.

**Disabled means disabled.** With ``use_kgcs_resolution=False`` — the default,
and therefore the state of every existing deployment — :func:`run_curation`
raises :class:`CurationDisabled` rather than returning an empty result. An
empty result is indistinguishable from "the pipeline ran and curated nothing",
and those are opposite facts. This mirrors ``ShadowIngestionDisabled`` on the
ingestion side and ``MigrationDisabledError`` on the canonical-store factory.

Every input candidate lands in exactly one bucket
-------------------------------------------------
The failure this module is written against is the silent drop: a candidate that
validated, did not auto-apply, and then simply vanished from every count. So
:class:`CurationRunResult` partitions the input, by construction, into three
disjoint tuples whose sizes must sum to the number of candidates supplied —

* :attr:`~CurationRunResult.rejected` — validation said no. Never reached the
  policy (the engine is fail-closed in its sequencing).
* :attr:`~CurationRunResult.deferred` — validated, but produced no operation.
  Each carries the route it got *and* the reason it produced nothing, and the
  reason names the constraint that actually binds. An artifact has no v1
  operation type, and an assertion whose subject is still an alias has no
  identity, **at any route** — reporting "routed ``LLM_ASSESS``" for those would
  send a reader to the adviser stage when the fix is a contract operation type
  in one case and the entity-resolution stage in the other.
* :attr:`~CurationRunResult.planned_candidate_ids` — in the emitted plan.

`test_pipeline.py::test_every_candidate_lands_in_exactly_one_bucket` checks the
partition on the real corpus, and checks it as a *partition* (the three id sets
are pairwise disjoint and their union is the input), not as a sum — three
counts can add up while the same candidate sits in two of them.

Committed is not planned
------------------------
:attr:`~CurationRunResult.committed_candidate_ids` is empty unless the executor
actually reported ``COMMITTED``. A plan that came back ``STALE``, ``ERROR`` or
``UNSUPPORTED_OPERATION`` planned its candidates and committed none of them,
and conflating the two would report a rolled-back or rejected batch as
canonical fact.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from agentic_kg.migration.config import ENV_KGCS_ENABLED, MigrationConfig
from agentic_kg.migration.curation._contracts import (
    AdjudicationRoute,
    ArtifactCandidate,
    AttributeAssertionCandidate,
    AuditSink,
    Candidate,
    ConfidencePolicy,
    CurationOperationType,
    CurationPlan,
    EngineResult,
    EntityCandidate,
    EpochPublisher,
    ExecutionAuditSink,
    ExecutionOutcome,
    ExecutionRecord,
    FixedClock,
    GraphMutationStore,
    GraphReader,
    IdFactory,
    InMemoryEpochPublisher,
    PlanExecutor,
    RelationCandidate,
    ResolutionDecision,
    is_identity_id,
)
from agentic_kg.migration.curation.policy import (
    CONTRACT_DEFAULT_POLICY,
    CURATION_GRAPH_ID,
    DEFAULT_SNAPSHOT_VERSION,
    REGISTERED_IDENTIFIER_NAMESPACES,
    RUN_INSTANT,
    curation_engine,
    registered_identifier_for,
)

#: Reason text for a candidate kind that validates and resolves but has no
#: operation type in the v1 curation vocabulary. ``artifact`` is the one such
#: kind KGIS produces (KGCS ADR candidate 0001: the contract has no artifact
#: operation), and an artifact contributing no graph operation is correct
#: rather than a gap — the legacy graph holds no artifacts either.
ARTIFACT_REASON = (
    "candidate_kind 'artifact' has no operation type in the v1 curation "
    "vocabulary, so it contributes no graph operation (KGCS ADR candidate "
    "0001). It stays in the ledger and the evidence registry."
)

#: Reason text for an assertion whose subject is still an alias. Resolving an
#: ``EntityRef`` to an identity id is entity resolution's job; with no ER wired
#: the resolution stage escalates the route and claims no identity, so the
#: planner cannot build an ``ATTACH_ASSERTION``.
UNRESOLVED_SUBJECT_REASON = (
    "no resolved identity: the candidate's subject is an alias EntityRef and "
    "entity resolution is not wired, so kgcs.policy escalated the route and "
    "claimed no identity. An ATTACH_ASSERTION cannot be built without one."
)


class UnsafeIdentityRelaxation(RuntimeError):
    """The identity gate is off and an unkeyed identity would be minted.

    ``ConfidencePolicy.require_identity_confidence_for_auto`` is the fail-closed
    guard against auto-minting a duplicate identity when no entity resolution
    has run. Turning it off is defensible for entities whose identity comes from
    a **registered identifier** — a DOI settles whether two paper candidates are
    the same paper — and indefensible for entities identified by a surface form,
    where deciding sameness is exactly ER's job.

    The policy, however, is batch-wide while that argument is candidate-level.
    Review of this subpackage demonstrated the gap: two ``Topic`` candidates for
    one concept minting two identities, irreversibly, because
    ``CREATE_IDENTITY`` has no inverse. Nothing held the line except the LLM
    extractor's scores happening to keep graded entities away from ``AUTO``.

    So the argument is enforced here rather than documented: with the gate off,
    a candidate that would mint an identity must carry an alias in
    :data:`~agentic_kg.migration.curation.policy.REGISTERED_IDENTIFIER_NAMESPACES`.
    Raised **before any execution**, so nothing reaches the graph.
    """


class CurationDisabled(RuntimeError):
    """The KGCS flag is off, so no curation run was attempted.

    Raised rather than returning an empty :class:`CurationRunResult`, because a
    result carrying zero operations would be read as a measurement of the
    pipeline instead of a statement about the flag.
    """


@dataclass(frozen=True)
class Deferral:
    """One candidate that did not become a canonical operation, and why.

    ``route`` is ``None`` exactly when validation rejected the candidate: an
    invalid candidate never reached the policy, so it has no route, and
    reporting one would invent a decision nobody made.
    """

    candidate_id: str
    candidate_kind: str
    entity_type: str | None
    route: str | None
    reason: str


@dataclass(frozen=True)
class CurationRunResult:
    """Everything one curation run decided, planned, and committed.

    ``engine`` is KGCS's own ``EngineResult`` — not a re-derived summary — so
    the decisions here are the core's decisions rather than this module's
    opinion of them. ``execution`` is ``None`` exactly when no plan was
    executed, which happens in two distinguishable ways: nothing auto-applied
    (``plan is None``), or no store was supplied (a plan-only run).
    """

    engine: EngineResult
    execution: ExecutionRecord | None
    rejected: tuple[Deferral, ...]
    deferred: tuple[Deferral, ...]
    planned_candidate_ids: tuple[str, ...]
    published_epoch: int | None
    candidates_seen: int

    @property
    def plan(self) -> CurationPlan | None:
        return self.engine.plan

    @property
    def committed(self) -> bool:
        """True iff the executor advanced canonical state in this run."""
        return self.execution is not None and self.execution.committed

    @property
    def committed_candidate_ids(self) -> tuple[str, ...]:
        """The candidates whose operations are now canonical fact.

        Empty unless the executor reported ``COMMITTED``. A ``STALE`` or
        ``ERROR`` outcome planned these candidates and committed none of them.
        """
        return self.planned_candidate_ids if self.committed else ()

    def route_counts(self) -> dict[str, int]:
        """Adjudication routes over the validated candidates, sorted.

        Derived from the resolution decisions the policy actually made, not
        from the deferral list: a count read off the deferrals would omit the
        candidates that routed ``AUTO`` and would silently agree with itself.
        """
        counts: dict[str, int] = {}
        for outcome in self.engine.outcomes:
            if outcome.resolution is None:
                continue
            key = str(outcome.resolution.route)
            counts[key] = counts.get(key, 0) + 1
        return dict(sorted(counts.items()))

    def operation_counts(self) -> dict[str, int]:
        """Planned operations per :class:`CurationOperationType`, sorted."""
        plan = self.engine.plan
        if plan is None:
            return {}
        counts: dict[str, int] = {}
        for operation in plan.operations:
            counts[operation.type.value] = counts.get(operation.type.value, 0) + 1
        return dict(sorted(counts.items()))


def _entity_type(candidate: Candidate) -> str | None:
    return candidate.entity_type if isinstance(candidate, EntityCandidate) else None


def _current_snapshot(store: GraphMutationStore | None) -> str:
    """The snapshot a plan against ``store`` should be computed against.

    ``kgcs`` defaults every plan's ``snapshot_version`` to ``"0"``, and the
    executor enforces that guard against the graph's current epoch whenever it
    can read one. On a graph that has already committed anything, a plan stamped
    ``"0"`` is therefore refused ``STALE`` before it reaches the store — which
    means a pipeline that never overrides the default can commit **once** in the
    lifetime of a graph and is silently inert thereafter. That is not a
    hypothetical: it is what the second curation batch in a session does.

    Reading the epoch off the store instead gives what the executor's own
    docstring calls true optimistic concurrency: the plan asserts the state it
    was computed against, and a commit by anyone else in between refuses it.

    The trade it makes is real and is not hidden. ``CREATE_IDENTITY`` keeps its
    own ``entity_version=0`` guard, so replaying a create is still refused by
    the store. ``ATTACH_ASSERTION`` emits no per-subject guard (knowing a
    subject's version needs a graph read the deterministic core does not do —
    KGCS ADR candidate 0003), so with the snapshot guard satisfied, replaying an
    attach is **not** refused by either layer. Per-subject preconditions are the
    fix and they are upstream work.
    """
    if isinstance(store, GraphReader):
        return str(store.current_epoch())
    return DEFAULT_SNAPSHOT_VERSION


def _has_unresolved_subject(candidate: Candidate) -> bool:
    """Does this candidate attach to an endpoint that is still an alias?

    Read off the candidate's own subject/object refs, not inferred from the
    route it received. The route is a *consequence* — ``kgcs.policy`` escalates
    a candidate with an alias endpoint to at least ``LLM_ASSESS`` — so reading
    the route back would say "it was escalated because it was escalated" and
    would stop discriminating the moment the thresholds moved.
    """
    refs: tuple[object, ...]
    if isinstance(candidate, RelationCandidate):
        refs = (candidate.subject, candidate.object)
    elif isinstance(candidate, AttributeAssertionCandidate):
        refs = (candidate.subject,)
    else:
        return False
    return any(not (isinstance(ref, str) and is_identity_id(ref)) for ref in refs)


def _deferral_reason(candidate: Candidate, resolution: ResolutionDecision) -> str:
    """Why this validated candidate produced no operation.

    Ordered by what actually *binds*, structural constraints first. An artifact
    has no operation type and an alias-subject assertion has no identity **at
    any route**; raising their confidence would change nothing. Reporting
    "routed LLM_ASSESS" for those would name the symptom and point a reader at
    the adviser stage, when the fix is a contract operation type in one case and
    the entity-resolution stage in the other. Only when neither structural
    constraint applies is the route the reason.
    """
    if isinstance(candidate, ArtifactCandidate):
        return ARTIFACT_REASON
    if _has_unresolved_subject(candidate):
        return UNRESOLVED_SUBJECT_REASON
    if resolution.route is not AdjudicationRoute.AUTO:
        return (
            f"routed {resolution.route.value}: the deterministic core defers "
            f"anything needing an LLM adviser or a human reviewer rather than "
            f"planning it. No adviser or review stage is wired on this path."
        )
    return (
        "routed AUTO with a resolved identity, but kgcs.planner emitted no "
        "operation for it — e.g. an attribute assertion whose value is None "
        "cannot form a valid Assertion."
    )


def classify(
    candidates: Sequence[Candidate], engine_result: EngineResult
) -> tuple[tuple[Deferral, ...], tuple[Deferral, ...], tuple[str, ...]]:
    """Partition the input into ``(rejected, deferred, planned_candidate_ids)``.

    The partition is computed against the *plan's own* ``candidate_ids`` rather
    than by re-deciding which candidates ought to have been planned. Re-deriving
    it would be a second implementation of the planner's dispatch rules, and the
    two would drift — at which point the deferral report would describe a
    pipeline that does not exist.
    """
    planned_ids = tuple(engine_result.plan.candidate_ids) if engine_result.plan else ()
    planned = set(planned_ids)
    by_id = {candidate.candidate_id: candidate for candidate in candidates}

    rejected: list[Deferral] = []
    deferred: list[Deferral] = []
    for outcome in engine_result.outcomes:
        candidate = by_id[outcome.candidate_id]
        if outcome.resolution is None:
            rejected.append(
                Deferral(
                    candidate_id=outcome.candidate_id,
                    candidate_kind=candidate.candidate_kind,
                    entity_type=_entity_type(candidate),
                    route=None,
                    reason="validation rejected the candidate: "
                    + "; ".join(outcome.validation.reasons),
                )
            )
            continue
        if outcome.candidate_id in planned:
            continue
        route = outcome.resolution.route
        reason = _deferral_reason(candidate, outcome.resolution)
        deferred.append(
            Deferral(
                candidate_id=outcome.candidate_id,
                candidate_kind=candidate.candidate_kind,
                entity_type=_entity_type(candidate),
                route=route.value,
                reason=reason,
            )
        )
    return tuple(rejected), tuple(deferred), planned_ids


def unkeyed_new_identities(
    candidates: Sequence[Candidate],
    engine_result: EngineResult,
    confidence_policy: ConfidencePolicy,
) -> tuple[EntityCandidate, ...]:
    """Entity candidates this run would mint an identity for without a registry.

    Empty — always — while the identity gate is on, because the gate is itself
    the guard and nothing routes ``AUTO`` under it. With the gate off, every
    candidate whose ``ResolutionDecision`` says ``create_new_identity`` must
    carry an alias that a registry could actually have issued **for a candidate
    of that type** — namespace, entity type and key spelling all checked
    together by ``registered_identifier_for``. Checking the namespace alone was
    the first version, and review defeated it with ``namespace="doi",
    key="banana"`` on a ``Topic``.

    Read off the decisions the policy actually made, not re-derived from scores:
    a second implementation of the routing rules here would drift from the one
    that mints the identity, and the drift would silently re-open the gap.
    """
    return tuple(
        candidate
        for candidate, keyed in _minting(candidates, engine_result, confidence_policy)
        if keyed is None
    )


def _minting(
    candidates: Sequence[Candidate],
    engine_result: EngineResult,
    confidence_policy: ConfidencePolicy,
) -> list[tuple[EntityCandidate, tuple[str, str] | None]]:
    """Every entity candidate that would mint, with the identifier keying it.

    One walk, used by both the "nothing unkeyed" rule and the "no two the same"
    rule, so the two cannot disagree about which candidates are in scope.
    """
    if confidence_policy.require_identity_confidence_for_auto:
        return []
    by_id = {candidate.candidate_id: candidate for candidate in candidates}
    minting: list[tuple[EntityCandidate, tuple[str, str] | None]] = []
    for outcome in engine_result.outcomes:
        resolution = outcome.resolution
        if resolution is None or not resolution.create_new_identity:
            continue
        candidate = by_id[outcome.candidate_id]
        if not isinstance(candidate, EntityCandidate):
            continue
        keyed: tuple[str, str] | None = None
        for alias in candidate.aliases:
            keyed = registered_identifier_for(candidate.entity_type, alias)
            if keyed is not None:
                break
        minting.append((candidate, keyed))
    return minting


def duplicate_registered_identities(
    candidates: Sequence[Candidate],
    engine_result: EngineResult,
    confidence_policy: ConfidencePolicy,
) -> dict[tuple[str, str], tuple[EntityCandidate, ...]]:
    """Registered identifiers that **two or more** candidates would mint under.

    The hole review found by walking through the first version of this guard,
    and the one that mattered: two candidates carrying the *identical* DOI each
    minted an identity, irreversibly, because ``DerivedIdFactory.identity_id``
    keys on ``candidate_id`` and nothing in the chain dedupes. That is R20's
    original defect — auto-minting duplicate identities with no entity
    resolution — reproduced through the very guard added to prevent it, using
    the guard's own poster-child type and namespace.

    It survived because no test could see it: the corpus's eight DOIs are all
    distinct, so "a DOI means these are the same paper" was a criterion
    quantified over an empty set.

    This function does not *merge* them. Minting one identity from a registered
    identifier instead of from a candidate id is registry-based entity
    resolution, and it belongs in ``kgcs``' ``IdFactory``/``ResolutionPolicy``,
    not in an adopter's guard — see the PR body. What it does is make the
    unenforceable claim refusable: a batch that would mint twice under one
    identifier is rejected rather than silently duplicated.
    """
    grouped: dict[tuple[str, str], list[EntityCandidate]] = {}
    for candidate, keyed in _minting(candidates, engine_result, confidence_policy):
        if keyed is not None:
            grouped.setdefault(keyed, []).append(candidate)
    return {key: tuple(group) for key, group in grouped.items() if len(group) > 1}


def run_curation(
    candidates: Sequence[Candidate],
    *,
    config: MigrationConfig,
    store: GraphMutationStore | None = None,
    confidence_policy: ConfidencePolicy | None = None,
    id_factory: IdFactory | None = None,
    instant: datetime | None = None,
    graph_id: str = CURATION_GRAPH_ID,
    snapshot_version: str | None = None,
    supported_operations: frozenset[CurationOperationType] | None = None,
    epoch_publisher: EpochPublisher | None = None,
    audit_sink: AuditSink | None = None,
    execution_audit_sink: ExecutionAuditSink | None = None,
    executed_by: str = "agentic-kg.migration.curation",
) -> CurationRunResult:
    """Curate ``candidates`` and, when a store is supplied, apply the plan.

    Args:
        candidates: The discriminated union ``CurationEngine.curate`` accepts —
            exactly what ``run_shadow_ingestion`` produces. No adapter sits
            between the two paths, deliberately: an adapter would be a place
            for the candidate contract to be reinterpreted.
        config: Injected, never fetched from the singleton (ADR-0004 dec. 4).
        store: The canonical ``GraphMutationStore``. ``None`` means plan only —
            nothing is executed and ``execution`` comes back ``None``. It is
            handed to :class:`PlanExecutor` and to nothing else.
        supported_operations: What ``store`` actually applies. Leave ``None``
            to take the executor's own default (``CREATE_IDENTITY`` and
            ``ATTACH_ASSERTION``, what the reference ``MemoryGraphStore``
            implements). A caller on the Neo4j adapter should pass
            ``agentic_kg.migration.neo4j.SUPPORTED_OPERATIONS``, which is wider:
            an under-declared set turns a supported operation into
            ``UNSUPPORTED_OPERATION`` with the store untouched.

    Raises:
        CurationDisabled: ``config.use_kgcs_resolution`` is ``False``.
        UnsafeIdentityRelaxation: the identity gate is off and a candidate with
            no registered-identifier alias would mint an identity. Raised before
            any execution.
    """
    if not config.use_kgcs_resolution:
        raise CurationDisabled(
            "KGCS curation is disabled: the injected MigrationConfig has "
            f"use_kgcs_resolution=False (set {ENV_KGCS_ENABLED}=1, or pass an "
            "explicitly enabled MigrationConfig). No run was attempted."
        )

    snapshot = (
        snapshot_version if snapshot_version is not None else _current_snapshot(store)
    )
    engine = curation_engine(
        confidence_policy=confidence_policy,
        id_factory=id_factory,
        instant=instant,
        graph_id=graph_id,
        snapshot_version=snapshot,
        audit_sink=audit_sink,
    )
    engine_result = engine.curate(candidates)

    # Before anything is executed, and regardless of whether a store was
    # supplied: a plan-only run still hands back CREATE_IDENTITY operations a
    # caller could apply itself.
    unkeyed = unkeyed_new_identities(
        candidates, engine_result, confidence_policy or CONTRACT_DEFAULT_POLICY
    )
    duplicates = duplicate_registered_identities(
        candidates, engine_result, confidence_policy or CONTRACT_DEFAULT_POLICY
    )
    if duplicates:
        shown = "; ".join(
            f"{namespace}:{key} claimed by {len(group)} candidates"
            for (namespace, key), group in sorted(duplicates.items())
        )
        raise UnsafeIdentityRelaxation(
            f"{len(duplicates)} registered identifier(s) would mint more than one "
            f"canonical identity in this batch: {shown}. The relaxed identity gate "
            "rests on the claim that two candidates carrying the same registered "
            "identifier are the same thing — but nothing in this chain dedupes "
            "them, so each would mint its own identity and CREATE_IDENTITY has no "
            "inverse. Refused rather than duplicated. Resolving them into one "
            "identity is entity resolution and belongs upstream; until it exists, "
            "submit one candidate per identifier."
        )

    if unkeyed:
        offenders = ", ".join(
            f"{c.entity_type}/{c.display_name or c.aliases[0].key}" for c in unkeyed[:5]
        )
        raise UnsafeIdentityRelaxation(
            f"{len(unkeyed)} candidate(s) would mint a canonical identity with "
            f"require_identity_confidence_for_auto=False and no registered "
            f"identifier that could have issued it for that entity type: {offenders}"
            + (" ..." if len(unkeyed) > 5 else "")
            + ". Minting an identity without entity resolution is how two "
            "candidates for one concept become two identities, and "
            "CREATE_IDENTITY has no inverse, so it cannot be rolled back. "
            "Relaxing the identity gate is defensible only for entities keyed "
            "by a registry (namespaces: "
            f"{sorted(REGISTERED_IDENTIFIER_NAMESPACES)}). Either wire entity "
            "resolution, or curate these candidates under the contract-default "
            "policy, which defers them."
        )

    rejected, deferred, planned_ids = classify(candidates, engine_result)

    execution: ExecutionRecord | None = None
    published: int | None = None
    if store is not None and engine_result.plan is not None:
        publisher = epoch_publisher or InMemoryEpochPublisher()
        executor = PlanExecutor(
            store,
            id_factory=id_factory,
            clock=FixedClock(instant or RUN_INSTANT),
            supported_operations=supported_operations,
            epoch_publisher=publisher,
            audit_sink=execution_audit_sink,
            executed_by=executed_by,
        )
        execution = executor.execute(engine_result.plan)
        published = publisher.published_epoch()

    return CurationRunResult(
        engine=engine_result,
        execution=execution,
        rejected=rejected,
        deferred=deferred,
        planned_candidate_ids=planned_ids,
        published_epoch=published,
        candidates_seen=len(candidates),
    )


__all__ = [
    "ARTIFACT_REASON",
    "UNRESOLVED_SUBJECT_REASON",
    "CurationDisabled",
    "CurationRunResult",
    "Deferral",
    "ExecutionOutcome",
    "UnsafeIdentityRelaxation",
    "classify",
    "duplicate_registered_identities",
    "run_curation",
    "unkeyed_new_identities",
]
