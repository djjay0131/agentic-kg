"""The bounded-adviser adjudication stage: laws 1, 13, 16, replay, flag-off.

Five properties, each with the failure it exists to refuse:

* **Flag-off is the old pipeline, byte for byte.** The stage is OFF by default;
  with it off the plan, the committed ids and the deferrals must be identical to
  a run that never heard of it. Asserted on plan JSON, not on a count.
* **Law 1 — the deterministic baseline survives every LLM failure.** A failing,
  timeouting or malformed adviser is turned into an abstain by KGCS, the gate
  holds, and the plan is byte-identical to the flag-off run.
* **Law 13 — advice cannot flip a reject.** Eligibility is read off the
  deterministic ``ResolutionDecision``; a candidate the validator rejected has no
  resolution and is never shown to an adviser, however loudly the adviser admits.
* **Law 16 — advisers have no write surface.** The adviser returns only an
  ``AdviserAssessment``; nothing the stage returns is or holds a
  ``GraphMutationStore``; and the source names no mutation verb.
* **Replay determinism.** Two runs through recorded fixtures produce
  byte-identical plans.

Plus the headline: with the SYNTHETIC recordings the stage promotes the deferred
graded entities, so ``curated_arm`` becomes gradable instead of an honest null.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from agentic_kg.migration.config import MigrationConfig
from agentic_kg.migration.curation import (
    CONTRACT_DEFAULT_POLICY,
    AdjudicationRequiresAdviser,
    AdmissionPolicy,
    CandidateAdmissionAdviser,
    SyntheticAdmissionOracle,
    adjudicate_llm_assess_candidates,
    admission_question,
    curated_arm,
    curation_engine,
    load_recorded_adviser,
    run_curation,
)
from agentic_kg.migration.curation.adjudication import _gate
from kg_contracts.candidates import EntityCandidate
from kg_contracts.stores import GraphMutationStore
from kg_contracts.testing.memory import MemoryGraphStore
from kgcs.advisers import (
    AdviserAssessment,
    FailingCompletionClient,
    MalformedCompletionClient,
    StructuredAdviser,
    TimeoutCompletionClient,
)
from kgcs.ids import DerivedIdFactory

from ._synthetic import graded_entity_candidate


def _store() -> MemoryGraphStore:
    """A pristine store per run: reusing one makes a replay come back ``STALE``."""
    return MemoryGraphStore()

FIXTURES = Path(__file__).parent / "fixtures"
RECORDINGS = FIXTURES / "admission_fixtures.synthetic.json"

#: The corpus has this many entity candidates routed ``LLM_ASSESS``. Pinned so a
#: change to the extractor or the policy fails here rather than silently making
#: the committed recording stale.
EXPECTED_ELIGIBLE = 106


def _adjudicated_config() -> MigrationConfig:
    return MigrationConfig(
        use_kgis_ingestion=True, use_kgcs_resolution=True, use_kgcs_adjudication=True
    )


def _resolve(candidates):
    """The deterministic engine result over ``candidates`` (no store, no write)."""
    return curation_engine().curate(candidates)


def _adjudicate(candidates, adviser, *, policy=None):
    return adjudicate_llm_assess_candidates(
        candidates,
        _resolve(candidates),
        adviser=adviser,
        policy=policy or AdmissionPolicy(),
        id_factory=DerivedIdFactory(),
        snapshot_version="0",
    )


def _run(candidates, *, config, store=None, adviser=None, policy=None):
    return run_curation(
        candidates,
        config=config,
        store=store,
        confidence_policy=CONTRACT_DEFAULT_POLICY,
        adviser=adviser,
        admission_policy=policy,
    )


def test_flag_off_is_byte_identical_to_today(shadow_candidates, memory_store) -> None:
    """The stage does not exist unless the flag says so.

    Two runs: one with no adjudication argument at all, one with the flag off
    but an adviser supplied. Both must be identical — proving the flag, not the
    argument's presence, is what gates the stage.
    """
    baseline = _run(
        shadow_candidates,
        config=MigrationConfig(use_kgis_ingestion=True, use_kgcs_resolution=True),
        store=_store(),
    )
    flag_off = _run(
        shadow_candidates,
        config=MigrationConfig(
            use_kgis_ingestion=True,
            use_kgcs_resolution=True,
            use_kgcs_adjudication=False,
        ),
        store=_store(),
        adviser=CandidateAdmissionAdviser(port=SyntheticAdmissionOracle()),
    )
    assert flag_off.plan is not None and baseline.plan is not None
    assert flag_off.plan.model_dump_json() == baseline.plan.model_dump_json()
    assert flag_off.committed_candidate_ids == baseline.committed_candidate_ids
    assert flag_off.adjudication is None


@pytest.mark.parametrize(
    "client",
    [
        FailingCompletionClient(),
        TimeoutCompletionClient(),
        MalformedCompletionClient(),
    ],
    ids=["failing", "timeout", "malformed"],
)
def test_law1_baseline_survives_every_adviser_failure(
    shadow_candidates, memory_store, client
) -> None:
    """Law 1: an LLM failure cannot move the deterministic plan.

    The malformed client's transport succeeds but its payload does not parse —
    the case a naive "did the call raise?" check would miss. All three must
    abstain, hold every candidate, and leave the plan byte-identical to the
    flag-off run.
    """
    baseline = _run(
        shadow_candidates,
        config=MigrationConfig(use_kgis_ingestion=True, use_kgcs_resolution=True),
        store=_store(),
    )
    result = _run(
        shadow_candidates,
        config=_adjudicated_config(),
        store=_store(),
        adviser=CandidateAdmissionAdviser(port=client),
    )
    assert result.adjudication is not None
    assert result.adjudication.admitted == 0
    assert result.adjudication.promoted == ()
    assert result.adjudication.decisions, "the adviser was never consulted"
    assert all(
        d.assessment is not None and d.assessment.abstained
        for d in result.adjudication.decisions
    )
    assert result.plan is not None and baseline.plan is not None
    assert result.plan.model_dump_json() == baseline.plan.model_dump_json()
    assert result.committed_candidate_ids == baseline.committed_candidate_ids


def test_law13_advice_cannot_flip_a_reject(memory_store) -> None:
    """A rejected candidate is unreachable to the adviser, whatever it advises.

    The candidate is bound to another graph, so validation rejects it and the
    engine records no ``ResolutionDecision``. With an admit-everything oracle
    the stage still has nothing to consult and nothing to promote.
    """
    rejected = graded_entity_candidate().model_copy(update={"graph_id": "other-graph"})
    result = _run(
        [rejected],
        config=_adjudicated_config(),
        store=memory_store,
        adviser=CandidateAdmissionAdviser(port=SyntheticAdmissionOracle()),
    )
    assert result.adjudication is not None
    assert result.adjudication.eligible == 0
    assert result.adjudication.promoted == ()
    assert [d.candidate_id for d in result.rejected] == [rejected.candidate_id]
    assert rejected.candidate_id not in result.committed_candidate_ids


def test_law13_adviser_is_never_shown_an_auto_candidate(shadow_candidates) -> None:
    """The stage consults only what the deterministic policy deferred.

    Reading eligibility off the policy decisions (not the candidate) means the
    eight auto-applied Paper identities are never re-litigated.
    """
    engine_result = _resolve(shadow_candidates)
    auto_ids = {
        outcome.candidate_id
        for outcome in engine_result.outcomes
        if outcome.resolution is not None and outcome.resolution.route.value == "AUTO"
    }
    result = _adjudicate(
        shadow_candidates, CandidateAdmissionAdviser(port=SyntheticAdmissionOracle())
    )
    consulted = {d.candidate_id for d in result.decisions}
    assert auto_ids, "the corpus stopped auto-routing; this test is now vacuous"
    assert consulted.isdisjoint(auto_ids)


def test_law16_advisers_have_no_write_surface(shadow_candidates) -> None:
    """Structural: the adviser and everything the stage returns is write-free."""
    adviser = CandidateAdmissionAdviser(port=SyntheticAdmissionOracle())
    assert isinstance(adviser, StructuredAdviser)
    for name in dir(adviser):
        if name.startswith("__"):
            continue
        assert not isinstance(getattr(adviser, name), GraphMutationStore)

    result = _adjudicate(shadow_candidates, adviser)
    for returned in (result, *result.decisions, *result.promoted):
        assert not isinstance(returned, GraphMutationStore)
        for name in dir(returned):
            if name.startswith("__"):
                continue
            assert not isinstance(getattr(returned, name), GraphMutationStore)


def test_law16_the_adjudication_source_names_no_mutation_verb() -> None:
    """Syntactic: the adviser machinery source holds no store and no plan write.

    An AST walk over the two modules this stage adds. The existing
    ``test_no_application_write_surface.py`` already proves no module in this
    subpackage takes a store alias outside ``pipeline``/``rollback``; this pins
    the narrower claim the law names — the *adviser* path in particular has no
    mutation surface.
    """
    forbidden_names = {"GraphMutationStore", "PlanExecutor"}
    forbidden_attrs = {"apply"}
    modules = ("adjudication.py", "clients.py")
    for module in modules:
        source = (
            Path(__file__).parents[3]
            / "src/agentic_kg/migration/curation"
            / module
        )
        tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                assert node.id not in forbidden_names, f"{module}: names {node.id}"
            if isinstance(node, ast.Attribute):
                assert node.attr not in forbidden_attrs, f"{module}: calls .{node.attr}"
    assert len(modules) == 2


def test_replay_is_byte_identical(shadow_candidates, memory_store) -> None:
    """Two runs over the recorded fixtures produce the same plan bytes."""
    config = _adjudicated_config()
    first = _run(
        shadow_candidates,
        config=config,
        store=_store(),
        adviser=load_recorded_adviser(RECORDINGS),
    )
    second = _run(
        shadow_candidates,
        config=config,
        store=_store(),
        adviser=load_recorded_adviser(RECORDINGS),
    )
    assert first.plan is not None and second.plan is not None
    assert first.plan.model_dump_json() == second.plan.model_dump_json()
    assert first.committed_candidate_ids == second.committed_candidate_ids
    assert first.adjudication is not None and second.adjudication is not None
    first_assessments = [
        d.assessment.model_dump_json()
        for d in first.adjudication.decisions
        if d.assessment is not None
    ]
    second_assessments = [
        d.assessment.model_dump_json()
        for d in second.adjudication.decisions
        if d.assessment is not None
    ]
    assert first_assessments == second_assessments


def test_the_committed_recording_covers_every_question(shadow_candidates) -> None:
    """The replay client asks exactly the recorded questions, no more, no fewer.

    A missing fixture surfaces as ``CompletionMiss`` — the loud wiring error —
    so a run over the whole corpus with the committed recordings is the coverage
    assertion.
    """
    result = _adjudicate(shadow_candidates, load_recorded_adviser(RECORDINGS))
    assert result.eligible == EXPECTED_ELIGIBLE, (
        "the number of eligible entity candidates moved; regenerate the "
        "recordings with scripts/record_admission_fixtures.py if that is intended"
    )


def _assessment(
    recommendation: str,
    *,
    evidence_ids: tuple[str, ...] = ("ev_x",),
    confidence: float | None = 0.99,
    abstained: bool = False,
) -> AdviserAssessment:
    return AdviserAssessment(
        adviser_type="test",
        adviser_version="1",
        model_id="test",
        model_version="1",
        prompt_version="1",
        recommendation=recommendation,
        evidence_ids=evidence_ids,
        confidence=confidence,
        abstained=abstained,
    )


def test_the_gate_holds_a_low_or_uncited_admission() -> None:
    """The gate's conservative edges, unit-tested without the corpus.

    Four crafted assessments: a low-confidence admit, an admit with no citation,
    a plain hold, and a valid admit. The first three must not promote — evidence
    and confidence are the difference between advice and a write.
    """
    policy = AdmissionPolicy(min_confidence=0.5, require_cited_evidence=True)
    low = _gate(policy, "c1", _assessment("admit", confidence=0.2))
    uncited = _gate(policy, "c2", _assessment("admit", evidence_ids=()))
    hold = _gate(policy, "c3", _assessment("hold"))
    abstained = _gate(policy, "c4", _assessment("hold", abstained=True, confidence=None))
    admit = _gate(policy, "c5", _assessment("admit"))
    assert [low.admitted, uncited.admitted, hold.admitted, abstained.admitted] == [
        False,
        False,
        False,
        False,
    ]
    assert admit.admitted is True


def test_the_synthetic_stage_makes_the_new_arm_gradable(
    shadow_candidates, doi_to_slug, memory_store
) -> None:
    """The headline: SYNTHETIC replay promotes the deferred graded entities.

    This is the wiring assertion the whole PR exists for — the `new` arm goes
    from an honest null to a graded arm. It says **nothing** about whether the
    entities should be promoted; the oracle admits every candidate, which is
    exactly why the numbers are labelled SYNTHETIC.
    """
    result = _run(
        shadow_candidates,
        config=_adjudicated_config(),
        store=memory_store,
        adviser=load_recorded_adviser(RECORDINGS),
    )
    assert result.adjudication is not None
    assert result.adjudication.admitted == result.adjudication.eligible > 0

    arm = curated_arm(result, shadow_candidates, doi_to_slug=doi_to_slug)
    assert arm.available, f"the arm is still an honest null: {arm.reason}"
    assert arm.graded_committed > 0
    assert arm.graded_deferred == 0, "the stage left graded entities deferred"
    assert arm.caveat is None


def test_enabling_adjudication_without_an_adviser_raises(shadow_candidates) -> None:
    """A silent skip would be indistinguishable from a model that held everything."""
    with pytest.raises(AdjudicationRequiresAdviser):
        _run(shadow_candidates, config=_adjudicated_config())


def test_the_adjudication_flag_reads_the_environment(monkeypatch) -> None:
    """The flag is the third independent switch, and defaults off."""
    from agentic_kg.migration.config import MigrationConfig

    monkeypatch.delenv("KGCS_ADJUDICATION_ENABLED", raising=False)
    assert MigrationConfig().use_kgcs_adjudication is False
    assert MigrationConfig().is_default is True

    monkeypatch.setenv("KGCS_ADJUDICATION_ENABLED", "yes")
    enabled = MigrationConfig()
    assert enabled.use_kgcs_adjudication is True
    # It is independent of the other two, but counted by the aggregate.
    assert enabled.any_enabled is True
    assert enabled.use_kgcs_resolution is False


def test_adjudication_without_resolution_is_refused(shadow_candidates) -> None:
    """The stage consumes deterministic routing, so it cannot run without it."""
    from agentic_kg.migration.curation import CurationDisabled

    with pytest.raises(CurationDisabled):
        run_curation(
            shadow_candidates,
            config=MigrationConfig(
                use_kgis_ingestion=True,
                use_kgcs_resolution=False,
                use_kgcs_adjudication=True,
            ),
            adviser=CandidateAdmissionAdviser(port=SyntheticAdmissionOracle()),
        )


def test_the_question_is_a_pure_function_of_the_candidate() -> None:
    """Same candidate, same request key — evidence order preserved."""
    candidate: EntityCandidate = graded_entity_candidate()
    question = admission_question(candidate)
    assert question.subject == candidate.semantic_key
    assert question.evidence_ids == tuple(
        ref.evidence_id for ref in candidate.evidence_refs
    )
    first = CandidateAdmissionAdviser(port=SyntheticAdmissionOracle()).build_request(question)
    second = CandidateAdmissionAdviser(port=SyntheticAdmissionOracle()).build_request(
        question
    )
    assert first.request_hash == second.request_hash
