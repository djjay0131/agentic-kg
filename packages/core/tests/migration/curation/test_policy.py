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

#: What the eight committed papers actually produce through the KGIS shadow
#: path. Pinned, and pinned *here*, because these are the numbers the PR body
#: and the upstream defect report quote — and the first review of this PR caught
#: the denominator quoted as 272 when it is 252. A prose number that no test
#: holds is a number that drifts, and this one had already been propagated into
#: two other repositories before it was caught.
CORPUS_CANDIDATE_COUNT = 252
CORPUS_KIND_COUNTS = {"artifact": 8, "attribute_assertion": 130, "entity": 114}
CORPUS_ENTITY_TYPES = {
    "Method": 21,
    "Model": 21,
    "Paper": 8,
    "Problem": 40,
    "ResearchConcept": 24,
}


def test_the_corpus_census_is_what_the_findings_quote(shadow_run) -> None:
    """The denominator and the type census, pinned against the real run.

    Read off KGIS's own submitted candidates, not off an extractor config: a
    count taken from the configuration would report what was *asked for* and
    would stay identical if every extractor returned nothing.

    Two body inaccuracies this closes, both found by review. The denominator was
    quoted as 272 and is 252. And the four graded types were described as coming
    from the LLM extractor when the corpus in fact yields **zero** ``Topic``
    candidates at all — ``Topic`` appears in this PR only as a synthetic fixture
    — while ``Problem`` (40 candidates, ungraded) went unmentioned.
    """
    candidates = (*shadow_run.paper_candidates, *shadow_run.candidates)
    assert len(candidates) == CORPUS_CANDIDATE_COUNT
    assert shadow_run.kind_counts() == CORPUS_KIND_COUNTS
    assert shadow_run.entity_counts() == CORPUS_ENTITY_TYPES
    assert "Topic" not in shadow_run.entity_counts(), (
        "the corpus now yields Topic candidates; the PR body's description of "
        "the graded types is stale"
    )


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
    # The denominator, pinned next to the zero. "0 of 252" is the sentence that
    # leaves this repo; a bare "no candidate routed AUTO" would stay true over a
    # corpus of one.
    assert sum(counts.values()) == CORPUS_CANDIDATE_COUNT
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


def test_the_contract_default_policy_is_the_contract_default_unchanged() -> None:
    """Not one threshold on the fail-closed default may move.

    The sibling of the structured-policy guard, and review found it missing:
    lowering ``CONTRACT_DEFAULT_POLICY``'s extraction thresholds passed the whole
    suite green, because the identity gate still blocked AUTO and hid the change.
    That is a latent trap rather than a live defect — the thresholds would
    already be pre-tuned the day the gate is closed upstream, and the deadlock
    measurement above would then be reporting a policy nobody declared.
    """
    changed = {
        k
        for k, v in CONTRACT_DEFAULT_POLICY.model_dump().items()
        if ConfidencePolicy().model_dump()[k] != v
    }
    assert changed == set(), (
        f"CONTRACT_DEFAULT_POLICY differs from ConfidencePolicy() in "
        f"{sorted(changed)}. The fail-closed default is the contract's, "
        f"unmodified; a deliberate adopter policy belongs in a named constant."
    )


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
