"""The nightly orchestrator and the nightly Cloud Run Job entrypoint.

``python -m agentic_kg.pipeline.nightly`` plans tonight's queries, runs each
through the in-process ingest backend (:mod:`agentic_kg.pipeline.ingest`), it
aggregates the per-query outcomes, enforces the paper and estimated-USD budgets,
writes the ``PipelineRun`` report both ways, and emits one structured JSON log
line. See ``docs/design/nightly-pipeline-contract.md``.

Exit codes (mirroring the ingest Job's convention):

* 0 — every planned query succeeded and both report writers succeeded;
* 1 — partial: some queries failed, the budget stopped the run, or a report
  writer failed while ingestion otherwise ran;
* 2 — failed: every planned query failed, or no query could be planned.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from agentic_kg.pipeline.catalog import QueryCatalog, load_catalog
from agentic_kg.pipeline.ingest import QueryOutcome, run_query_ingest
from agentic_kg.pipeline.plan import (
    DEFAULT_MAX_PAPERS,
    PlannedQuery,
    plan_queries,
)
from agentic_kg.pipeline.report import (
    Budget,
    Failure,
    PipelineRunReport,
    QueryEntry,
    Totals,
    iso,
    new_run_id,
    utc_now,
    write_pipeline_run_node,
    write_report_json,
)

logger = logging.getLogger(__name__)

ENV_MAX_PAPERS = "NIGHTLY_MAX_PAPERS"
ENV_MAX_LLM_USD = "NIGHTLY_MAX_LLM_USD"
ENV_RUNS_DIR = "NIGHTLY_RUNS_DIR"
ENV_RUNS_BUCKET = "NIGHTLY_RUNS_BUCKET"
ENV_NAMESPACE = "NIGHTLY_NAMESPACE"
ENV_TRIGGER = "NIGHTLY_TRIGGER"
ENV_GIT_SHA = "NIGHTLY_GIT_SHA"
ENV_IMAGE = "NIGHTLY_IMAGE"

Runner = Callable[..., QueryOutcome]


class NightlyResult:
    """The outcome of one :func:`run_nightly` call."""

    def __init__(self, report: PipelineRunReport, url: str | None, exit_code: int):
        self.report = report
        self.url = url
        self.exit_code = exit_code

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"NightlyResult(run_id={self.report.run_id!r}, "
            f"status={self.report.status!r}, exit_code={self.exit_code})"
        )


def _run_one(planned: PlannedQuery, *, run_id: str, namespace: str, runner: Runner) -> QueryOutcome:
    child_run_id = f"{run_id}:{planned.query_id}"
    try:
        return runner(
            planned.query,
            planned.limit,
            run_id=child_run_id,
            namespace=namespace,
        )
    except Exception as exc:  # noqa: BLE001 - recorded per query, never fatal
        logger.exception("query %s raised", planned.query_id)
        return QueryOutcome(error=f"{type(exc).__name__}: {exc}")


def _merge_honest_nulls(target: dict[str, str], source: dict[str, str]) -> None:
    for key, value in source.items():
        target.setdefault(key, value)


def run_nightly(
    *,
    trigger: str = "schedule",
    now: datetime | None = None,
    catalog: QueryCatalog | None = None,
    catalog_path: str | Path | None = None,
    max_papers: int = DEFAULT_MAX_PAPERS,
    max_llm_usd: float = 0.0,
    namespace: str = "staging",
    git_sha: str = "unknown",
    image: str = "unknown",
    runs_dir: str | Path | None = None,
    runs_bucket: str | None = None,
    runner: Runner = run_query_ingest,
    repo: Any = None,
    write_json: bool = True,
    write_neo4j: bool = True,
) -> NightlyResult:
    """Plan, run, aggregate and persist one nightly execution."""
    started = now or utc_now()
    if started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    run_id = new_run_id(started)

    if catalog is None:
        catalog = load_catalog(catalog_path)

    try:
        planned = plan_queries(catalog, day=started.date(), max_papers=max_papers)
    except ValueError as exc:
        report = PipelineRunReport(
            run_id=run_id,
            started_at=iso(started),
            finished_at=iso(utc_now()),
            status="failed",
            trigger=trigger,
            namespace=namespace,
            budget=Budget(max_papers=max_papers, max_llm_usd=max_llm_usd),
            failures=[Failure(step="plan", message=str(exc))],
            git_sha=git_sha,
            image=image,
        )
        return _finalize(report, url=None, write_json=write_json, write_neo4j=write_neo4j,
                         runs_dir=runs_dir, runs_bucket=runs_bucket, repo=repo)

    entries = [
        QueryEntry(
            query_id=item.query_id,
            query=item.query,
            topic=item.topic,
            limit=item.limit,
        )
        for item in planned
    ]
    report = PipelineRunReport(
        run_id=run_id,
        started_at=iso(started),
        status="running",
        trigger=trigger,
        namespace=namespace,
        queries=entries,
        totals=Totals(),
        budget=Budget(max_papers=max_papers, max_llm_usd=max_llm_usd),
        git_sha=git_sha,
        image=image,
    )

    if not planned:
        report.failures.append(Failure(step="plan", message="no queries were planned"))
        report.status = "failed"
        report.finished_at = iso(utc_now())
        return _finalize(report, url=None, write_json=write_json, write_neo4j=write_neo4j,
                         runs_dir=runs_dir, runs_bucket=runs_bucket, repo=repo)

    # --- run each query under the budgets ---------------------------------
    # The planner already bounds the plan by ``max_papers``; the loop's checks
    # are the runtime guard for the case where an ingest returns more papers
    # than its plan requested, or the estimated LLM spend crosses the cap.
    papers_used = 0
    runtime_stopped = False
    usd_stopped = False
    for planned_q, entry in zip(planned, entries):
        if papers_used + planned_q.limit > max_papers:
            runtime_stopped = True
            entry.status = "skipped"
            entry.error = "budget exhausted before this query ran"
            continue
        outcome = _run_one(planned_q, run_id=run_id, namespace=namespace, runner=runner)
        entry.papers_seen = outcome.papers_seen
        entry.papers_new = outcome.papers_new
        if outcome.error:
            entry.status = "failed"
            entry.error = outcome.error
            report.failures.append(
                Failure(step=f"ingest:{planned_q.query_id}", message=outcome.error)
            )
        else:
            entry.status = "succeeded"

        report.totals.papers_seen += outcome.papers_seen
        report.totals.papers_new += outcome.papers_new
        report.totals.committed_operations += outcome.committed_operations
        report.totals.deferred_candidates += outcome.deferred_candidates
        _merge_honest_nulls(report.totals.honest_nulls, outcome.honest_nulls)
        for reason, count in outcome.deferral_reasons.items():
            report.deferral_reasons[reason] = (
                report.deferral_reasons.get(reason, 0) + count
            )
        papers_used += max(outcome.papers_seen, 0)
        report.budget.est_llm_usd += outcome.est_llm_usd

        if 0 < max_llm_usd < report.budget.est_llm_usd:
            usd_stopped = True
            entry.error = entry.error or "estimated LLM budget exhausted"
            break

    # ``stopped_by_budget`` is honest about both ways the budget bound the run:
    # the planner leaving enabled queries out of tonight's rotation, and the
    # runtime stopping early. Only the latter is a partial run.
    subset_by_budget = len(planned) < len(catalog.enabled)
    report.budget.stopped_by_budget = subset_by_budget or runtime_stopped or usd_stopped

    # Mark anything never reached (a USD stop leaves later entries pending).
    for entry in entries:
        if entry.status == "pending":
            entry.status = "skipped"
            entry.error = entry.error or "not run"

    ran = [e for e in entries if e.status in ("succeeded", "failed")]
    any_failed = any(e.status == "failed" for e in entries)
    if ran and all(e.status == "failed" for e in entries):
        report.status = "failed"
    elif any_failed or runtime_stopped or usd_stopped:
        report.status = "partial"
    else:
        report.status = "succeeded"
    report.finished_at = iso(utc_now())

    return _finalize(report, url=None, write_json=write_json, write_neo4j=write_neo4j,
                     runs_dir=runs_dir, runs_bucket=runs_bucket, repo=repo)


def _finalize(
    report: PipelineRunReport,
    *,
    url: str | None,
    write_json: bool,
    write_neo4j: bool,
    runs_dir: str | Path | None,
    runs_bucket: str | None,
    repo: Any,
) -> NightlyResult:
    """Persist the report, emit the structured log line, and pick an exit code."""
    if report.finished_at is None:
        report.finished_at = iso(utc_now())

    write_errors: list[tuple[str, str]] = []

    if write_json:
        if runs_dir is None:
            write_errors.append(("report_json", "no runs_dir configured (NIGHTLY_RUNS_DIR)"))
        else:
            try:
                url = write_report_json(
                    report, runs_dir=runs_dir, runs_bucket=runs_bucket
                )
            except Exception as exc:  # noqa: BLE001 - logged, affects exit code
                logger.exception("failed to write the PipelineRun JSON report")
                write_errors.append(("report_json", str(exc)))

    if write_neo4j:
        try:
            write_pipeline_run_node(report, repo=repo)
        except Exception as exc:  # noqa: BLE001 - logged, affects exit code
            logger.exception("failed to write the PipelineRun Neo4j node")
            write_errors.append(("neo4j", str(exc)))

    for step, message in write_errors:
        logger.error("report persistence failed: %s: %s", step, message)

    event = (
        "pipeline_run_completed"
        if report.status in ("succeeded", "partial")
        else "pipeline_run_failed"
    )
    logger.info(
        json.dumps(
            {
                "event": event,
                "run_id": report.run_id,
                "status": report.status,
                "totals": report.totals.model_dump(),
                "url": url,
            }
        )
    )

    if report.status == "failed":
        exit_code = 2
    elif report.status == "succeeded" and not write_errors:
        exit_code = 0
    else:
        exit_code = 1
    return NightlyResult(report=report, url=url, exit_code=exit_code)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m agentic_kg.pipeline.nightly",
        description="Run the nightly ingestion pipeline once.",
    )
    parser.add_argument(
        "--trigger",
        default=None,
        choices=["schedule", "manual"],
        help="How this run was triggered (default: NIGHTLY_TRIGGER or 'schedule').",
    )
    parser.add_argument("--catalog", default=None, help="Path to ingest-queries.yaml.")
    parser.add_argument("--max-papers", type=int, default=None, help="Paper budget.")
    parser.add_argument(
        "--max-llm-usd", type=float, default=None,
        help="Estimated-USD budget; <= 0 disables the cap.",
    )
    parser.add_argument("--namespace", default=None, help="Canonical namespace.")
    parser.add_argument("--runs-dir", default=None, help="Directory for the JSON reports.")
    parser.add_argument("--runs-bucket", default=None, help="Bucket for the gs:// URL.")
    parser.add_argument("--git-sha", default=None)
    parser.add_argument("--image", default=None)
    parser.add_argument(
        "--plan-only",
        action="store_true",
        help="Print tonight's plan and exit without ingesting or writing.",
    )
    parser.add_argument("--no-neo4j", action="store_true", help="Skip the Neo4j node.")
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    trigger = args.trigger or os.environ.get(ENV_TRIGGER, "schedule")
    max_papers = (
        args.max_papers
        if args.max_papers is not None
        else int(os.environ.get(ENV_MAX_PAPERS, str(DEFAULT_MAX_PAPERS)))
    )
    max_llm_usd = (
        args.max_llm_usd
        if args.max_llm_usd is not None
        else float(os.environ.get(ENV_MAX_LLM_USD, "0"))
    )
    namespace = args.namespace or os.environ.get(ENV_NAMESPACE, "staging")
    runs_dir = args.runs_dir or os.environ.get(ENV_RUNS_DIR)
    runs_bucket = args.runs_bucket or os.environ.get(ENV_RUNS_BUCKET)
    git_sha = args.git_sha or os.environ.get(ENV_GIT_SHA, "unknown")
    image = args.image or os.environ.get(ENV_IMAGE, "unknown")
    catalog_path = args.catalog or None

    if args.plan_only:
        catalog = load_catalog(catalog_path)
        planned = plan_queries(
            catalog,
            day=utc_now().date(),
            max_papers=max_papers,
        )
        print(json.dumps([item.to_dict() for item in planned], indent=2))
        return 0

    result = run_nightly(
        trigger=trigger,
        catalog_path=catalog_path,
        max_papers=max_papers,
        max_llm_usd=max_llm_usd,
        namespace=namespace,
        git_sha=git_sha,
        image=image,
        runs_dir=runs_dir,
        runs_bucket=runs_bucket,
        write_neo4j=not args.no_neo4j,
    )
    return result.exit_code


if __name__ == "__main__":
    sys.exit(main())
