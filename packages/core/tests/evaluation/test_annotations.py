"""Converter: existing gold -> versioned JSONL, and frozen-fixture integrity."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from agentic_kg.evaluation.annotations import (
    PAPER_DOIS,
    convert_gold_directory,
    convert_gold_file,
    convert_importer_output_directory,
    find_span,
    infer_section,
)
from agentic_kg.evaluation.schema import (
    FieldType,
    read_jsonl,
    split_records,
    write_jsonl,
)

REPO_ROOT = Path(__file__).resolve().parents[4]
CHAIN_ROOT = (
    REPO_ROOT / "packages" / "core" / "tests" / "extraction" / "fixtures" / "ground_truth_chain"
)
IMPORTER_OUTPUT = REPO_ROOT / "docs" / "ground-truth" / "importer-output"
FIXTURES = Path(__file__).parent / "fixtures" / "rq1"


def test_find_span_maps_normalized_offsets_back_to_original():
    text = "Intro.\n\nThe   model  uses\n entity merging here."
    span = find_span(text, "the model uses entity merging")
    assert span is not None
    assert text[span.start : span.end].replace("\n", " ") == "The   model  uses  entity merging"


def test_find_span_folds_ligatures():
    text = "we use reiﬁcation of triples"
    span = find_span(text, "reification of triples")
    assert span is not None
    assert text[span.start : span.end] == "reiﬁcation of triples"


def test_find_span_returns_none_for_missing_quote():
    assert find_span("short text", "not present") is None


def test_infer_section_uses_most_recent_heading():
    text = "Abstract. An abstract.\nIntroduction\nBody.\nMethods\nWe do things."
    assert infer_section(text, text.index("Body")) == "introduction"
    assert infer_section(text, text.index("We do")) == "methods"


def test_infer_section_accepts_a_long_run_in_abstract():
    # The heading and a full sentence share one line; the short-line guard is
    # deliberately bypassed for abstracts only.
    text = "ABSTRACT " + "word " * 40 + "\nI. INTRODUCTION\nBody."
    assert infer_section(text, text.index("word")) == "abstract"
    assert infer_section(text, text.index("Body")) == "introduction"


def test_infer_section_ignores_long_body_sentences_that_mention_evaluation():
    text = (
        "Introduction\n"
        "Evaluation of the triples quality is a long body sentence that continues "
        "well past the heading-length guard because it is prose, not a heading."
    )
    assert infer_section(text, text.index("Evaluation")) == "introduction"


class TestGoldConversion:
    def test_real_gold_set_is_converted(self):
        records, coverage = convert_gold_directory(CHAIN_ROOT, CHAIN_ROOT)
        assert records
        assert coverage
        assert {record.annotator_id for record in records} == {
            "human",
            "claude",
            "reconciled",
        }
        assert {record.field_type for record in records} == {FieldType.PROBLEM}
        assert {record.paper_slug for record in records} == {
            "cskg",
            "cskg2",
            "empire",
            "fact_completion",
        }
        # Every coverage record names the problem field, because every gold
        # file has a `problems:` key even when the list is empty.
        assert all(FieldType.PROBLEM in record.fields for record in coverage)

    def test_empire_doi_is_corrected_from_the_slug_table(self):
        records, _ = convert_gold_file(CHAIN_ROOT / "human" / "paper_empire.gold.yml", CHAIN_ROOT)
        assert records
        assert {record.paper_doi for record in records} == {PAPER_DOIS["empire"]}
        assert PAPER_DOIS["empire"] != "10.1007/978-3-031-19433-7_39"

    def test_spans_are_recovered_where_the_quote_exists(self):
        records, _ = convert_gold_directory(CHAIN_ROOT, CHAIN_ROOT)
        with_span = [record for record in records if record.char_span is not None]
        assert len(with_span) == len(records)
        # Every problem quote sits in a section the heading scanner recognises.
        assert all(record.section != "unknown" for record in records)


class TestImporterConversion:
    def test_real_importer_output_is_converted(self):
        records = convert_importer_output_directory(IMPORTER_OUTPUT, CHAIN_ROOT)
        assert records
        assert {record.annotator_id for record in records} == {"frozen-importer-output"}
        counts = Counter((record.paper_slug, record.field_type.value) for record in records)
        assert counts[("cskg", "problem")] == 1
        assert counts[("llm_ontology_gen", "dataset")] == 3
        assert counts[("llm_ontology_gen", "metric")] == 6


class TestCommittedFixtures:
    """The CI job reads frozen JSONL; regenerating must produce the same bytes."""

    def test_gold_fixture_matches_converter(self, tmp_path):
        records, coverage = convert_gold_directory(CHAIN_ROOT, CHAIN_ROOT)
        generated = tmp_path / "gold.jsonl"
        write_jsonl(generated, [*records, *coverage])
        committed = FIXTURES / "gold.jsonl"
        assert generated.read_text(encoding="utf-8") == committed.read_text(encoding="utf-8")

    def test_prediction_fixture_matches_converter(self, tmp_path):
        records = convert_importer_output_directory(IMPORTER_OUTPUT, CHAIN_ROOT)
        generated = tmp_path / "predictions.jsonl"
        write_jsonl(generated, records)
        committed = FIXTURES / "predictions.jsonl"
        assert generated.read_text(encoding="utf-8") == committed.read_text(encoding="utf-8")

    def test_fixture_records_are_valid_and_versioned(self):
        for name in ("gold.jsonl", "predictions.jsonl"):
            records = read_jsonl(FIXTURES / name)
            assert records
            assert {record.schema_version for record in records} == {"rq1-annotation/v1"}

    def test_gold_fixture_has_coverage_separate_from_items(self):
        annotations, coverage = split_records(read_jsonl(FIXTURES / "gold.jsonl"))
        assert annotations
        assert coverage
        assert len(annotations) + len(coverage) == 46
