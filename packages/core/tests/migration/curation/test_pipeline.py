"""The curation run: gating, partition, determinism, and the executor seam."""

from __future__ import annotations

import pytest
from agentic_kg.migration.config import MigrationConfig
from agentic_kg.migration.curation import (
    ARTIFACT_REASON,
    CONTRACT_DEFAULT_POLICY,
    STRUCTURED_IDENTITY_POLICY,
    UNRESOLVED_SUBJECT_REASON,
    CurationDisabled,
    run_curation,
)
from kgcs.executor.executor import ExecutionOutcome

from ._synthetic import graded_entity_candidate


class ExplodingStore:
    """A ``GraphMutationStore`` that fails the test if it is ever written to.

    The assertion "nothing was written" is usually made by looking at an empty
    store afterwards, which passes just as well when the write happened and was
    rolled back, or when the store was never the one under test. This one
    cannot be satisfied by an accident: reaching ``apply`` at all is the
    failure.
    """

    def __init__(self) -> None:
        self.apply_calls = 0

    def apply(self, batch, preconditions):  # pragma: no cover - must not run
        self.apply_calls += 1
        raise AssertionError(
            f"the canonical store was written to: batch {batch.batch_id} with "
            f"{len(batch.operations)} operation(s)"
        )


# --------------------------------------------------------------------------
# Gating
# --------------------------------------------------------------------------


def test_a_disabled_config_raises_rather_than_returning_an_empty_result() -> None:
    """Off means a refusal, not a zero.

    A ``CurationRunResult`` carrying no operations is indistinguishable from a
    run that curated nothing, and those are opposite facts — one is a statement
    about the flag, the other a measurement of the pipeline.
    """
    with pytest.raises(CurationDisabled) as excinfo:
        run_curation([graded_entity_candidate()], config=MigrationConfig())
    assert "use_kgcs_resolution=False" in str(excinfo.value)
    assert "KGCS_RESOLUTION_ENABLED" in str(excinfo.value)


def test_the_kgis_flag_alone_does_not_enable_curation() -> None:
    """The two switches are independent; KGIS on does not turn KGCS on."""
    with pytest.raises(CurationDisabled):
        run_curation(
            [graded_entity_candidate()],
            config=MigrationConfig(use_kgis_ingestion=True, use_kgcs_resolution=False),
        )


def test_a_disabled_run_never_reaches_the_store() -> None:
    """The refusal happens before the executor is constructed."""
    store = ExplodingStore()
    with pytest.raises(CurationDisabled):
        run_curation(
            [graded_entity_candidate()], config=MigrationConfig(), store=store
        )
    assert store.apply_calls == 0


# --------------------------------------------------------------------------
# The partition
# --------------------------------------------------------------------------


def test_every_candidate_lands_in_exactly_one_bucket(
    shadow_candidates: tuple[object, ...], enabled_config: MigrationConfig
) -> None:
    """Rejected / deferred / planned partition the input — by identity, not count.

    Checked as sets rather than as a sum. Three counts can add to the right
    total while the same candidate sits in two buckets and another sits in
    none, which is exactly the silent drop this structure exists to prevent.
    """
    result = run_curation(
        shadow_candidates,
        config=enabled_config,
        confidence_policy=STRUCTURED_IDENTITY_POLICY,
    )
    rejected = {d.candidate_id for d in result.rejected}
    deferred = {d.candidate_id for d in result.deferred}
    planned = set(result.planned_candidate_ids)
    everything = {c.candidate_id for c in shadow_candidates}

    assert everything, "the corpus fixture produced no candidates"
    assert rejected & deferred == set()
    assert rejected & planned == set()
    assert deferred & planned == set()
    assert rejected | deferred | planned == everything
    assert len(result.rejected) + len(result.deferred) + len(planned) == len(everything)


def test_a_deferral_names_the_reason_it_produced_nothing(
    shadow_candidates: tuple[object, ...], enabled_config: MigrationConfig
) -> None:
    """Artifacts and alias-subject assertions are distinguished, not merged.

    Both are "validated but produced no operation". They have different owners:
    one is a contract gap with no artifact operation type, the other is the
    missing entity-resolution stage. A single reason string would hide whichever
    gap is not currently loudest.
    """
    result = run_curation(
        shadow_candidates,
        config=enabled_config,
        confidence_policy=STRUCTURED_IDENTITY_POLICY,
    )
    by_kind = {d.candidate_kind: d.reason for d in result.deferred}
    assert "artifact" in by_kind, f"no artifact was deferred: {sorted(by_kind)}"
    assert by_kind["artifact"] == ARTIFACT_REASON
    assert "attribute_assertion" in by_kind
    assert by_kind["attribute_assertion"] == UNRESOLVED_SUBJECT_REASON


def test_an_alias_subject_assertion_reports_the_missing_resolver(
    enabled_config: MigrationConfig,
) -> None:
    """An AUTO-routed assertion with an unresolved subject names ER, not luck."""
    from kg_contracts.candidates import AttributeAssertionCandidate, SourceCoordinates
    from kg_contracts.identity import EntityRef

    from ._synthetic import GRAPH_ID, auto_routing_scores

    candidate = AttributeAssertionCandidate(
        graph_id=GRAPH_ID,
        producer="test-producer",
        producer_run_id="run-synthetic",
        ontology_version="1",
        source_coordinates=SourceCoordinates(source_type="paper", locator="paper://doi/x"),
        semantic_key="paper/x/title",
        scores=auto_routing_scores(),
        subject=EntityRef(entity_type="Paper", namespace="doi", key="x"),
        attribute="title",
        value="X",
    )
    result = run_curation([candidate], config=enabled_config)
    assert len(result.deferred) == 1
    assert result.deferred[0].reason == UNRESOLVED_SUBJECT_REASON


def test_route_counts_come_from_the_policy_not_from_the_deferrals(
    shadow_candidates: tuple[object, ...], enabled_config: MigrationConfig
) -> None:
    """AUTO appears in the route counts even though it is never deferred.

    A count derived from the deferral list would omit every auto-applied
    candidate and would agree with itself perfectly while under-reporting the
    only route that writes to the graph.
    """
    result = run_curation(
        shadow_candidates,
        config=enabled_config,
        confidence_policy=STRUCTURED_IDENTITY_POLICY,
    )
    counts = result.route_counts()
    # 16 = eight DOI-keyed Paper identities + eight document artifacts. The
    # artifacts route AUTO and are still deferred (no v1 operation type), which
    # is precisely why this count cannot come from the deferral list.
    assert counts.get("AUTO", 0) == 16
    assert sum(counts.values()) == len(shadow_candidates) - len(result.rejected)


# --------------------------------------------------------------------------
# Determinism
# --------------------------------------------------------------------------


def test_the_same_candidates_produce_a_byte_identical_plan(
    shadow_candidates: tuple[object, ...], enabled_config: MigrationConfig
) -> None:
    """Identical input, identical plan — the whole basis of replay comparison."""
    first = run_curation(
        shadow_candidates,
        config=enabled_config,
        confidence_policy=STRUCTURED_IDENTITY_POLICY,
    )
    second = run_curation(
        shadow_candidates,
        config=enabled_config,
        confidence_policy=STRUCTURED_IDENTITY_POLICY,
    )
    assert first.plan is not None and second.plan is not None
    assert first.plan.model_dump_json() == second.plan.model_dump_json()


def test_candidate_order_is_carried_into_the_plan(
    shadow_candidates: tuple[object, ...], enabled_config: MigrationConfig
) -> None:
    """The plan's candidate order is the input's, and order is observable.

    ``CurationPlanner`` derives ``plan_id`` from the joined candidate ids, so a
    reordering changes the plan id. Comparing the two as *sets* would call two
    materially different plans equal.
    """
    result = run_curation(
        shadow_candidates,
        config=enabled_config,
        confidence_policy=STRUCTURED_IDENTITY_POLICY,
    )
    reversed_result = run_curation(
        tuple(reversed(shadow_candidates)),
        config=enabled_config,
        confidence_policy=STRUCTURED_IDENTITY_POLICY,
    )
    assert result.plan is not None and reversed_result.plan is not None
    assert set(result.plan.candidate_ids) == set(reversed_result.plan.candidate_ids)
    assert list(result.plan.candidate_ids) != list(reversed_result.plan.candidate_ids)
    assert result.plan.plan_id != reversed_result.plan.plan_id


# --------------------------------------------------------------------------
# The executor seam
# --------------------------------------------------------------------------


def test_a_plan_only_run_executes_nothing(
    shadow_candidates: tuple[object, ...], enabled_config: MigrationConfig
) -> None:
    result = run_curation(
        shadow_candidates,
        config=enabled_config,
        confidence_policy=STRUCTURED_IDENTITY_POLICY,
    )
    assert result.plan is not None
    assert result.execution is None
    assert result.committed is False
    assert result.committed_candidate_ids == ()
    assert result.published_epoch is None


def test_an_empty_plan_never_touches_the_store(
    shadow_candidates: tuple[object, ...], enabled_config: MigrationConfig
) -> None:
    """Under the contract default nothing auto-applies, so nothing is applied."""
    store = ExplodingStore()
    result = run_curation(
        shadow_candidates,
        config=enabled_config,
        store=store,
        confidence_policy=CONTRACT_DEFAULT_POLICY,
    )
    assert result.plan is None
    assert result.execution is None
    assert store.apply_calls == 0


def test_a_committed_run_advances_and_publishes_the_epoch(
    shadow_candidates: tuple[object, ...],
    enabled_config: MigrationConfig,
    memory_store: object,
) -> None:
    result = run_curation(
        shadow_candidates,
        config=enabled_config,
        store=memory_store,
        confidence_policy=STRUCTURED_IDENTITY_POLICY,
    )
    assert result.execution is not None
    assert result.execution.outcome is ExecutionOutcome.COMMITTED
    assert result.execution.new_epoch == 1
    assert result.published_epoch == 1
    assert memory_store.current_epoch() == 1
    assert set(result.committed_candidate_ids) == set(result.planned_candidate_ids)


def test_the_committed_identities_are_actually_in_the_graph(
    shadow_candidates: tuple[object, ...],
    enabled_config: MigrationConfig,
    memory_store: object,
) -> None:
    """Read the identities back through the store, not off the plan.

    Asserting that the plan contains eight ``CREATE_IDENTITY`` operations and
    then reporting eight identities would be both sides of the claim coming
    from the same artifact. The graph is the other side.
    """
    result = run_curation(
        shadow_candidates,
        config=enabled_config,
        store=memory_store,
        confidence_policy=STRUCTURED_IDENTITY_POLICY,
    )
    assert result.committed
    minted = {
        op.payload["identity_id"]
        for op in result.plan.operations
        if op.type.value == "CREATE_IDENTITY"
    }
    assert len(minted) == 8
    for identity_id in sorted(minted):
        assert memory_store.get_entity(identity_id) is not None, (
            f"{identity_id} was committed but is not readable from the graph"
        )


def test_replaying_a_committed_plan_is_stale_and_commits_nothing(
    shadow_candidates: tuple[object, ...],
    enabled_config: MigrationConfig,
    memory_store: object,
) -> None:
    """Idempotent replay: the second apply is refused, the epoch does not move."""
    kwargs = dict(
        config=enabled_config,
        store=memory_store,
        confidence_policy=STRUCTURED_IDENTITY_POLICY,
    )
    first = run_curation(shadow_candidates, **kwargs)
    assert first.committed

    second = run_curation(shadow_candidates, **kwargs)
    assert second.execution is not None
    assert second.execution.outcome is ExecutionOutcome.STALE
    assert second.committed is False
    assert second.committed_candidate_ids == ()
    assert second.planned_candidate_ids == first.planned_candidate_ids
    assert memory_store.current_epoch() == 1


def test_a_second_batch_commits_against_the_current_epoch(
    enabled_config: MigrationConfig, memory_store: object
) -> None:
    """Curation is not a one-shot: batch two applies to a non-virgin graph.

    ``kgcs`` stamps every plan ``snapshot_version="0"`` by default, and the
    executor enforces that guard against the graph's epoch — so a pipeline that
    never overrides it commits once and is silently inert forever after.
    ``run_curation`` reads the store's current epoch instead. The epochs, not
    just the outcomes, are asserted: two ``COMMITTED`` records that both landed
    on epoch 1 would mean the second overwrote the first.
    """
    first = run_curation(
        [graded_entity_candidate(key="a", surface="Knowledge Graphs")],
        config=enabled_config,
        store=memory_store,
        confidence_policy=CONTRACT_DEFAULT_POLICY,
    )
    second = run_curation(
        [graded_entity_candidate(key="b", surface="Information Extraction")],
        config=enabled_config,
        store=memory_store,
        confidence_policy=CONTRACT_DEFAULT_POLICY,
    )
    assert first.execution.outcome is ExecutionOutcome.COMMITTED
    assert second.execution.outcome is ExecutionOutcome.COMMITTED
    assert (first.execution.new_epoch, second.execution.new_epoch) == (1, 2)
    assert memory_store.current_epoch() == 2


def test_pinning_the_empty_graph_snapshot_makes_the_second_batch_stale(
    enabled_config: MigrationConfig, memory_store: object
) -> None:
    """The control for the test above: with ``snapshot_version="0"`` it is inert.

    Without this, "reading the epoch fixes it" would be an unfalsifiable claim —
    the first test would pass identically against a store whose epoch never
    moves and an executor that never checks.
    """
    kwargs = dict(
        config=enabled_config,
        store=memory_store,
        confidence_policy=CONTRACT_DEFAULT_POLICY,
        snapshot_version="0",
    )
    first = run_curation([graded_entity_candidate(key="a")], **kwargs)
    second = run_curation([graded_entity_candidate(key="b")], **kwargs)
    assert first.execution.outcome is ExecutionOutcome.COMMITTED
    assert second.execution.outcome is ExecutionOutcome.STALE


def test_an_attach_assertion_carries_no_per_subject_guard(
    enabled_config: MigrationConfig, memory_store: object
) -> None:
    """Recorded, not fixed: the attach path has only the plan-level snapshot guard.

    ``CREATE_IDENTITY`` emits an ``entity_version=0`` precondition the store
    enforces, so replaying a create is refused whatever the snapshot says. An
    ``ATTACH_ASSERTION`` emits none — knowing a subject's current version needs
    a graph read the deterministic core does not do (KGCS ADR candidate 0003) —
    so once the snapshot guard is satisfied, nothing refuses a replayed attach.
    This test exists so that gap is a documented, checked fact rather than a
    surprise, and it goes red the day upstream closes it.
    """
    from ._synthetic import attachable_attribute_candidate

    entity = run_curation(
        [graded_entity_candidate()],
        config=enabled_config,
        store=memory_store,
        confidence_policy=CONTRACT_DEFAULT_POLICY,
    )
    identity_id = entity.engine.outcomes[0].resolution.resolved_identity
    attach = run_curation(
        [attachable_attribute_candidate(subject=identity_id)],
        config=enabled_config,
        store=memory_store,
        confidence_policy=CONTRACT_DEFAULT_POLICY,
    )
    assert attach.operation_counts() == {"ATTACH_ASSERTION": 1}
    assert {p.kind for p in attach.plan.preconditions} == {"snapshot_version"}

    assert {p.kind for p in entity.plan.preconditions} == {
        "snapshot_version",
        "entity_version",
    }
