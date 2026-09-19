"""Behavioural scenarios asserted twice: once for real, once under a mutant.

A scenario lives here rather than inside a test function so that
``test_conformance_is_falsifiable.py`` can run the *same* assertions against a
deliberately broken adapter and confirm they go red. A mutation test that
re-implements the scenario it is supposed to falsify proves only that the copy
reddens; sharing the function is what makes the two halves the same check.

Not named ``test_*``, so pytest does not collect it directly.
"""

from __future__ import annotations

from collections.abc import Callable

from kg_contracts.curation import CurationOperation, CurationOperationType, Precondition
from kg_contracts.stores import GraphMutationBatch
from kg_contracts.testing.factories import make_assertion, make_entity

#: The precondition kind the reference store enforces and KGCS's planner emits.
ENTITY_VERSION = "entity_version"


def assert_entity_version_guard(make_store: Callable[[], object]) -> None:
    """A *satisfiable* ``entity_version`` precondition, end to end.

    The shared suite only ever uses an ``expected`` value that cannot hold
    (``"99"`` against a fresh identity), so it never observes the version
    *increment* — it passes identically against a store whose counter is frozen
    at zero. That gap is not academic: `kgcs.planner` guards every
    ``CREATE_IDENTITY`` with ``entity_version=0``, and the executor's
    documented "idempotent replay" property rests on the store failing that
    guard once the identity exists. A frozen counter silently converts a
    replayed creation into an identity clobber, and nothing in
    ``GraphMutationStoreContract`` notices.

    So this walks the counter: 0 → commit → the same guard now fails → the
    guard naming the *new* version commits.

    Raises:
        AssertionError: if the version counter does not advance, or a
            satisfiable guard is rejected, or a stale one is honoured.
    """
    store = make_store()
    entity = make_entity(key="version-guard")
    create = CurationOperation(
        type=CurationOperationType.CREATE_IDENTITY, payload=entity.model_dump(mode="json")
    )
    fresh_identity = Precondition(kind=ENTITY_VERSION, subject=entity.identity_id, expected="0")

    # 1. A guard that *holds* must commit. If this fails the test is vacuous —
    #    everything below would "pass" by refusing every batch.
    first = store.apply(  # type: ignore[attr-defined]
        GraphMutationBatch(plan_id="pl_v1", operations=(create,)),
        preconditions=(fresh_identity,),
    )
    assert first.committed is True, (
        f"a satisfiable entity_version=0 guard was rejected: {first.error!r} "
        f"{first.failed_preconditions!r}"
    )

    # 2. Replay. The identity now exists, so the same guard must NOT hold.
    #    This is the assertion a frozen version counter turns red.
    replay = store.apply(  # type: ignore[attr-defined]
        GraphMutationBatch(plan_id="pl_v2", operations=(create,)),
        preconditions=(fresh_identity,),
    )
    assert replay.committed is False, (
        "a replayed CREATE_IDENTITY passed its entity_version=0 guard - the "
        "version counter did not advance, so the store would clobber an "
        "existing identity on replay"
    )
    assert replay.failed_preconditions == (fresh_identity,)

    # 3. The counter advanced by exactly one, so a guard naming 1 holds.
    assertion = make_assertion(subject_identity=entity.identity_id, predicate="after_v1")
    at_version_one = Precondition(kind=ENTITY_VERSION, subject=entity.identity_id, expected="1")
    third = store.apply(  # type: ignore[attr-defined]
        GraphMutationBatch(
            plan_id="pl_v3",
            operations=(
                CurationOperation(
                    type=CurationOperationType.ATTACH_ASSERTION,
                    payload=assertion.model_dump(mode="json"),
                ),
            ),
        ),
        preconditions=(at_version_one,),
    )
    assert third.committed is True, (
        f"entity_version=1 should hold after exactly one commit touching the "
        f"subject: {third.error!r} {third.failed_preconditions!r}"
    )

    # 4. And attaching an assertion bumps the subject's version too, so the
    #    same guard is now stale.
    stale = store.apply(  # type: ignore[attr-defined]
        GraphMutationBatch(
            plan_id="pl_v4",
            operations=(
                CurationOperation(
                    type=CurationOperationType.ATTACH_ASSERTION,
                    payload=make_assertion(
                        subject_identity=entity.identity_id, predicate="after_v2"
                    ).model_dump(mode="json"),
                ),
            ),
        ),
        preconditions=(at_version_one,),
    )
    assert stale.committed is False, (
        "ATTACH_ASSERTION did not advance its subject's version, so an "
        "optimistic-concurrency guard computed against the old version still "
        "passes"
    )
