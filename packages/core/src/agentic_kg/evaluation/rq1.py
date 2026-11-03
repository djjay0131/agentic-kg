"""RQ1 extraction-reliability harness — command line entry point.

Evaluate::

    python -m agentic_kg.evaluation.rq1 \
        --gold <path-or-dir> --pred <path-or-run-id> --out report.json

Convert the repository's existing fixtures::

    python -m agentic_kg.evaluation.rq1 convert \
        --gold-yaml packages/core/tests/extraction/fixtures/ground_truth_chain \
        --out packages/core/tests/evaluation/fixtures/rq1/gold.jsonl

    python -m agentic_kg.evaluation.rq1 convert \
        --importer-output docs/ground-truth/importer-output \
        --out packages/core/tests/evaluation/fixtures/rq1/predictions.jsonl

``--pred`` accepts a JSONL file, a directory, the importer-output YAML
directory, or a *run id*. A run id is resolved under ``--runs-dir``
(default ``runs/rq1``) as ``<id>.jsonl`` or ``<id>/predictions.jsonl``, so a
live extraction can be evaluated without copying it into the gold tree.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

from agentic_kg.evaluation.annotations import (
    convert_gold_directory,
    convert_gold_file,
    convert_importer_output_directory,
    convert_importer_output_file,
)
from agentic_kg.evaluation.matching import (
    MatchConfig,
    default_embedding_similarity,
)
from agentic_kg.evaluation.metrics import (
    DEFAULT_RESAMPLES,
    DEFAULT_SEED,
    build_metrics,
    build_report_data,
)
from agentic_kg.evaluation.report import build_json_report, write_reports
from agentic_kg.evaluation.schema import (
    SCHEMA_VERSION,
    AnnotationRecord,
    CoverageRecord,
    read_jsonl,
    split_records,
    write_jsonl,
)


def _repo_root() -> Path:
    candidate = Path(__file__).resolve().parents[5]
    if (candidate / "packages" / "core").is_dir():
        return candidate
    return Path.cwd()


def _default_source_text_dir() -> Path:
    return (
        _repo_root()
        / "packages"
        / "core"
        / "tests"
        / "extraction"
        / "fixtures"
        / "ground_truth_chain"
    )


def _collect_jsonl(path: Path) -> list:
    records: list = []
    for file_path in sorted(path.rglob("*.jsonl")):
        records.extend(read_jsonl(file_path))
    return records


def load_gold(
    source: str | Path, source_text_dir: str | Path
) -> tuple[list[AnnotationRecord], list[CoverageRecord], str]:
    """Load annotation JSONL, or convert a .gold.yml file/directory on the fly."""
    path = Path(source)
    if not path.exists():
        raise FileNotFoundError(f"gold path does not exist: {path}")
    if path.is_dir():
        jsonl = sorted(path.rglob("*.jsonl"))
        if jsonl:
            annotations, coverage = split_records(_collect_jsonl(path))
            return annotations, coverage, str(path)
        annotations, coverage = convert_gold_directory(path, source_text_dir)
        return annotations, coverage, str(path)
    if path.suffix in {".yml", ".yaml"}:
        annotations, coverage = convert_gold_file(path, source_text_dir)
        return annotations, coverage, str(path)
    if path.suffix == ".jsonl":
        annotations, coverage = split_records(read_jsonl(path))
        return annotations, coverage, str(path)
    raise ValueError(f"unsupported gold file type: {path.suffix!r}")


def _resolve_run_id(run_id: str, runs_dir: str | Path) -> Path:
    runs_dir = Path(runs_dir)
    candidates = (
        runs_dir / f"{run_id}.jsonl",
        runs_dir / run_id / "predictions.jsonl",
        runs_dir / run_id / "pred.jsonl",
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(
        f"prediction run id {run_id!r} not found; looked for "
        + ", ".join(str(path) for path in candidates)
    )


def load_predictions(
    source: str | Path,
    source_text_dir: str | Path,
    runs_dir: str | Path = "runs/rq1",
) -> tuple[list[AnnotationRecord], str]:
    """Load predictions from JSONL, importer-output YAML, a directory, or a run id."""
    path = Path(source)
    if not path.exists():
        resolved = _resolve_run_id(str(source), runs_dir)
        annotations, _ = split_records(read_jsonl(resolved))
        return annotations, str(resolved)
    if path.is_dir():
        jsonl = sorted(path.rglob("*.jsonl"))
        if jsonl:
            annotations, _ = split_records(_collect_jsonl(path))
            return annotations, str(path)
        return convert_importer_output_directory(path, source_text_dir), str(path)
    if path.suffix in {".yml", ".yaml"}:
        return convert_importer_output_file(path, source_text_dir), str(path)
    if path.suffix == ".jsonl":
        annotations, _ = split_records(read_jsonl(path))
        return annotations, str(path)
    raise ValueError(f"unsupported prediction file type: {path.suffix!r}")


def _evaluate_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m agentic_kg.evaluation.rq1",
        description="Measure extraction precision/recall/F1 against RQ1 annotations.",
    )
    parser.add_argument("--gold", required=True, help="annotation JSONL, directory or .gold.yml")
    parser.add_argument(
        "--pred",
        required=True,
        help="prediction JSONL/YAML/directory, or a run id under --runs-dir",
    )
    parser.add_argument("--out", required=True, help="JSON report path")
    parser.add_argument(
        "--markdown",
        default=None,
        help="Markdown report path (default: --out with .md)",
    )
    parser.add_argument(
        "--annotator",
        default=None,
        help="use only this annotator's records as gold (default: reconciled > human > claude)",
    )
    parser.add_argument(
        "--span-iou",
        type=float,
        default=0.5,
        help="minimum span IoU for a span match (default 0.5)",
    )
    parser.add_argument(
        "--embedding-similarity",
        action="store_true",
        help="enable optional embedding matching (off by default; needs sentence-transformers)",
    )
    parser.add_argument(
        "--embedding-threshold",
        type=float,
        default=0.8,
        help="minimum cosine similarity when embeddings are enabled (default 0.8)",
    )
    parser.add_argument(
        "--bootstrap",
        type=int,
        default=DEFAULT_RESAMPLES,
        help=f"bootstrap resamples (default {DEFAULT_RESAMPLES}; 0 disables)",
    )
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED, help="bootstrap seed")
    parser.add_argument(
        "--runs-dir",
        default=str(_repo_root() / "runs" / "rq1"),
        help="directory used to resolve a run id passed as --pred",
    )
    parser.add_argument(
        "--source-text-dir",
        default=str(_default_source_text_dir()),
        help="directory holding paper_<slug>.txt (used when converting YAML on the fly)",
    )
    parser.add_argument(
        "--corpus",
        default=None,
        help="optional JSON manifest of the corpus papers, for honest coverage reporting",
    )
    parser.add_argument("--quiet", action="store_true", help="suppress the summary")
    return parser


def _convert_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m agentic_kg.evaluation.rq1 convert",
        description="Convert the repository's existing fixtures into RQ1 JSONL.",
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--gold-yaml", help="ground_truth_chain directory or one .gold.yml")
    source.add_argument("--importer-output", help="docs/ground-truth/importer-output directory")
    parser.add_argument("--out", required=True, help="output JSONL path")
    parser.add_argument(
        "--source-text-dir",
        default=str(_default_source_text_dir()),
        help="directory holding paper_<slug>.txt",
    )
    parser.add_argument(
        "--annotator-id",
        default="frozen-importer-output",
        help="annotator id used for importer-output predictions",
    )
    return parser


def _summary(report: dict) -> str:
    overall = report["metrics"]["overall"]["micro"]
    coverage = report["metrics"]["coverage"]
    lines = [
        f"gold papers={len(coverage['gold_papers'])} "
        f"graded={len(coverage['graded_papers'])} "
        f"predictions={len(coverage['prediction_papers'])}",
        f"overall micro P={_short(overall['precision'])} "
        f"R={_short(overall['recall'])} F1={_short(overall['f1'])}",
        "fields: " + ", ".join(coverage["graded_fields"] or ["none"]),
    ]
    if coverage["ungraded_fields"]:
        lines.append("honest null: " + ", ".join(coverage["ungraded_fields"]))
    return "\n".join(lines)


def _load_corpus(path: str | None) -> list[dict]:
    if not path:
        return []
    source = Path(path)
    if not source.is_file():
        raise FileNotFoundError(f"corpus manifest does not exist: {source}")
    data = json.loads(source.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        data = data.get("papers", [])
    if not isinstance(data, list):
        raise ValueError("corpus manifest must be a list of {slug, doi} objects")
    return [entry for entry in data if isinstance(entry, dict)]


def _short(score: dict) -> str:
    if score["value"] is None:
        return "n/a"
    return f"{score['value']:.3f}"


def run_evaluate(argv: Sequence[str]) -> int:
    args = _evaluate_parser().parse_args(list(argv))
    if args.embedding_similarity:
        similarity_fn = default_embedding_similarity()
    else:
        similarity_fn = None
    config = MatchConfig(
        span_iou_threshold=args.span_iou,
        embedding_enabled=args.embedding_similarity,
        embedding_threshold=args.embedding_threshold,
        similarity_fn=similarity_fn,
    )
    annotations, coverage, gold_label = load_gold(args.gold, args.source_text_dir)
    predictions, prediction_label = load_predictions(args.pred, args.source_text_dir, args.runs_dir)
    data = build_report_data(
        annotations,
        coverage,
        predictions,
        match_config=config,
        annotator=args.annotator,
    )
    metrics = build_metrics(
        data,
        n_resamples=max(0, args.bootstrap),
        seed=args.seed,
        bootstrap=True,
    )
    report = build_json_report(
        data,
        metrics,
        gold_source=gold_label,
        prediction_source=prediction_label,
        match_config=config,
        n_resamples=max(0, args.bootstrap),
        seed=args.seed,
        annotator=args.annotator,
        corpus=_load_corpus(args.corpus),
    )
    json_path, md_path = write_reports(report, args.out, args.markdown)
    if not args.quiet:
        print(_summary(report))
        print(f"wrote {json_path} and {md_path}")
    return 0


def run_convert(argv: Sequence[str]) -> int:
    args = _convert_parser().parse_args(list(argv))
    if args.gold_yaml:
        source = Path(args.gold_yaml)
        if source.is_dir():
            annotations, coverage = convert_gold_directory(source, args.source_text_dir)
        else:
            annotations, coverage = convert_gold_file(source, args.source_text_dir)
        records = [*annotations, *coverage]
    else:
        records = convert_importer_output_directory(
            args.importer_output,
            args.source_text_dir,
            annotator_id=args.annotator_id,
        )
    write_jsonl(args.out, records)
    print(f"wrote {len(records)} records ({SCHEMA_VERSION}) to {args.out}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "convert":
        return run_convert(argv[1:])
    if argv and argv[0] == "evaluate":
        argv = argv[1:]
    return run_evaluate(argv)


if __name__ == "__main__":
    raise SystemExit(main())
