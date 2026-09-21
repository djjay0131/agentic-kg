"""One shadow run over the eight committed papers, asserted end to end.

The run is real: the real segmenter over the real committed text, the real KGIS
`ExtractionPipeline`, the real builders, the real contract validation, the real
SQLite ledger and evidence registry. Only the model is replayed — see
`replay.py` for what those responses are and, more importantly, what they are
not.

Every count asserted below is a lower bound rather than an exact number, except
where the exact number is a property of the corpus (eight papers, eight
artifacts). Pinning "exactly 24 ResearchConcepts" would turn an improvement in
the extractor into a failing test, and the defects worth catching here are
structural — a type that stops being produced, evidence that stops resolving,
a coordinate that stops parsing — not arithmetic drift.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from agentic_kg.migration.config import MigrationConfig
from agentic_kg.migration.ingestion import ShadowStores, importer_replay_client
from agentic_kg.migration.ingestion.corpus import CorpusPaper
from agentic_kg.migration.ingestion.identity import norm, normalize_doi, parse_fragment
from agentic_kg.migration.ingestion.ontology import ENTITY_TYPES, ONTOLOGY_VERSION
from agentic_kg.migration.ingestion.papers import STRUCTURED_PRODUCER
from agentic_kg.migration.ingestion.pipeline import (
    RUN_INSTANT,
    ShadowRunResult,
    run_shadow_ingestion,
)
from kg_contracts.candidates import (
    ArtifactCandidate,
    AttributeAssertionCandidate,
    EntityCandidate,
)

#: Types this PR's extractors are configured to produce. `Topic` is deliberately
#: not here: the replay responses come from the committed importer output, whose
#: own `_caveats` block records that Topic/BELONGS_TO was never populated
#: (issue #58), so the topic extractor correctly emits nothing. Listing it would
#: be asserting against a fixture that does not hold what the assertion claims —
#: §9.0's fourth shape.
EXPECTED_ENTITY_TYPES = ("Method", "Model", "Paper", "Problem", "ResearchConcept")


def test_the_run_produced_candidates(shadow_run: ShadowRunResult) -> None:
    """§9.0 obligation 1 for every test in this module."""
    assert shadow_run.candidates, "the extraction arm submitted nothing"
    assert shadow_run.paper_candidates, "the structured arm submitted nothing"
    assert shadow_run.report.candidates_built > 0
    assert shadow_run.report.failures == [], shadow_run.report.failures


def test_every_configured_entity_type_is_produced(shadow_run: ShadowRunResult) -> None:
    counts = shadow_run.entity_counts()
    for entity_type in EXPECTED_ENTITY_TYPES:
        assert counts.get(entity_type, 0) > 0, (
            f"no {entity_type} candidates were produced; counts={counts}"
        )


def test_the_topic_extractor_found_nothing_and_that_is_the_corpus(
    shadow_run: ShadowRunResult, corpus: tuple[CorpusPaper, ...]
) -> None:
    """Zero Topics is a fact about the fixture, and the extractor is live.

    `== 0` alone is not enough, and an independent review was right to say so:
    it passes identically over a deleted extractor, an extractor whose type the
    ontology rejects, or one that raises on every chunk. All three are real
    regressions and all three read as "the corpus has no topics".

    So the assertion has two halves. **Negative:** every committed importer
    file carries `topic: {domain: null, area: null, subtopic: null}` — the
    BELONGS_TO bug (issue #58) — so the run correctly yields none.
    **Positive:** given a topic-bearing payload the *same pipeline, same
    extractor, same ontology* produces Topic entities with no failures. Only
    the second half can tell a silent extractor from an empty corpus.
    """
    import copy
    import dataclasses

    import yaml

    assert shadow_run.entity_counts().get("Topic", 0) == 0

    # Negative half: the corpus really is topic-free, checked at source.
    for paper in corpus:
        payload = yaml.safe_load(paper.importer_path.read_text(encoding="utf-8"))
        topic = payload.get("topic") or {}
        named = [topic.get(level) for level in ("domain", "area", "subtopic")]
        assert not any(named), f"{paper.slug} carries topics: {named}"

    # Positive half: the same extractor, given topics, emits them.
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        source = corpus[0]
        payload = copy.deepcopy(
            yaml.safe_load(source.importer_path.read_text(encoding="utf-8"))
        )
        payload["topic"] = {
            "domain": "Computer Science",
            "area": "Artificial Intelligence",
            "subtopic": "Knowledge Graphs",
        }
        patched = Path(tmp) / source.importer_path.name
        patched.write_text(yaml.safe_dump(payload), encoding="utf-8")
        with_topics = dataclasses.replace(source, importer_path=patched)

        stores = ShadowStores.in_memory()
        try:
            result = run_shadow_ingestion(
                [with_topics],
                config=MigrationConfig(use_kgis_ingestion=True),
                client=importer_replay_client([with_topics]),
                stores=stores,
                include_papers=False,
            )
            assert result.report.failures == [], result.report.failures
            assert result.entity_counts().get("Topic", 0) == 3, (
                f"the topic extractor produced no Topic entities from a "
                f"topic-bearing payload, so the zero above says nothing about "
                f"the corpus: {result.entity_counts()}"
            )
        finally:
            stores.close()


def test_the_report_declares_the_ontology_it_was_validated_against(
    shadow_run: ShadowRunResult,
) -> None:
    """`report.coverage.declared` must not contradict AC-14.

    `ExtractionPipeline` takes no `ontology=` argument, so its report comes back
    `declared=False` with empty tallies — an honest null for KGIS, which really
    does not know the vocabulary, but wrong here: this path validates every
    candidate against `RESEARCH_ONTOLOGY` and rejects undeclared terms. A
    reader seeing "no ontology declared" beside a strictly-validated run would
    reasonably conclude AC-14 was not enforced.

    The defect this catches is the raw upstream report being passed through
    unpopulated. `declared=False` is exactly what that looks like, and nothing
    else in the suite would notice.
    """
    coverage = shadow_run.report.coverage
    assert coverage.declared, (
        "the run's own report says no ontology was declared, contradicting the "
        "strict validator it was built with"
    )
    assert coverage.ontology_version == ONTOLOGY_VERSION
    assert set(coverage.declared_entity_types) == set(ENTITY_TYPES)
    assert coverage.entity_types, "no entity terms were tallied"
    assert not coverage.unknown_entity_types
    assert not coverage.unknown_attributes


def test_the_report_names_the_declared_terms_nothing_used(
    shadow_run: ShadowRunResult,
) -> None:
    """The zero-Topic outcome is visible in the report, not only in a test.

    `unused_*` is the axis that makes "declared a Topic extractor, produced no
    Topics" legible to an operator reading the run, rather than only to whoever
    opens the test file.
    """
    unused = set(shadow_run.report.coverage.unused_entity_types)
    assert "Topic" in unused, (
        f"Topic was declared and never used, so it must appear as unused: {unused}"
    )


def test_one_artifact_candidate_per_paper(
    shadow_run: ShadowRunResult, corpus: tuple[CorpusPaper, ...]
) -> None:
    """The PDF text itself, as an artifact that contributes no graph operation."""
    artifacts = [
        c for c in shadow_run.candidates if isinstance(c, ArtifactCandidate)
    ]
    assert len(artifacts) == len(corpus)


def test_no_candidate_carries_a_confidence_kwarg(
    shadow_run: ShadowRunResult,
) -> None:
    """AC-10: both score axes present, `confidence` absent.

    `CandidateScores` is `extra="forbid"`, so a stray `confidence=` would be a
    `ValidationError` at construction — which means this assertion is about the
    *other* half of AC-10: that both required axes really are populated on
    every candidate rather than one of them being left at a default nobody
    chose.
    """
    everything = (*shadow_run.paper_candidates, *shadow_run.candidates)
    assert everything
    for candidate in everything:
        scores = candidate.scores
        assert not hasattr(scores, "confidence")
        assert 0.0 <= scores.extraction_confidence <= 1.0
        assert 0.0 <= scores.source_reliability <= 1.0
        assert scores.identity_confidence is None, (
            "identity_confidence is filled by KGCS resolution, never by a "
            "producer (§3.5)"
        )


def test_every_semantic_key_is_hierarchical_and_holds_no_uuid(
    shadow_run: ShadowRunResult,
) -> None:
    """AC-11: `<type>/<namespace>/<key>`, never a UUID.

    A flat key silently disables KGCS's `UniqueSourceConstraint` and
    `SourceKeyChannel`, both of which `rpartition` on `/`.
    """
    import re

    uuid_re = re.compile(
        r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I
    )
    everything = (*shadow_run.paper_candidates, *shadow_run.candidates)
    assert everything
    for candidate in everything:
        key = candidate.semantic_key
        assert key.count("/") >= 2, f"{key!r} is not hierarchical"
        assert not uuid_re.search(key), f"{key!r} contains a UUID"


def test_extracted_candidates_carry_a_spec_fragment(
    shadow_run: ShadowRunResult,
) -> None:
    """Every extracted candidate's coordinate parses as §3.4.

    Artifacts are excluded: `build_document_artifact` sets `fragment="document"`
    for the document as a whole, which is KGIS's own coordinate for a thing that
    has no span. Including them would force either a fake span or a weakened
    grammar.
    """
    extracted = [
        c
        for c in shadow_run.candidates
        if not isinstance(c, ArtifactCandidate)
        and c.producer != STRUCTURED_PRODUCER
    ]
    assert extracted
    for candidate in extracted:
        fragment = candidate.source_coordinates.fragment
        assert fragment is not None, candidate.semantic_key
        parsed = parse_fragment(fragment)
        assert parsed.end > parsed.start


def test_every_evidence_ref_resolves(shadow_run: ShadowRunResult) -> None:
    """No dangling citation, anywhere.

    The registry's `resolve` raises `EvidenceNotFoundError` on a dangling ref
    rather than silently dropping it, so this exercises upstream's own rule
    instead of restating it. The defect it catches: evidence written under a
    different id than the one the candidate cites, which produces a ledger full
    of claims whose grounding cannot be retrieved.
    """
    registry = shadow_run.stores.evidence
    everything = (*shadow_run.paper_candidates, *shadow_run.candidates)
    checked = 0
    for candidate in everything:
        assert candidate.evidence_refs, f"{candidate.semantic_key} cites nothing"
        for ref in candidate.evidence_refs:
            evidence = registry.get(ref.evidence_id)
            assert evidence is not None, (
                f"{candidate.semantic_key} cites {ref.evidence_id}, which the "
                f"registry does not hold"
            )
            checked += 1
    assert checked > 0


def test_evidence_locators_carry_the_span_as_a_string(
    shadow_run: ShadowRunResult,
) -> None:
    """The known KGIS gap, asserted as the shape it actually has.

    `kg_contracts.Evidence` has no span, page or offset field. KGIS encodes the
    span into `source_locator` as `f"{locator}#{fragment}"`. This test pins that
    encoding so a future upstream change — a real span field, or a different
    join character — is noticed here rather than in a projector that silently
    stops finding coordinates.
    """
    registry = shadow_run.stores.evidence
    extracted = [
        c
        for c in shadow_run.candidates
        if not isinstance(c, ArtifactCandidate) and c.source_coordinates.fragment
    ]
    assert extracted
    for candidate in extracted[:50]:
        evidence = registry.get(candidate.evidence_refs[0].evidence_id)
        assert evidence is not None
        assert evidence.source_locator.endswith(
            "#" + str(candidate.source_coordinates.fragment)
        )
        assert not hasattr(evidence, "span")
        assert not hasattr(evidence, "page")


def test_evidence_ids_are_explicit_not_random_ulids(
    shadow_run: ShadowRunResult, corpus: tuple[CorpusPaper, ...]
) -> None:
    """Re-collection idempotency.

    A second run over the same corpus must re-collect the *same* evidence, not
    pile up duplicates. The `Evidence` default is a random ULID, so the only way
    to check this is to run twice and compare — comparing one run against itself
    would pass under any id scheme at all.
    """
    stores = ShadowStores.in_memory()
    try:
        second = run_shadow_ingestion(
            corpus,
            config=MigrationConfig(use_kgis_ingestion=True),
            client=importer_replay_client(corpus),
            stores=stores,
        )
        first_ids = {
            ref.evidence_id
            for c in shadow_run.candidates
            for ref in c.evidence_refs
        }
        second_ids = {
            ref.evidence_id for c in second.candidates for ref in c.evidence_refs
        }
        assert first_ids
        assert first_ids == second_ids
    finally:
        stores.close()


def test_the_run_is_reproducible(
    shadow_run: ShadowRunResult, corpus: tuple[CorpusPaper, ...]
) -> None:
    """Two independent runs produce identical candidate ids.

    Two *separate* in-memory ledgers, not the same one twice: submitting to a
    ledger that already holds the candidates returns `DUPLICATE` and writes
    nothing, so a second pass over one store would be a no-op and prove nothing
    (§9.0, the AC-9 shape).
    """
    stores = ShadowStores.in_memory()
    try:
        second = run_shadow_ingestion(
            corpus,
            config=MigrationConfig(use_kgis_ingestion=True),
            client=importer_replay_client(corpus),
            stores=stores,
        )
        first_ids = sorted(c.candidate_id for c in shadow_run.candidates)
        second_ids = sorted(c.candidate_id for c in second.candidates)
        assert first_ids
        assert first_ids == second_ids
        assert shadow_run.deterministic_client and second.deterministic_client
    finally:
        stores.close()


def test_candidates_carry_the_fixed_run_instant(shadow_run: ShadowRunResult) -> None:
    """A wall clock would make two identical runs differ in every row."""
    assert shadow_run.candidates
    assert all(c.created_at == RUN_INSTANT for c in shadow_run.candidates)


def test_problem_identities_are_paper_scoped(shadow_run: ShadowRunResult) -> None:
    """Spec §3.3/§6.4.1: `problem/paper_span/<doi>#<surface>#<span>`.

    The defect: a key built without the DOI. Every paper's problems would then
    share a namespace, two papers stating the same problem would collapse into
    one identity at ingestion time, and the ledger would look *cleaner* for it.
    """
    problems = [
        c
        for c in shadow_run.candidates
        if isinstance(c, EntityCandidate) and c.entity_type == "Problem"
    ]
    assert problems, "no Problem candidates to check"
    dois = set()
    for candidate in problems:
        assert candidate.semantic_key.startswith("problem/paper_span/")
        alias = candidate.aliases[0]
        assert alias.namespace == "paper_span"
        doi, _, rest = alias.key.partition("#")
        surface, _, span = rest.partition("#")
        assert doi and surface and span, alias.key
        assert len(span) == 16, f"span component is not sha256-16: {span!r}"
        assert surface == norm(surface), "the surface component is not normalized"
        dois.add(doi)
    assert len(dois) > 1, (
        "every Problem came from one paper, so this test cannot show the key "
        "is paper-scoped"
    )


def test_surface_identities_use_the_normalized_form(
    shadow_run: ShadowRunResult,
) -> None:
    """No similarity threshold appears anywhere in a candidate (§3.3).

    Legacy identity for these three labels is "cosine >= 0.90 against whichever
    node arrived first". The replacement must be reproducible from the source
    text, which means the alias key is exactly `NORM(name)`.
    """
    surfaced = [
        c
        for c in shadow_run.candidates
        if isinstance(c, EntityCandidate)
        and c.entity_type in {"ResearchConcept", "Model", "Method"}
    ]
    assert surfaced
    for candidate in surfaced:
        key = candidate.aliases[0].key
        assert key == norm(key), f"{key!r} is not in normal form"
        if candidate.display_name:
            assert key == norm(candidate.display_name)


def test_paper_identities_key_on_the_doi(
    shadow_run: ShadowRunResult, corpus: tuple[CorpusPaper, ...]
) -> None:
    papers = [
        c
        for c in shadow_run.paper_candidates
        if isinstance(c, EntityCandidate) and c.entity_type == "Paper"
    ]
    assert len(papers) == len(corpus)
    expected = {normalize_doi(p.doi) for p in corpus}
    assert {c.aliases[0].key for c in papers} == expected
    assert {c.semantic_key for c in papers} == {f"paper/doi/{d}" for d in expected}


def test_paper_attributes_are_assertions_not_properties(
    shadow_run: ShadowRunResult,
) -> None:
    """`title` and `year` are first-class bitemporal claims, not inline values."""
    assertions = [
        c
        for c in shadow_run.paper_candidates
        if isinstance(c, AttributeAssertionCandidate)
    ]
    assert assertions
    assert {c.attribute for c in assertions} == {"title", "year"}


def test_the_ledger_holds_everything_that_was_submitted(
    shadow_run: ShadowRunResult,
) -> None:
    """The ledger is the record, not the in-memory result object."""
    entries = shadow_run.stores.ledger.ledger_entries()
    assert entries
    in_ledger = {entry.candidate.candidate_id for entry in entries}
    for candidate in (*shadow_run.paper_candidates, *shadow_run.candidates):
        assert candidate.candidate_id in in_ledger, candidate.semantic_key


def test_resubmitting_the_same_candidates_is_a_duplicate_not_a_second_row(
    shadow_run: ShadowRunResult,
) -> None:
    """Exercises the ledger's own dedup, on the real store this run used."""
    from kg_contracts.stores import SubmissionStatus

    ledger = shadow_run.stores.ledger
    before = len(ledger.ledger_entries())
    sample = list(shadow_run.candidates)[:5]
    assert sample
    result = ledger.submit(sample)
    assert all(o.status is SubmissionStatus.DUPLICATE for o in result.outcomes)
    assert len(ledger.ledger_entries()) == before


def test_a_result_reports_only_its_own_run(
    corpus: tuple[CorpusPaper, ...], enabled_config: MigrationConfig, tmp_path
) -> None:
    """A second run against a shared ledger does not inherit the first's rows.

    The defect: reading `ledger_entries()` unscoped. Against an in-memory store
    — which every other test here uses — that is indistinguishable from the
    correct behaviour, so this is the only place the difference shows. The
    second run submits a *disjoint* paper, so an unscoped read would report
    both papers' candidates and `entity_counts()` would roughly double while
    every candidate in it remained perfectly valid.
    """
    root = tmp_path / "shared"
    first_papers, second_papers = corpus[:1], corpus[1:2]

    stores = ShadowStores.at(root)
    try:
        first = run_shadow_ingestion(
            first_papers,
            config=enabled_config,
            client=importer_replay_client(first_papers),
            stores=stores,
            run_id="run_first",
        )
        assert first.candidates
    finally:
        stores.close()

    reopened = ShadowStores.at(root)
    try:
        second = run_shadow_ingestion(
            second_papers,
            config=enabled_config,
            client=importer_replay_client(second_papers),
            stores=reopened,
            run_id="run_second",
        )
        assert second.candidates
        assert len(reopened.ledger.ledger_entries()) > len(second.candidates), (
            "the shared ledger holds only the second run's rows, so this test "
            "cannot show the result is scoped"
        )
        assert all(c.producer_run_id == "run_second" for c in second.candidates)
        first_ids = {c.candidate_id for c in first.candidates}
        assert not (first_ids & {c.candidate_id for c in second.candidates})
    finally:
        reopened.close()


def test_a_replay_miss_is_raised_not_fabricated(
    corpus: tuple[CorpusPaper, ...], enabled_config: MigrationConfig
) -> None:
    """An unrecorded prompt must fail loudly.

    Exercises upstream's `ReplayCompletionClient` rather than restating its
    rule: a client primed for a *different* paper is handed this one, and the
    run must report failures rather than quietly returning candidates built
    from an invented completion.
    """
    other, this = corpus[0], corpus[1]
    stores = ShadowStores.in_memory()
    try:
        result = run_shadow_ingestion(
            [this],
            config=enabled_config,
            client=importer_replay_client([other]),
            stores=stores,
        )
        assert result.report.failures, (
            "a replay client with no response for this paper produced no "
            "failures, which means something fabricated a completion"
        )
        assert any("ReplayMiss" in f for f in result.report.failures)
    finally:
        stores.close()


def test_a_persistent_run_survives_reopening_the_ledger(
    corpus: tuple[CorpusPaper, ...], enabled_config: MigrationConfig, tmp_path
) -> None:
    """The isolated on-disk store actually persists, and only there."""
    root = tmp_path / "shadow"
    stores = ShadowStores.at(root)
    try:
        result = run_shadow_ingestion(
            corpus[:2], config=enabled_config, client=importer_replay_client(corpus[:2]),
            stores=stores,
        )
        assert result.candidates
        expected = {c.candidate_id for c in result.candidates}
    finally:
        stores.close()

    reopened = ShadowStores.at(root)
    try:
        persisted = {e.candidate.candidate_id for e in reopened.ledger.ledger_entries()}
        assert expected <= persisted
    finally:
        reopened.close()


def test_building_a_run_with_no_papers_is_an_error(
    enabled_config: MigrationConfig,
) -> None:
    from agentic_kg.migration.ingestion.pipeline import build_shadow_pipeline

    stores = ShadowStores.in_memory()
    try:
        with pytest.raises(ValueError):
            build_shadow_pipeline(
                [], config=enabled_config, client=importer_replay_client([]), stores=stores
            )
    finally:
        stores.close()
