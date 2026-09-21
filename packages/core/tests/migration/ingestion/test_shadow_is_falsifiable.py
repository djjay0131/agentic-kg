"""Mutation tests: proof that this suite can actually go red.

§9.0 obligation 5 — "a criterion must be able to fail for the reason it names.
State the defect the check exists to catch, and confirm that an implementation
exhibiting that defect turns it red — a mutation is the cheap way."

A green run says nothing on its own. A suite wired to a chunker that emits
nothing, a corpus loader that finds no files, or a validator that admits
everything passes exactly as convincingly as a correct one. This programme has
found eleven checks that verified nothing; the only defence that survives review
is demonstrating, test by test, that the named check notices the named defect.

**How the mutations are applied.** Wherever a call goes through a module
global, the mutation patches the *production* module — by construction the real
code path with one piece changed, which a hand-written broken class would drift
away from. Where a test binds a value at import (`from ... import X`), the
mutation patches that binding instead: the question a mutation test asks is "if
this behaved wrongly, would the check notice?", and the name the check actually
reads is where the wrong behaviour has to be injected. Each case below says
which of the two it is.

**The control.** Every mutated case is paired with the same test run unmutated
in the same helper, so "it went red" is measured the same way in both
directions — a test that were already failing for an unrelated reason would
otherwise look like a successful mutation.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest
from agentic_kg.migration.config import MigrationConfig
from agentic_kg.migration.ingestion import ShadowStores, importer_replay_client
from agentic_kg.migration.ingestion import documents as documents_module
from agentic_kg.migration.ingestion import extractors as extractors_module
from agentic_kg.migration.ingestion.corpus import CorpusPaper
from agentic_kg.migration.ingestion.documents import SectionChunk
from agentic_kg.migration.ingestion.identity import JoinKey
from agentic_kg.migration.ingestion.pipeline import ShadowRunResult, run_shadow_ingestion
from kgis.extraction import runner as kgis_runner

# Imported as *modules*, never `from ... import test_*`: binding a `test_`
# function into this namespace would make pytest collect and run it a second
# time here, under fixtures it was not written for.
from . import test_corpus as corpus_tests
from . import test_documents as document_tests
from . import test_identity as identity_tests
from . import test_shadow_run as run_tests


def reddens(call: Callable[[], None]) -> bool:
    """Run one check; report whether it failed.

    Exceptions are swallowed deliberately — a mutant is *expected* to raise,
    and the point is to record whether the check noticed. `BaseException` is
    not caught: a `KeyboardInterrupt` during a mutation run is not a
    measurement.
    """
    try:
        call()
    except Exception:  # noqa: BLE001 - failure is the measurement
        return True
    return False


def _fresh_run(corpus: tuple[CorpusPaper, ...]) -> ShadowRunResult:
    """A shadow run built *now*, so a patched module is in effect for it.

    The session-scoped `shadow_run` fixture was built before any mutation was
    applied, so re-using it would measure the unmutated pipeline and every
    mutation would look like it changed nothing.
    """
    stores = ShadowStores.in_memory()
    return run_shadow_ingestion(
        corpus,
        config=MigrationConfig(use_kgis_ingestion=True),
        client=importer_replay_client(corpus),
        stores=stores,
    )


# ---------------------------------------------------------------------------
# Control: the unmutated suite passes through the same harness
# ---------------------------------------------------------------------------


def test_control_the_named_checks_pass_unmutated(
    corpus: tuple[CorpusPaper, ...], shadow_run: ShadowRunResult
) -> None:
    """Every check a mutation below targets, green with no mutation applied.

    Without this, "the mutation turned it red" could equally mean "it was
    already red".
    """
    checks: dict[str, Callable[[], None]] = {
        "chunk_offsets_resolve": (
            lambda: document_tests.test_chunk_offsets_resolve_to_the_chunk_text(corpus)
        ),
        "fragment_grammar": lambda: document_tests.test_every_fragment_speaks_the_spec_grammar(
            corpus
        ),
        "content_span_window": document_tests.test_content_span_searches_only_the_section_window,
        "norm_punctuation": identity_tests.test_norm_preserves_punctuation,
        "join_scoped_by_doi": identity_tests.test_join_key_is_scoped_by_doi,
        "corpus_join_total": corpus_tests.test_the_join_is_total_over_the_committed_text_files,
        "problem_paper_scoped": lambda: run_tests.test_problem_identities_are_paper_scoped(
            shadow_run
        ),
        "evidence_resolves": lambda: run_tests.test_every_evidence_ref_resolves(
            shadow_run
        ),
        "entity_types_produced": lambda: run_tests.test_every_configured_entity_type_is_produced(
            shadow_run
        ),
    }
    assert checks
    failed = sorted(name for name, call in checks.items() if reddens(call))
    assert not failed, f"these checks are red before any mutation: {failed}"


# ---------------------------------------------------------------------------
# M1 — the segmenter's heading offset passed straight through
# ---------------------------------------------------------------------------


def test_naive_section_offsets_break_the_resolvability_check(
    corpus: tuple[CorpusPaper, ...], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Defect: `content_span` returning `(start_char, end_char)` unchanged.

    This is the defect a reasonable implementer would ship. `Section` carries
    both offsets and they look like the answer; the resulting fragments parse,
    resolve to real text, and are wrong — they include the heading line and
    lose the strip. Production mutation: `SectionChunker` calls `content_span`
    as a module global.
    """
    monkeypatch.setattr(
        documents_module,
        "content_span",
        lambda text, section: (section.start_char, section.end_char),
    )
    assert reddens(
        lambda: document_tests.test_chunk_offsets_resolve_to_the_chunk_text(corpus)
    ), "passing the heading offset through left the resolvability check green"


def test_an_unbounded_content_search_breaks_the_window_check(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    """Defect: `text.find(content)` with no window.

    Test-binding mutation: the check calls `content_span` by its imported name.
    """
    def unbounded(text: str, section: object) -> tuple[int, int] | None:
        content = getattr(section, "content", "")
        offset = text.find(content)
        return None if offset == -1 else (offset, offset + len(content))

    monkeypatch.setattr(document_tests, "content_span", unbounded)
    assert reddens(
        document_tests.test_content_span_searches_only_the_section_window
    ), "an unbounded search left the window check green"


# ---------------------------------------------------------------------------
# M2 — KGIS's own fragment grammar leaking through
# ---------------------------------------------------------------------------


def test_the_kgis_fragment_grammar_breaks_the_grammar_check(
    corpus: tuple[CorpusPaper, ...], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Defect: a plain `kgis.Chunk`, or a subclass that forgot to override.

    `chunk:{index}@chars:{start}-{end}` is well-formed, resolvable and entirely
    different from the grammar §3.4 mandates and `parse_fragment` reads. Only
    this check stands between the two. Production mutation: the property is
    restored to KGIS's on the real subclass.
    """
    monkeypatch.setattr(
        SectionChunk,
        "fragment",
        property(lambda self: f"chunk:{self.index}@chars:{self.start}-{self.end}"),
    )
    assert reddens(
        lambda: document_tests.test_every_fragment_speaks_the_spec_grammar(corpus)
    ), "the KGIS grammar left the §3.4 grammar check green"


# ---------------------------------------------------------------------------
# M3 — `NORM` stripping punctuation
# ---------------------------------------------------------------------------


def test_punctuation_stripping_breaks_the_norm_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Defect: the spec's own first draft.

    §3.1 measured this one: a punctuation-stripping `NORM` disagrees with the
    join key on 26 of 194 corpus names. Test-binding mutation.
    """
    import re

    monkeypatch.setattr(
        identity_tests,
        "norm",
        lambda text: re.sub(r"[^\w\s]", "", text).casefold().strip(),
    )
    assert reddens(
        identity_tests.test_norm_preserves_punctuation
    ), "a punctuation-stripping NORM left the punctuation check green"


# ---------------------------------------------------------------------------
# M4 — a Problem key that is not paper-scoped
# ---------------------------------------------------------------------------


def test_a_doi_less_join_key_breaks_the_paper_scoping_check(
    corpus: tuple[CorpusPaper, ...], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Defect: `K` built without its `doi` component.

    §6.4.1 calls `doi` load-bearing because the same concept recurs across the
    citation chain by design. Without it every paper's problems share one
    namespace and two papers stating the same problem silently become one
    identity — and the ledger looks *tidier* for it, which is why nothing
    downstream would flag it.

    Production mutation: `PaperScopedKeyBuilder.build` calls `join_key` as a
    module global in `extractors`.
    """
    real = extractors_module.join_key

    def doi_less(*, entity_type: str, doi: str, surface: str, quoted_text: str) -> JoinKey:
        built = real(
            entity_type=entity_type, doi=doi, surface=surface, quoted_text=quoted_text
        )
        return JoinKey(
            entity_type=built.entity_type,
            doi="shared",
            surface=built.surface,
            span=built.span,
        )

    monkeypatch.setattr(extractors_module, "join_key", doi_less)
    mutated = _fresh_run(corpus)
    try:
        assert reddens(
            lambda: run_tests.test_problem_identities_are_paper_scoped(mutated)
        ), "a DOI-less join key left the paper-scoping check green"
    finally:
        mutated.stores.close()


# ---------------------------------------------------------------------------
# M5 — evidence cited under an id the registry does not hold
# ---------------------------------------------------------------------------


def test_a_random_evidence_id_breaks_the_resolution_and_determinism_checks(
    corpus: tuple[CorpusPaper, ...], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Defect: taking `Evidence`'s default random-ULID id.

    The whole reason the extraction path derives ids with `stable_suffix`. Under
    the default, a candidate cites an id the registry never stored (the registry
    writes under the deterministic id), so refs dangle *and* re-collection
    duplicates. Production mutation: `ExtractionPipeline._cite_chunk` calls
    `chunk_evidence_ref` as a module global in KGIS's runner.
    """
    from kg_contracts.evidence import EvidenceRef, EvidenceRelationship

    counter = {"n": 0}

    def random_ref(chunk: object, config: object) -> EvidenceRef:
        counter["n"] += 1
        return EvidenceRef(
            evidence_id=f"ev_not_stored_{counter['n']:04d}",
            relationship=EvidenceRelationship.DERIVED_FROM,
        )

    monkeypatch.setattr(kgis_runner, "chunk_evidence_ref", random_ref)
    mutated = _fresh_run(corpus)
    try:
        assert reddens(
            lambda: run_tests.test_every_evidence_ref_resolves(mutated)
        ), "a dangling evidence ref left the resolution check green"
    finally:
        mutated.stores.close()


# ---------------------------------------------------------------------------
# M6 — an extractor that stops producing its type
# ---------------------------------------------------------------------------


def test_a_silent_extractor_breaks_the_type_coverage_check(
    corpus: tuple[CorpusPaper, ...], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Defect: an extractor configured but emitting nothing.

    The realistic cause is a prompt edit that changes the request key, after
    which the replay client raises `ReplayMiss` on every chunk, the runner
    isolates the failure per (extractor, chunk) as designed, and the run
    completes with one entity type quietly missing. `report.failures` would
    carry it — and nothing reads `report.failures` unless a test does.

    Production mutation: drop the Problem extractor from the configured set.
    """
    real = extractors_module.research_extractors
    monkeypatch.setattr(
        extractors_module,
        "research_extractors",
        lambda: tuple(c for c in real() if c.extractor_id != "problem"),
    )
    import agentic_kg.migration.ingestion.pipeline as pipeline_module

    monkeypatch.setattr(
        pipeline_module, "research_extractors", extractors_module.research_extractors
    )
    mutated = _fresh_run(corpus)
    try:
        assert mutated.entity_counts().get("Problem", 0) == 0
        assert reddens(
            lambda: run_tests.test_every_configured_entity_type_is_produced(mutated)
        ), "a missing entity type left the coverage check green"
    finally:
        mutated.stores.close()


# ---------------------------------------------------------------------------
# M6b — the raw upstream report passed through unpopulated
# ---------------------------------------------------------------------------


def test_an_unpopulated_coverage_breaks_the_declaration_check(
    corpus: tuple[CorpusPaper, ...], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Defect: returning `ExtractionPipeline`'s report as it comes.

    That is the *natural* implementation — the report is already there, and
    every count on it is right. Only `coverage` is wrong, because the pipeline
    was never given an ontology, and it reads `declared=False`: the run says no
    vocabulary was declared while its validator was rejecting undeclared terms.
    Production mutation: `_populate_coverage` becomes a no-op.
    """
    import agentic_kg.migration.ingestion.pipeline as pipeline_module

    monkeypatch.setattr(pipeline_module, "_populate_coverage", lambda report, cands: None)
    mutated = _fresh_run(corpus)
    try:
        assert mutated.report.coverage.declared is False, (
            "the unmutated pipeline already declares coverage, so this mutation "
            "changes nothing and proves nothing"
        )
        assert reddens(
            lambda: run_tests.test_the_report_declares_the_ontology_it_was_validated_against(
                mutated
            )
        ), "an undeclared coverage left the declaration check green"
    finally:
        mutated.stores.close()


# ---------------------------------------------------------------------------
# M7 — the corpus join table losing a row
# ---------------------------------------------------------------------------


def test_a_missing_join_row_breaks_the_totality_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Defect: a paper silently dropped from the corpus.

    Seven papers instead of eight, every count 12% lower, every ratio
    unchanged. Test-binding mutation, because the check reads the table by its
    imported name.
    """
    reduced = dict(corpus_tests.SLUG_TO_IMPORTER_FILE)
    reduced.pop("empire")
    monkeypatch.setattr(corpus_tests, "SLUG_TO_IMPORTER_FILE", reduced)
    assert reddens(
        corpus_tests.test_the_join_is_total_over_the_committed_text_files
    ), "a missing join row left the totality check green"


# ---------------------------------------------------------------------------
# M8 — the ontology validator left at the ExtractionPipeline default
# ---------------------------------------------------------------------------


def test_the_default_validator_admits_an_undeclared_term(
    corpus: tuple[CorpusPaper, ...], monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC-14, proved end to end rather than by reading the constructor.

    `ExtractionPipeline` takes no `ontology=` and defaults to
    `OntologyCandidateValidator(None)`, which admits every term. The two halves
    below are the discrimination: an extractor targeting an undeclared entity
    type is *rejected* by the wired validator and *admitted* by the default. If
    both admitted it, `test_an_undeclared_entity_type_is_rejected` would be
    measuring nothing.
    """
    from agentic_kg.migration.ingestion._contracts import OntologyCandidateValidator
    from agentic_kg.migration.ingestion.extractors import (
        EXTRACTION_SCORING,
        MODEL_ID,
        NormalizedSurfaceBuilder,
        _surface_entity_builder,
    )
    from kgis.extraction.config import ExtractorConfig

    undeclared = ExtractorConfig(
        extractor_id="research_concept",  # reuses the replay responses
        target_type="Sandwich",
        builder=NormalizedSurfaceBuilder(_surface_entity_builder(entity_type="Sandwich")),
        prompt_template=(
            extractors_module.research_concept_extractor().prompt_template
        ),
        model_id=MODEL_ID,
        model_version=extractors_module.MODEL_VERSION,
        extractor_version="1",
        prompt_version="1",
        scoring=EXTRACTION_SCORING,
    )

    def run_with(validator_factory: object) -> int:
        import agentic_kg.migration.ingestion.pipeline as pipeline_module

        with monkeypatch.context() as patch:
            patch.setattr(
                pipeline_module, "research_candidate_validator", validator_factory
            )
            stores = ShadowStores.in_memory()
            try:
                result = run_shadow_ingestion(
                    corpus[:1],
                    config=MigrationConfig(use_kgis_ingestion=True),
                    client=importer_replay_client(corpus[:1], extractors=[undeclared]),
                    stores=stores,
                    extractors=[undeclared],
                    include_papers=False,
                )
                return result.entity_counts().get("Sandwich", 0)
            finally:
                stores.close()

    from agentic_kg.migration.ingestion.ontology import research_candidate_validator

    admitted_by_default = run_with(lambda *, strict=True: OntologyCandidateValidator(None))
    rejected_when_wired = run_with(research_candidate_validator)

    assert admitted_by_default > 0, (
        "the default validator rejected an undeclared term, so AC-14 is not "
        "guarding against anything and this test is vacuous"
    )
    assert rejected_when_wired == 0, (
        "the wired validator admitted an undeclared entity type; the shadow "
        "path is not ontology-checked"
    )
