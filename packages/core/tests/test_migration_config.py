"""
Tests for the agentic_kg.migration seam.

These tests deliberately do NOT require the optional 'migration' extra to be
installed. The default CI job (.github/workflows/test.yml) runs
`pip install ./packages/core` with no extras, so anything asserting that
`kgis`/`kgcs` import would fail there. Instead we assert the two things that
must hold on a default install:

  1. the migration config defaults to OFF, and
  2. the import guard produces an actionable error rather than an opaque
     ModuleNotFoundError.

The one test that exercises a *successful* guarded import is skipped unless
the extra happens to be present, so it is informative locally and inert in CI.
"""

import re
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest
from agentic_kg.migration import (
    ENV_KGCS_ENABLED,
    ENV_KGIS_ENABLED,
    MigrationConfig,
    MigrationDependencyError,
    check_migration_module,
    get_migration_config,
    require_migration_module,
    reset_migration_config,
)
from agentic_kg.migration.imports import _MODULE_TO_DISTRIBUTION

KGIS_MODULES = ["kg_contracts", "kgis", "kg_eval"]
ALL_MODULES = KGIS_MODULES + ["kgcs"]


@pytest.fixture
def clean_migration_env(monkeypatch):
    """Ensure no migration flag leaks in from the ambient environment."""
    monkeypatch.delenv(ENV_KGIS_ENABLED, raising=False)
    monkeypatch.delenv(ENV_KGCS_ENABLED, raising=False)
    reset_migration_config()
    yield
    reset_migration_config()


# =============================================================================
# Defaults — the migration must be OFF unless explicitly enabled
# =============================================================================


class TestMigrationConfigDefaults:
    def test_defaults_to_off(self, clean_migration_env):
        """With no env vars set, every migration switch is off."""
        config = MigrationConfig()
        assert config.use_kgis_ingestion is False
        assert config.use_kgcs_resolution is False
        assert config.any_enabled is False
        assert config.is_default is True

    def test_singleton_defaults_to_off(self, clean_migration_env):
        """get_migration_config() is off by default too."""
        assert get_migration_config().any_enabled is False

    def test_empty_string_is_off(self, monkeypatch, clean_migration_env):
        """An env var set to empty string does not enable the path."""
        monkeypatch.setenv(ENV_KGIS_ENABLED, "")
        monkeypatch.setenv(ENV_KGCS_ENABLED, "")
        assert MigrationConfig().any_enabled is False

    @pytest.mark.parametrize("value", ["false", "0", "no", "off", "maybe", "TRUEish"])
    def test_falsy_values_are_off(self, monkeypatch, clean_migration_env, value):
        """Anything outside the truthy set means disabled."""
        monkeypatch.setenv(ENV_KGIS_ENABLED, value)
        assert MigrationConfig().use_kgis_ingestion is False

    def test_importing_seam_does_not_import_optional_packages(self):
        """The seam must be free to import on a default install.

        Importing agentic_kg.migration and constructing the config must never
        pull in kgis/kgcs — callers have to be able to branch on the config
        even when the extra is absent. Checked in a clean subprocess so the
        result holds whether or not the extra is installed here.
        """
        code = (
            "import sys, agentic_kg.migration as m; m.MigrationConfig(); "
            "print([n for n in ('kg_contracts','kgis','kg_eval','kgcs') "
            "if n in sys.modules])"
        )
        result = subprocess.run(
            [sys.executable, "-c", code], capture_output=True, text=True
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "[]"


# =============================================================================
# Enabling via env var
# =============================================================================


class TestMigrationConfigEnabling:
    @pytest.mark.parametrize("value", ["1", "true", "TRUE", "True", "yes", "on", "  true  "])
    def test_truthy_values_enable_kgis(self, monkeypatch, clean_migration_env, value):
        monkeypatch.setenv(ENV_KGIS_ENABLED, value)
        config = MigrationConfig()
        assert config.use_kgis_ingestion is True
        assert config.any_enabled is True
        assert config.is_default is False

    @pytest.mark.parametrize("value", ["1", "true", "yes", "on"])
    def test_truthy_values_enable_kgcs(self, monkeypatch, clean_migration_env, value):
        monkeypatch.setenv(ENV_KGCS_ENABLED, value)
        assert MigrationConfig().use_kgcs_resolution is True

    def test_switches_are_independent(self, monkeypatch, clean_migration_env):
        """Enabling KGIS must not enable KGCS — they migrate separately."""
        monkeypatch.setenv(ENV_KGIS_ENABLED, "true")
        config = MigrationConfig()
        assert config.use_kgis_ingestion is True
        assert config.use_kgcs_resolution is False

    def test_singleton_reflects_env(self, monkeypatch, clean_migration_env):
        monkeypatch.setenv(ENV_KGCS_ENABLED, "true")
        assert get_migration_config().use_kgcs_resolution is True

    def test_reset_clears_singleton(self, monkeypatch, clean_migration_env):
        """reset_migration_config() lets a later env change take effect."""
        assert get_migration_config().any_enabled is False
        monkeypatch.setenv(ENV_KGIS_ENABLED, "true")
        # Still cached as off...
        assert get_migration_config().any_enabled is False
        reset_migration_config()
        assert get_migration_config().any_enabled is True

    def test_explicit_construction_overrides_env(self, monkeypatch, clean_migration_env):
        """Explicit kwargs win over the environment, as elsewhere in config.py."""
        monkeypatch.setenv(ENV_KGIS_ENABLED, "true")
        assert MigrationConfig(use_kgis_ingestion=False).use_kgis_ingestion is False


# =============================================================================
# Import guard
# =============================================================================


class TestImportGuard:
    @pytest.mark.parametrize("module_name", ALL_MODULES)
    def test_guard_message_is_actionable_when_extra_absent(self, module_name, monkeypatch):
        """When the extra is not installed, the error names the fix.

        Simulated by blocking the module in sys.modules so the test asserts
        the same behaviour whether or not the extra happens to be installed.
        """
        monkeypatch.delitem(sys.modules, module_name, raising=False)
        monkeypatch.setitem(sys.modules, module_name, None)  # forces ImportError

        with pytest.raises(MigrationDependencyError) as excinfo:
            require_migration_module(module_name)

        message = str(excinfo.value)
        distribution = _MODULE_TO_DISTRIBUTION[module_name]
        assert module_name in message
        assert distribution in message
        assert "migration" in message
        assert "pip install" in message
        # It must say *why* a plain PyPI install won't help.
        assert "PyPI" in message

    def test_guard_error_is_an_importerror(self, monkeypatch):
        """Callers already catching ImportError keep working."""
        monkeypatch.setitem(sys.modules, "kgis", None)
        with pytest.raises(ImportError):
            require_migration_module("kgis")

    def test_unknown_module_rejected(self):
        """Typos fail loudly rather than producing a misleading install hint."""
        with pytest.raises(ValueError, match="not a known KGIS/KGCS module"):
            require_migration_module("not_a_real_kg_package")

    def test_availability_check_does_not_raise(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "kgcs", None)
        assert check_migration_module("kgcs") is False

    def test_all_three_kgis_packages_are_registered(self):
        """agentic-kgis ships three packages; the guard must know all of them."""
        for name in KGIS_MODULES:
            assert _MODULE_TO_DISTRIBUTION[name] == "agentic-kgis"
        assert _MODULE_TO_DISTRIBUTION["kgcs"] == "agentic-kgcs"


class TestImportGuardWithExtraInstalled:
    """Only meaningful when the optional extra is present. Skipped in default CI."""

    @pytest.mark.parametrize("module_name", ALL_MODULES)
    def test_import_succeeds_when_installed(self, module_name):
        pytest.importorskip(
            module_name,
            reason=f"{module_name} requires the optional 'migration' extra",
        )
        assert require_migration_module(module_name) is not None


# =============================================================================
# Guard discriminator: "extra missing" vs "installed but broken"
# =============================================================================


@pytest.fixture
def fake_installed_package(tmp_path, monkeypatch):
    """A genuinely importable package registered as a migration distribution.

    Lets us exercise the guard's discriminator against a package that really
    IS installed, without requiring the optional extra. Mirrors the shape of
    a real kgis install: the root imports fine, but a submodule is absent and
    another submodule fails on a missing third-party dependency.
    """
    name = "fake_kg_pkg"
    pkg = tmp_path / name
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "broken.py").write_text("import definitely_not_installed_xyz\n")

    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setitem(_MODULE_TO_DISTRIBUTION, name, "agentic-kgis")
    for mod in list(sys.modules):
        if mod == name or mod.startswith(name + "."):
            monkeypatch.delitem(sys.modules, mod, raising=False)
    yield name


class TestGuardDiscriminatesInstalledFromMissing:
    """An installed-but-broken package must never be reported as a missing extra.

    This is the defect the guard exists to prevent, so it is tested against a
    package that is actually importable rather than against a mocked import.
    """

    def test_root_imports_when_installed(self, fake_installed_package):
        assert require_migration_module(fake_installed_package) is not None

    def test_missing_submodule_is_not_reported_as_missing_extra(
        self, fake_installed_package
    ):
        """REGRESSION: `pkg.missing_sub` on an installed pkg is a broken install.

        The naive discriminator compared only the top-level name, so a missing
        *submodule* of a present package raised MigrationDependencyError
        ("not part of the default install"). A PR-2 caller branching on
        availability would then silently fall back to the legacy path on what
        is really a broken install — the exact agentic-kgis #39 shape.
        """
        target = f"{fake_installed_package}.does_not_exist"
        with pytest.raises(ModuleNotFoundError) as excinfo:
            require_migration_module(target)
        assert not isinstance(excinfo.value, MigrationDependencyError)
        assert "broken install" in str(excinfo.value)

    def test_missing_submodule_does_not_silently_report_unavailable(
        self, fake_installed_package
    ):
        """REGRESSION: availability check must raise, not return False.

        Returning False here is what would route a caller to the legacy path
        without anyone noticing the install was broken.
        """
        with pytest.raises(ModuleNotFoundError):
            check_migration_module(f"{fake_installed_package}.does_not_exist")

    def test_missing_third_party_dep_is_not_reported_as_missing_extra(
        self, fake_installed_package
    ):
        """The literal agentic-kgis #39 shape, against a real import."""
        with pytest.raises(ModuleNotFoundError) as excinfo:
            require_migration_module(f"{fake_installed_package}.broken")
        assert not isinstance(excinfo.value, MigrationDependencyError)
        message = str(excinfo.value)
        assert "definitely_not_installed_xyz" in message
        assert "broken install" in message

    def test_truly_absent_package_still_reports_missing_extra(self, monkeypatch):
        """The happy path of the discriminator must keep working."""
        monkeypatch.setitem(_MODULE_TO_DISTRIBUTION, "totally_absent_pkg", "agentic-kgis")
        with pytest.raises(MigrationDependencyError):
            require_migration_module("totally_absent_pkg")
        assert check_migration_module("totally_absent_pkg") is False


# =============================================================================
# pyproject integrity — CI assertions guarding the opt-in property
# =============================================================================

REPO_ROOT = Path(__file__).resolve().parents[3]
ROOT_PYPROJECT = REPO_ROOT / "pyproject.toml"
CORE_PYPROJECT = REPO_ROOT / "packages" / "core" / "pyproject.toml"


def _load(path: Path) -> dict:
    if not path.is_file():
        pytest.skip(f"{path} not present (tests not run from a repo checkout)")
    with path.open("rb") as handle:
        return tomllib.load(handle)


class TestPyprojectIntegrity:
    """`allow-direct-references` is project-wide, so guard what it unlocks.

    Setting [tool.hatch.metadata] allow-direct-references = true is required
    for the `migration` extra, but it is NOT scoped to that extra: it silently
    permits a `git+` URL anywhere in the file, including in the mandatory
    `project.dependencies`. A direct reference there would make an unpinned,
    non-PyPI git dependency compulsory for every install — quietly destroying
    the opt-in property this whole PR is built on.
    """

    @pytest.mark.parametrize("path", [ROOT_PYPROJECT, CORE_PYPROJECT], ids=["root", "core"])
    def test_default_dependencies_contain_no_direct_references(self, path):
        """Nothing in the DEFAULT dependency set may be a git/URL reference."""
        dependencies = _load(path)["project"].get("dependencies", [])
        offenders = [d for d in dependencies if "git+" in d or "@" in d.split(";")[0]]
        assert offenders == [], (
            f"{path.name}: direct reference(s) in project.dependencies — these "
            f"would be mandatory for every install, not opt-in: {offenders}"
        )

    @pytest.mark.parametrize("path", [ROOT_PYPROJECT, CORE_PYPROJECT], ids=["root", "core"])
    def test_migration_extra_is_pinned_to_exact_commits(self, path):
        """Every migration pin must be a 40-hex commit SHA.

        Neither repo is on PyPI, agentic-kgis has zero tags, and its version
        sat at 0.2.0 across 122 commits — a branch name or version specifier
        would pin nothing reproducible.
        """
        extra = _load(path)["project"]["optional-dependencies"]["migration"]
        assert extra, f"{path.name}: migration extra is empty"
        for requirement in extra:
            assert "git+" in requirement, f"{path.name}: {requirement} is not a git pin"
            ref = requirement.rsplit("@", 1)[-1]
            assert re.fullmatch(r"[0-9a-f]{40}", ref), (
                f"{path.name}: {requirement!r} does not end in a 40-hex commit "
                f"SHA (got {ref!r}) — branches and tags are mutable"
            )

    def test_migration_extras_stay_in_sync_between_pyprojects(self):
        """Both distributions ship the same agentic_kg package.

        They must therefore offer identical pins, or which one you installed
        would silently decide which KGIS/KGCS you got. Re-pin both together.
        """
        root = _load(ROOT_PYPROJECT)["project"]["optional-dependencies"]["migration"]
        core = _load(CORE_PYPROJECT)["project"]["optional-dependencies"]["migration"]
        assert sorted(root) == sorted(core), (
            "migration extras have drifted between pyproject.toml and "
            "packages/core/pyproject.toml; they must be re-pinned together"
        )

    @pytest.mark.parametrize("path", [ROOT_PYPROJECT, CORE_PYPROJECT], ids=["root", "core"])
    def test_allow_direct_references_is_enabled(self, path):
        """Without this, even a plain `pip install ./packages/core` fails.

        hatchling rejects PEP 508 direct references at metadata time, so the
        mere presence of the extra breaks the default install CI runs.
        """
        config = _load(path)
        assert config["tool"]["hatch"]["metadata"]["allow-direct-references"] is True
