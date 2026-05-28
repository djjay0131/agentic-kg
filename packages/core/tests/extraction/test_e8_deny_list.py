"""E-8 Unit 7 — B3 alias deny-list YAML fixture + loader.

Spec pattern (iii): the deny-list is a structured YAML fixture with
per-entry provenance (term / reason / added), loaded into a frozenset
at module import. Adding terms requires a PR plus the verify-gate
calibration eval. Tested here:

- The fixture file exists and parses.
- Initial terms are present.
- Loader returns a ``frozenset`` (immutable) of lowercased terms.
- Loader rejects malformed entries early (missing fields, wrong types).
"""

from pathlib import Path

import pytest
import yaml
from agentic_kg.extraction.fixtures import b3_deny_list as deny_module

FIXTURE_PATH = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "agentic_kg"
    / "extraction"
    / "fixtures"
    / "b3_deny_list.yml"
)


class TestFixtureFile:
    def test_file_exists(self):
        assert FIXTURE_PATH.exists()

    def test_file_parses_as_yaml(self):
        raw = yaml.safe_load(FIXTURE_PATH.read_text())
        assert isinstance(raw, dict)
        assert "deny_list" in raw
        assert isinstance(raw["deny_list"], list)

    def test_initial_terms_present(self):
        raw = yaml.safe_load(FIXTURE_PATH.read_text())
        terms = {entry["term"] for entry in raw["deny_list"]}
        # Spec-mandated initial batch.
        for required in (
            "model",
            "network",
            "system",
            "approach",
            "method",
            "algorithm",
            "paper",
            "work",
        ):
            assert required in terms, f"missing required deny-list term: {required}"

    def test_every_entry_has_provenance(self):
        raw = yaml.safe_load(FIXTURE_PATH.read_text())
        for entry in raw["deny_list"]:
            assert "term" in entry
            assert "reason" in entry
            assert "added" in entry
            assert len(entry["reason"]) > 0


class TestModuleLevelConstant:
    def test_default_deny_list_is_frozenset(self):
        assert isinstance(deny_module.DEFAULT_ALIAS_DENY_LIST, frozenset)

    def test_default_deny_list_lowercased(self):
        # Matching is case-insensitive — by lowercasing at load time we
        # avoid every call site doing .lower() on inputs.
        for t in deny_module.DEFAULT_ALIAS_DENY_LIST:
            assert t == t.lower()

    def test_default_contains_spec_minimum(self):
        for required in (
            "model",
            "network",
            "system",
            "approach",
            "method",
            "algorithm",
            "paper",
            "work",
        ):
            assert required in deny_module.DEFAULT_ALIAS_DENY_LIST


class TestLoadDenyList:
    def test_load_from_explicit_path(self, tmp_path):
        custom = tmp_path / "deny.yml"
        custom.write_text(
            "deny_list:\n"
            "  - term: foo\n"
            "    reason: testing\n"
            "    added: 2026-05-27\n"
            "  - term: BAR\n"
            "    reason: also testing\n"
            "    added: 2026-05-27\n"
        )
        result = deny_module.load_deny_list(custom)
        assert isinstance(result, frozenset)
        assert "foo" in result
        # Case-folded.
        assert "bar" in result
        assert "BAR" not in result

    def test_load_rejects_missing_term(self, tmp_path):
        bad = tmp_path / "deny.yml"
        bad.write_text(
            "deny_list:\n"
            "  - reason: missing the term field\n"
            "    added: 2026-05-27\n"
        )
        with pytest.raises(ValueError, match="term"):
            deny_module.load_deny_list(bad)

    def test_load_rejects_non_string_term(self, tmp_path):
        bad = tmp_path / "deny.yml"
        bad.write_text(
            "deny_list:\n"
            "  - term: 42\n"
            "    reason: numbers are not terms\n"
            "    added: 2026-05-27\n"
        )
        with pytest.raises(ValueError):
            deny_module.load_deny_list(bad)

    def test_load_rejects_root_without_deny_list_key(self, tmp_path):
        bad = tmp_path / "deny.yml"
        bad.write_text("not_deny_list: []\n")
        with pytest.raises(ValueError, match="deny_list"):
            deny_module.load_deny_list(bad)

    def test_load_handles_missing_file(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            deny_module.load_deny_list(tmp_path / "does-not-exist.yml")
