"""``pin_gate`` can fail for each reason it names, and passes on the real repo.

A gate with no test is the failure mode the gate exists to prevent, so each of
its four rejection paths is driven to an actual ``PinGateError`` here, and the
positive control runs against this repository's own pyprojects.

No Neo4j and no network: the negative cases drive :func:`check` against
generated pyprojects, and only the final positive control needs the extra
installed. This module still lives under ``migration/neo4j/``, whose
``conftest.py`` skips the whole directory when the opt-in extra is absent, so
in practice these run in the canonical-adapter job alongside the gate itself —
which is also the only job where a resolved-commit check means anything.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from .pin_gate import PinGateError, check, read_pins

KGIS = "de48639a7a008debbdb2712e20ca26b7b593b22b"
KGCS = "f68d1d7186578c2c80989e75b4d3d0e2c81ec911"

REPO_ROOT = Path(__file__).resolve().parents[5]
PYPROJECTS = [REPO_ROOT / "pyproject.toml", REPO_ROOT / "packages" / "core" / "pyproject.toml"]


def _pyproject(tmp_path: Path, name: str, kgis: str = KGIS, kgcs: str = KGCS) -> Path:
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        textwrap.dedent(f"""
            [project]
            name = "probe"
            version = "0.0.0"
            dependencies = ["neo4j>=5.0.0"]

            [project.optional-dependencies]
            migration = [
                "agentic-kgis @ git+https://github.com/djjay0131/agentic-kgis@{kgis}",
                "agentic-kgcs @ git+https://github.com/djjay0131/agentic-kgcs@{kgcs}",
            ]
        """),
        encoding="utf-8",
    )
    return path


# --- the parser ---------------------------------------------------------------


def test_reads_pins_from_an_optional_dependency_group(tmp_path: Path) -> None:
    pins = read_pins(_pyproject(tmp_path, "pyproject.toml"))
    assert pins == {"agentic-kgis": KGIS, "agentic-kgcs": KGCS}


def test_ignores_ordinary_requirements(tmp_path: Path) -> None:
    """A version specifier is not a pin and must not be mistaken for one."""
    assert "neo4j" not in read_pins(_pyproject(tmp_path, "pyproject.toml"))


# --- the four rejection paths -------------------------------------------------


def test_rejects_pyprojects_that_disagree(tmp_path: Path) -> None:
    """The root file's comment requires the two to match; now something enforces it."""
    a = _pyproject(tmp_path / "a", "pyproject.toml")
    b = _pyproject(tmp_path / "b", "pyproject.toml", kgcs="0" * 40)
    with pytest.raises(PinGateError, match="pinned inconsistently"):
        check([a, b])


def test_rejects_a_mutable_revision(tmp_path: Path) -> None:
    """A branch or tag pins nothing reproducible - the stated reason for SHAs."""
    path = _pyproject(tmp_path, "pyproject.toml", kgcs="main")
    with pytest.raises(PinGateError, match="not a full 40-character commit SHA"):
        check([path])


def test_rejects_a_package_with_no_git_pin(tmp_path: Path) -> None:
    path = tmp_path / "pyproject.toml"
    path.write_text(
        '[project]\nname = "p"\nversion = "0"\ndependencies = ["agentic-kgcs>=1.0.0"]\n',
        encoding="utf-8",
    )
    with pytest.raises(PinGateError, match="not pinned by git URL"):
        check([path])


def test_rejects_an_empty_file_list(tmp_path: Path) -> None:
    """Verifying nothing must not read as verifying everything."""
    with pytest.raises(PinGateError, match="nothing was verified"):
        check([])


def test_rejects_a_commit_that_is_not_what_got_installed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The case the old CI step could not see: importable, but the wrong commit."""
    from . import pin_gate

    monkeypatch.setattr(pin_gate, "installed_commit", lambda _name: "0" * 40)
    with pytest.raises(PinGateError, match="resolved to 0{40}, but the pyprojects pin"):
        pin_gate.check([_pyproject(tmp_path, "pyproject.toml")], tracked=("agentic-kgcs",))


def test_rejects_an_index_install(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """No ``direct_url.json`` means the version number is the only identity left."""
    from . import pin_gate

    monkeypatch.setattr(pin_gate, "installed_commit", lambda _name: None)
    with pytest.raises(PinGateError, match="not installed from its git pin"):
        pin_gate.check([_pyproject(tmp_path, "pyproject.toml")], tracked=("agentic-kgcs",))


# --- positive control ---------------------------------------------------------


def test_this_repository_pins_both_packages_consistently() -> None:
    """Both real pyprojects, parsed for real. No install needed for this half."""
    pins = [read_pins(p) for p in PYPROJECTS]
    assert pins[0]["agentic-kgcs"] == pins[1]["agentic-kgcs"] == KGCS
    assert pins[0]["agentic-kgis"] == pins[1]["agentic-kgis"] == KGIS


def test_the_gate_passes_against_the_installed_extra() -> None:
    """The whole gate, end to end, exactly as CI invokes it."""
    pytest.importorskip("kgcs", reason="the opt-in 'migration' extra is not installed")
    report = check(PYPROJECTS)
    assert KGCS in report and KGIS in report
