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

import subprocess
import sys

import pytest
from agentic_kg.migration import (
    ENV_KGCS_ENABLED,
    ENV_KGIS_ENABLED,
    MigrationConfig,
    MigrationDependencyError,
    get_migration_config,
    is_migration_module_available,
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

    def test_broken_install_is_not_reported_as_missing_extra(self, monkeypatch):
        """A failure *inside* the package must not be relabelled.

        This is the exact shape of the agentic-kgis bug fixed by PR #39:
        `import kgis` raised ModuleNotFoundError for 'pytest', not for 'kgis'.
        Telling the operator to install the migration extra would send them
        down the wrong path.
        """
        import importlib

        def fake_import(name):
            raise ModuleNotFoundError("No module named 'pytest'", name="pytest")

        monkeypatch.setattr(importlib, "import_module", fake_import)

        with pytest.raises(ModuleNotFoundError) as excinfo:
            require_migration_module("kgis")

        message = str(excinfo.value)
        assert not isinstance(excinfo.value, MigrationDependencyError)
        assert "pytest" in message
        assert "broken install" in message
        assert "not a missing 'migration' extra" in message

    def test_unknown_module_rejected(self):
        """Typos fail loudly rather than producing a misleading install hint."""
        with pytest.raises(ValueError, match="not a known KGIS/KGCS module"):
            require_migration_module("not_a_real_kg_package")

    def test_availability_check_does_not_raise(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "kgcs", None)
        assert is_migration_module_available("kgcs") is False

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
