"""Start or resume a research workflow on a long-lived Cloud Run Job.

A gate decision used to run the next agent step inside the HTTP request. The
evaluation step generates code and waits for a Cloud Run Job sandbox, so a slow
model or experiment ran into the request deadline (staging: "OpenAI extraction
failed: Request timed out"). The fix is to persist the decision and hand the
actual work to a Cloud Run Job execution of the existing ``job`` image:

    python -m agentic_kg.job_runner workflow resume <run_id>

The Job reads the same durable LangGraph checkpoint and run metadata in Neo4j,
so nothing about the workflow state lives only in the request process. This is
the same execution-time ``args`` override mechanism #163 uses for ``llm-check``.

No Terraform change is required: the API already has ``GCP_PROJECT``,
``GCP_REGION`` and ``GCP_ENV``, and the existing ingest Job has the Neo4j and
LLM secrets. The workflow-specific execution settings (graph source, sandbox
coordinates) travel in the run metadata rather than in env overrides.

IAM: starting a Job with ``containerOverrides.args`` needs
``run.jobs.runWithOverrides``. ``roles/run.invoker`` alone is not enough (the
repo records the same fact for the Workflows-triggered Jobs in ``infra``). The
required grant is:

    roles/run.jobsExecutorWithOverrides on
    projects/<project>/locations/<region>/jobs/agentic-kg-ingest-<env>
    for the API runtime service account
    (<project-number>-compute@developer.gserviceaccount.com on staging)

The owner applies IAM through the reviewed Terraform path, not this branch.
When the call is denied (or no Job coordinates are configured) the dispatcher
returns ``False`` and the runner falls back to the historical in-request path,
so nothing regresses while the grant is absent.
"""

from __future__ import annotations

import logging
import os
from typing import Callable, Optional, Protocol, runtime_checkable

logger = logging.getLogger(__name__)

EXECUTION_MODE_ENV = "WORKFLOW_EXECUTION_MODE"
JOB_NAME_ENV = "WORKFLOW_JOB_NAME"
PROJECT_ENV = "GCP_PROJECT"
REGION_ENV = "GCP_REGION"
ENV_NAME_ENV = "GCP_ENV"


MODES = ("auto", "job", "inline")
DEFAULT_MODE = "auto"

#: The existing Job image already carries the core CLI, the migration extra,
#: Neo4j and the LLM secrets. Reusing it is what makes this a no-Terraform fix.
DEFAULT_JOB_PREFIX = "agentic-kg-ingest-"


def default_job_name(env: Optional[dict[str, str]] = None) -> str:
    """The Job to execute when ``WORKFLOW_JOB_NAME`` is not set."""
    source = os.environ if env is None else env
    configured = (source.get(JOB_NAME_ENV) or "").strip()
    if configured:
        return configured
    environment = (source.get(ENV_NAME_ENV) or "staging").strip()
    return f"{DEFAULT_JOB_PREFIX}{environment}"


def job_resource_name(
    *,
    project: str,
    region: str,
    job: str,
) -> str:
    """The Run Admin v2 resource name for a Job."""
    return f"projects/{project}/locations/{region}/jobs/{job}"


class WorkflowDispatchError(RuntimeError):
    """A workflow Job execution could not be started."""


@runtime_checkable
class WorkflowDispatcher(Protocol):
    """Start/resume one workflow run outside the current HTTP process."""

    def dispatch(self, run_id: str, *, phase: str) -> bool:
        """Return ``True`` when a background execution was started."""


class CloudRunJobDispatcher:
    """Execute ``agentic-kg workflow <phase> <run_id>`` on a Cloud Run Job."""

    def __init__(
        self,
        *,
        job_resource: str,
        run_client: object,
        strict: bool = False,
    ) -> None:
        self.job_resource = job_resource
        self._run_client = run_client
        self.strict = strict

    def dispatch(self, run_id: str, *, phase: str) -> bool:
        """Start one execution of the Job with an ``args`` override.

        Raises:
            WorkflowDispatchError: in ``strict`` mode, or when the client
                reports a non-permission failure. The caller decides whether to
                fall back in-request.
        """
        args = ["workflow", phase, run_id]
        try:
            execution = self._run_client.run_job(
                self.job_resource, args=args, env={}
            )
        except Exception as exc:  # noqa: BLE001 - the fallback is the safety net
            message = (
                "could not start the workflow Job execution "
                f"({self.job_resource}, args={args!r}): {exc}. The API runtime "
                "service account needs roles/run.jobsExecutorWithOverrides on "
                "this Job."
            )
            logger.warning("%s", message)
            if self.strict:
                raise WorkflowDispatchError(message) from exc
            return False
        logger.info(
            "dispatched workflow %s (%s) to %s as %s",
            run_id,
            phase,
            self.job_resource,
            execution,
        )
        return True


def _build_run_client(project: str, region: str):
    """A Run Admin client using the API service account's ambient credentials."""
    from agentic_kg.agents.config import SandboxConfig
    from agentic_kg.agents.sandbox import CloudRunJobClient

    config = SandboxConfig()
    config.project_id = project
    config.region = region
    return CloudRunJobClient(config)


def build_dispatcher(
    *,
    env: Optional[dict[str, str]] = None,
    run_client_factory: Optional[Callable[[str, str], object]] = None,
) -> Optional[WorkflowDispatcher]:
    """Build the dispatcher selected by ``WORKFLOW_EXECUTION_MODE``.

    ``auto`` (the default) uses the Job when its coordinates are available and
    falls back in-request on any failure. ``job`` fails loudly instead of
    falling back. ``inline`` keeps the historical in-request execution.
    """
    source = dict(os.environ if env is None else env)
    mode = (source.get(EXECUTION_MODE_ENV) or DEFAULT_MODE).strip().lower()
    if mode not in MODES:
        raise ValueError(
            f"unsupported {EXECUTION_MODE_ENV} {mode!r}; expected one of "
            f"{', '.join(MODES)}"
        )
    if mode == "inline":
        return None

    project = (source.get(PROJECT_ENV) or "").strip()
    region = (source.get(REGION_ENV) or "").strip()
    job = default_job_name(source)
    if not project or not region:
        if mode == "job":
            raise WorkflowDispatchError(
                f"{EXECUTION_MODE_ENV}=job needs {PROJECT_ENV} and {REGION_ENV}"
            )
        logger.info(
            "workflow Job dispatch unavailable (no project/region); "
            "using in-request execution"
        )
        return None

    factory = run_client_factory or _build_run_client
    return CloudRunJobDispatcher(
        job_resource=job_resource_name(project=project, region=region, job=job),
        run_client=factory(project, region),
        strict=(mode == "job"),
    )


__all__ = [
    "CloudRunJobDispatcher",
    "DEFAULT_MODE",
    "DEFAULT_JOB_PREFIX",
    "ENV_NAME_ENV",
    "EXECUTION_MODE_ENV",
    "JOB_NAME_ENV",
    "MODES",
    "PROJECT_ENV",
    "REGION_ENV",
    "WorkflowDispatchError",
    "WorkflowDispatcher",
    "build_dispatcher",
    "default_job_name",
    "job_resource_name",
]
