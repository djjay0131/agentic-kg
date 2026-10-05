"""Does anything route ``AUTO`` now? Measured over the real corpus.

The headline claim of ``agentic-kgis`` 0.3.0 (ADR-0024) is that the
``identity_confidence`` AUTO deadlock is fixed: before it, *nothing* could ever
route ``AUTO``, because ``ConfidencePolicy.route()`` applied an existing-identity
confidence gate to every candidate and nothing in KGIS, KGCS or kg_eval ever
produces an ``identity_confidence``. 0.3.0 adds ``IdentityDisposition``, so a
candidate that mints a *new* identity is no longer asked for confidence in a
link it does not have.

This module measures what that is worth **here**, at contract defaults, over
all 252 candidates one shadow run produces. It reports three distributions, and
they are not the same number:

``A``  through ``kgcs.policy.ResolutionPolicy`` — the producer that actually
       runs in the pipeline;
``B``  through ``ConfidencePolicy.route()`` called directly with each
       disposition — the contract, in isolation;
``C``  through ``ConfidencePolicy.route()`` with the disposition each candidate
       *kind* warrants — the best an adopter could do today.

The finding is the gap between A and C, and it is why this file exists rather
than a one-line assertion that AUTO now works.

**No threshold is lowered anywhere in this module.** Every policy is
``ConfidencePolicy()`` at its published defaults, and
:func:`test_every_policy_here_is_the_published_default` asserts it — a measured
AUTO count obtained by relaxing ``auto_min_extraction`` would be a statement
about this file, not about the platform.

This measures *routing*. It does not, and must not be read to, say anything
about extraction quality: the extraction-arm candidates' scores come from the
replay client (``replay.py``), whose ``REPLAY_CONFIDENCE`` is a declared
constant of ``0.8``, not a model's opinion. Where that constant decides a route
it is called out below.
"""

from __future__ import annotations

from collections import Counter

import pytest
from kg_contracts.candidates import AttributeAssertionCandidate, RelationCandidate
from kg_contracts.identity import is_identity_id
from kg_contracts.policy import AdjudicationRoute, ConfidencePolicy, IdentityDisposition
from kgcs.policy import ResolutionPolicy

#: The shadow corpus: eight committed papers, both arms. Pinned as a literal
#: because every distribution below is "N of CORPUS_SIZE" and a number that
#: silently changed would make the ratios unreadable across runs.
CORPUS_SIZE = 252

#: Candidates whose scores clear ``auto_min_extraction`` / ``auto_min_source_reliability``
#: at the published defaults: the 8 structured ``Paper`` entities, their 16
#: attribute assertions, and the 8 document artifacts. Everything else carries
#: the replay client's declared 0.8 / 0.75.
AUTO_ELIGIBLE_ON_SCORES = 32

#: Of those, the ones whose *kind* warrants ``NEW_IDENTITY``: the 8 structured
#: ``Paper`` entity candidates. An entity candidate mints an identity; an
#: attribute assertion attaches to one, and no resolver has run.
AUTO_AT_WARRANTED_DISPOSITION = 8


def _all(shadow_run):
    return (*shadow_run.paper_candidates, *shadow_run.candidates)


def _warranted(candidate) -> IdentityDisposition:
    """The disposition this candidate's kind actually warrants today.

    An ``entity`` candidate proposes a brand-new identity: ``NEW_IDENTITY``.

    Everything else attaches to a subject that is carried as an ``EntityRef``
    alias, not a minted identity id — no entity resolution has run in the
    shadow path, and KGIS structurally cannot run one (it holds no graph read
    surface). That is ``UNRESOLVED``, and it is the honest label:
    ``RESOLVED_EXISTING`` would claim a resolution nobody performed.
    """
    if candidate.candidate_kind == "entity":
        return IdentityDisposition.NEW_IDENTITY
    return IdentityDisposition.UNRESOLVED


def _routes(candidates, disposition=None) -> Counter:
    policy = ConfidencePolicy()
    if disposition is None:
        return Counter(policy.route(c.scores, _warranted(c)).value for c in candidates)
    return Counter(policy.route(c.scores, disposition).value for c in candidates)


# --- the corpus this is measured over -----------------------------------------


def test_the_corpus_is_the_size_every_number_below_is_relative_to(shadow_run) -> None:
    candidates = _all(shadow_run)
    assert len(candidates) == CORPUS_SIZE
    assert shadow_run.kind_counts() == {
        "artifact": 8,
        "attribute_assertion": 130,
        "entity": 114,
    }


def test_no_candidate_carries_an_identity_confidence(shadow_run) -> None:
    """The precondition of the whole deadlock, measured rather than assumed.

    ``identity_confidence`` is filled by KGCS resolution and never by ingestion.
    All 252 carry ``None`` — the "absent" the identity gate has to decide what
    to do about.
    """
    scores = [c.scores.identity_confidence for c in _all(shadow_run)]
    assert scores.count(None) == CORPUS_SIZE


def test_every_policy_here_is_the_published_default() -> None:
    """No threshold in this module is relaxed to manufacture an AUTO."""
    policy = ConfidencePolicy()
    assert policy.auto_min_extraction == 0.95
    assert policy.auto_min_source_reliability == 0.90
    assert policy.auto_min_identity_confidence == 0.95
    assert policy.auto_max_policy_risk == 0.20
    assert policy.assess_min_extraction == 0.80
    assert policy.require_identity_confidence_for_auto is True
    assert policy.allow_auto_for_new_identity is True
    assert ResolutionPolicy()._confidence_policy == policy


# --- A: the producer that actually runs ---------------------------------------


def test_the_kgcs_producer_still_routes_nothing_to_auto(shadow_run) -> None:
    """**The finding.** 0 of 252, unchanged by the re-pin.

    ``kgcs.policy.ResolutionPolicy.resolve()`` calls
    ``self._confidence_policy.route(candidate.scores)`` with **one argument**.
    ADR-0024's ``identity_disposition`` parameter therefore takes its default,
    ``RESOLVED_EXISTING`` — the disposition that still requires a stated
    ``identity_confidence`` — for every candidate, including the entity
    candidates that mint a new identity and by construction have none.

    So the deadlock is fixed in ``kg_contracts`` and **not wired into the
    producer**. Nothing outside ``kg_contracts`` references
    ``IdentityDisposition`` at all at these pins; see
    ``test_no_upstream_producer_passes_a_disposition``.
    """
    decisions = [ResolutionPolicy().resolve(c) for c in _all(shadow_run)]
    routes = Counter(d.route.value for d in decisions)

    assert routes[AdjudicationRoute.AUTO.value] == 0
    assert routes == {AdjudicationRoute.LLM_ASSESS.value: CORPUS_SIZE}


def test_the_decisions_the_producer_emits_are_unresolved(shadow_run) -> None:
    """And the block is now *stronger* than before, not weaker.

    ``ResolutionPolicy._dispose`` mints an identity only when the route is
    already ``AUTO``, so a non-AUTO entity candidate comes back with
    ``create_new_identity=False`` and ``resolved_identity=None`` — which
    ``ResolutionDecision.identity_disposition()`` reads as ``UNRESOLVED``. Under
    0.3.0 ``UNRESOLVED`` is blocked outright, so feeding the decision's own
    disposition back into ``route()`` cannot break the cycle either: the
    disposition depends on the route, and the route depends on the disposition.

    This is a *tightening*, correctly: nothing resolved these candidates.
    """
    decisions = [ResolutionPolicy().resolve(c) for c in _all(shadow_run)]
    dispositions = Counter(d.identity_disposition().value for d in decisions)
    assert dispositions == {IdentityDisposition.UNRESOLVED.value: CORPUS_SIZE}

    # Re-routing with the decision's own disposition changes nothing.
    policy = ConfidencePolicy()
    rerouted = Counter(
        policy.route(c.scores, d.identity_disposition()).value
        for c, d in zip(_all(shadow_run), decisions, strict=True)
    )
    assert rerouted[AdjudicationRoute.AUTO.value] == 0


def test_no_upstream_producer_passes_a_disposition() -> None:
    """The diagnosis, as a check rather than a claim in a docstring.

    ``ResolutionPolicy.resolve`` is the only place in the installed stack that
    calls ``ConfidencePolicy.route``, and it passes one argument. If a future
    pin wires the disposition through, this goes red and the measurement above
    needs re-taking — which is the point.
    """
    import inspect

    source = inspect.getsource(ResolutionPolicy.resolve)
    assert "_confidence_policy.route(candidate.scores)" in source, (
        "ResolutionPolicy.resolve no longer routes without a disposition - "
        "re-measure test_the_kgcs_producer_still_routes_nothing_to_auto"
    )


# --- B: the contract in isolation ---------------------------------------------


def test_the_contract_itself_can_now_route_auto(shadow_run) -> None:
    """The deadlock *is* broken one layer up: 32 of 252 at ``NEW_IDENTITY``.

    Told that these candidates mint new identities, the published policy routes
    every candidate whose extraction and source scores clear the defaults. That
    is the ADR-0024 fix doing exactly what it claims — and it is unreachable
    from the pipeline, because the pipeline never says ``NEW_IDENTITY``.
    """
    candidates = _all(shadow_run)
    new_identity = _routes(candidates, IdentityDisposition.NEW_IDENTITY)
    assert new_identity[AdjudicationRoute.AUTO.value] == AUTO_ELIGIBLE_ON_SCORES
    assert new_identity[AdjudicationRoute.LLM_ASSESS.value] == CORPUS_SIZE - AUTO_ELIGIBLE_ON_SCORES


@pytest.mark.parametrize(
    "disposition",
    [IdentityDisposition.RESOLVED_EXISTING, IdentityDisposition.UNRESOLVED],
)
def test_the_other_two_dispositions_still_route_nothing(shadow_run, disposition) -> None:
    """Both for the same reason and both correctly.

    ``RESOLVED_EXISTING`` demands a stated ``identity_confidence`` (honest-null:
    absent blocks). ``UNRESOLVED`` is blocked outright. Neither is a regression
    — they are the two cases where a missing resolution score genuinely means
    something is unknown.
    """
    routes = _routes(_all(shadow_run), disposition)
    assert routes[AdjudicationRoute.AUTO.value] == 0
    assert routes[AdjudicationRoute.LLM_ASSESS.value] == CORPUS_SIZE


# --- C: the best an adopter could do today ------------------------------------


def test_at_the_warranted_disposition_eight_candidates_route_auto(shadow_run) -> None:
    """**The other finding.** 8 of 252, and they are the structured arm.

    With each candidate given the disposition its kind actually warrants, the
    eight that route ``AUTO`` are precisely the structured ``Paper`` entities:
    a direct read of a source record, ``source_reliability=1.0`` and
    ``extraction_confidence=1.0`` by construction (``STRUCTURED_SCORING``).

    The other 244 do not, for two distinct reasons, and the distinction matters:

    * the 106 *extracted* entity candidates carry ``extraction_confidence=0.8``
      and fail ``auto_min_extraction=0.95`` on extraction alone — and 0.8 is
      ``replay.REPLAY_CONFIDENCE``, a declared constant standing in for a model
      that has not run. This number is a property of the replay fixture, **not**
      of the new platform, and would change with a real recording;
    * the 130 attribute assertions and 8 artifacts name no resolved subject, so
      ``UNRESOLVED`` blocks them regardless of score. KGCS would independently
      floor them at ``LLM_ASSESS`` for the same reason.
    """
    candidates = _all(shadow_run)
    routes = _routes(candidates)
    assert routes[AdjudicationRoute.AUTO.value] == AUTO_AT_WARRANTED_DISPOSITION
    assert routes[AdjudicationRoute.HUMAN.value] == 0
    assert sum(routes.values()) == CORPUS_SIZE

    policy = ConfidencePolicy()
    auto = [
        c
        for c in candidates
        if policy.route(c.scores, _warranted(c)) is AdjudicationRoute.AUTO
    ]
    assert {c.entity_type for c in auto} == {"Paper"}
    assert {c.producer for c in auto} == {"kgis.structured"}


def test_the_extracted_entities_are_blocked_on_extraction_not_on_identity(shadow_run) -> None:
    """Separating the two causes, so neither is credited to the other.

    Clearing the identity gate entirely still leaves the 106 extracted entity
    candidates at ``LLM_ASSESS``: their blocker is ``extraction_confidence``,
    which no amount of ADR-0024 touches. Stated as a measurement so the re-pin
    is not over-credited.
    """
    gateless = ConfidencePolicy(require_identity_confidence_for_auto=False)
    entities = [c for c in _all(shadow_run) if c.candidate_kind == "entity"]
    routes = Counter(gateless.route(c.scores).value for c in entities)
    assert routes[AdjudicationRoute.AUTO.value] == 8
    assert routes[AdjudicationRoute.LLM_ASSESS.value] == 106


def test_every_assertion_subject_is_an_unresolved_alias(shadow_run) -> None:
    """The ground for calling the assertions ``UNRESOLVED`` rather than resolved."""
    resolved = 0
    total = 0
    for candidate in _all(shadow_run):
        if not isinstance(candidate, AttributeAssertionCandidate | RelationCandidate):
            continue
        refs = (
            (candidate.subject, candidate.object)
            if isinstance(candidate, RelationCandidate)
            else (candidate.subject,)
        )
        total += 1
        if all(isinstance(r, str) and is_identity_id(r) for r in refs):
            resolved += 1
    assert total == 130
    assert resolved == 0, (
        "an assertion whose subject is already a minted identity id would "
        "warrant RESOLVED_EXISTING, and the measurement above would need redoing"
    )
