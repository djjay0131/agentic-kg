"""Convert the repository's existing gold set into RQ1 annotation JSONL.

Three sources are understood:

* ``packages/core/tests/extraction/fixtures/ground_truth_chain/<reviewer>/*.gold.yml``
  — the human, Claude and reconciled reviews. The converter emits one
  :class:`~agentic_kg.evaluation.schema.AnnotationRecord` per problem (and per
  nested assumption/constraint/dataset/metric/baseline when a future gold file
  carries them), plus one coverage record per paper/reviewer.
* ``docs/ground-truth/importer-output/*.yml`` — the frozen legacy importer
  output. It is a *prediction* source, never gold.
* A plain JSONL annotation file produced by this converter or by an annotator.
  The loader lives in :mod:`agentic_kg.evaluation.schema`.

Char spans are recovered, not invented: the converter looks the verbatim quote
up in the committed ``paper_<slug>.txt`` fixture (whitespace-insensitive, as
``scripts/validate_ground_truth.py`` does) and maps the match back to original
offsets. When the quote cannot be located, the span is left null rather than
guessed, and string matching carries the item.
"""

from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path
from typing import Any, Iterable, Mapping

import yaml

from agentic_kg.evaluation.schema import (
    AnnotationRecord,
    CharSpan,
    CoverageRecord,
    FieldType,
    SectionType,
)

#: slug -> DOI for the eight-paper chain, transcribed from
#: ``docs/ground-truth/README.md``. This table is deliberately **not** derived
#: from the gold YAML: ``human/paper_empire.gold.yml`` carries ``cskg``'s DOI
#: under the ``empire`` slug (a copy-paste error in an unreconciled review), so
#: deriving identity from those files would import the error into the answer key.
PAPER_DOIS: Mapping[str, str] = {
    "cskg": "10.1007/978-3-031-19433-7_39",
    "cskg2": "10.1038/s41597-025-05200-8",
    "kg_construction_survey": "10.2139/ssrn.4605059",
    "llm_ontology_gen": "10.1016/j.ipm.2025.104262",
    "fact_completion": "10.1109/access.2022.3220241",
    "kg_validation_hitl": "10.1016/j.ipm.2025.104145",
    "hypothesis_generation": "10.1016/j.knosys.2025.113280",
    "empire": "10.1109/esem56168.2023.10304795",
}

#: The importer output files are named by chain position and title, not slug.
IMPORTER_OUTPUT_SLUGS: Mapping[str, str] = {
    "1-cs-kg.yml": "cskg",
    "2-cs-kg-2-0.yml": "cskg2",
    "3-construction-of-kgs.yml": "kg_construction_survey",
    "4-llms-for-scholarly-ontology-generation.yml": "llm_ontology_gen",
    "5-completing-scientific-facts.yml": "fact_completion",
    "6-kg-validation-hitl.yml": "kg_validation_hitl",
    "7-research-hypothesis-generation.yml": "hypothesis_generation",
    "8-divide-and-conquer-the-empire.yml": "empire",
}

#: Headings are only trusted as headings when they occupy a short line. The
#: section fixtures are plain text, so a body sentence that happens to contain
#: "evaluation" must not open a section.
_MAX_HEADING_LENGTH = 90

_HEADING_PATTERNS: tuple[tuple[re.Pattern[str], SectionType], ...] = (
    (
        re.compile(
            r"^(?:a\s?b\s?s\s?t\s?r\s?a\s?c\s?t|abstract)\b[.:\u2014\u2013-]?",
            re.I,
        ),
        SectionType.ABSTRACT,
    ),
    (re.compile(r"^(?:\d+\.?\s*|[ivxl]+\.?\s*)?introduction\b", re.I), SectionType.INTRODUCTION),
    (re.compile(r"^(?:\d+\.?\s*|[ivxl]+\.?\s*)?related\s+work\b", re.I), SectionType.RELATED_WORK),
    (re.compile(r"^(?:\d+\.?\s*|[ivxl]+\.?\s*)?background\b", re.I), SectionType.BACKGROUND),
    (
        re.compile(
            r"^(?:\d+\.?\s*|[ivxl]+\.?\s*)?(?:methods?|methodology|approach)\b",
            re.I,
        ),
        SectionType.METHODS,
    ),
    (
        re.compile(
            r"^(?:\d+\.?\s*|[ivxl]+\.?\s*)?(?:experiments?|experimental\s+(?:setup|design)|"
            r"evaluation|technical\s+validation)\b",
            re.I,
        ),
        SectionType.EXPERIMENTS,
    ),
    (re.compile(r"^(?:\d+\.?\s*|[ivxl]+\.?\s*)?results?\b", re.I), SectionType.RESULTS),
    (re.compile(r"^(?:\d+\.?\s*|[ivxl]+\.?\s*)?discussion\b", re.I), SectionType.DISCUSSION),
    (re.compile(r"^(?:\d+\.?\s*|[ivxl]+\.?\s*)?limitations?\b", re.I), SectionType.LIMITATIONS),
    (re.compile(r"^(?:\d+\.?\s*|[ivxl]+\.?\s*)?future\s+work\b", re.I), SectionType.FUTURE_WORK),
    (re.compile(r"^(?:\d+\.?\s*|[ivxl]+\.?\s*)?conclusions?\b", re.I), SectionType.CONCLUSION),
    (re.compile(r"^(?:\d+\.?\s*|[ivxl]+\.?\s*)?acknowledg", re.I), SectionType.ACKNOWLEDGMENTS),
    (re.compile(r"^(?:\d+\.?\s*|[ivxl]+\.?\s*)?references\b", re.I), SectionType.REFERENCES),
    (re.compile(r"^(?:\d+\.?\s*|[ivxl]+\.?\s*)?appendix\b", re.I), SectionType.APPENDIX),
)


def _section_starts(text: str) -> list[tuple[int, str]]:
    """Return ``(char_offset, section)`` for every heading-like line."""
    starts: list[tuple[int, str]] = []
    offset = 0
    for line in text.splitlines(keepends=True):
        stripped = line.strip()
        if stripped:
            for pattern, section in _HEADING_PATTERNS:
                if not pattern.match(stripped):
                    continue
                # A run-in abstract ("Abstract. In recent years ...") puts the
                # heading and a full sentence on one line, so the short-line
                # guard would miss exactly the case it exists to serve. Other
                # headings keep the guard: a body sentence that happens to
                # start with "evaluation" must not open a section.
                if section is SectionType.ABSTRACT or len(stripped) <= _MAX_HEADING_LENGTH:
                    starts.append((offset + (len(line) - len(line.lstrip())), section.value))
                break
        offset += len(line)
    return starts


def infer_section(text: str, offset: int | None) -> str:
    """The section whose heading most recently preceded ``offset``."""
    if offset is None:
        return SectionType.UNKNOWN.value
    current = SectionType.UNKNOWN.value
    for start, section in _section_starts(text):
        if start > offset:
            break
        current = section
    return current


def _collapsed_with_map(text: str) -> tuple[str, list[int]]:
    """NFKC+casefold ``text``, collapse whitespace, map chars to original offsets.

    ``unicodedata.normalize("NFKC", ch)`` can expand one source character into
    several (``ﬁ`` -> ``fi``) and ``casefold`` can too (``ß`` -> ``ss``); every
    produced character maps back to the one source offset, so a match still
    yields a valid span in the original text. Folding here (rather than only on
    the search arguments) is what keeps the index mapping honest.
    """
    out: list[str] = []
    mapping: list[int] = []
    pending_space = False
    for index, char in enumerate(text):
        if char.isspace():
            pending_space = bool(out)
            continue
        if pending_space:
            out.append(" ")
            mapping.append(index)
            pending_space = False
        expanded = unicodedata.normalize("NFKC", char).casefold()
        for folded in expanded:
            out.append(folded)
            mapping.append(index)
    return "".join(out), mapping


def find_span(text: str, quote: str | None) -> CharSpan | None:
    """Locate ``quote`` in ``text`` modulo whitespace and ligatures.

    Case-insensitive because the extractor and reviewers disagree about
    capitalization; whitespace-insensitive because the PDF text hard-wraps
    mid-sentence. Returns ``None`` rather than raising: a quote that cannot be
    located is a labelling defect worth surfacing, not a reason to lose every
    other item in the file.
    """
    if not quote or not quote.strip():
        return None
    haystack, mapping = _collapsed_with_map(text)
    needle, _ = _collapsed_with_map(quote)
    needle = needle.strip()
    if not needle:
        return None
    index = haystack.find(needle)
    if index < 0:
        return None
    start = mapping[index]
    end = mapping[index + len(needle) - 1] + 1
    return CharSpan(start=start, end=end)


def _paper_text(source_text_dir: Path, slug: str) -> str | None:
    path = source_text_dir / f"paper_{slug}.txt"
    if not path.is_file():
        return None
    return path.read_text(encoding="utf-8")


def _nested_items(problem: Mapping[str, Any], key: str) -> Iterable[Mapping[str, Any]]:
    value = problem.get(key)
    if isinstance(value, list):
        for item in value:
            if isinstance(item, Mapping):
                yield item


def _text_of_nested(field: FieldType, item: Mapping[str, Any]) -> str | None:
    if field is FieldType.ASSUMPTION:
        return item.get("text") or item.get("statement")
    if field is FieldType.CONSTRAINT:
        return item.get("text") or item.get("description")
    return item.get("name") or item.get("text")


def _record_from_item(
    *,
    doi: str,
    slug: str,
    field: FieldType,
    text: str,
    section: str,
    span: CharSpan | None,
    quote: str | None,
    annotator_id: str,
    provenance: str,
    implicit: bool = False,
    aliases: Iterable[str] = (),
    notes: str | None = None,
) -> AnnotationRecord:
    return AnnotationRecord(
        paper_doi=doi,
        paper_slug=slug,
        section=section,
        field_type=field,
        text=text,
        aliases=tuple(aliases),
        char_span=span,
        evidence_quote=quote,
        implicit=implicit,
        annotator_id=annotator_id,
        provenance=provenance,
        notes=notes,
    )


def convert_gold_file(
    path: str | Path, source_text_dir: str | Path
) -> tuple[list[AnnotationRecord], list[CoverageRecord]]:
    """Convert one ``.gold.yml`` review into annotation + coverage records."""
    path = Path(path)
    source_text_dir = Path(source_text_dir)
    with path.open(encoding="utf-8") as handle:
        gold = yaml.safe_load(handle) or {}

    slug = gold.get("slug")
    if not slug:
        raise ValueError(f"{path}: missing slug")
    doi = PAPER_DOIS.get(slug)
    if doi is None:
        # Fall back to the file's DOI only when the slug is unknown; the known
        # table wins because at least one unreconciled review has a wrong DOI.
        doi = str(gold.get("doi") or "").strip()
    if not doi:
        raise ValueError(f"{path}: no DOI for slug {slug!r}")

    annotator = str(gold.get("reviewer") or path.parent.name)
    provenance = f"gold:{annotator}"
    text = _paper_text(source_text_dir, slug)
    records: list[AnnotationRecord] = []
    covered: set[FieldType] = set()
    seen: set[tuple[str, str, str]] = set()

    problems = gold.get("problems")
    problem_list = problems if isinstance(problems, list) else []
    if problems is not None:
        covered.add(FieldType.PROBLEM)

    for problem in problem_list:
        if not isinstance(problem, Mapping):
            continue
        statement = problem.get("statement")
        if not isinstance(statement, str) or not statement.strip():
            continue
        quote = problem.get("quoted_text")
        span = find_span(text, quote) if text else None
        offset = span.start if span else None
        section = infer_section(text, offset) if text else SectionType.UNKNOWN.value
        key = (FieldType.PROBLEM.value, section, statement.strip().casefold())
        if key in seen:
            continue
        seen.add(key)
        records.append(
            _record_from_item(
                doi=doi,
                slug=slug,
                field=FieldType.PROBLEM,
                text=statement,
                section=section,
                span=span,
                quote=quote if isinstance(quote, str) else None,
                annotator_id=annotator,
                provenance=provenance,
            )
        )

        nested_fields = (
            (FieldType.ASSUMPTION, "assumptions"),
            (FieldType.CONSTRAINT, "constraints"),
            (FieldType.DATASET, "datasets"),
            (FieldType.METRIC, "metrics"),
            (FieldType.BASELINE, "baselines"),
        )
        for field, key_name in nested_fields:
            if key_name not in problem:
                continue
            covered.add(field)
            for item in _nested_items(problem, key_name):
                item_text = _text_of_nested(field, item)
                if not isinstance(item_text, str) or not item_text.strip():
                    continue
                item_quote = item.get("quoted_text") or quote
                item_span = find_span(text, item_quote) if text else None
                item_offset = item_span.start if item_span else offset
                item_section = infer_section(text, item_offset) if text else section
                item_key = (
                    field.value,
                    item_section,
                    item_text.strip().casefold(),
                )
                if item_key in seen:
                    continue
                seen.add(item_key)
                records.append(
                    _record_from_item(
                        doi=doi,
                        slug=slug,
                        field=field,
                        text=item_text,
                        section=item_section,
                        span=item_span,
                        quote=item_quote if isinstance(item_quote, str) else None,
                        annotator_id=annotator,
                        provenance=provenance,
                        implicit=bool(item.get("implicit", False)),
                        aliases=item.get("aliases") or (),
                        notes=item.get("description"),
                    )
                )

    coverage = [
        CoverageRecord(
            paper_doi=doi,
            paper_slug=slug,
            annotator_id=annotator,
            fields=tuple(covered),
        )
    ]
    return records, coverage


def convert_gold_directory(
    root: str | Path, source_text_dir: str | Path
) -> tuple[list[AnnotationRecord], list[CoverageRecord]]:
    """Convert every ``*.gold.yml`` under ``root`` (recursively)."""
    root = Path(root)
    records: list[AnnotationRecord] = []
    coverage: list[CoverageRecord] = []
    for path in sorted(root.rglob("*.gold.yml")):
        file_records, file_coverage = convert_gold_file(path, source_text_dir)
        records.extend(file_records)
        coverage.extend(file_coverage)
    if not coverage:
        raise ValueError(f"no *.gold.yml files found under {root}")
    return records, coverage


def _parse_embedded_list(value: Any) -> list[Any]:
    """The legacy importer stores nested lists as JSON strings (``'[]'``)."""
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return []
        return parsed if isinstance(parsed, list) else []
    return []


def _importer_slug(path: Path, data: Mapping[str, Any]) -> str | None:
    if path.name in IMPORTER_OUTPUT_SLUGS:
        return IMPORTER_OUTPUT_SLUGS[path.name]
    paper = data.get("paper") or {}
    n = str(paper.get("n") or "")
    for name, slug in IMPORTER_OUTPUT_SLUGS.items():
        if name.startswith(f"{n}-"):
            return slug
    doi = str(paper.get("doi") or "").strip().casefold()
    for slug, known_doi in PAPER_DOIS.items():
        if known_doi.casefold() == doi:
            return slug
    return None


def convert_importer_output_file(
    path: str | Path,
    source_text_dir: str | Path,
    *,
    annotator_id: str = "frozen-importer-output",
) -> list[AnnotationRecord]:
    """Convert one frozen importer-output YAML into prediction records."""
    path = Path(path)
    source_text_dir = Path(source_text_dir)
    with path.open(encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    slug = _importer_slug(path, data)
    if slug is None:
        raise ValueError(f"{path}: cannot resolve slug from filename or paper block")
    doi = PAPER_DOIS[slug]
    text = _paper_text(source_text_dir, slug)
    records: list[AnnotationRecord] = []

    for problem in data.get("problems") or []:
        if not isinstance(problem, Mapping):
            continue
        statement = problem.get("statement")
        if not isinstance(statement, str) or not statement.strip():
            continue
        quote = problem.get("quoted_text")
        span = find_span(text, quote) if text else None
        offset = span.start if span else None
        section = infer_section(text, offset) if text else SectionType.UNKNOWN.value
        records.append(
            _record_from_item(
                doi=doi,
                slug=slug,
                field=FieldType.PROBLEM,
                text=statement,
                section=section,
                span=span,
                quote=quote if isinstance(quote, str) else None,
                annotator_id=annotator_id,
                provenance="prediction:importer-output",
            )
        )

        for field, key_name in (
            (FieldType.ASSUMPTION, "assumptions"),
            (FieldType.CONSTRAINT, "constraints"),
            (FieldType.DATASET, "datasets"),
            (FieldType.METRIC, "metrics"),
            (FieldType.BASELINE, "baselines"),
        ):
            for item in _parse_embedded_list(problem.get(key_name)):
                if not isinstance(item, Mapping):
                    continue
                item_text = _text_of_nested(field, item)
                if not isinstance(item_text, str) or not item_text.strip():
                    continue
                records.append(
                    _record_from_item(
                        doi=doi,
                        slug=slug,
                        field=field,
                        text=item_text,
                        section=section,
                        span=span,
                        quote=quote if isinstance(quote, str) else None,
                        annotator_id=annotator_id,
                        provenance="prediction:importer-output",
                        implicit=bool(item.get("implicit", False)),
                        aliases=item.get("aliases") or (),
                        notes=item.get("description"),
                    )
                )
    return records


def convert_importer_output_directory(
    root: str | Path,
    source_text_dir: str | Path,
    *,
    annotator_id: str = "frozen-importer-output",
) -> list[AnnotationRecord]:
    """Convert every ``*.yml`` frozen importer output under ``root``."""
    root = Path(root)
    records: list[AnnotationRecord] = []
    for path in sorted(root.glob("*.yml")):
        records.extend(
            convert_importer_output_file(path, source_text_dir, annotator_id=annotator_id)
        )
    if not records:
        raise ValueError(f"no importer-output *.yml files found under {root}")
    return records
