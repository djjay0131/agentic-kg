"""JSON and Markdown rendering for the RQ1 report.

The JSON is the machine-readable record; the Markdown is the artifact a human
reads in the CI run page. Both render the same numbers, and neither substitutes
``0.0`` for an unmeasured value: a null metric prints its reason.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from agentic_kg.evaluation.matching import MatchConfig
from agentic_kg.evaluation.metrics import ReportData, Score
from agentic_kg.evaluation.schema import SCHEMA_VERSION


def _score_payload(score: Score) -> dict:
    return {
        "value": score.value,
        "ci_low": score.ci_low,
        "ci_high": score.ci_high,
        "sufficient": score.sufficient,
        "note": score.note,
    }


def _prf_dict_to_payload(block: Mapping) -> dict:
    return {
        "precision": _score_payload(block["precision"]),
        "recall": _score_payload(block["recall"]),
        "f1": _score_payload(block["f1"]),
        "tp": block["tp"],
        "fp": block["fp"],
        "fn": block["fn"],
    }


def normalise_metrics(metrics: Mapping[str, Any]) -> dict:
    """Convert the dataclass-bearing metrics tree into plain JSON values."""
    payload: dict[str, Any] = {
        "overall": {arm: _prf_dict_to_payload(block) for arm, block in metrics["overall"].items()},
        "by_field": {
            field: {arm: _prf_dict_to_payload(block) for arm, block in arms.items()}
            for field, arms in metrics["by_field"].items()
        },
        "by_section": {
            section: {arm: _prf_dict_to_payload(block) for arm, block in arms.items()}
            for section, arms in metrics["by_section"].items()
        },
        "agreement": metrics.get("agreement"),
        "error_buckets": metrics.get("error_buckets"),
        "coverage": metrics.get("coverage"),
        "notes": metrics.get("notes", []),
    }
    return payload


def build_json_report(
    data: ReportData,
    metrics: Mapping[str, Any],
    *,
    gold_source: str,
    prediction_source: str,
    match_config: MatchConfig,
    n_resamples: int,
    seed: int,
    annotator: str | None,
    corpus: list[dict] | None = None,
) -> dict:
    corpus = corpus or []
    coverage = dict(metrics.get("coverage") or {})
    coverage["corpus_papers"] = corpus
    coverage["corpus_size"] = len(corpus)
    return {
        "schema_version": SCHEMA_VERSION,
        "harness": "rq1-extraction-reliability",
        "inputs": {
            "gold": gold_source,
            "predictions": prediction_source,
            "annotator": annotator,
            "corpus_size": len(corpus),
        },
        "matching": {
            "span_iou_threshold": match_config.span_iou_threshold,
            "embedding_similarity": match_config.embedding_enabled,
            "embedding_threshold": match_config.embedding_threshold,
            "deterministic_default": not match_config.embedding_enabled,
        },
        "bootstrap": {"resamples": n_resamples, "seed": seed, "unit": "paper"},
        "metrics": normalise_metrics({**metrics, "coverage": coverage}),
    }


def _fmt_value(score: Mapping[str, Any]) -> str:
    if score.get("value") is None:
        note = score.get("note") or "insufficient"
        return f"n/a ({note})"
    value = f"{score['value']:.3f}"
    if score.get("ci_low") is not None and score.get("ci_high") is not None:
        value += f" [{score['ci_low']:.3f}, {score['ci_high']:.3f}]"
    return value


def _row(label: str, micro: Mapping, macro: Mapping) -> str:
    return (
        f"| {label} | {_fmt_value(micro['precision'])} | "
        f"{_fmt_value(micro['recall'])} | {_fmt_value(micro['f1'])} | "
        f"{_fmt_value(macro['precision'])} | "
        f"{_fmt_value(macro['recall'])} | {_fmt_value(macro['f1'])} |"
    )


_TABLE_HEADER = (
    "| Slice | P (micro) | R (micro) | F1 (micro) | "
    "P (macro) | R (macro) | F1 (macro) |\n"
    "|---|---:|---:|---:|---:|---:|---:|"
)


def render_markdown(report: Mapping[str, Any]) -> str:
    metrics = report["metrics"]
    coverage = metrics["coverage"]
    lines: list[str] = []
    lines.append("# RQ1 extraction-reliability report")
    lines.append("")
    lines.append(
        "Precision / recall / F1 of extracted problems, assumptions, constraints, "
        "datasets, metrics and baselines against annotated gold. Micro rows pool "
        "items; macro rows average per-paper scores. Intervals are 95% clustered "
        "bootstrap over papers."
    )
    lines.append("")
    lines.append("## Inputs")
    lines.append("")
    lines.append(f"- Gold: `{report['inputs']['gold']}`")
    lines.append(f"- Predictions: `{report['inputs']['predictions']}`")
    if report["inputs"].get("annotator"):
        lines.append(f"- Annotator filter: `{report['inputs']['annotator']}`")
    lines.append(
        f"- Matcher: normalized string + span IoU >= {report['matching']['span_iou_threshold']}"
        + (
            f" + embeddings >= {report['matching']['embedding_threshold']}"
            if report["matching"]["embedding_similarity"]
            else " (no embeddings)"
        )
    )
    lines.append(
        f"- Bootstrap: {report['bootstrap']['resamples']} resamples, "
        f"seed {report['bootstrap']['seed']}, unit = paper"
    )
    lines.append("")
    lines.append("## Coverage")
    lines.append("")
    if coverage.get("corpus_size"):
        lines.append(
            f"- Corpus papers: {coverage['corpus_size']} (the eight-paper ground-truth chain)"
        )
    lines.append(f"- Predicted papers: {len(coverage['prediction_papers'])}")
    lines.append(f"- Papers with annotated gold: {len(coverage['gold_papers'])}")
    lines.append(f"- Graded papers: {len(coverage['graded_papers'])}")
    lines.append("- Graded fields: " + (", ".join(coverage["graded_fields"]) or "none"))
    lines.append(
        "- Ungraded fields (honest null): " + (", ".join(coverage["ungraded_fields"]) or "none")
    )
    lines.append(
        f"- Predictions excluded outside annotated cells: {coverage['ungraded_predictions']}"
    )
    lines.append("")
    lines.append("## Overall")
    lines.append("")
    lines.append(_TABLE_HEADER)
    lines.append(_row("overall", metrics["overall"]["micro"], metrics["overall"]["macro"]))
    lines.append("")
    lines.append("## By field type")
    lines.append("")
    lines.append(_TABLE_HEADER)
    for field, arms in metrics["by_field"].items():
        lines.append(_row(field, arms["micro"], arms["macro"]))
    lines.append("")
    lines.append("## By section type")
    lines.append("")
    lines.append(_TABLE_HEADER)
    for section, arms in metrics["by_section"].items():
        lines.append(_row(section, arms["micro"], arms["macro"]))
    lines.append("")

    agreement = metrics.get("agreement")
    lines.append("## Inter-annotator agreement")
    lines.append("")
    if not agreement:
        lines.append("_No agreement computed._")
    else:
        lines.append(f"Annotators: {', '.join(agreement['annotators'])}")
        lines.append("")
        lines.append("| Pair | Cohen's kappa | Observed | Expected | Items | Note |")
        lines.append("|---|---:|---:|---:|---:|---|")
        for pair in agreement["pairwise_kappa"]:
            value = "n/a" if pair["value"] is None else f"{pair['value']:.3f}"
            observed = (
                "n/a" if pair["observed_agreement"] is None else f"{pair['observed_agreement']:.3f}"
            )
            expected = (
                "n/a" if pair["expected_agreement"] is None else f"{pair['expected_agreement']:.3f}"
            )
            lines.append(
                f"| {pair['annotator_a']} vs {pair['annotator_b']} | {value} | "
                f"{observed} | {expected} | {pair['n_items']} | "
                f"{pair['note'] or ''} |"
            )
        alpha = agreement.get("krippendorff_alpha")
        if alpha:
            value = "n/a" if alpha["value"] is None else f"{alpha['value']:.3f}"
            lines.append("")
            lines.append(
                f"Krippendorff's alpha (nominal): **{value}** over "
                f"{alpha['n_units']} units / {alpha['n_ratings']} ratings"
                + (f" — {alpha['note']}" if alpha.get("note") else "")
            )
    lines.append("")

    lines.append("## Error buckets")
    lines.append("")
    buckets = metrics.get("error_buckets") or {}
    lines.append("| Bucket | Count | By field |")
    lines.append("|---|---:|---|")
    for bucket, count in (buckets.get("counts") or {}).items():
        by_field = buckets.get("by_field", {}).get(bucket, {})
        rendered = ", ".join(f"{key}={value}" for key, value in by_field.items())
        lines.append(f"| {bucket} | {count} | {rendered} |")
    if not buckets.get("counts"):
        lines.append("| _none_ | 0 | |")
    lines.append("")

    examples = buckets.get("examples") or {}
    if examples:
        lines.append("### Examples")
        lines.append("")
        for bucket, rows in examples.items():
            lines.append(f"**{bucket}**")
            lines.append("")
            for row in rows:
                lines.append(
                    f"- `{row['paper']}` / {row['section']} / {row['field']}: {row['text'][:200]}"
                )
            lines.append("")

    notes = metrics.get("notes") or []
    if notes:
        lines.append("## Notes")
        lines.append("")
        for note in notes:
            lines.append(f"- {note}")
        lines.append("")
    lines.append(
        "_kg_eval grounding note: the pinned agentic-kgis v0.6.0 already counts "
        "`DERIVED_FROM` and `SUPPORTS` as grounding evidence (`_GROUNDING_RELATIONSHIPS`). "
        "The SUPPORTS-only bug was fixed upstream in agentic-kgis #60/#62, so this "
        "harness does not re-implement or work around it._"
    )
    lines.append("")
    return "\n".join(lines)


def write_reports(
    report: Mapping[str, Any],
    out_json: str | Path,
    out_markdown: str | Path | None = None,
) -> tuple[Path, Path]:
    import json

    out_json = Path(out_json)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(
        json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    if out_markdown is None:
        out_markdown = out_json.with_suffix(".md")
    out_markdown = Path(out_markdown)
    out_markdown.parent.mkdir(parents=True, exist_ok=True)
    out_markdown.write_text(render_markdown(report), encoding="utf-8")
    return out_json, out_markdown


__all__ = [
    "build_json_report",
    "normalise_metrics",
    "render_markdown",
    "write_reports",
]
