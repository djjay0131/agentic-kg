"""The one test in this package that runs without the ``migration`` extra.

Every other test here needs ``kg_eval`` and is skipped on a default install. CI
does a default install. So this package currently reports green in CI having
verified nothing, and a silent skip is indistinguishable from a pass.

This file makes that gap visible. It lives one directory **above**
``evaluation/`` on purpose: a module-level ``pytest.importorskip`` in a
``conftest.py`` skips the whole directory it governs at collection time, so a
copy of this file inside ``evaluation/`` would itself be skipped without
``kg_eval`` — a test about an invisible skip, made invisible by that same skip.

**The check is on importability, not on the workflow text.** An earlier version
grepped ``.github/workflows/test.yml`` for ``packages/core[...migration...]``,
which a reviewer defeated twice without touching the workflow: once with
``cd packages/core && pip install -e '.[migration]'`` (the extra named relative
to a different working directory) and once with a direct ``pip install
agentic-kgis`` (the distribution, never the extra). Both leave the evaluation
tests running while the guard reports the gap still open — the guard asserting
the opposite of the truth. What actually matters is whether the modules are
importable in the environment the tests run in, and that is what is asserted
below, by install path and all. The workflow scan is kept only as a secondary,
best-effort signal.
"""

from __future__ import annotations

import importlib.util
import os
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
WORKFLOW = REPO_ROOT / ".github/workflows/test.yml"

#: Modules the evaluation subpackage needs. Both ship in ``agentic-kgis``.
REQUIRED_MODULES = ("kg_eval", "kg_contracts")

#: CI sets this; GitHub Actions always does.
IN_CI = os.environ.get("CI", "").strip().lower() == "true"


def _importable(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def _evaluation_stack_importable() -> bool:
    return all(_importable(name) for name in REQUIRED_MODULES)


def test_workflow_exists() -> None:
    assert WORKFLOW.is_file(), f"expected the test workflow at {WORKFLOW}"


def test_migration_extra_is_still_absent_from_ci() -> None:
    """Fails — on purpose — the moment CI can import the evaluation stack.

    Asserted only under ``CI``, because locally the extra is installed
    deliberately (that is how these tests are run at all) and its presence there
    says nothing about the gap. In CI its presence says everything: BACKLOG KG-1
    has landed and the stale "does not run in CI" note must go.
    """
    if not IN_CI:
        assert True  # locally the extra may legitimately be installed
        return
    assert not _evaluation_stack_importable(), (
        "kg_eval/kg_contracts are importable in CI — BACKLOG KG-1 has landed and the "
        "evaluation tests are now really running. Delete this test and the 'these tests "
        "do not run in CI' note in evaluation/conftest.py."
    )


def test_the_guard_agrees_with_what_the_suite_actually_did() -> None:
    """Importability and the conftest skip must tell the same story.

    The failure this catches is the guard drifting out of agreement with
    reality — reporting a gap that has closed, or a closure that has not
    happened. Whichever way it drifts, the report built on it is wrong.
    """
    importable = _evaluation_stack_importable()
    conftest = (Path(__file__).parent / "evaluation" / "conftest.py").read_text(
        encoding="utf-8"
    )
    assert 'pytest.importorskip(\n    "kg_eval"' in conftest, (
        "evaluation/conftest.py no longer guards on kg_eval; this guard's premise "
        "(that the tests skip cleanly without the extra) is void"
    )
    if importable:
        import kg_eval  # noqa: F401  — proves find_spec was not lying
        from agentic_kg.migration.evaluation import runner  # noqa: F401


def test_workflow_scan_is_a_secondary_signal_only() -> None:
    """Best-effort scan of the workflow, broadened past the bypasses found.

    Catches ``[migration]`` named relative to any directory, and a direct install
    of the distribution that ships ``kg_eval``. It is explicitly *not* the
    guard — importability is — because no text scan can anticipate every way an
    install can be spelled. Recorded as a test so the two signals are compared.
    """
    text = WORKFLOW.read_text(encoding="utf-8")
    install_lines = [
        line.strip()
        for line in text.splitlines()
        if re.search(r"\b(pip|uv)\b.*\binstall\b", line)
    ]
    assert install_lines, "no install commands found in the test workflow"
    extra_named = [line for line in install_lines if re.search(r"\[[^\]]*migration", line)]
    distribution_named = [
        line for line in install_lines if re.search(r"agentic[-_]kgis", line)
    ]
    found = extra_named + distribution_named
    if found and IN_CI:
        assert _evaluation_stack_importable(), (
            "the workflow appears to install the migration extra "
            f"({found}) but the modules are not importable — the install is broken, "
            "which would silently skip every evaluation test"
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
