"""The single model-configuration surface: precedence and a retired-id guard.

The first half pins the precedence operators rely on (one env flip reaches
every caller; the legacy OpenAI variable still works). The second half is the
denylist: no source file may spell a model the providers have retired, because
that is exactly how staging 404-ed on 2026-11-27.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from agentic_kg.llm_config import (
    DEFAULT_ANTHROPIC_MODEL,
    DEFAULT_EMBEDDING_MODEL,
    DEFAULT_MAX_OUTPUT_TOKENS,
    DEFAULT_OPENAI_MODEL,
    DEFAULT_RANKING_BATCH_SIZE,
    DEFAULT_REASONING_EFFORT,
    OUTPUT_TOKEN_CEILING,
    resolve_embedding_model,
    resolve_llm_model,
    resolve_llm_provider,
    resolve_max_output_tokens,
    resolve_ranking_batch_size,
    resolve_reasoning_effort,
)


class TestModelPrecedence:
    def test_llm_model_wins_over_explicit_and_legacy(self):
        env = {
            "LLM_MODEL": "env-model",
            "OPENAI_EXTRACTION_MODEL": "legacy-model",
        }
        assert resolve_llm_model("openai", explicit="arg-model", env=env) == "env-model"

    def test_legacy_openai_variable_still_works(self):
        env = {"OPENAI_EXTRACTION_MODEL": "legacy-model"}
        assert resolve_llm_model("openai", explicit="arg-model", env=env) == "legacy-model"

    def test_legacy_openai_variable_does_not_apply_to_anthropic(self):
        env = {"OPENAI_EXTRACTION_MODEL": "legacy-model"}
        assert (
            resolve_llm_model("anthropic", explicit="arg-model", env=env)
            == "arg-model"
        )

    def test_explicit_model_when_no_env(self):
        assert resolve_llm_model("openai", explicit="arg-model", env={}) == "arg-model"

    def test_provider_defaults(self):
        assert resolve_llm_model("openai", env={}) == DEFAULT_OPENAI_MODEL
        assert resolve_llm_model("anthropic", env={}) == DEFAULT_ANTHROPIC_MODEL

    def test_blank_env_falls_through(self):
        env = {"LLM_MODEL": "   ", "OPENAI_EXTRACTION_MODEL": ""}
        assert resolve_llm_model("openai", explicit="arg-model", env=env) == "arg-model"


class TestProviderPrecedence:
    def test_explicit_provider_wins_over_env(self):
        assert resolve_llm_provider("anthropic", env={"LLM_PROVIDER": "openai"}) == (
            "anthropic"
        )

    def test_env_provider_used_when_not_explicit(self):
        assert resolve_llm_provider(env={"LLM_PROVIDER": "anthropic"}) == "anthropic"

    def test_provider_defaults_to_openai(self):
        assert resolve_llm_provider(env={}) == "openai"

    def test_unknown_provider_raises(self):
        with pytest.raises(ValueError, match="unsupported LLM provider"):
            resolve_llm_provider(env={"LLM_PROVIDER": "bedrock"})


class TestReasoningEffort:
    def test_default(self):
        assert resolve_reasoning_effort(env={}) == DEFAULT_REASONING_EFFORT

    def test_env_wins(self):
        assert (
            resolve_reasoning_effort(env={"LLM_REASONING_EFFORT": "high"}) == "high"
        )

    def test_explicit_when_no_env(self):
        assert resolve_reasoning_effort("low", env={}) == "low"

    def test_env_normalized_to_lowercase(self):
        assert (
            resolve_reasoning_effort(env={"LLM_REASONING_EFFORT": "  MEDIUM  "})
            == "medium"
        )

    def test_unknown_effort_raises(self):
        with pytest.raises(ValueError, match="unsupported reasoning effort"):
            resolve_reasoning_effort(env={"LLM_REASONING_EFFORT": "extreme"})


class TestEmbeddingModel:
    def test_env_wins(self):
        env = {"EMBEDDING_MODEL": "embedding-from-env"}
        assert resolve_embedding_model(explicit="arg", env=env) == "embedding-from-env"

    def test_explicit_when_no_env(self):
        assert resolve_embedding_model(explicit="arg", env={}) == "arg"

    def test_default(self):
        assert resolve_embedding_model(env={}) == DEFAULT_EMBEDDING_MODEL

    def test_embedding_config_reads_the_same_surface(self, monkeypatch):
        from agentic_kg.config import EmbeddingConfig

        monkeypatch.setenv("EMBEDDING_MODEL", "embedding-from-env")
        assert EmbeddingConfig().model == "embedding-from-env"


class TestMaxOutputTokens:
    """Reasoning tokens count against the completion budget: precedence + guard."""

    def test_default(self):
        assert resolve_max_output_tokens(env={}) == DEFAULT_MAX_OUTPUT_TOKENS

    def test_env_wins_over_default(self):
        assert (
            resolve_max_output_tokens(env={"LLM_MAX_OUTPUT_TOKENS": "20000"})
            == 20000
        )

    def test_explicit_wins_over_env(self):
        assert (
            resolve_max_output_tokens(
                12000, env={"LLM_MAX_OUTPUT_TOKENS": "20000"}
            )
            == 12000
        )

    def test_ceiling_is_enforced(self):
        with pytest.raises(ValueError, match="exceeds the supported ceiling"):
            resolve_max_output_tokens(OUTPUT_TOKEN_CEILING + 1, env={})

    def test_malformed_env_raises(self):
        with pytest.raises(ValueError, match="LLM_MAX_OUTPUT_TOKENS"):
            resolve_max_output_tokens(env={"LLM_MAX_OUTPUT_TOKENS": "lots"})

    def test_non_positive_env_raises(self):
        with pytest.raises(ValueError, match="LLM_MAX_OUTPUT_TOKENS"):
            resolve_max_output_tokens(env={"LLM_MAX_OUTPUT_TOKENS": "0"})

    def test_llm_config_reads_the_surface(self, monkeypatch):
        from agentic_kg.extraction.llm_client import LLMConfig

        monkeypatch.setenv("LLM_MAX_OUTPUT_TOKENS", "20000")
        assert LLMConfig().max_tokens == 20000


class TestRankingBatchSize:
    def test_default(self):
        assert resolve_ranking_batch_size(env={}) == DEFAULT_RANKING_BATCH_SIZE

    def test_env_wins(self):
        assert (
            resolve_ranking_batch_size(env={"LLM_RANKING_BATCH_SIZE": "3"}) == 3
        )

    def test_explicit_wins_over_env(self):
        assert (
            resolve_ranking_batch_size(
                2, env={"LLM_RANKING_BATCH_SIZE": "3"}
            )
            == 2
        )

    def test_non_positive_raises(self):
        with pytest.raises(ValueError, match="LLM_RANKING_BATCH_SIZE"):
            resolve_ranking_batch_size(env={"LLM_RANKING_BATCH_SIZE": "0"})


# Model ids the providers' deprecation pages list as retired. Matched as whole
# tokens so ``gpt-4`` does not match ``gpt-4o`` or ``gpt-4-turbo``.
RETIRED_MODEL_IDS = (
    "gpt-4",
    "gpt-4-0613",
    "gpt-4-1106-preview",
    "gpt-4-turbo",
    "gpt-4-turbo-2024-04-09",
    "gpt-3.5-turbo",
    "gpt-3.5-turbo-0125",
    "claude-2.0",
    "claude-2.1",
    "claude-3-sonnet-20240229",
    "claude-3-opus-20240229",
    "claude-3-haiku-20240307",
    "claude-3-5-sonnet-20240620",
    "claude-3-5-sonnet-20241022",
    "claude-3-5-haiku-20241022",
    "claude-3-7-sonnet-20250219",
)

_SOURCE_ROOTS = (
    "packages/core/src",
    "packages/api/src",
    "packages/ui/src",
    "docker",
    "infra",
    ".github",
    "config",
    "scripts",
    "deploy",
)
_SUFFIXES = {
    ".py",
    ".ts",
    ".tsx",
    ".js",
    ".yml",
    ".yaml",
    ".tf",
    ".tfvars",
    ".toml",
    ".json",
    ".sh",
    ".env",
    ".example",
}


def _source_files() -> list[Path]:
    root = Path(__file__).resolve().parents[3]
    files: list[Path] = []
    for relative in _SOURCE_ROOTS:
        base = root / relative
        if not base.exists():
            continue
        for path in base.rglob("*"):
            if not path.is_file():
                continue
            if path.suffix in _SUFFIXES or path.name.startswith("Dockerfile"):
                files.append(path)
    return files


def test_no_source_file_contains_a_retired_model_id():
    root = Path(__file__).resolve().parents[3]
    pattern = re.compile(
        r"(?<![A-Za-z0-9._-])("
        + "|".join(re.escape(model) for model in RETIRED_MODEL_IDS)
        + r")(?![A-Za-z0-9._-])"
    )
    offenders: list[str] = []
    for path in _source_files():
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for match in pattern.finditer(text):
            line = text.count("\n", 0, match.start()) + 1
            offenders.append(f"{path.relative_to(root)}:{line}: {match.group(0)}")
    assert not offenders, (
        "retired model id(s) in source; route model choice through "
        "agentic_kg.llm_config and Terraform instead:\n" + "\n".join(offenders)
    )


class TestRequestTimeout:
    """Reasoning calls need minutes; code generation needs the longest lease."""

    def test_default(self):
        from agentic_kg.llm_config import (
            DEFAULT_REQUEST_TIMEOUT_SECONDS,
            resolve_request_timeout,
        )

        assert (
            resolve_request_timeout(env={})
            == DEFAULT_REQUEST_TIMEOUT_SECONDS
        )

    def test_env_wins_over_default(self):
        from agentic_kg.llm_config import resolve_request_timeout

        assert (
            resolve_request_timeout(env={"LLM_REQUEST_TIMEOUT_SECONDS": "240"})
            == 240.0
        )

    def test_code_generation_keeps_its_floor_when_the_base_is_lower(self):
        from agentic_kg.llm_config import (
            CODE_GENERATION_TIMEOUT_SECONDS,
            resolve_request_timeout,
        )

        monkeypatch_env = {"LLM_REQUEST_TIMEOUT_SECONDS": "30"}
        assert (
            resolve_request_timeout(
                call_site="evaluation.code_generation", env=monkeypatch_env
            )
            == CODE_GENERATION_TIMEOUT_SECONDS
        )

    def test_explicit_timeout_wins(self):
        from agentic_kg.llm_config import resolve_request_timeout

        assert (
            resolve_request_timeout(
                12,
                call_site="evaluation.code_generation",
                env={"LLM_REQUEST_TIMEOUT_SECONDS": "240"},
            )
            == 12.0
        )

    def test_malformed_env_raises(self):
        from agentic_kg.llm_config import resolve_request_timeout

        with pytest.raises(ValueError, match="LLM_REQUEST_TIMEOUT_SECONDS"):
            resolve_request_timeout(env={"LLM_REQUEST_TIMEOUT_SECONDS": "soon"})

    def test_non_positive_env_raises(self):
        from agentic_kg.llm_config import resolve_request_timeout

        with pytest.raises(ValueError, match="LLM_REQUEST_TIMEOUT_SECONDS"):
            resolve_request_timeout(env={"LLM_REQUEST_TIMEOUT_SECONDS": "0"})
