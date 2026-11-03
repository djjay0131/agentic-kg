"""The annotation record contract: versioning, normalization, JSONL round-trip."""

from __future__ import annotations

import json

import pytest
from agentic_kg.evaluation.schema import (
    SCHEMA_VERSION,
    AnnotationRecord,
    CharSpan,
    CoverageRecord,
    FieldType,
    normalize_text,
    normalized_surfaces,
    read_jsonl,
    write_jsonl,
)


class TestNormalizeText:
    def test_ligature_folds_to_ascii(self):
        assert normalize_text("reiﬁcation") == "reification"

    def test_case_and_whitespace(self):
        assert normalize_text("  Knowledge   Graph \n") == "knowledge graph"

    def test_dash_variants_collapse(self):
        assert normalize_text("part–of–speech") == "part-of-speech"

    def test_edge_punctuation_is_stripped(self):
        assert normalize_text("(entity merging).") == "entity merging"

    def test_hyphen_is_not_removed(self):
        # "paraphrase-distilroberta" and the extractor's dehyphenated form are
        # different strings; gold uses aliases to bridge that, not fuzzy logic.
        assert normalize_text("paraphrase-distilroberta") != normalize_text(
            "paraphrasedistilroberta"
        )


class TestAnnotationRecord:
    def test_doi_is_case_folded(self):
        record = AnnotationRecord(
            paper_doi="10.1109/ACCESS.2022.3220241",
            field_type=FieldType.PROBLEM,
            text="a problem",
            annotator_id="human",
        )
        assert record.paper_doi == "10.1109/access.2022.3220241"

    def test_section_is_normalized(self):
        record = AnnotationRecord(
            paper_doi="doi",
            section="Related Work",
            field_type=FieldType.PROBLEM,
            text="a problem",
            annotator_id="human",
        )
        assert record.section == "related_work"

    def test_fingerprint_is_stable_across_case(self):
        a = AnnotationRecord(
            paper_doi="doi",
            field_type=FieldType.PROBLEM,
            text="A Problem",
            annotator_id="human",
        )
        b = AnnotationRecord(
            paper_doi="DOI",
            field_type=FieldType.PROBLEM,
            text="a problem",
            annotator_id="claude",
        )
        assert a.item_id == b.item_id

    def test_char_span_must_be_ordered(self):
        with pytest.raises(ValueError):
            CharSpan(start=10, end=5)

    def test_aliases_are_deduped(self):
        record = AnnotationRecord(
            paper_doi="doi",
            field_type=FieldType.PROBLEM,
            text="a problem",
            aliases=("Alias", "alias", " other "),
            annotator_id="human",
        )
        assert record.aliases == ("Alias", "other")

    def test_surfaces_include_alias(self):
        record = AnnotationRecord(
            paper_doi="doi",
            field_type=FieldType.PROBLEM,
            text="entity resolution",
            aliases=("entity merging",),
            annotator_id="human",
        )
        assert record.surfaces() == {"entity resolution", "entity merging"}


class TestCoverageRecord:
    def test_fields_are_deduped_and_ordered(self):
        coverage = CoverageRecord(
            paper_doi="doi",
            annotator_id="human",
            fields=(FieldType.PROBLEM, FieldType.PROBLEM, FieldType.DATASET),
        )
        assert coverage.fields == (
            FieldType.PROBLEM,
            FieldType.DATASET,
        )


class TestJsonl:
    def test_round_trip(self, tmp_path):
        records = [
            CoverageRecord(
                paper_doi="doi:one",
                annotator_id="human",
                fields=(FieldType.PROBLEM,),
            ),
            AnnotationRecord(
                paper_doi="doi:one",
                field_type=FieldType.PROBLEM,
                text="A problem statement",
                annotator_id="human",
            ),
        ]
        path = tmp_path / "records.jsonl"
        write_jsonl(path, records)
        loaded = read_jsonl(path)
        assert [record.model_dump() for record in loaded] == [
            record.model_dump()
            for record in sorted(
                records,
                key=lambda r: (
                    r.paper_doi,
                    r.annotator_id,
                    0 if isinstance(r, CoverageRecord) else 1,
                ),
            )
        ]

    def test_unknown_version_is_rejected(self, tmp_path):
        path = tmp_path / "bad.jsonl"
        path.write_text(
            json.dumps(
                {
                    "schema_version": "rq1-annotation/v999",
                    "paper_doi": "doi",
                    "field_type": "problem",
                    "text": "x",
                    "annotator_id": "human",
                }
            )
            + "\n",
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="invalid record"):
            read_jsonl(path)

    def test_blank_lines_are_ignored(self, tmp_path):
        path = tmp_path / "blank.jsonl"
        path.write_text("\n\n", encoding="utf-8")
        assert read_jsonl(path) == []

    def test_version_constant(self):
        assert SCHEMA_VERSION == "rq1-annotation/v1"


def test_normalized_surfaces_drops_empty_alias():
    assert normalized_surfaces("x", ("", "  ")) == {"x"}
