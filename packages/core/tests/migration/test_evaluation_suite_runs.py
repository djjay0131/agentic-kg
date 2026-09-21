"""The evaluation suite must actually run somewhere, and never skip silently.

**History, because the guard's shape changed for a reason.** This file used to
assert the opposite of what it asserts now. When the evaluation subpackage
landed, no CI job installed the ``migration`` extra, so every test under
``evaluation/`` was skipped in CI — reported green having verified nothing. The
guard asserted that gap existed and told whoever closed it to delete the test.

PR #74 closed it. Its ``migration-canonical-adapter`` job installs
``./packages/core[migration]`` and runs ``pytest packages/core/tests/migration``,
which is this directory. The evaluation tests now really execute in CI, the old
assertion fired exactly as designed, and it is gone.

What replaces it is the durable half. There are two CI contexts for this
directory and they are *supposed* to differ:

* ``test.yml`` -> ``test (3.12)`` installs no extras and runs
  ``packages/core/tests``. The evaluation tests skip there, correctly and by
  design.
* ``integration-tests.yml`` -> ``migration-canonical-adapter`` installs the
  extra and runs ``packages/core/tests/migration``. They must really run there.

So "the extra is absent" is no longer a fact worth asserting — it depends on
which job you are in. Two things are still worth asserting, and each would
silently void this entire suite:

1. **A skip that should have been a run.** If ``kg_eval`` is importable and the
   tests skip anyway, the suite is a no-op wearing a green check. This is the
   failure this repo keeps rediscovering in new places, so it is checked
   directly rather than inferred.
2. **No job runs them at all.** If the one job that installs the extra is
   deleted or stops running this path, the tests go back to skipping everywhere
   and nothing else notices. The workflow check is a supporting leg here, not
   the only one — an earlier version of this guard relied on workflow-grepping
   alone and was defeated twice by install spellings it had not anticipated.

This module lives one directory **above** ``evaluation/`` on purpose: a
module-level ``pytest.importorskip`` in a ``conftest.py`` skips the whole
directory it governs at collection time, so a copy of this file inside
``evaluation/`` would itself be skipped without ``kg_eval`` — a test about an
invisible skip, made invisible by that same skip. Nothing here imports
``kg_eval``.
"""

from __future__ import annotations

import importlib.util
import os
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
TEST_WORKFLOW = REPO_ROOT / ".github/workflows/test.yml"
INTEGRATION_WORKFLOW = REPO_ROOT / ".github/workflows/integration-tests.yml"
EVALUATION_DIR = Path(__file__).parent / "evaluation"

#: Modules the evaluation subpackage needs. Both ship in ``agentic-kgis``.
REQUIRED_MODULES = ("kg_eval", "kg_contracts")

#: Values that count as "this is CI". GitHub Actions sets ``CI=true``, but other
#: runners spell it ``1``, ``yes`` or ``on``, and arming only on the literal
#: ``"true"`` silently disarms anything keyed on it everywhere else. Matches the
#: truthiness convention already used by ``agentic_kg.migration.config``.
_TRUTHY = frozenset({"1", "true", "yes", "on"})


def in_ci(value: str | None) -> bool:
    """Does ``value`` (a raw ``CI`` env var) mean "this is CI"?

    A named function rather than a module-level expression so the rule can be
    tested by *calling* it with each spelling. An earlier version asserted on the
    module's own source text and was vacuous: the assertion quoted the pattern it
    was searching for, so ``inspect.getsource`` always found it.
    """
    return (value or "").strip().lower() in _TRUTHY


IN_CI = in_ci(os.environ.get("CI"))


def _importable(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def _evaluation_stack_importable() -> bool:
    return all(_importable(name) for name in REQUIRED_MODULES)


def _install_lines(workflow: Path) -> list[str]:
    text = workflow.read_text(encoding="utf-8")
    return [
        line.strip()
        for line in text.splitlines()
        if re.search(r"\b(pip|uv)\b.*\binstall\b", line)
    ]


def _installs_the_extra(workflow: Path) -> list[str]:
    return [
        line
        for line in _install_lines(workflow)
        if re.search(r"\[[^\]]*migration", line) or re.search(r"agentic[-_]kgis", line)
    ]


# --------------------------------------------------------------------------


def test_workflows_exist() -> None:
    assert TEST_WORKFLOW.is_file(), f"expected {TEST_WORKFLOW}"
    assert INTEGRATION_WORKFLOW.is_file(), f"expected {INTEGRATION_WORKFLOW}"


def test_the_evaluation_tests_really_run_when_the_stack_is_importable() -> None:
    """A skip that should have been a run is this suite's worst failure mode.

    It is indistinguishable from a pass in a green check, and it is precisely how
    the evaluation tests spent their first CI runs verifying nothing. If
    ``kg_eval`` resolves, the modules must import and the suite must be live.
    """
    if not _evaluation_stack_importable():
        return  # correctly skipping; the other tests cover that context

    import kg_eval  # noqa: F401  — proves find_spec was not lying
    from agentic_kg.migration.evaluation import runner

    assert runner.run_evaluation is not None


def test_a_ci_job_installs_the_extra_and_runs_this_directory() -> None:
    """Deleting that job would send the suite back to skipping everywhere.

    Nothing else would notice: every other job installs no extras, so the tests
    would collect, skip, and report green. This is the supporting leg to the
    importability check above, never the only one — an earlier guard relied on
    workflow text alone and was defeated twice by install spellings it had not
    anticipated.
    """
    text = INTEGRATION_WORKFLOW.read_text(encoding="utf-8")
    assert _installs_the_extra(INTEGRATION_WORKFLOW), (
        "no job in integration-tests.yml installs the 'migration' extra. Without "
        "one, every test under tests/migration/evaluation/ skips in CI and the "
        "suite verifies nothing while reporting green."
    )
    assert re.search(r"pytest\s+packages/core/tests/migration\b", text), (
        "no job runs `pytest packages/core/tests/migration`, so the evaluation "
        "suite is not executed by CI at all."
    )


def test_the_no_extras_job_is_still_expected_to_skip() -> None:
    """``test (3.12)`` installs no extras, and that is correct, not a defect.

    Pinned so the two contexts stay deliberately different. If this job ever
    starts installing the extra, the evaluation tests run twice per CI run —
    harmless, but it should be a decision rather than a surprise.
    """
    assert _install_lines(TEST_WORKFLOW), "no install commands found in test.yml"
    with_extra = _installs_the_extra(TEST_WORKFLOW)
    assert not with_extra, (
        f"test.yml now installs the migration extra ({with_extra}). That is not "
        "wrong, but the evaluation suite will now run in two jobs; update this "
        "test deliberately rather than leaving it stale."
    )


def test_the_conftest_guard_is_still_in_place() -> None:
    """The skip must stay an ``importorskip``, not become a bare import.

    A bare import would turn a no-extras run into a collection *error* for the
    whole directory rather than a clean skip — and ``test (3.12)`` is a no-extras
    run.
    """
    conftest = (EVALUATION_DIR / "conftest.py").read_text(encoding="utf-8")
    assert 'pytest.importorskip(\n    "kg_eval"' in conftest, (
        "evaluation/conftest.py no longer guards on kg_eval; a no-extras job "
        "would error on collection instead of skipping cleanly"
    )


def test_in_ci_accepts_every_truthy_spelling_and_rejects_the_rest() -> None:
    """Behavioural, not textual.

    Replacing the predicate with ``== "true"`` makes ``in_ci("1")`` False and
    fails this test. An earlier version asserted that the module's source
    contained a particular string — and since the assertion itself contained that
    string, it could never fail. A test that quotes its own subject is not a test.
    """
    for spelling in ("true", "TRUE", " True ", "1", "yes", "YES", "on", "ON"):
        assert in_ci(spelling) is True, spelling
    for spelling in ("", "   ", "false", "0", "no", "off", "maybe", None):
        assert in_ci(spelling) is False, spelling


def test_in_ci_drives_the_module_level_flag() -> None:
    assert IN_CI == in_ci(os.environ.get("CI"))


def test_evaluation_package_is_not_imported_by_default_install_paths() -> None:
    """Nothing outside this subpackage may import it.

    ``agentic_kg.migration.evaluation`` imports ``kg_eval`` unguarded, which is
    correct for a tool whose whole purpose is ``kg_eval`` — and fatal if some
    module on the default import path reaches for it. This asserts the blast
    radius stays zero, and it is the check that matters most now that the extra
    really is installed in one CI job: a stray import would break every job that
    does *not* install it.
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
