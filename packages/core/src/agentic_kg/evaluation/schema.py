"""Versioned annotation / prediction records for the RQ1 harness.

The on-disk format is JSON Lines: one object per line. Two record types share
the file format:

* ``record_type: "annotation"`` — one *positive* item an annotator marked as
  present (or, with ``present: false``, explicitly marked absent). One line per
  item is the contract the brief asks for.
* ``record_type: "coverage"`` — a small metadata record saying which field
  types an annotator actually reviewed for a paper. It is what lets the harness
  distinguish *"the annotator found zero datasets"* from *"nobody annotated
  datasets"*. Without it, every unlabelled field would silently become a
  negative label and every extraction in it a false positive.

Both carry the same ``schema_version``. Bump :data:`SCHEMA_VERSION` for any
incompatible field change; loaders reject an unknown version rather than
half-reading it.

The schema is intentionally flat and boring. Char spans are optional because
the existing gold set records verbatim quotes, not offsets; the converter
recovers offsets by locating the quote in the committed paper text. Matching
falls back to normalized string equality when a span is missing.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from enum import StrEnum
from pathlib import Path
from typing import Iterable, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SCHEMA_VERSION = "rq1-annotation/v1"


#: The six extraction fields RQ1 grades. Deliberately narrower than the E-8
#: entity model: topics/concepts/models/methods are a different research
#: question (E-8) and a different gold set.
class FieldType(StrEnum):
    PROBLEM = "problem"
    ASSUMPTION = "assumption"
    CONSTRAINT = "constraint"
    DATASET = "dataset"
    METRIC = "metric"
    BASELINE = "baseline"


FIELD_TYPES: tuple[FieldType, ...] = tuple(FieldType)


#: Section labels used for stratification. Free-form section names are accepted
#: (a paper may legitimately have a section we have never seen); these are the
#: canonical values the converter emits so the report table is stable.
class SectionType(StrEnum):
    ABSTRACT = "abstract"
    INTRODUCTION = "introduction"
    RELATED_WORK = "related_work"
    BACKGROUND = "background"
    METHODS = "methods"
    EXPERIMENTS = "experiments"
    RESULTS = "results"
    DISCUSSION = "discussion"
    LIMITATIONS = "limitations"
    FUTURE_WORK = "future_work"
    CONCLUSION = "conclusion"
    ACKNOWLEDGMENTS = "acknowledgments"
    REFERENCES = "references"
    APPENDIX = "appendix"
    UNKNOWN = "unknown"


_PUNCT_EDGES = " \t\r\n.,;:!?()[]{}<>\"'`"


def normalize_text(value: str) -> str:
    """Deterministic surface normalization used by the matcher.

    NFKC folds ligatures (``ﬁ`` → ``fi``) and compatibility forms, which is
    essential because the PDF extractor emits ligatures while human annotators
    type the expanded spelling. Case is folded, whitespace collapsed, Unicode
    dash variants unified to ``-``, and leading/trailing punctuation stripped.

    There is deliberately no stemming, edit distance, or optional knob here:
    a true positive must be literal (after normalization) so nobody can tune a
    threshold until an extractor looks good. Span IoU and the embedding flag are
    the only non-literal matchers, and both are visible in the report.
    """
    text = unicodedata.normalize("NFKC", value)
    text = re.sub(r"[\u2010\u2011\u2012\u2013\u2014\u2015]", "-", text)
    text = re.sub(r"\s+", " ", text).strip()
    text = text.casefold()
    return text.strip(_PUNCT_EDGES)


def normalized_surfaces(text: str, aliases: Iterable[str] = ()) -> frozenset[str]:
    """Every non-empty normalized surface form of one item."""
    forms = {normalize_text(text)}
    forms.update(normalize_text(alias) for alias in aliases)
    forms.discard("")
    return frozenset(forms)


def item_fingerprint(paper_doi: str, field_type: FieldType | str, text: str) -> str:
    """Stable short id for an item, used in report examples and deduping."""
    payload = f"{paper_doi.casefold()}\x1f{str(field_type)}\x1f{normalize_text(text)}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


class CharSpan(BaseModel):
    """A half-open character interval into the paper's committed text."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    start: int = Field(ge=0)
    end: int = Field(ge=0)

    @model_validator(mode="after")
    def _ordered(self) -> CharSpan:
        if self.end < self.start:
            raise ValueError(f"char span end {self.end} precedes start {self.start}")
        return self

    def as_tuple(self) -> tuple[int, int]:
        return self.start, self.end


class AnnotationRecord(BaseModel):
    """One annotated (or predicted) extraction item."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["rq1-annotation/v1"] = SCHEMA_VERSION
    record_type: Literal["annotation"] = "annotation"
    paper_doi: str = Field(min_length=1)
    paper_slug: str | None = None
    section: str = SectionType.UNKNOWN.value
    field_type: FieldType
    text: str = Field(min_length=1)
    aliases: tuple[str, ...] = ()
    char_span: CharSpan | None = None
    evidence_quote: str | None = None
    implicit: bool = False
    annotator_id: str = Field(min_length=1)
    provenance: str | None = None
    present: bool = True
    notes: str | None = None

    @field_validator("paper_doi")
    @classmethod
    def _fold_doi(cls, value: str) -> str:
        # DOIs are case-insensitive. The legacy importer emitted
        # "10.1109/ACCESS..." where every other source says lowercase; folding
        # here is what stops that from silently splitting one paper in two.
        return value.strip().casefold()

    @field_validator("section")
    @classmethod
    def _normalize_section(cls, value: str) -> str:
        value = value.strip().casefold().replace(" ", "_").replace("-", "_")
        return value or SectionType.UNKNOWN.value

    @field_validator("aliases")
    @classmethod
    def _clean_aliases(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        seen: list[str] = []
        for value in values:
            value = value.strip()
            if value and value.casefold() not in {v.casefold() for v in seen}:
                seen.append(value)
        return tuple(seen)

    @property
    def item_id(self) -> str:
        return item_fingerprint(self.paper_doi, self.field_type, self.text)

    def surfaces(self) -> frozenset[str]:
        return normalized_surfaces(self.text, self.aliases)


class CoverageRecord(BaseModel):
    """Which field types one annotator reviewed for one paper.

    ``fields`` is the *attempted* set, not the non-empty set. A reviewer who
    read the methods section for datasets and found none emits
    ``fields: ["dataset"]`` with no annotation records; that is a real measured
    zero, and the harness scores predictions against it. A reviewer who never
    looked for datasets omits the field, and dataset predictions on that paper
    are ungraded instead of being counted as hallucinations.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["rq1-annotation/v1"] = SCHEMA_VERSION
    record_type: Literal["coverage"] = "coverage"
    paper_doi: str = Field(min_length=1)
    paper_slug: str | None = None
    annotator_id: str = Field(min_length=1)
    fields: tuple[FieldType, ...] = ()

    @field_validator("paper_doi")
    @classmethod
    def _fold_doi(cls, value: str) -> str:
        return value.strip().casefold()

    @field_validator("fields")
    @classmethod
    def _dedupe_fields(cls, values: tuple[FieldType, ...]) -> tuple[FieldType, ...]:
        ordered = sorted(set(values), key=lambda f: list(FieldType).index(f))
        return tuple(ordered)


Record = AnnotationRecord | CoverageRecord


def parse_record(data: dict) -> Record:
    """Parse one JSON object into the record type it declares."""
    kind = data.get("record_type", "annotation")
    if kind == "coverage":
        return CoverageRecord.model_validate(data)
    if kind == "annotation":
        return AnnotationRecord.model_validate(data)
    raise ValueError(f"unknown record_type {kind!r}; expected 'annotation' or 'coverage'")


def read_jsonl(path: str | Path) -> list[Record]:
    """Read a JSONL file, rejecting malformed lines with the line number."""
    path = Path(path)
    records: list[Record] = []
    with path.open(encoding="utf-8") as handle:
        for lineno, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                records.append(parse_record(json.loads(line)))
            except Exception as exc:  # noqa: BLE001 - re-raise with location context
                raise ValueError(f"{path}:{lineno}: invalid record: {exc}") from exc
    return records


def write_jsonl(path: str | Path, records: Iterable[Record]) -> None:
    """Write records in a stable order so regenerating a fixture is a no-op."""
    ordered = sorted(records, key=_sort_key)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for record in ordered:
            handle.write(json.dumps(record.model_dump(mode="json"), sort_keys=True))
            handle.write("\n")


def _sort_key(record: Record) -> tuple:
    if isinstance(record, CoverageRecord):
        return (
            record.paper_doi,
            record.annotator_id,
            0,
            "",
            "",
            "",
        )
    return (
        record.paper_doi,
        record.annotator_id,
        1,
        record.field_type.value,
        record.section,
        normalize_text(record.text),
    )


def split_records(records: Iterable[Record]) -> tuple[list[AnnotationRecord], list[CoverageRecord]]:
    annotations: list[AnnotationRecord] = []
    coverage: list[CoverageRecord] = []
    for record in records:
        if isinstance(record, CoverageRecord):
            coverage.append(record)
        else:
            annotations.append(record)
    return annotations, coverage
