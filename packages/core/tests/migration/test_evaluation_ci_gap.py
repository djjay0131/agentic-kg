"""The one test in this package that runs without the ``migration`` extra.

Every other test here is skipped on a default install, because ``kg_eval`` is
not there. CI does a default install. So this whole package currently reports
green in CI having verified nothing, and a silent skip is indistinguishable from
a pass.

This file makes that gap visible: it reads the workflow and asserts what it
actually installs. Today it asserts the gap exists (BACKLOG KG-1 — add a job
installing the ``migration`` extra). When KG-1 lands, this test fails, and
whoever lands it deletes the assertion along with the stale skip note in
``conftest.py``. A known gap that fails loudly when it closes is cheaper than a
comment that rots.

It lives one directory **above** ``evaluation/`` on purpose. A module-level
``pytest.importorskip`` in a ``conftest.py`` skips the whole directory it governs
at collection time, so a copy of this file inside ``evaluation/`` would itself be
skipped without ``kg_eval`` — a test about an invisible skip, made invisible by
that same skip. Nothing in this file imports ``kg_eval``.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
WORKFLOW = REPO_ROOT / ".github/workflows/test.yml"


def _install_lines() -> list[str]:
    text = WORKFLOW.read_text(encoding="utf-8")
    return [line.strip() for line in text.splitlines() if "packages/core" in line]


def test_workflow_exists() -> None:
    assert WORKFLOW.is_file(), f"expected the test workflow at {WORKFLOW}"


def test_migration_extra_is_still_absent_from_ci() -> None:
    """Fails — on purpose — the moment CI starts installing the extra.

    While this passes, the evaluation tests in this package are skipped in CI and
    are enforced only by a local run:

        uv run --with-editable './packages/core[migration]' --with pytest \\
            pytest packages/core/tests/migration/evaluation -q
    """
    installs = _install_lines()
    assert installs, "no install of packages/core found in the test workflow"
    with_extra = [line for line in installs if re.search(r"packages/core\[[^\]]*migration", line)]
    assert not with_extra, (
        "CI now installs the 'migration' extra — BACKLOG KG-1 has landed. "
        "Delete this test and the 'these tests do not run in CI' note in "
        f"conftest.py. Found: {with_extra}"
    )


def test_evaluation_package_is_not_imported_by_default_install_paths() -> None:
    """Nothing outside this subpackage may import it.

    ``agentic_kg.migration.evaluation`` imports ``kg_eval`` unguarded, which is
    correct for a tool whose whole purpose is ``kg_eval`` — and fatal if some
    module on the default import path reaches for it. This asserts the blast
    radius stays zero.
    """
    src = REPO_ROOT / "packages/core/src/agentic_kg"
    offenders = []
    for path in src.rglob("*.py"):
        if "migration/evaluation" in path.as_posix():
            continue
        text = path.read_text(encoding="utf-8")
        if "migration.evaluation" in text or re.search(r"^\s*import kg_eval", text, re.M):
            offenders.append(str(path.relative_to(REPO_ROOT)))
    assert offenders == [], (
        "these modules reach into the evaluation subpackage (or kg_eval) from the "
        f"default import path, which breaks a no-extras install: {offenders}"
    )
