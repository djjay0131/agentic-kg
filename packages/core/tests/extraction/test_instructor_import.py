"""
Tests for instructor import handling (SM-4 / extraction-dep-pinning).

Covers two concerns:
1. The resolved environment can actually construct an instructor client
   (regression guard against a future dependency-floor regression).
2. `_get_instructor_client` distinguishes a genuinely-absent package from an
   installed-but-broken one, surfacing the real error instead of the misleading
   "instructor package not installed".
"""

import builtins

import pytest
from agentic_kg.extraction.llm_client import (
    AnthropicClient,
    LLMConfig,
    LLMError,
    LLMProvider,
    OpenAIClient,
)


class TestInstructorImportsInResolvedEnv:
    """Hermetic guard: the pinned floors resolve an importable instructor."""

    def test_instructor_imports_and_constructs_in_resolved_env(self):
        """import instructor + instructor.from_openai succeed with no network.

        If the dependency floors ever regress to a non-importing combo (the
        SM-4 root cause was instructor 1.12 / openai 1.99), this goes RED in the
        unit gate before anything deploys.
        """
        import instructor
        from openai import OpenAI

        client = instructor.from_openai(OpenAI(api_key="sk-test"))
        assert client is not None


def _fake_import_raising(exc: Exception):
    """Return an __import__ replacement that raises `exc` for `instructor` only."""
    real_import = builtins.__import__

    def fake(name, *args, **kwargs):
        if name == "instructor":
            raise exc
        return real_import(name, *args, **kwargs)

    return fake


@pytest.mark.parametrize(
    "client_cls, provider",
    [
        (OpenAIClient, LLMProvider.OPENAI),
        (AnthropicClient, LLMProvider.ANTHROPIC),
    ],
)
class TestInstructorImportErrorMessages:
    """Both clients must distinguish 'not installed' from 'installed but broken'."""

    def test_module_not_found_reports_not_installed(
        self, monkeypatch, client_cls, provider
    ):
        """ModuleNotFoundError → the 'not installed' message is correct."""
        monkeypatch.setattr(
            builtins,
            "__import__",
            _fake_import_raising(ModuleNotFoundError("No module named 'instructor'")),
        )
        client = client_cls(LLMConfig(provider=provider, api_key="x"))
        with pytest.raises(LLMError, match="not installed"):
            client._get_instructor_client()

    def test_broken_import_reports_version_conflict(
        self, monkeypatch, client_cls, provider
    ):
        """A non-ModuleNotFound ImportError → the real cause is surfaced.

        This is the bug SM-4 fixes: previously any ImportError was reported as
        'instructor package not installed', hiding a version conflict.
        """
        monkeypatch.setattr(
            builtins,
            "__import__",
            _fake_import_raising(ImportError("cannot import name 'X' from 'openai'")),
        )
        client = client_cls(LLMConfig(provider=provider, api_key="x"))
        with pytest.raises(LLMError, match="failed to import") as exc_info:
            client._get_instructor_client()
        # The original error text must survive so the conflict is diagnosable.
        assert "cannot import name 'X'" in str(exc_info.value)
