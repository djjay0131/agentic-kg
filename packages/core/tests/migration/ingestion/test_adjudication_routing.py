"""Does anything route ``AUTO`` now? Re-measured over the real corpus.

The headline claim of ``agentic-kgis`` 0.3.0 (ADR-0024) was that the
``identity_confidence`` AUTO deadlock is fixed: before it, *nothing* could ever
route ``AUTO``, because ``ConfidencePolicy.route()`` applied an existing-identity
confidence gate to every candidate and nothing in KGIS, KGCS or kg_eval ever
produces an ``identity_confidence``. 0.3.0 added ``IdentityDisposition``, so a
candidate that mints a *new* identity is no longer asked for confidence in a
link it does not have.

At the **previous** pins that fix was inert here:
``kgcs.policy.ResolutionPolicy.resolve`` still called ``route(scores)`` with one
argument, so
every candidate took the default ``RESOLVED_EXISTING`` disposition and the
corpus measured **0 of 252 AUTO**. Agentic-kgcs #44 (727df56) derives the
disposition *before* routing, and this module re-measures the corpus under the
unmodified contract defaults. It reports three distributions:

``A``  through ``kgcs.policy.ResolutionPolicy`` — the producer that actually
       runs in the pipeline;
``B``  through ``ConfidencePolicy.route()`` called directly with each
       disposition — the contract, in isolation;
``C``  through ``ConfidencePolicy.route()`` with the disposition each candidate
       *kind* warrants, derived here independently of the producer.

**A and C now agree at 8 of 252.** The eight are the DOI-keyed structured
``Paper`` identities; the gap this file used to report is closed, and the
remaining work is not routing — the extracted graded entities are blocked on
their *extraction* scores, not on identity.

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
    """The disposition this candidate's kind warrants, derived here by kind.

    This is the adopter's independent reading of ADR-0022, **not** a call to
    the producer — deriving it separately is what lets the module assert that
    the producer now agrees, rather than restating it.

    * An ``entity`` candidate proposes a brand-new identity: ``NEW_IDENTITY``.
    * An ``artifact`` names no identity and no fact, so it keeps the
      pre-ADR-0024 default ``RESOLVED_EXISTING`` — the gate is applied to it as
      it always was.
    * A ``relation`` / ``attribute_assertion`` attaches to a subject carried as
      an ``EntityRef`` alias, not a minted identity id — no entity resolution
      has run in the shadow path, and KGIS structurally cannot run one (it
      holds no graph read surface). That is ``UNRESOLVED``, and it is the
      honest label: ``RESOLVED_EXISTING`` would claim a resolution nobody
      performed.
    """
    if candidate.candidate_kind == "entity":
        return IdentityDisposition.NEW_IDENTITY
    if candidate.candidate_kind == "artifact":
        return IdentityDisposition.RESOLVED_EXISTING
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


def test_the_kgcs_producer_now_routes_the_structured_arm_auto(shadow_run) -> None:
    """**The finding.** 8 of 252 through the producer, up from 0.

    ``ResolutionPolicy.resolve`` now derives the candidate's
    ``IdentityDisposition`` *before* routing (agentic-kgcs #44, ADR-0022), so
    the entity candidates that clear the unmoved extraction thresholds and mint
    on an absent ``identity_confidence`` are no longer blocked. The eight are
    precisely the DOI-keyed structured ``Paper`` identities.
    """
    decisions = [ResolutionPolicy().resolve(c) for c in _all(shadow_run)]
    routes = Counter(d.route.value for d in decisions)

    assert routes[AdjudicationRoute.AUTO.value] == AUTO_AT_WARRANTED_DISPOSITION
    assert routes[AdjudicationRoute.LLM_ASSESS.value] == (
        CORPUS_SIZE - AUTO_AT_WARRANTED_DISPOSITION
    )
    assert routes[AdjudicationRoute.HUMAN.value] == 0

    by_id = {c.candidate_id: c for c in _all(shadow_run)}
    auto = [by_id[d.candidate_id] for d in decisions if d.route is AdjudicationRoute.AUTO]
    assert {c.entity_type for c in auto} == {"Paper"}
    assert {c.producer for c in auto} == {"kgis.structured"}
    assert all(d.create_new_identity for d in decisions if d.route is AdjudicationRoute.AUTO)


def test_the_producer_derives_the_disposition_this_module_derives(shadow_run) -> None:
    """A and C agree because the producer computes the disposition by kind.

    ``_warranted`` derives the input disposition independently, from the
    candidate's kind; the producer derives it in
    ``ResolutionPolicy.identity_disposition``. Asserting the two agree is the
    evidence that the 8-of-252 above is the *warranted* routing, not an
    artefact of how one function happens to be written.
    """
    policy = ResolutionPolicy()
    candidates = _all(shadow_run)
    for candidate in candidates:
        assert policy.identity_disposition(candidate) is _warranted(candidate), (
            f"{candidate.candidate_kind} candidate disagrees between the "
            f"adopter's derivation and the producer"
        )


def test_the_producer_derives_a_disposition_before_routing() -> None:
    """The diagnosis, as a check rather than a claim in a docstring.

    ``ResolutionPolicy.resolve`` is the only place in the installed stack that
    calls ``ConfidencePolicy.route``, and agentic-kgcs #44 made it compute the
    disposition first and pass it in. If a future pin changes that shape, this
    goes red and the measurement above needs re-taking — which is the point.
    """
    import inspect

    source = inspect.getsource(ResolutionPolicy.resolve)
    assert "identity_disposition(candidate)" in source, (
        "ResolutionPolicy.resolve no longer derives a disposition - re-measure "
        "test_the_kgcs_producer_now_routes_the_structured_arm_auto"
    )
    assert ".route(candidate.scores, disposition)" in source, (
        "ResolutionPolicy.resolve no longer passes the disposition into "
        "route() - the AUTO deadlock may be back"
    )


def test_the_non_auto_decisions_are_unresolved(shadow_run) -> None:
    """The 244 that do not mint come back ``UNRESOLVED``, and correctly.

    ``ResolutionPolicy._dispose`` mints an identity only when the route is
    already ``AUTO``, so a non-AUTO candidate comes back with
    ``create_new_identity=False`` and ``resolved_identity=None`` — which
    ``ResolutionDecision.identity_disposition()`` reads as ``UNRESOLVED``.
    Feeding that back into ``route()`` reproduces the same block, so the cycle
    is closed rather than merely opened: the eight that minted are exactly the
    eight that re-route ``AUTO``, and no unkeyed candidate slips through by
    re-deriving its disposition from the route it produced.
    """
    decisions = [ResolutionPolicy().resolve(c) for c in _all(shadow_run)]
    dispositions = Counter(d.identity_disposition().value for d in decisions)
    assert dispositions == {
        IdentityDisposition.NEW_IDENTITY.value: AUTO_AT_WARRANTED_DISPOSITION,
        IdentityDisposition.UNRESOLVED.value: CORPUS_SIZE - AUTO_AT_WARRANTED_DISPOSITION,
    }

    # Re-routing with the decision's own disposition changes nothing.
    policy = ConfidencePolicy()
    rerouted = Counter(
        policy.route(c.scores, d.identity_disposition()).value
        for c, d in zip(_all(shadow_run), decisions, strict=True)
    )
    assert rerouted[AdjudicationRoute.AUTO.value] == AUTO_AT_WARRANTED_DISPOSITION


# --- B: the contract in isolation ---------------------------------------------


def test_the_contract_itself_can_now_route_auto(shadow_run) -> None:
    """The deadlock *is* broken one layer up: 32 of 252 at ``NEW_IDENTITY``.

    Told that these candidates mint new identities, the published policy routes
    every candidate whose extraction and source scores clear the defaults. That
    is the ADR-0024 fix doing exactly what it claims, at the contract layer. It
    is now the disposition the *producer* derives too, so the 32-of-252 upper
    bound is what the pipeline would route if every candidate could truthfully
    claim ``NEW_IDENTITY`` — and only the 8 entities actually do.
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

    With each candidate given the disposition its kind warrants, derived here
    independently of the producer, the eight that route ``AUTO`` are precisely
    the structured ``Paper`` entities: a direct read of a source record,
    ``source_reliability=1.0`` and ``extraction_confidence=1.0`` by
    construction (``STRUCTURED_SCORING``). This now equals the producer's own
    route count, which is the closure this file reports.

    The other 244 do not, for three distinct reasons, and the distinction
    matters:

    * the 106 *extracted* entity candidates carry ``extraction_confidence=0.8``
      and fail ``auto_min_extraction=0.95`` on extraction alone — and 0.8 is
      ``replay.REPLAY_CONFIDENCE``, a declared constant standing in for a model
      that has not run. This number is a property of the replay fixture, **not**
      of the new platform, and would change with a real recording;
    * the 130 attribute assertions name a subject carried as an ``EntityRef``
      alias, so ``UNRESOLVED`` blocks them regardless of score. KGCS would
      independently floor them at ``LLM_ASSESS`` for the same reason;
    * the 8 artifacts take the pre-ADR-0024 default ``RESOLVED_EXISTING``, and
      the identity gate blocks them because they carry no ``identity_confidence``
      either.
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
