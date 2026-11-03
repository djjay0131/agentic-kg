"""Precision / recall / F1, clustered bootstrap intervals, and error buckets.

The unit of aggregation is a :class:`Cell`: one paper, one field type, one
section, with TP/FP/FN counts. Building the cells once and deriving every view
from them is what makes micro and macro numbers agree with each other and lets
the bootstrap resample *papers* (the independent unit) rather than items (which
are clustered within papers and would produce intervals that are far too narrow).

Honest-null policy, inherited from the migration evaluator's ADR-0009 stance:

* precision is ``None`` when no predictions were made (no denominator);
* recall is ``None`` when no gold items were reviewed (no denominator);
* F1 is ``None`` when either input is ``None``;
* a confidence interval is ``None`` when fewer than
  :data:`MIN_CI_RESAMPLES` resamples produced a defined value.
"""

from __future__ import annotations

import random
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Iterable, Sequence

from agentic_kg.evaluation.agreement import AgreementReport, compute_agreement
from agentic_kg.evaluation.matching import GroupMatch, MatchConfig, match_items
from agentic_kg.evaluation.schema import (
    AnnotationRecord,
    CoverageRecord,
    FieldType,
    SectionType,
)

DEFAULT_SEED = 20261026
DEFAULT_RESAMPLES = 1000
MIN_CI_RESAMPLES = 20

ERROR_BUCKETS: tuple[str, ...] = (
    "missed_implicit_assumption",
    "missed_field",
    "wrong_section",
    "bad_evidence_link",
    "hallucinated_field",
)


@dataclass(frozen=True)
class Score:
    value: float | None
    ci_low: float | None = None
    ci_high: float | None = None
    sufficient: bool = True
    note: str | None = None

    @staticmethod
    def measured(value: float) -> Score:
        return Score(value=value)

    @staticmethod
    def insufficient(note: str) -> Score:
        return Score(value=None, sufficient=False, note=note)


@dataclass(frozen=True)
class PRF:
    precision: Score
    recall: Score
    f1: Score
    tp: int
    fp: int
    fn: int

    def to_dict(self) -> dict:
        return {
            "precision": self.precision,
            "recall": self.recall,
            "f1": self.f1,
            "tp": self.tp,
            "fp": self.fp,
            "fn": self.fn,
        }


@dataclass(frozen=True)
class Cell:
    paper: str
    field: str
    section: str
    tp: int = 0
    fp: int = 0
    fn: int = 0

    def merge(self, other: Cell) -> Cell:
        if (self.paper, self.field, self.section) != (
            other.paper,
            other.field,
            other.section,
        ):
            raise ValueError("cannot merge cells with different keys")
        return Cell(
            paper=self.paper,
            field=self.field,
            section=self.section,
            tp=self.tp + other.tp,
            fp=self.fp + other.fp,
            fn=self.fn + other.fn,
        )


@dataclass
class BucketCounts:
    counts: dict[str, int] = field(default_factory=dict)
    by_field: dict[str, dict[str, int]] = field(default_factory=dict)
    examples: dict[str, list[dict]] = field(default_factory=dict)

    def add(self, bucket: str, *, field: str, example: dict) -> None:
        self.counts[bucket] = self.counts.get(bucket, 0) + 1
        self.by_field.setdefault(bucket, {})
        self.by_field[bucket][field] = self.by_field[bucket].get(field, 0) + 1
        bucket_examples = self.examples.setdefault(bucket, [])
        if len(bucket_examples) < 5:
            bucket_examples.append(example)

    def to_dict(self) -> dict:
        return {
            "counts": dict(sorted(self.counts.items())),
            "by_field": {
                bucket: dict(sorted(fields.items()))
                for bucket, fields in sorted(self.by_field.items())
            },
            "examples": {bucket: examples for bucket, examples in sorted(self.examples.items())},
        }


@dataclass
class ReportData:
    cells: list[Cell]
    bucket: BucketCounts
    graded_papers: list[str]
    gold_papers: list[str]
    prediction_papers: list[str]
    graded_fields: list[str]
    ungraded_fields: list[str]
    ungraded_predictions: int
    matches: list[GroupMatch]
    agreement: AgreementReport | None = None
    notes: list[str] = field(default_factory=list)


def _prf_from_counts(tp: int, fp: int, fn: int) -> tuple[Score, Score, Score]:
    precision = (
        Score.insufficient("no predictions to measure precision")
        if tp + fp == 0
        else Score.measured(tp / (tp + fp))
    )
    recall = (
        Score.insufficient("no gold items reviewed for this slice")
        if tp + fn == 0
        else Score.measured(tp / (tp + fn))
    )
    if precision.value is None or recall.value is None:
        f1 = Score.insufficient("F1 needs both precision and recall")
    elif precision.value + recall.value == 0.0:
        f1 = Score.measured(0.0)
    else:
        f1 = Score.measured(2 * precision.value * recall.value / (precision.value + recall.value))
    return precision, recall, f1


def _sum_cells(cells: Iterable[Cell]) -> tuple[int, int, int]:
    tp = fp = fn = 0
    for cell in cells:
        tp += cell.tp
        fp += cell.fp
        fn += cell.fn
    return tp, fp, fn


def _micro_block(cells: Sequence[Cell]) -> tuple[Score, Score, Score, int, int, int]:
    tp, fp, fn = _sum_cells(cells)
    precision, recall, f1 = _prf_from_counts(tp, fp, fn)
    return precision, recall, f1, tp, fp, fn


def _macro_block(cells: Sequence[Cell], paper_keys: Sequence[str]) -> tuple[Score, Score, Score]:
    by_paper: dict[str, list[Cell]] = defaultdict(list)
    for cell in cells:
        by_paper[cell.paper].append(cell)
    precisions: list[float] = []
    recalls: list[float] = []
    f1s: list[float] = []
    for paper in paper_keys:
        paper_cells = by_paper.get(paper, [])
        if not paper_cells:
            continue
        precision, recall, f1 = _prf_from_counts(*_sum_cells(paper_cells))
        if precision.value is not None:
            precisions.append(precision.value)
        if recall.value is not None:
            recalls.append(recall.value)
        if f1.value is not None:
            f1s.append(f1.value)
    if not precisions and not recalls and not f1s:
        note = "no paper contributed a measurable slice"
        return (
            Score.insufficient(note),
            Score.insufficient(note),
            Score.insufficient(note),
        )
    return (
        Score.measured(sum(precisions) / len(precisions))
        if precisions
        else Score.insufficient("no paper had a precision denominator"),
        Score.measured(sum(recalls) / len(recalls))
        if recalls
        else Score.insufficient("no paper had a recall denominator"),
        Score.measured(sum(f1s) / len(f1s))
        if f1s
        else Score.insufficient("no paper had a defined F1"),
    )


def _percentile(values: Sequence[float], fraction: float) -> float:
    if not values:
        raise ValueError("percentile of empty sequence")
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = fraction * (len(ordered) - 1)
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def _collect_ci(
    values: list[float], ci: float = 0.95
) -> tuple[float | None, float | None, str | None]:
    if len(values) < MIN_CI_RESAMPLES:
        return (
            None,
            None,
            f"{len(values)} defined resamples (< {MIN_CI_RESAMPLES}); interval withheld",
        )
    alpha = (1 - ci) / 2
    return _percentile(values, alpha), _percentile(values, 1 - alpha), None


def _with_ci(score: Score, values: list[float]) -> Score:
    if score.value is None:
        return score
    low, high, note = _collect_ci(values)
    return Score(
        value=score.value,
        ci_low=low,
        ci_high=high,
        sufficient=True,
        note=note,
    )


def _bootstrap_micro(
    cells: Sequence[Cell],
    paper_keys: Sequence[str],
    *,
    n_resamples: int,
    seed: int,
) -> tuple[Score, Score, Score]:
    rng = random.Random(seed)
    by_paper: dict[str, list[Cell]] = defaultdict(list)
    for cell in cells:
        by_paper[cell.paper].append(cell)
    precisions: list[float] = []
    recalls: list[float] = []
    f1s: list[float] = []
    for _ in range(n_resamples):
        sample = [paper_keys[rng.randrange(len(paper_keys))] for _ in paper_keys]
        sampled_cells = [cell for paper in sample for cell in by_paper.get(paper, [])]
        precision, recall, f1 = _prf_from_counts(*_sum_cells(sampled_cells))
        if precision.value is not None:
            precisions.append(precision.value)
        if recall.value is not None:
            recalls.append(recall.value)
        if f1.value is not None:
            f1s.append(f1.value)
    precision, recall, f1, tp, fp, fn = _micro_block(cells)
    return (
        _with_ci(precision, precisions),
        _with_ci(recall, recalls),
        _with_ci(f1, f1s),
    )


def _bootstrap_macro(
    cells: Sequence[Cell],
    paper_keys: Sequence[str],
    *,
    n_resamples: int,
    seed: int,
) -> tuple[Score, Score, Score]:
    rng = random.Random(seed)
    precisions: list[float] = []
    recalls: list[float] = []
    f1s: list[float] = []
    for _ in range(n_resamples):
        sample = [paper_keys[rng.randrange(len(paper_keys))] for _ in paper_keys]
        precision, recall, f1 = _macro_block(cells, sample)
        if precision.value is not None:
            precisions.append(precision.value)
        if recall.value is not None:
            recalls.append(recall.value)
        if f1.value is not None:
            f1s.append(f1.value)
    precision, recall, f1 = _macro_block(cells, paper_keys)
    return (
        _with_ci(precision, precisions),
        _with_ci(recall, recalls),
        _with_ci(f1, f1s),
    )


def _block(
    cells: Sequence[Cell],
    paper_keys: Sequence[str],
    *,
    n_resamples: int,
    seed: int,
    bootstrap: bool,
) -> PRF:
    if bootstrap and n_resamples > 0 and len(paper_keys) >= 2:
        precision, recall, f1 = _bootstrap_micro(
            cells, paper_keys, n_resamples=n_resamples, seed=seed
        )
    else:
        p, r, f, _, _, _ = _micro_block(cells)
        precision, recall, f1 = p, r, f
    tp, fp, fn = _sum_cells(cells)
    return PRF(precision=precision, recall=recall, f1=f1, tp=tp, fp=fp, fn=fn)


def _macro(
    cells: Sequence[Cell],
    paper_keys: Sequence[str],
    *,
    n_resamples: int,
    seed: int,
    bootstrap: bool,
) -> PRF:
    if bootstrap and n_resamples > 0 and len(paper_keys) >= 2:
        precision, recall, f1 = _bootstrap_macro(
            cells, paper_keys, n_resamples=n_resamples, seed=seed
        )
    else:
        precision, recall, f1 = _macro_block(cells, paper_keys)
    return PRF(
        precision=precision,
        recall=recall,
        f1=f1,
        tp=sum(cell.tp for cell in cells),
        fp=sum(cell.fp for cell in cells),
        fn=sum(cell.fn for cell in cells),
    )


def _cells_from_matches(matches: Iterable[GroupMatch]) -> list[Cell]:
    cells: list[Cell] = []
    for group in matches:
        for pair in group.pairs:
            cells.append(
                Cell(
                    paper=group.paper_doi,
                    field=group.field_type,
                    section=pair.gold.section,
                    tp=1,
                )
            )
        for gold in group.unmatched_gold:
            cells.append(
                Cell(
                    paper=group.paper_doi,
                    field=group.field_type,
                    section=gold.section,
                    fn=1,
                )
            )
        for prediction in group.unmatched_predictions:
            cells.append(
                Cell(
                    paper=group.paper_doi,
                    field=group.field_type,
                    section=prediction.section,
                    fp=1,
                )
            )
    return cells


def _example(item: AnnotationRecord, **extra) -> dict:
    payload = {
        "paper": item.paper_slug or item.paper_doi,
        "section": item.section,
        "field": item.field_type.value,
        "text": item.text,
        "item_id": item.item_id,
    }
    payload.update(extra)
    return payload


def _error_buckets(matches: Iterable[GroupMatch]) -> BucketCounts:
    bucket = BucketCounts()
    for group in matches:
        field = group.field_type
        for pair in group.pairs:
            if pair.section_mismatch:
                bucket.add(
                    "wrong_section",
                    field=field,
                    example=_example(
                        pair.gold,
                        predicted_section=pair.prediction.section,
                        match_method=pair.method,
                    ),
                )
            if pair.bad_evidence_link:
                bucket.add(
                    "bad_evidence_link",
                    field=field,
                    example=_example(pair.gold, match_method=pair.method),
                )
        for gold in group.unmatched_gold:
            if gold.field_type is FieldType.ASSUMPTION and gold.implicit:
                bucket.add(
                    "missed_implicit_assumption",
                    field=field,
                    example=_example(gold),
                )
            else:
                bucket.add("missed_field", field=field, example=_example(gold))
        for prediction in group.unmatched_predictions:
            bucket.add(
                "hallucinated_field",
                field=field,
                example=_example(prediction),
            )
    return bucket


def _select_gold_annotators(
    records: Sequence[AnnotationRecord],
    coverage: Sequence[CoverageRecord],
) -> tuple[list[AnnotationRecord], list[CoverageRecord]]:
    """Pick one answer key per paper: reconciled > human > claude > any.

    ``human/`` and ``claude/`` are independent reviews, not answer keys; only
    ``reconciled/`` is authoritative. Mixing reviews would turn disagreement
    into duplicate gold obligations. When no reconciled file exists we use the
    human review, and then Claude's, and say so in the report — the alternative
    is reporting no number at all for two papers that do have a usable review.
    """
    priority = {"reconciled": 0, "human": 1, "claude": 2}
    by_paper: dict[str, set[str]] = defaultdict(set)
    for record in coverage:
        by_paper[record.paper_doi].add(record.annotator_id)
    selected: dict[str, str] = {}
    for paper, annotators in by_paper.items():
        selected[paper] = sorted(annotators, key=lambda name: (priority.get(name, 99), name))[0]

    gold_records = [
        record for record in records if selected.get(record.paper_doi) == record.annotator_id
    ]
    gold_coverage = [
        record for record in coverage if selected.get(record.paper_doi) == record.annotator_id
    ]
    return gold_records, gold_coverage


def build_report_data(
    annotations: Sequence[AnnotationRecord],
    coverage: Sequence[CoverageRecord],
    predictions: Sequence[AnnotationRecord],
    *,
    match_config: MatchConfig | None = None,
    annotator: str | None = None,
    agreement_records: Sequence[AnnotationRecord] | None = None,
) -> ReportData:
    """Select the answer key, align predictions, and build all metric cells.

    ``agreement_records`` defaults to the full annotation set before answer-key
    selection, because agreement is a property of the reviewers, not of the
    projection one of them wins.
    """
    match_config = match_config or MatchConfig()
    all_records = list(annotations)
    if annotator is not None:
        gold_records = [r for r in all_records if r.annotator_id == annotator]
        gold_coverage = [c for c in coverage if c.annotator_id == annotator]
        selected_names = {annotator}
    else:
        gold_records, gold_coverage = _select_gold_annotators(all_records, coverage)
        selected_names = {r.annotator_id for r in gold_coverage}

    gold_papers = sorted({r.paper_doi for r in gold_coverage})
    prediction_papers = sorted({r.paper_doi for r in predictions})
    covered_by_paper_field: dict[str, set[str]] = defaultdict(set)
    for record in gold_coverage:
        covered_by_paper_field[record.paper_doi].update(field.value for field in record.fields)

    graded_fields = sorted(
        {field for fields in covered_by_paper_field.values() for field in fields}
    )
    ungraded_fields = sorted(field.value for field in FieldType if field.value not in graded_fields)

    graded_matches: list[GroupMatch] = []
    ungraded_predictions = 0
    gradeable_records: list[AnnotationRecord] = []
    for record in gold_records:
        fields = covered_by_paper_field.get(record.paper_doi, set())
        if record.field_type.value in fields:
            gradeable_records.append(record)

    gradeable_predictions: list[AnnotationRecord] = []
    for record in predictions:
        fields = covered_by_paper_field.get(record.paper_doi, set())
        if record.field_type.value in fields:
            gradeable_predictions.append(record)
        else:
            ungraded_predictions += 1

    graded_matches = match_items(gradeable_records, gradeable_predictions, match_config)
    cells = _cells_from_matches(graded_matches)
    graded_papers = sorted(
        {cell.paper for cell in cells if cell.tp + cell.fp + cell.fn > 0}
        | {
            paper
            for paper in gold_papers
            if any(field in covered_by_paper_field.get(paper, set()) for field in graded_fields)
        }
    )

    agreement_source = list(agreement_records) if agreement_records is not None else all_records
    agreement = compute_agreement(agreement_source, list(coverage))
    notes: list[str] = []
    if selected_names:
        notes.append("answer key annotator(s): " + ", ".join(sorted(selected_names)))
    if len(gold_papers) < len(prediction_papers):
        notes.append(
            f"{len(gold_papers)} of {len(prediction_papers)} predicted papers have "
            "annotated gold; predictions on the rest are ungraded, not false positives"
        )
    if ungraded_fields:
        notes.append(
            "field types with no annotated gold, metrics are honest nulls: "
            + ", ".join(ungraded_fields)
        )
    if ungraded_predictions:
        notes.append(
            f"{ungraded_predictions} predictions fall outside annotated "
            "(paper, field) cells and are excluded from precision"
        )
    return ReportData(
        cells=cells,
        bucket=_error_buckets(graded_matches),
        graded_papers=graded_papers,
        gold_papers=gold_papers,
        prediction_papers=prediction_papers,
        graded_fields=graded_fields,
        ungraded_fields=ungraded_fields,
        ungraded_predictions=ungraded_predictions,
        matches=graded_matches,
        agreement=agreement,
        notes=notes,
    )


def build_metrics(
    data: ReportData,
    *,
    n_resamples: int = DEFAULT_RESAMPLES,
    seed: int = DEFAULT_SEED,
    bootstrap: bool = True,
) -> dict:
    """Render the point estimates and clustered CIs into a JSON-ready dict."""
    papers = data.graded_papers
    fields = data.graded_fields
    sections = sorted({cell.section for cell in data.cells}) or [SectionType.UNKNOWN.value]

    overall_micro = _block(
        data.cells,
        papers,
        n_resamples=n_resamples,
        seed=seed,
        bootstrap=bootstrap,
    )
    overall_macro = _macro(
        data.cells,
        papers,
        n_resamples=n_resamples,
        seed=seed,
        bootstrap=bootstrap,
    )

    by_field: dict[str, dict] = {}
    for field_name in fields:
        field_cells = [cell for cell in data.cells if cell.field == field_name]
        by_field[field_name] = {
            "micro": _block(
                field_cells,
                papers,
                n_resamples=n_resamples,
                seed=seed,
                bootstrap=bootstrap,
            ).to_dict(),
            "macro": _macro(
                field_cells,
                papers,
                n_resamples=n_resamples,
                seed=seed,
                bootstrap=bootstrap,
            ).to_dict(),
        }
    for field_name in data.ungraded_fields:
        by_field[field_name] = {
            "micro": PRF(
                precision=Score.insufficient("field type was not annotated"),
                recall=Score.insufficient("field type was not annotated"),
                f1=Score.insufficient("field type was not annotated"),
                tp=0,
                fp=0,
                fn=0,
            ).to_dict(),
            "macro": PRF(
                precision=Score.insufficient("field type was not annotated"),
                recall=Score.insufficient("field type was not annotated"),
                f1=Score.insufficient("field type was not annotated"),
                tp=0,
                fp=0,
                fn=0,
            ).to_dict(),
        }

    by_section: dict[str, dict] = {}
    for section in sections:
        section_cells = [cell for cell in data.cells if cell.section == section]
        by_section[section] = {
            "micro": _block(
                section_cells,
                papers,
                n_resamples=n_resamples,
                seed=seed,
                bootstrap=bootstrap,
            ).to_dict(),
            "macro": _macro(
                section_cells,
                papers,
                n_resamples=n_resamples,
                seed=seed,
                bootstrap=bootstrap,
            ).to_dict(),
        }

    agreement = data.agreement
    agreement_payload = None
    if agreement is not None:
        agreement_payload = {
            "annotators": list(agreement.annotators),
            "pairwise_kappa": [
                {
                    "annotator_a": result.annotator_a,
                    "annotator_b": result.annotator_b,
                    "value": result.value,
                    "observed_agreement": result.observed_agreement,
                    "expected_agreement": result.expected_agreement,
                    "n_items": result.n_items,
                    "note": result.note,
                }
                for result in agreement.pairwise_kappa
            ],
            "krippendorff_alpha": None
            if agreement.krippendorff_alpha is None
            else {
                "value": agreement.krippendorff_alpha.value,
                "n_annotators": agreement.krippendorff_alpha.n_annotators,
                "n_units": agreement.krippendorff_alpha.n_units,
                "n_ratings": agreement.krippendorff_alpha.n_ratings,
                "observed_disagreement": agreement.krippendorff_alpha.observed_disagreement,
                "expected_disagreement": agreement.krippendorff_alpha.expected_disagreement,
                "note": agreement.krippendorff_alpha.note,
            },
            "notes": list(agreement.notes),
        }

    return {
        "overall": {
            "micro": overall_micro.to_dict(),
            "macro": overall_macro.to_dict(),
        },
        "by_field": by_field,
        "by_section": by_section,
        "agreement": agreement_payload,
        "error_buckets": data.bucket.to_dict(),
        "coverage": {
            "gold_papers": data.gold_papers,
            "prediction_papers": data.prediction_papers,
            "graded_papers": data.graded_papers,
            "graded_fields": data.graded_fields,
            "ungraded_fields": data.ungraded_fields,
            "ungraded_predictions": data.ungraded_predictions,
        },
        "notes": data.notes,
    }


__all__ = [
    "DEFAULT_RESAMPLES",
    "DEFAULT_SEED",
    "ERROR_BUCKETS",
    "PRF",
    "Score",
    "build_metrics",
    "build_report_data",
    "match_items",
]
