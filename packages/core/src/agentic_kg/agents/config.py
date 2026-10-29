"""
Agent configuration.

Per-agent LLM settings, sandbox configuration, and checkpoint policies.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from agentic_kg.agents.graph_port import graph_source_from_env


@dataclass
class AgentLLMConfig:
    """LLM settings for a specific agent."""

    model: str = "gpt-4o"
    temperature: float = 0.3
    max_tokens: int = 4096


@dataclass
class SandboxConfig:
    """Execution sandbox configuration for the Evaluation Agent."""

    image: str = field(
        default_factory=lambda: os.getenv(
            "SANDBOX_IMAGE", "python:3.12-slim"
        )
    )
    timeout_seconds: int = field(
        default_factory=lambda: int(os.getenv("SANDBOX_TIMEOUT", "300"))
    )
    memory_limit: str = field(
        default_factory=lambda: os.getenv("SANDBOX_MEMORY", "2g")
    )
    cpu_limit: float = field(
        default_factory=lambda: float(os.getenv("SANDBOX_CPU", "1.0"))
    )
    network_disabled: bool = True
    read_only_rootfs: bool = True
    pip_packages: list[str] = field(
        default_factory=lambda: [
            "numpy",
            "scipy",
            "scikit-learn",
            "pandas",
        ]
    )

    # Backend selection. ``docker`` is the local/dev default; staging sets
    # ``cloudrun_job`` because Cloud Run has no Docker daemon.
    backend: str = field(
        default_factory=lambda: os.getenv("EXPERIMENT_BACKEND", "docker")
    )
    gcs_bucket: str = field(
        default_factory=lambda: os.getenv("EXPERIMENT_GCS_BUCKET", "")
    )
    gcs_prefix: str = field(
        default_factory=lambda: os.getenv("EXPERIMENT_GCS_PREFIX", "experiments")
    )
    gcs_endpoint: str = field(
        default_factory=lambda: os.getenv(
            "EXPERIMENT_GCS_ENDPOINT", "https://storage.googleapis.com"
        )
    )
    project_id: str = field(
        default_factory=lambda: os.getenv("GCP_PROJECT", "")
    )
    region: str = field(
        default_factory=lambda: os.getenv("GCP_REGION", "us-central1")
    )
    job_name: str = field(
        default_factory=lambda: os.getenv("EXPERIMENT_JOB_NAME", "")
    )
    image_digest: str | None = field(
        default_factory=lambda: os.getenv("EXPERIMENT_IMAGE_DIGEST") or None
    )
    poll_interval_seconds: float = field(
        default_factory=lambda: float(
            os.getenv("EXPERIMENT_POLL_INTERVAL_SECONDS", "2")
        )
    )
    max_output_chars: int = 50000


@dataclass
class CheckpointConfig:
    """Which workflow steps require human approval."""

    require_problem_selection: bool = True
    require_proposal_approval: bool = True
    require_evaluation_review: bool = True


@dataclass
class AgentConfig:
    """Top-level configuration for the agent workflow."""

    ranking: AgentLLMConfig = field(default_factory=lambda: AgentLLMConfig(
        temperature=0.2,
    ))
    continuation: AgentLLMConfig = field(default_factory=lambda: AgentLLMConfig(
        temperature=0.5,
        max_tokens=8192,
    ))
    evaluation: AgentLLMConfig = field(default_factory=lambda: AgentLLMConfig(
        temperature=0.2,
        max_tokens=8192,
    ))
    synthesis: AgentLLMConfig = field(default_factory=lambda: AgentLLMConfig(
        temperature=0.3,
        max_tokens=4096,
    ))
    sandbox: SandboxConfig = field(default_factory=SandboxConfig)
    checkpoints: CheckpointConfig = field(default_factory=CheckpointConfig)
    #: ``AGENT_GRAPH_SOURCE``: legacy (default) or canonical (ADR-0010).
    graph_source: str = field(default_factory=graph_source_from_env)


_config: AgentConfig | None = None


def get_agent_config() -> AgentConfig:
    """Get agent configuration singleton."""
    global _config
    if _config is None:
        _config = AgentConfig()
    return _config


def reset_agent_config() -> None:
    """Reset config singleton (for testing)."""
    global _config
    _config = None
