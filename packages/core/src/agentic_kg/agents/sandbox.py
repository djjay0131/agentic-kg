"""
Docker sandbox for safe code execution.

Runs LLM-generated Python scripts in isolated Docker containers
with no network access, memory/time limits, and read-only filesystem.
"""

from __future__ import annotations

import json
import logging
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from agentic_kg.agents.config import SandboxConfig, get_agent_config

logger = logging.getLogger(__name__)


@dataclass
class SandboxResult:
    """Result of a sandboxed code execution."""

    success: bool
    stdout: str
    stderr: str
    exit_code: int
    timed_out: bool = False
    metrics: Optional[dict] = None

    def parse_metrics(self) -> dict:
        """Try to parse JSON metrics from stdout."""
        if self.metrics:
            return self.metrics
        # Look for JSON in stdout (last line or entire output)
        for line in reversed(self.stdout.strip().splitlines()):
            line = line.strip()
            if line.startswith("{"):
                try:
                    data = json.loads(line)
                    self.metrics = data
                    return data
                except json.JSONDecodeError:
                    continue
        return {}


class DockerSandbox:
    """
    Executes Python scripts in isolated Docker containers.

    Security measures:
    - No network access (network_disabled=True)
    - Memory limit (default 2GB)
    - CPU limit (default 1 core)
    - Time limit (default 5 minutes)
    - Read-only root filesystem
    - Non-root user
    - Only /tmp is writable
    """

    def __init__(self, config: Optional[SandboxConfig] = None) -> None:
        self.config = config or get_agent_config().sandbox
        self._client = None

    def _get_client(self):
        """Lazy-load Docker client."""
        if self._client is None:
            try:
                import docker

                self._client = docker.from_env()
            except ImportError:
                raise RuntimeError(
                    "docker package required for sandbox execution. "
                    "Install with: pip install docker"
                )
            except Exception as e:
                raise RuntimeError(f"Failed to connect to Docker: {e}")
        return self._client

    def execute(self, code: str, pip_packages: Optional[list[str]] = None) -> SandboxResult:
        """
        Execute a Python script in a sandboxed container.

        Args:
            code: Python source code to execute.
            pip_packages: Additional pip packages to install (beyond defaults).

        Returns:
            SandboxResult with stdout, stderr, exit code, and parsed metrics.
        """
        client = self._get_client()
        packages = list(self.config.pip_packages)
        if pip_packages:
            packages.extend(pip_packages)

        # Build the execution script with pip install + user code
        setup_script = ""
        if packages:
            pkg_str = " ".join(packages)
            setup_script = f"pip install --quiet {pkg_str} 2>/dev/null && "

        # Write code to a temp file that gets mounted
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", delete=False, prefix="sandbox_"
        ) as f:
            f.write(code)
            script_path = f.name

        try:
            container = client.containers.run(
                image=self.config.image,
                command=f"/bin/sh -c '{setup_script}python /tmp/script.py'",
                volumes={
                    script_path: {"bind": "/tmp/script.py", "mode": "ro"},
                },
                network_disabled=self.config.network_disabled,
                mem_limit=self.config.memory_limit,
                cpu_quota=int(self.config.cpu_limit * 100000),
                read_only=self.config.read_only_rootfs,
                tmpfs={"/tmp": "size=256m"},
                user="nobody",
                detach=True,
                remove=False,
            )

            # Wait with timeout
            result = container.wait(timeout=self.config.timeout_seconds)
            exit_code = result.get("StatusCode", -1)

            stdout = container.logs(stdout=True, stderr=False).decode(
                "utf-8", errors="replace"
            )
            stderr = container.logs(stdout=False, stderr=True).decode(
                "utf-8", errors="replace"
            )

            # Truncate large outputs
            max_output = 50000
            if len(stdout) > max_output:
                stdout = stdout[:max_output] + "\n... (truncated)"
            if len(stderr) > max_output:
                stderr = stderr[:max_output] + "\n... (truncated)"

            container.remove(force=True)

            sandbox_result = SandboxResult(
                success=exit_code == 0,
                stdout=stdout,
                stderr=stderr,
                exit_code=exit_code,
            )
            sandbox_result.parse_metrics()
            return sandbox_result

        except Exception as e:
            error_msg = str(e)
            timed_out = "timed out" in error_msg.lower() or "timeout" in error_msg.lower()

            # Try to clean up container
            try:
                container.remove(force=True)
            except Exception:
                pass

            logger.error(f"Sandbox execution failed: {e}")
            return SandboxResult(
                success=False,
                stdout="",
                stderr=error_msg,
                exit_code=-1,
                timed_out=timed_out,
            )
        finally:
            # Clean up temp file
            try:
                Path(script_path).unlink(missing_ok=True)
            except Exception:
                pass
