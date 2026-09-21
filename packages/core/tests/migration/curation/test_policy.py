"""What this path will and will not auto-apply — measured, not asserted.

Every test here is a *measurement of the platform as pinned*. If one goes red,
the right first question is "did KGCS/KGIS change?", not "is the test wrong".
That is deliberate: the numbers in the PR body come from these, and a claim
about what the deterministic core can auto-apply has to be re-checked on every
re-pin rather than taken from a docstring.
"""

from __future__ import annotations

from agentic_kg.migration.config import MigrationConfig
from agentic_kg.migration.curation import (
    CONTRACT_DEFAULT_POLICY,
    GRADED_ENTITY_TYPES,
    STRUCTURED_IDENTITY_POLICY,
    curation_engine,
    run_curation,
)
from kg_contracts.policy import AdjudicationRoute, ConfidencePolicy

from ._synthetic import graded_entity_candidate


def test_the_contract_default_policy_auto_routes_nothing_kgis_produces(
    shadow_candidates: tuple[object, ...], enabled_config: MigrationConfig
) -> None:
    """The measured deadlock: zero AUTO over the whole committed corpus.

    Not "few" and not "some" — zero, out of every candidate the eight-paper
    shadow run emits. The cause is in `policy.py`: ``AUTO`` requires
    ``identity_confidence``, and nothing in the KGIS/KGCS chain produces one.
    """
    result = run_curation(
        shadow_candidates,
        config=enabled_config,
        confidence_policy=CONTRACT_DEFAULT_POLICY,
    )
    counts = result.route_counts()
    assert counts, "no candidate was routed at all; the corpus fixture is empty"
    assert AdjudicationRoute.AUTO.value not in counts, (
        f"a candidate routed AUTO under the unmodified contract policy: {counts}. "
        f"If the platform gained an identity_confidence producer this is good "
        f"news and the PR's findings need rewriting."
    )
    assert result.plan is None
    assert result.planned_candidate_ids == ()


def test_the_structured_policy_changes_exactly_one_contract_field() -> None:
    """One field, named, and no threshold moved.

    This is the test that stops the arm being made to look good later. Lowering
    ``auto_min_extraction`` until the LLM extractor's 0.8 candidates cleared the
    bar would produce a `new` arm full of numbers and would be tuning the policy
    against the answer key. Any such edit reddens here with the field named.
    """
    default = ConfidencePolicy().model_dump()
    declared = STRUCTURED_IDENTITY_POLICY.model_dump()
    changed = {k for k, v in declared.items() if default[k] != v}
    assert changed == {"require_identity_confidence_for_auto"}, (
        f"STRUCTURED_IDENTITY_POLICY now differs from the contract default in "
        f"{sorted(changed)}. Only the identity gate may differ; a moved "
        f"threshold is policy tuning against the gold set."
    )
    assert declared["require_identity_confidence_for_auto"] is False


def test_no_graded_entity_type_auto_routes_under_either_declared_policy(
    shadow_candidates: tuple[object, ...], enabled_config: MigrationConfig
) -> None:
    """Neither declared policy auto-applies a Topic, Concept, Model or Method.

    The four scored types all come from the LLM extractor at
    ``extraction_confidence=0.8`` / ``source_reliability=0.75``, below both
    unmoved AUTO thresholds. This is why the `new` arm still has no graded
    output, and it is a fact about the extractor's scores, not about the
    identity gate.
    """
    seen_graded = 0
    for policy in (CONTRACT_DEFAULT_POLICY, STRUCTURED_IDENTITY_POLICY):
        result = run_curation(
            shadow_candidates, config=enabled_config, confidence_policy=policy
        )
        by_id = {c.candidate_id: c for c in shadow_candidates}
        for outcome in result.engine.outcomes:
            entity_type = getattr(by_id[outcome.candidate_id], "entity_type", None)
            if entity_type not in GRADED_ENTITY_TYPES:
                continue
            seen_graded += 1
            assert outcome.resolution is not None
            assert outcome.resolution.route is not AdjudicationRoute.AUTO, (
                f"{entity_type} candidate {outcome.candidate_id} routed AUTO "
                f"under {policy.policy_version}; the graded-arm claim in the PR "
                f"body is stale."
            )
    assert seen_graded > 0, "the corpus produced no graded-type candidate to check"


def test_the_structured_policy_does_auto_route_the_structured_arm(
    shadow_candidates: tuple[object, ...], enabled_config: MigrationConfig
) -> None:
    """The opt-in is not inert: the DOI-keyed Paper identities do auto-apply.

    Without this, the test above would be satisfied by a policy that changes
    nothing at all, and "one field, no thresholds moved" would be a statement
    about a dead constant.
    """
    result = run_curation(
        shadow_candidates,
        config=enabled_config,
        confidence_policy=STRUCTURED_IDENTITY_POLICY,
    )
    assert result.plan is not None, "the structured policy planned nothing"
    assert result.operation_counts() == {"CREATE_IDENTITY": 8}, (
        f"expected one CREATE_IDENTITY per corpus paper, got "
        f"{result.operation_counts()}"
    )


def test_the_engine_binds_the_graph_id_so_a_foreign_candidate_is_rejected(
    enabled_config: MigrationConfig,
) -> None:
    """A candidate from another graph is rejected at validation, not curated.

    ``CurationEngine.create`` defaults ``graph_id=None``, which accepts every
    graph. Binding it is the whole reason `curation_engine` exists rather than
    calling ``create`` at each site.
    """
    foreign = graded_entity_candidate().model_copy(update={"graph_id": "some-other-graph"})
    result = run_curation([foreign], config=enabled_config)
    assert len(result.rejected) == 1
    assert result.rejected[0].candidate_id == foreign.candidate_id
    assert result.deferred == ()
    assert result.plan is None


def test_the_engine_clock_is_fixed_so_audit_records_replay_identically() -> None:
    """Two separately constructed engines stamp the same ``recorded_at``.

    ``AuditRecord.recorded_at`` is the one field in the pipeline that cannot be
    a pure function of the input. With ``SystemClock`` — ``CurationEngine.create``'s
    default — two runs differ in every audit row and replay comparison is
    meaningless.
    """
    candidate = graded_entity_candidate()
    first = curation_engine().curate([candidate])
    second = curation_engine().curate([candidate])
    assert first.audit_records, "no audit record was produced to compare"
    assert [r.recorded_at for r in first.audit_records] == [
        r.recorded_at for r in second.audit_records
    ]
    assert [r.audit_id for r in first.audit_records] == [
        r.audit_id for r in second.audit_records
    ]
