"""Inter-annotator agreement for the RQ1 annotation set.

Two curve-free, hand-checkable statistics are reported:

* **Cohen's kappa** for every pair of annotators, over the union of items in the
  ``(paper, field)`` cells both annotators reviewed. Absence of a record inside
  a reviewed cell is a negative label; a cell one annotator did not review is
  excluded rather than silently labelled negative (that is what the coverage
  records are for).
* **Krippendorff's alpha** (nominal) over all annotators at once, allowing
  missing ratings. Two annotators with identical labels yield alpha 1.0;
  chance-level agreement yields approximately 0.0.

Both return ``None`` with a reason when they are undefined: fewer than two
annotators, no commonly reviewed cells, or no variation in the labels (kappa's
expected disagreement is zero, so the ratio has no meaning).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from itertools import combinations

from agentic_kg.evaluation.schema import (
    AnnotationRecord,
    CoverageRecord,
    normalize_text,
)


@dataclass(frozen=True)
class KappaResult:
    annotator_a: str
    annotator_b: str
    value: float | None
    observed_agreement: float | None
    expected_agreement: float | None
    n_items: int
    note: str | None = None


@dataclass(frozen=True)
class AlphaResult:
    value: float | None
    n_annotators: int
    n_units: int
    n_ratings: int
    observed_disagreement: float | None
    expected_disagreement: float | None
    note: str | None = None


@dataclass(frozen=True)
class AgreementReport:
    annotators: tuple[str, ...]
    pairwise_kappa: tuple[KappaResult, ...] = ()
    krippendorff_alpha: AlphaResult | None = None
    notes: tuple[str, ...] = field(default_factory=tuple)


def _covered_cells(coverage: list[CoverageRecord]) -> dict[str, set[tuple[str, str]]]:
    covered: dict[str, set[tuple[str, str]]] = {}
    for record in coverage:
        for field_type in record.fields:
            covered.setdefault(record.annotator_id, set()).add((record.paper_doi, field_type.value))
    return covered


def _labels_by_annotator(
    records: list[AnnotationRecord],
) -> dict[str, dict[tuple[str, str, str], int]]:
    labels: dict[str, dict[tuple[str, str, str], int]] = {}
    for record in records:
        key = (record.paper_doi, record.field_type.value, normalize_text(record.text))
        labels.setdefault(record.annotator_id, {})[key] = 1 if record.present else 0
    return labels


def cohen_kappa(a: list[int], b: list[int]) -> tuple[float | None, float, float | None, str | None]:
    """Binary Cohen's kappa and the agreement legs it is built from."""
    if len(a) != len(b):
        raise ValueError("label vectors must be aligned")
    n = len(a)
    if n == 0:
        return None, 0.0, None, "no commonly labelled items"
    observed = sum(1 for left, right in zip(a, b) if left == right) / n
    left_pos = sum(a) / n
    right_pos = sum(b) / n
    expected = left_pos * right_pos + (1 - left_pos) * (1 - right_pos)
    if expected >= 1.0:
        return None, observed, expected, "no label variation; kappa is undefined"
    return (observed - expected) / (1 - expected), observed, expected, None


def _pairwise_kappa(
    annotators: list[str],
    labels: dict[str, dict[tuple[str, str, str], int]],
    covered: dict[str, set[tuple[str, str]]],
) -> list[KappaResult]:
    results: list[KappaResult] = []
    for left, right in combinations(annotators, 2):
        common = covered.get(left, set()) & covered.get(right, set())
        if not common:
            results.append(
                KappaResult(
                    annotator_a=left,
                    annotator_b=right,
                    value=None,
                    observed_agreement=None,
                    expected_agreement=None,
                    n_items=0,
                    note="no commonly reviewed (paper, field) cells",
                )
            )
            continue
        keys: set[tuple[str, str, str]] = set()
        for cell in common:
            for key in labels.get(left, {}):
                if (key[0], key[1]) == cell:
                    keys.add(key)
            for key in labels.get(right, {}):
                if (key[0], key[1]) == cell:
                    keys.add(key)
        ordered = sorted(keys)
        a = [labels.get(left, {}).get(key, 0) for key in ordered]
        b = [labels.get(right, {}).get(key, 0) for key in ordered]
        value, observed, expected, note = cohen_kappa(a, b)
        results.append(
            KappaResult(
                annotator_a=left,
                annotator_b=right,
                value=value,
                observed_agreement=observed,
                expected_agreement=expected,
                n_items=len(ordered),
                note=note,
            )
        )
    return results


def krippendorff_alpha_nominal(
    units: list[list[int | None]],
) -> tuple[float | None, float | None, float | None, int, int, str | None]:
    """Nominal Krippendorff's alpha over units of optional binary ratings.

    ``units[u]`` is the list of ratings for unit ``u`` (``None`` = missing).
    Returns ``(alpha, Do, De, n_units, n_ratings, note)``.
    """
    cleaned = [[int(v) for v in unit if v is not None] for unit in units]
    cleaned = [unit for unit in cleaned if unit]
    n_ratings = sum(len(unit) for unit in cleaned)
    n_units = len(cleaned)
    if n_units == 0 or n_ratings == 0:
        return None, None, None, 0, 0, "no ratings"
    if n_ratings <= n_units:
        return None, None, None, n_units, n_ratings, "every unit has a single rating"

    observed_numerator = 0.0
    for unit in cleaned:
        m = len(unit)
        if m < 2:
            continue
        ones = sum(unit)
        zeros = m - ones
        observed_numerator += (ones * zeros * 2) / (m - 1)
    observed = observed_numerator / n_ratings

    ones = sum(sum(unit) for unit in cleaned)
    zeros = n_ratings - ones
    expected = (2 * ones * zeros) / (n_ratings * (n_ratings - 1))
    if expected <= 0.0:
        note = "no label variation; alpha is undefined"
        return None, observed, expected, n_units, n_ratings, note
    return 1.0 - observed / expected, observed, expected, n_units, n_ratings, None


def _krippendorff(
    annotators: list[str],
    labels: dict[str, dict[tuple[str, str, str], int]],
    covered: dict[str, set[tuple[str, str]]],
) -> AlphaResult | None:
    all_cells: set[tuple[str, str]] = set()
    for annotator in annotators:
        all_cells |= covered.get(annotator, set())
    multi_cells = {
        cell
        for cell in all_cells
        if sum(1 for annotator in annotators if cell in covered.get(annotator, set())) >= 2
    }
    if not multi_cells:
        return AlphaResult(
            value=None,
            n_annotators=len(annotators),
            n_units=0,
            n_ratings=0,
            observed_disagreement=None,
            expected_disagreement=None,
            note="no (paper, field) cell was reviewed by at least two annotators",
        )

    keys: set[tuple[str, str, str]] = set()
    for cell in multi_cells:
        for annotator in annotators:
            if cell not in covered.get(annotator, set()):
                continue
            for key in labels.get(annotator, {}):
                if (key[0], key[1]) == cell:
                    keys.add(key)

    units: list[list[int | None]] = []
    for key in sorted(keys):
        cell = (key[0], key[1])
        ratings: list[int | None] = []
        for annotator in annotators:
            if cell not in covered.get(annotator, set()):
                ratings.append(None)
            else:
                ratings.append(labels.get(annotator, {}).get(key, 0))
        units.append(ratings)

    value, observed, expected, n_units, n_ratings, note = krippendorff_alpha_nominal(units)
    return AlphaResult(
        value=value,
        n_annotators=len(annotators),
        n_units=n_units,
        n_ratings=n_ratings,
        observed_disagreement=observed,
        expected_disagreement=expected,
        note=note,
    )


def compute_agreement(
    records: list[AnnotationRecord],
    coverage: list[CoverageRecord],
) -> AgreementReport:
    """Pairwise kappa + Krippendorff's alpha over all annotators in the set."""
    annotators = sorted(
        {record.annotator_id for record in records} | {record.annotator_id for record in coverage}
    )
    if len(annotators) < 2:
        return AgreementReport(
            annotators=tuple(annotators),
            notes=("fewer than two annotators; agreement is undefined",),
        )
    covered = _covered_cells(coverage)
    labels = _labels_by_annotator(records)
    return AgreementReport(
        annotators=tuple(annotators),
        pairwise_kappa=tuple(_pairwise_kappa(annotators, labels, covered)),
        krippendorff_alpha=_krippendorff(annotators, labels, covered),
    )
