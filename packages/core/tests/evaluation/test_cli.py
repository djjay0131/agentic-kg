"""CLI: evaluate the frozen fixture, write JSON+Markdown, deterministic output."""

from __future__ import annotations

import json
from pathlib import Path

from agentic_kg.evaluation.rq1 import main

FIXTURES = Path(__file__).parent / "fixtures" / "rq1"
REPO_ROOT = Path(__file__).resolve().parents[4]


def _report(tmp_path: Path, name: str = "report.json") -> dict:
    out = tmp_path / name
    code = main(
        [
            "--gold",
            str(FIXTURES / "gold.jsonl"),
            "--pred",
            str(FIXTURES / "predictions.jsonl"),
            "--corpus",
            str(FIXTURES / "corpus.json"),
            "--bootstrap",
            "50",
            "--out",
            str(out),
            "--quiet",
        ]
    )
    assert code == 0
    return json.loads(out.read_text(encoding="utf-8"))


def test_evaluate_writes_json_and_markdown(tmp_path):
    report = _report(tmp_path)
    assert report["schema_version"] == "rq1-annotation/v1"
    assert report["harness"] == "rq1-extraction-reliability"
    assert report["metrics"]["coverage"]["corpus_size"] == 8
    assert len(report["metrics"]["coverage"]["gold_papers"]) == 4
    markdown = (tmp_path / "report.md").read_text(encoding="utf-8")
    assert "# RQ1 extraction-reliability report" in markdown
    assert "## By field type" in markdown
    assert "## Inter-annotator agreement" in markdown
    assert "Krippendorff's alpha" in markdown


def test_output_is_deterministic(tmp_path):
    first = _report(tmp_path, "first.json")
    second = _report(tmp_path, "second.json")
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)


def test_ungraded_fields_are_honest_nulls(tmp_path):
    report = _report(tmp_path)
    by_field = report["metrics"]["by_field"]
    for field in ("assumption", "baseline", "constraint", "dataset", "metric"):
        assert by_field[field]["micro"]["precision"]["value"] is None
        assert by_field[field]["micro"]["precision"]["sufficient"] is False


def test_convert_subcommand_round_trips(tmp_path):
    out = tmp_path / "gold.jsonl"
    code = main(
        [
            "convert",
            "--gold-yaml",
            str(
                REPO_ROOT
                / "packages"
                / "core"
                / "tests"
                / "extraction"
                / "fixtures"
                / "ground_truth_chain"
            ),
            "--out",
            str(out),
        ]
    )
    assert code == 0
    assert out.read_text(encoding="utf-8") == (FIXTURES / "gold.jsonl").read_text(encoding="utf-8")


def test_run_id_resolution(tmp_path):
    runs = tmp_path / "runs"
    runs.mkdir()
    (runs / "r1.jsonl").write_text(
        (FIXTURES / "predictions.jsonl").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    code = main(
        [
            "--gold",
            str(FIXTURES / "gold.jsonl"),
            "--pred",
            "r1",
            "--runs-dir",
            str(runs),
            "--bootstrap",
            "0",
            "--out",
            str(tmp_path / "report.json"),
            "--quiet",
        ]
    )
    assert code == 0
