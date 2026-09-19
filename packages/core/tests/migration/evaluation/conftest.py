"""Shared fixtures for the ground-truth evaluation runner tests.

Every test in this package needs ``kg_eval``, which ships inside the pinned
``agentic-kgis`` behind the optional ``migration`` extra. The skip is declared
once here, at collection time, so a default install collects and skips this
package cleanly instead of erroring on import.

**This means these tests do not currently run in CI.** ``.github/workflows/
test.yml`` installs ``./packages/core`` with no extras, so ``kg_eval`` is absent
and every test below is skipped — reported green, having verified nothing. That
is tracked as BACKLOG KG-1 (add a job that installs the ``migration`` extra).
Until KG-1 lands, the only enforcement of this package is local:

    uv run --with-editable './packages/core[migration]' --with pytest \\
        pytest packages/core/tests/migration/evaluation -q

A skip that is invisible is worse than a failure, so
``test_ci_gap.py::test_migration_extra_is_still_absent_from_ci`` asserts the gap
itself: when a CI job does install the extra, that test fails and this comment
gets deleted along with it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip(
    "kg_eval",
    reason=(
        "kg_eval ships in agentic-kgis, behind the optional 'migration' extra. "
        "Install with: uv pip install -e './packages/core[migration]'"
    ),
)

REPO_ROOT = Path(__file__).resolve().parents[5]
CHAIN_ROOT = REPO_ROOT / "packages/core/tests/extraction/fixtures/ground_truth_chain"
IMPORTER_OUTPUT = REPO_ROOT / "docs/ground-truth/importer-output"


@pytest.fixture(scope="session")
def chain_root() -> Path:
    assert CHAIN_ROOT.is_dir(), f"ground-truth chain fixtures missing at {CHAIN_ROOT}"
    return CHAIN_ROOT


@pytest.fixture(scope="session")
def importer_output_dir() -> Path:
    assert IMPORTER_OUTPUT.is_dir(), f"importer output missing at {IMPORTER_OUTPUT}"
    return IMPORTER_OUTPUT


@pytest.fixture(scope="session")
def papers(chain_root: Path):
    from agentic_kg.migration.evaluation.corpus import load_reconciled_papers

    return load_reconciled_papers(chain_root)


@pytest.fixture(scope="session")
def surface_index(papers):
    from agentic_kg.migration.evaluation.adapter import build_surface_index

    return build_surface_index(papers)


@pytest.fixture(scope="session")
def report(chain_root: Path, importer_output_dir: Path):
    from agentic_kg.migration.evaluation.runner import run_evaluation

    return run_evaluation(chain_root=chain_root, importer_output_dir=importer_output_dir)
