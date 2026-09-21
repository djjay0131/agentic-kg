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
