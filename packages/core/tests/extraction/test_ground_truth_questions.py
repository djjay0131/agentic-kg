"""R-3 question set (llm/features/r3-answer-with-provenance.md, C10 / AC-11).

Runs the fixture's own checker so every gold quote stays verbatim in its paper
text, and the per-kind / per-paper coverage holds, on every CI run.
"""

import importlib.util
from pathlib import Path

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "ground_truth_chain"


def _checker():
    spec = importlib.util.spec_from_file_location(
        "check_questions", FIXTURE_DIR / "check_questions.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_questions_v1_quotes_and_coverage(capsys):
    rc = _checker().main(["check_questions.py", str(FIXTURE_DIR / "questions.v1.yml")])
    assert rc == 0, capsys.readouterr().out


def test_checker_rejects_a_misquoted_gold(tmp_path, capsys):
    text = (FIXTURE_DIR / "questions.v1.yml").read_text(encoding="utf-8")
    bad = tmp_path / "questions.yml"
    bad.write_text(text.replace("0.435", "0.45", 1), encoding="utf-8")
    assert _checker().main(["check_questions.py", str(bad)]) == 1
