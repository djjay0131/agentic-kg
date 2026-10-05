"""The relaxed identity gate is constrained to the case it was argued for.

``STRUCTURED_IDENTITY_POLICY`` turns off
``ConfidencePolicy.require_identity_confidence_for_auto`` — the fail-closed
guard against auto-minting a duplicate identity when no entity resolution has
run. The argument for turning it off was candidate-level ("a DOI settles which
paper this is"); the policy it lives on is **batch-wide**.

An independent reviewer closed that gap by demonstrating it rather than arguing
it: two ``Topic`` candidates for one concept, ``identity_confidence=None``, two
irreversible identities minted, ``roll_back`` returning ``plan=None`` with both
operations non-compensable. Nothing enforced the justification — the only thing
holding the line was the LLM extractor's 0.8/0.75 scores happening to keep
graded entities away from ``AUTO``, which is a coincidence of the corpus, not a
property of the design.

So the justification is now a rule: with the gate off, a candidate that would
mint an identity must carry an alias in a **registered-identifier** namespace,
and ``run_curation`` refuses the whole run otherwise — before anything is
executed, plan-only runs included, because a plan-only run still hands back
``CREATE_IDENTITY`` operations a caller could apply itself.

Every test below names which half it checks: that the rule fires on the unsafe
case, or that it does **not** fire on the safe one. Both halves are necessary —
a guard that refused everything would satisfy the first and make the policy
useless.
"""

from __future__ import annotations

import pytest
from agentic_kg.migration.config import MigrationConfig
from agentic_kg.migration.curation import (
    CONTRACT_DEFAULT_POLICY,
    REGISTERED_IDENTIFIER_NAMESPACES,
    STRUCTURED_IDENTITY_POLICY,
    UnsafeIdentityRelaxation,
    curation_engine,
    run_curation,
    unkeyed_new_identities,
)
from kg_contracts.policy import ConfidencePolicy
from kg_contracts.testing.factories import make_scores

from ._synthetic import graded_entity_candidate
from .test_pipeline import ExplodingStore


def unkeyed_scores():
    """High extraction confidence, and **no** identity confidence at all.

    This is the score set every KGIS-produced candidate really has — nothing in
    the chain writes ``identity_confidence`` — with the extraction scores raised
    so the candidate clears the ``AUTO`` thresholds once the identity gate is
    off. It is the exact shape of the reviewer's demonstration.
    """
    return make_scores(
        extraction_confidence=0.99,
        source_reliability=0.99,
        identity_confidence=None,
        policy_risk=0.0,
    )


def one_concept_two_candidates():
    """Two candidates naming the same concept, as two extractions would."""
    return [
        graded_entity_candidate(surface="Knowledge Graphs", key=key).model_copy(
            update={"scores": unkeyed_scores()}
        )
        for key in ("kg1", "kg2")
    ]


def enabled() -> MigrationConfig:
    return MigrationConfig(use_kgcs_resolution=True)


# --------------------------------------------------------------------------
# The rule fires on the unsafe case
# --------------------------------------------------------------------------


def test_the_reviewers_two_topic_demonstration_is_now_refused() -> None:
    """The exact case that minted two irreversible identities is rejected."""
    candidates = one_concept_two_candidates()
    with pytest.raises(UnsafeIdentityRelaxation) as excinfo:
        run_curation(
            candidates,
            config=enabled(),
            confidence_policy=STRUCTURED_IDENTITY_POLICY,
        )
    message = str(excinfo.value)
    assert "Topic" in message
    assert "2 candidate(s)" in message
    assert "CREATE_IDENTITY has no inverse" in message


def test_the_refusal_happens_before_the_store_is_touched() -> None:
    """Fail-closed: no operation reaches the canonical graph.

    ``ExplodingStore`` fails the test if ``apply`` is called at all, so this
    cannot be satisfied by a write that happened and was rolled back.
    """
    store = ExplodingStore()
    with pytest.raises(UnsafeIdentityRelaxation):
        run_curation(
            one_concept_two_candidates(),
            config=enabled(),
            store=store,
            confidence_policy=STRUCTURED_IDENTITY_POLICY,
        )
    assert store.apply_calls == 0


def test_a_plan_only_run_is_refused_too() -> None:
    """No store is not the same as no danger.

    A plan-only run returns ``CREATE_IDENTITY`` operations, and a caller holding
    a plan can apply it through an executor of its own. Refusing only when a
    store was passed would put the guard on the wrong side of the seam.
    """
    with pytest.raises(UnsafeIdentityRelaxation):
        run_curation(
            one_concept_two_candidates(),
            config=enabled(),
            confidence_policy=STRUCTURED_IDENTITY_POLICY,
        )


def test_the_guard_keys_on_the_policy_field_not_on_the_named_constant() -> None:
    """An ad-hoc relaxed policy is guarded identically.

    Keying on ``policy is STRUCTURED_IDENTITY_POLICY`` would be defeated by one
    line of caller code constructing the same thing. The rule reads the
    ``require_identity_confidence_for_auto`` field.
    """
    ad_hoc = ConfidencePolicy(require_identity_confidence_for_auto=False)
    assert ad_hoc is not STRUCTURED_IDENTITY_POLICY
    with pytest.raises(UnsafeIdentityRelaxation):
        run_curation(
            one_concept_two_candidates(), config=enabled(), confidence_policy=ad_hoc
        )


# --------------------------------------------------------------------------
# The rule does NOT fire on the safe case
# --------------------------------------------------------------------------


def test_doi_keyed_paper_identities_are_still_admitted(
    shadow_candidates: tuple[object, ...], memory_store: object
) -> None:
    """The control. A guard that refused everything would be useless.

    The eight corpus ``Paper`` identities are keyed by DOI — a registry settles
    which paper each is — so the relaxation applies to them exactly as argued,
    and the run still commits.
    """
    result = run_curation(
        shadow_candidates,
        config=enabled(),
        store=memory_store,
        confidence_policy=STRUCTURED_IDENTITY_POLICY,
    )
    assert result.committed
    assert result.operation_counts() == {"CREATE_IDENTITY": 8}


def test_the_contract_default_policy_is_unaffected() -> None:
    """With the gate on, the guard is inert — the gate is already the guard.

    The same two candidates curate without a refusal, because nothing routes
    ``AUTO`` and no identity is minted. Scoping matters: a guard that fired
    under the default policy would break every run in this repo.
    """
    candidates = one_concept_two_candidates()
    result = run_curation(
        candidates, config=enabled(), confidence_policy=CONTRACT_DEFAULT_POLICY
    )
    assert result.plan is None
    assert len(result.deferred) == 2


def test_the_detector_reports_nothing_for_a_registry_keyed_candidate(
    shadow_candidates: tuple[object, ...]
) -> None:
    """The detector itself discriminates, checked without going through the raise.

    ``test_doi_keyed_paper_identities_are_still_admitted`` passes if the guard
    never fires *for any reason*, including a broken detector that always
    returns empty. This drives the detector on both inputs and requires it to
    separate them.
    """
    engine = curation_engine(confidence_policy=STRUCTURED_IDENTITY_POLICY)
    safe = engine.curate(shadow_candidates)
    assert unkeyed_new_identities(
        shadow_candidates, safe, STRUCTURED_IDENTITY_POLICY
    ) == ()

    unsafe_candidates = one_concept_two_candidates()
    unsafe = curation_engine(
        confidence_policy=STRUCTURED_IDENTITY_POLICY
    ).curate(unsafe_candidates)
    flagged = unkeyed_new_identities(
        unsafe_candidates, unsafe, STRUCTURED_IDENTITY_POLICY
    )
    assert [c.entity_type for c in flagged] == ["Topic", "Topic"]


def test_the_namespace_allowlist_is_what_admits_the_paper_arm() -> None:
    """The rule is a registry allowlist, not an entity-type allowlist.

    Stated as a test because the distinction is the whole argument: ``Paper`` is
    admitted because its alias namespace is a registry, not because of its type
    name. A ``Paper`` arriving without a DOI alias is refused, which an
    entity-type allowlist would wave through.
    """
    assert "doi" in REGISTERED_IDENTIFIER_NAMESPACES

    unkeyed_paper = graded_entity_candidate(
        surface="Some Paper", entity_type="Paper", key="p9"
    ).model_copy(update={"scores": unkeyed_scores()})
    assert all(a.namespace != "doi" for a in unkeyed_paper.aliases)

    with pytest.raises(UnsafeIdentityRelaxation) as excinfo:
        run_curation(
            [unkeyed_paper],
            config=enabled(),
            confidence_policy=STRUCTURED_IDENTITY_POLICY,
        )
    assert "Paper" in str(excinfo.value)


# --------------------------------------------------------------------------
# The four ways an independent reviewer walked through the first version
# --------------------------------------------------------------------------
#
# The first version checked only that *some* alias carried a registered
# namespace. Each test below is one of the reviewer's attacks, and each names
# which of the four checks now stops it: namespace, entity type, key spelling,
# and — the one that mattered — uniqueness within the batch.


def doi_keyed(entity_type: str = "Paper", *, doi: str, key: str) -> object:
    """A candidate whose alias claims the DOI registry.

    ``entity_type`` is a parameter because the attacks turn on the combination:
    a DOI-namespaced alias on a ``Topic`` is the whole of attack A.
    """
    from kg_contracts.candidates import EntityCandidate, SourceCoordinates
    from kg_contracts.identity import EntityRef

    from ._synthetic import GRAPH_ID

    return EntityCandidate(
        graph_id=GRAPH_ID,
        producer="test-producer",
        producer_run_id="run-synthetic",
        ontology_version="1",
        source_coordinates=SourceCoordinates(source_type="paper", locator="paper://doi/x"),
        semantic_key=f"{entity_type.lower()}/{key}",
        scores=unkeyed_scores(),
        entity_type=entity_type,
        aliases=(EntityRef(entity_type=entity_type, namespace="doi", key=doi),),
        display_name=key,
        properties={},
    )


@pytest.mark.parametrize(
    ("entity_type", "doi", "why"),
    [
        ("Topic", "banana", "attack A: a key the DOI registry could never issue"),
        ("Topic", "  not-a-doi  ", "attack B: whitespace around a non-DOI"),
        ("Topic", "10.1007/978-3-031-19433-7_39", "attack D: a real DOI on a Topic"),
        # The type check alone refuses the three above, because all three are
        # Topics. These two are Papers — the type the DOI registry *does*
        # identify — so only the key-spelling check can refuse them. Without
        # them, mutating the pattern check away left every parameter green.
        ("Paper", "banana", "attack A on the right type: only the spelling refuses it"),
        ("Paper", "  10.1007/x  ", "attack B on the right type"),
        ("Paper", "10.1007/x\n", "a trailing newline, which `$` would have admitted"),
    ],
    ids=[
        "nonsense-key",
        "whitespace-key",
        "real-doi-wrong-type",
        "nonsense-key-right-type",
        "whitespace-key-right-type",
        "trailing-newline-key",
    ],
)
def test_a_doi_namespace_alone_no_longer_admits_a_candidate(
    entity_type: str, doi: str, why: str
) -> None:
    """Namespace is necessary and not sufficient — measured, not argued.

    All three passed the first version of this guard. A namespace is a *claim*
    about which registry settles an identity; a key that registry could not have
    issued is not that identity, and a registry that identifies works says
    nothing about whether two topics are one topic.
    """
    with pytest.raises(UnsafeIdentityRelaxation):
        run_curation(
            [doi_keyed(entity_type, doi=doi, key="t1")],
            config=enabled(),
            confidence_policy=STRUCTURED_IDENTITY_POLICY,
        )


def test_a_real_doi_on_a_paper_is_still_admitted() -> None:
    """The control. Tightening the check must not close the case it exists for."""
    result = run_curation(
        [doi_keyed("Paper", doi="10.1007/978-3-031-19433-7_39", key="p1")],
        config=enabled(),
        confidence_policy=STRUCTURED_IDENTITY_POLICY,
    )
    assert result.operation_counts() == {"CREATE_IDENTITY": 1}


def test_two_papers_with_the_same_doi_are_refused_not_duplicated() -> None:
    """**Attack C.** The original defect, reproduced through its own fix.

    Two candidates carrying the identical DOI each minted an identity —
    irreversibly, since ``CREATE_IDENTITY`` has no inverse — because
    ``DerivedIdFactory.identity_id`` keys on ``candidate_id`` and nothing
    dedupes. ``policy.py`` stated "two candidates carrying the same DOI are the
    same paper" as the entire content of the relaxation's justification, and the
    pipeline did not act on it.

    No existing test could have caught this: the corpus's eight DOIs are all
    distinct, so the claim was quantified over an empty set. **This corpus
    contains a repeated DOI**, which is the point of it.
    """
    doi = "10.1007/978-3-031-19433-7_39"
    batch = [doi_keyed("Paper", doi=doi, key="p1"), doi_keyed("Paper", doi=doi, key="p2")]

    with pytest.raises(UnsafeIdentityRelaxation) as excinfo:
        run_curation(batch, config=enabled(), confidence_policy=STRUCTURED_IDENTITY_POLICY)
    message = str(excinfo.value)
    assert "doi:" + doi in message
    assert "claimed by 2 candidates" in message
    assert "CREATE_IDENTITY has no inverse" in message


def test_the_same_doi_in_different_case_is_the_same_identifier() -> None:
    """DOIs are case-insensitive, and this corpus exercises it.

    The importer emitted ``10.1109/ACCESS...`` where the curation table says
    ``10.1109/access...``. A duplicate check that compared raw bytes would let
    exactly that pair through — two identities for one paper, on a difference
    the DOI system says does not exist.
    """
    batch = [
        doi_keyed("Paper", doi="10.1109/ACCESS.2022.3220241", key="p1"),
        doi_keyed("Paper", doi="10.1109/access.2022.3220241", key="p2"),
    ]
    with pytest.raises(UnsafeIdentityRelaxation):
        run_curation(batch, config=enabled(), confidence_policy=STRUCTURED_IDENTITY_POLICY)


def test_two_papers_with_different_dois_are_not_refused() -> None:
    """The control for the duplicate rule: distinct identifiers still commit.

    Without this, the rule above would be satisfied by a check that refused any
    batch with two papers in it — which is every real batch.
    """
    batch = [
        doi_keyed("Paper", doi="10.1007/978-3-031-19433-7_39", key="p1"),
        doi_keyed("Paper", doi="10.1038/s41597-025-05200-8", key="p2"),
    ]
    result = run_curation(
        batch, config=enabled(), confidence_policy=STRUCTURED_IDENTITY_POLICY
    )
    assert result.operation_counts() == {"CREATE_IDENTITY": 2}


def test_the_duplicate_rule_is_inert_under_the_contract_default() -> None:
    """With the gate on nothing mints, so there is nothing to duplicate."""
    doi = "10.1007/978-3-031-19433-7_39"
    batch = [doi_keyed("Paper", doi=doi, key="p1"), doi_keyed("Paper", doi=doi, key="p2")]
    result = run_curation(
        batch, config=enabled(), confidence_policy=CONTRACT_DEFAULT_POLICY
    )
    assert result.plan is None


def test_the_real_corpus_has_distinct_dois_which_is_why_it_missed_this() -> None:
    """Named so the gap is recorded, not just closed.

    The control corpus cannot exercise the duplicate rule, and a reader who sees
    it pass should not conclude the rule was tested. It was tested by the
    synthetic batch above; this asserts *why* the corpus could not do it.
    """
    from agentic_kg.migration.ingestion.corpus import load_corpus

    dois = [paper.doi.casefold() for paper in load_corpus()]
    assert len(dois) == 8
    assert len(set(dois)) == 8, (
        "the corpus now repeats a DOI; the duplicate rule is no longer quantified "
        "over an empty set there, and this test's premise needs rewriting"
    )


def test_the_gate_being_on_is_itself_the_guard() -> None:
    """The short-circuit that keeps this whole rule off the default path.

    ``_minting`` returns nothing while ``require_identity_confidence_for_auto``
    is on, and that early exit is **not** redundant with the
    ``create_new_identity`` filter: a candidate that carries a real
    ``identity_confidence`` routes ``AUTO`` and mints *with the gate on*, and it
    is entitled to — the gate is the ER-equivalent guard, so the
    registered-identifier rule must not also apply. A surface-keyed candidate
    minting under the contract default is exactly that case, and removing the
    short-circuit refuses it.
    """
    from ._synthetic import graded_entity_candidate

    candidate = graded_entity_candidate()
    assert all(alias.namespace != "doi" for alias in candidate.aliases)
    assert candidate.scores.identity_confidence == 0.99

    result = run_curation(
        [candidate], config=enabled(), confidence_policy=CONTRACT_DEFAULT_POLICY
    )
    assert result.operation_counts() == {"CREATE_IDENTITY": 1}
