"""The nightly review-queue adviser stage and its Job entrypoint.

After the nightly ingest has run, this stage reads the run's KGIS ledger,
re-runs the deterministic admission policy over the candidates, and asks one
bounded KGCS adviser to grade each candidate the policy deferred. Every grade
is written as a :class:`~agentic_kg.knowledge_graph.models.review_item.ReviewItem`
in the canonical namespace; a human then approves or rejects it on the Review
page, and an approval goes back through KGCS (never around it).

Run it with::

    python -m agentic_kg.pipeline.curate --run-id nightly-... --ledger-dir /mnt/ledger/staging

The Job passes the run's ledger directory and either ``NIGHTLY_RUN_ID`` or the
``.latest_run_id`` marker the nightly job writes next to its reports. The stage
is idempotent: candidates already queued for the run are skipped, so a retry
after a budget stop grades only the remainder.

Evidence text, not just ids
---------------------------
The adviser is wired with the ledger's evidence registry as KGCS's
``EvidenceLookup``. KGCS then renders the actual quoted text into the prompt
(with its untrusted-evidence notice) and stamps ``prompt_version``
``2+evidence``. The same text is stored on the ReviewItem so the human sees
exactly what the model saw.

What is queued, and what is not
-------------------------------
* Entity candidates (``Problem``, ``ResearchConcept``, ``Method``, ``Model``,
  ``Topic``, ...) are always reviewable; an approval can mint the identity.
* Relation / attribute-assertion candidates are reviewable **only when their
  endpoints are already identity ids**. With entity resolution unwired, an
  alias endpoint cannot be planned by KGCS, so queuing those would ask a human
  to approve something the system cannot execute. They stay in the run's
  ``deferral_reasons``.
* Artifacts have no operation type by KGCS contract and are never queued.

This is a deliberate narrowing, stated in the PR body and the runbook.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from agentic_kg.knowledge_graph.models.review_item import (
    VERDICT_ADMIT,
    VERDICT_REJECT,
    ReviewEvidenceRef,
    ReviewItem,
)

logger = logging.getLogger(__name__)

#: Name of the marker file the nightly job writes next to its reports. The
#: adviser Job reads it because Cloud Workflows does not parse Job stdout.
LATEST_RUN_ID_FILENAME = ".latest_run_id"

#: A conservative, explicitly-an-estimate USD figure per adviser call. The
#: provider client does not report token usage through KGCS's CompletionPort, so
#: budgeting cannot be exact; this number exists so ``NIGHTLY_MAX_LLM_USD`` is
#: enforced rather than decorative. It is not a billing measurement.
EST_USD_PER_ADVISER_CALL = 0.001

#: Evidence text kept on each ReviewItem (the adviser sees KGCS's own, bounded
#: render; this is the human-facing copy).
MAX_EVIDENCE_CHARS = 2000
MAX_EVIDENCE_REFS = 8

#: Cap on items graded in one invocation. The default is generous but finite so
#: a runaway ledger cannot make one Job execution unbounded.
DEFAULT_MAX_ITEMS = 500

ENV_RUN_ID = "NIGHTLY_RUN_ID"
ENV_LEDGER_DIR = "NIGHTLY_LEDGER_DIR"
ENV_MAX_LLM_USD = "NIGHTLY_MAX_LLM_USD"
ENV_RUNS_DIR = "NIGHTLY_RUNS_DIR"
ENV_RUNS_BUCKET = "NIGHTLY_RUNS_BUCKET"
ENV_NAMESPACE = "KGCS_CANONICAL_NAMESPACE"
ENV_MAX_ITEMS = "NIGHTLY_CURATE_MAX_ITEMS"


@dataclass
class CurateResult:
    """What one adviser-stage invocation did."""

    run_id: str
    namespace: str
    candidates_seen: int = 0
    deferred_candidates: int = 0
    reviewable_candidates: int = 0
    review_items_written: int = 0
    review_items_skipped: int = 0
    ungraded: int = 0
    verdict_counts: dict[str, int] = field(default_factory=dict)
    excluded: dict[str, int] = field(default_factory=dict)
    est_llm_usd: float = 0.0
    stopped_by_budget: bool = False
    honest_nulls: dict[str, str] = field(default_factory=dict)
    failures: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "namespace": self.namespace,
            "candidates_seen": self.candidates_seen,
            "deferred_candidates": self.deferred_candidates,
            "reviewable_candidates": self.reviewable_candidates,
            "review_items_written": self.review_items_written,
            "review_items_skipped": self.review_items_skipped,
            "ungraded": self.ungraded,
            "verdict_counts": self.verdict_counts,
            "excluded": self.excluded,
            "est_llm_usd": round(self.est_llm_usd, 6),
            "stopped_by_budget": self.stopped_by_budget,
            "honest_nulls": self.honest_nulls,
            "failures": self.failures,
        }


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _stage_config() -> Any:
    """The config used to re-run the deterministic policy (never the env)."""
    from agentic_kg.migration.config import MigrationConfig

    return MigrationConfig(
        use_kgis_ingestion=True,
        use_kgcs_resolution=True,
    )


def _import_curation() -> Any:
    from agentic_kg.migration import curation

    return curation


def _candidate_summary(candidate: Any) -> str:
    kind = getattr(candidate, "candidate_kind", "candidate")
    display = getattr(candidate, "display_name", None)
    if display:
        return str(display)[:240]
    if kind == "relation":
        return (
            f"{getattr(candidate, 'relation_type', '?')}: "
            f"{getattr(candidate, 'subject', '?')} -> {getattr(candidate, 'object', '?')}"
        )[:240]
    if kind == "attribute_assertion":
        return (
            f"{getattr(candidate, 'attribute', '?')} = "
            f"{getattr(candidate, 'value', '?')}"
        )[:240]
    return str(getattr(candidate, "semantic_key", candidate.candidate_id))[:240]


def _representations(candidate: Any) -> list[str]:
    lines: list[str] = []
    reps = getattr(candidate, "representations", {}) or {}
    for key, value in reps.items():
        text = getattr(value, "text", None)
        if text is None and isinstance(value, str):
            text = value
        if isinstance(text, str) and text.strip():
            lines.append(f"{key}: {text.strip()[:1000]}")
    return lines


def _review_question(candidate: Any) -> Any:
    """The deterministic adviser question for any reviewable candidate."""
    from agentic_kg.migration.curation._contracts import (
        AdviserQuestion,
        ArtifactCandidate,
        AttributeAssertionCandidate,
        EntityCandidate,
        RelationCandidate,
    )

    evidence_ids = tuple(
        ref.evidence_id for ref in (getattr(candidate, "evidence_refs", ()) or ())
    )
    relationships = tuple(
        (
            ref.evidence_id,
            getattr(ref.relationship, "value", str(ref.relationship)),
        )
        for ref in (getattr(candidate, "evidence_refs", ()) or ())
    )

    context = [f"candidate_kind: {candidate.candidate_kind}"]
    if isinstance(candidate, EntityCandidate):
        context.append(f"entity_type: {candidate.entity_type}")
        if candidate.display_name:
            context.append(f"display_name: {candidate.display_name}")
        if candidate.aliases:
            context.append(
                "aliases: "
                + ", ".join(f"{a.namespace}:{a.key}" for a in candidate.aliases)
            )
    elif isinstance(candidate, RelationCandidate):
        context.append(f"relation_type: {candidate.relation_type}")
        context.append(f"subject: {candidate.subject}")
        context.append(f"object: {candidate.object}")
    elif isinstance(candidate, AttributeAssertionCandidate):
        context.append(f"subject: {candidate.subject}")
        context.append(f"attribute: {candidate.attribute}")
        context.append(f"value: {candidate.value}")
    elif isinstance(candidate, ArtifactCandidate):  # pragma: no cover - excluded
        context.append(f"artifact_type: {candidate.artifact_type}")
    context.extend(_representations(candidate))

    return AdviserQuestion(
        kind="review_admission",
        subject=str(getattr(candidate, "semantic_key", candidate.candidate_id)),
        evidence_ids=evidence_ids,
        evidence_relationships=relationships,
        context=tuple(context),
        trace_id=getattr(candidate, "trace_id", "") or "",
    )


def _adviser_class() -> Any:
    """Import the KGCS adviser base lazily (the optional extra)."""
    from agentic_kg.migration.curation._contracts import StructuredAdviser

    class ReviewAdviser(StructuredAdviser):
        """A bounded adviser for the nightly review queue.

        Same KGCS machinery as every other adviser (render → complete → parse →
        abstain, no write surface). The vocabulary adds ``merge`` so the model
        can say "this is an existing entity" without being forced to reject it;
        a merge recommendation is stored as ``merge-into:<id>`` and is *not*
        executable in this slice, so the item is marked non-committable.
        """

        ADVISER_TYPE = "review_admission"
        ADVISER_VERSION = "review_admission/1"
        TEMPLATE_ID = "review_admission"
        PROMPT_VERSION = "1"
        INSTRUCTION = (
            "You are a bounded review-queue adviser. Using only the quoted "
            "evidence, decide whether this extracted candidate should be "
            "admitted to the canonical research graph (recommendation 'admit'), "
            "held back ('hold'), or merged into an existing canonical entity "
            "('merge'). For 'merge', include a 'merge_into' field containing the "
            "existing identity id. Cite the evidence ids you relied on and list "
            "contradictions. You never write: your output is advice for a human "
            "reviewer and a deterministic commit path."
        )
        RECOMMENDATIONS = frozenset({"admit", "hold", "merge"})
        INSUFFICIENT = "hold"

        def _parse(self, response: Any, question: Any) -> Any:
            assessment = super()._parse(response, question)
            if assessment.abstained or assessment.recommendation != "merge":
                return assessment
            try:
                payload = json.loads(response.text)
            except (json.JSONDecodeError, ValueError):
                return self._abstain(
                    question,
                    rationale="merge recommendation was not valid JSON",
                    response=response,
                )
            target = payload.get("merge_into") if isinstance(payload, dict) else None
            if not isinstance(target, str) or not target.strip():
                return self._abstain(
                    question,
                    rationale="merge recommendation carried no merge_into id",
                    response=response,
                )
            return assessment.model_copy(
                update={"recommendation": f"merge-into:{target.strip()}"}
            )

    return ReviewAdviser


def _committability(candidate: Any) -> tuple[bool, Optional[str]]:
    """Whether an approved item can be executed by KGCS today."""
    from agentic_kg.migration.curation._contracts import (
        AttributeAssertionCandidate,
        EntityCandidate,
        RelationCandidate,
        is_identity_id,
    )

    if isinstance(candidate, EntityCandidate):
        return True, None
    if isinstance(candidate, (RelationCandidate, AttributeAssertionCandidate)):
        refs = (
            (candidate.subject, candidate.object)
            if isinstance(candidate, RelationCandidate)
            else (candidate.subject,)
        )
        if all(isinstance(ref, str) and is_identity_id(ref) for ref in refs):
            return True, None
        return (
            False,
            "endpoints are still aliases; entity resolution is not wired, so "
            "KGCS cannot plan this assertion",
        )
    return False, "this candidate kind has no committable operation"


def _evidence_refs(candidate: Any, evidence_store: Any) -> list[ReviewEvidenceRef]:
    refs: list[ReviewEvidenceRef] = []
    for ref in (getattr(candidate, "evidence_refs", ()) or ())[:MAX_EVIDENCE_REFS]:
        try:
            evidence = evidence_store.get(ref.evidence_id)
        except Exception:  # noqa: BLE001 - a broken registry must not break the queue
            evidence = None
        content = getattr(evidence, "content", None) if evidence is not None else None
        if isinstance(content, str):
            content = content[:MAX_EVIDENCE_CHARS]
        refs.append(
            ReviewEvidenceRef(
                evidence_id=ref.evidence_id,
                relationship=getattr(
                    ref.relationship, "value", str(ref.relationship)
                ),
                content=content,
            )
        )
    return refs


def _map_verdict(recommendation: str, abstained: bool) -> str:
    if abstained:
        return VERDICT_REJECT
    if recommendation == "admit":
        return VERDICT_ADMIT
    if recommendation == "hold":
        return VERDICT_REJECT
    if recommendation.startswith("merge-into:"):
        return recommendation
    return VERDICT_REJECT


def _candidates_for_run(ledger: Any, run_id: str) -> tuple[Any, ...]:
    """Candidates whose producer run id belongs to this nightly run.

    The nightly orchestrator runs each planned query as its own migration run
    (``<nightly_run_id>:<query_id>``), so the parent run id is the prefix.
    """
    prefix = f"{run_id}:"
    return tuple(
        entry.candidate
        for entry in ledger.ledger_entries()
        if entry.candidate.producer_run_id == run_id
        or entry.candidate.producer_run_id.startswith(prefix)
    )


def run_curation_stage(
    *,
    run_id: str,
    namespace: str,
    ledger_dir: Optional[str | Path],
    review_store: Any,
    completion: Any = None,
    config: Any = None,
    max_llm_usd: float = 0.0,
    est_usd_per_call: float = EST_USD_PER_ADVISER_CALL,
    max_items: Optional[int] = DEFAULT_MAX_ITEMS,
    now: Optional[str] = None,
) -> CurateResult:
    """Grade this run's deferred candidates and write ReviewItems.

    Parameters are injected so the whole stage runs without a database, a
    network, or a real model in tests.
    """
    result = CurateResult(run_id=run_id, namespace=namespace)

    if not ledger_dir:
        result.honest_nulls["ledger"] = (
            "not_configured: no ledger directory was supplied, so there are no "
            "KGIS candidates to grade for this run (the legacy ingest path does "
            "not write one)"
        )
        return result

    from agentic_kg.migration.ingestion._contracts import ArtifactCandidate
    from agentic_kg.migration.ingestion.stores import ShadowStores

    curation = _import_curation()

    stores = ShadowStores.at(ledger_dir)
    try:
        candidates = _candidates_for_run(stores.ledger, run_id)
        result.candidates_seen = len(candidates)
        if not candidates:
            result.honest_nulls["review_candidates"] = (
                "none: the ledger has no candidates produced by this run; a "
                "legacy ingest writes no KGIS ledger, and a repeated run may "
                "already have been graded"
            )
            return result

        # Reconstruct the deterministic decisions the nightly ingest made. The
        # stage consumes them; it never re-decides eligibility from scores.
        curation_result = curation.run_curation(
            candidates,
            config=config or _stage_config(),
            store=None,
            confidence_policy=curation.STRUCTURED_IDENTITY_POLICY,
        )
        by_id = {candidate.candidate_id: candidate for candidate in candidates}
        result.deferred_candidates = len(curation_result.deferred)

        reviewable: list[tuple[Any, bool, Optional[str], str]] = []
        for deferral in curation_result.deferred:
            candidate = by_id[deferral.candidate_id]
            if isinstance(candidate, ArtifactCandidate):
                result.excluded["artifact"] = result.excluded.get("artifact", 0) + 1
                continue
            committable, blocker = _committability(candidate)
            if not committable and deferral.route == "LLM_ASSESS":
                # An entity is always committable; an assertion with alias
                # endpoints is not, and is not a useful human decision yet.
                result.excluded["uncommittable_assertion"] = (
                    result.excluded.get("uncommittable_assertion", 0) + 1
                )
                continue
            if not committable:
                result.excluded["no_operation"] = (
                    result.excluded.get("no_operation", 0) + 1
                )
                continue
            reviewable.append((candidate, committable, blocker, deferral.reason))

        result.reviewable_candidates = len(reviewable)
        existing = review_store.candidate_ids_for_run(run_id)

        if completion is None:
            from agentic_kg.migration.curation.clients import OpenAICompletionClient

            completion = OpenAICompletionClient()
        adviser = _adviser_class()(
            port=completion,
            evidence_lookup=stores.evidence,
        )

        spent = 0.0
        stamp = now or _now_iso()
        graded = 0
        for candidate, committable, blocker, deferral_reason in reviewable:
            if candidate.candidate_id in existing:
                result.review_items_skipped += 1
                continue
            if max_items is not None and graded >= max_items:
                result.ungraded += 1
                continue
            if (
                max_llm_usd > 0
                and spent + est_usd_per_call > max_llm_usd
            ):
                result.stopped_by_budget = True
                result.ungraded += 1
                continue

            try:
                assessment = adviser.assess(_review_question(candidate))
                spent += est_usd_per_call
                graded += 1
                verdict = _map_verdict(
                    assessment.recommendation, assessment.abstained
                )
                if verdict.startswith("merge-into:"):
                    committable = False
                    blocker = (
                        "the adviser recommended a merge; merge decisions are "
                        "not executable in this slice"
                    )
                item = ReviewItem.build(
                    namespace=namespace,
                    run_id=run_id,
                    candidate_id=candidate.candidate_id,
                    now=stamp,
                    updated_at=stamp,
                    candidate_kind=getattr(candidate, "candidate_kind", ""),
                    entity_type=getattr(candidate, "entity_type", None),
                    candidate_summary=_candidate_summary(candidate),
                    candidate_payload=candidate.model_dump(mode="json"),
                    evidence_refs=_evidence_refs(candidate, stores.evidence),
                    adviser_verdict=verdict,
                    confidence=assessment.confidence,
                    rationale=assessment.rationale,
                    adviser_abstained=assessment.abstained,
                    adviser_type=assessment.adviser_type,
                    adviser_version=assessment.adviser_version,
                    adviser_model_id=assessment.model_id,
                    adviser_model_version=assessment.model_version,
                    prompt_version=assessment.prompt_version,
                    status="pending",
                    committable=committable,
                    commit_blocker=blocker,
                )
                review_store.save(item)
                result.review_items_written += 1
                result.verdict_counts[verdict] = (
                    result.verdict_counts.get(verdict, 0) + 1
                )
            except Exception as exc:  # noqa: BLE001 - one candidate never kills the run
                logger.exception(
                    "failed to grade candidate %s", candidate.candidate_id
                )
                result.failures.append(
                    f"{candidate.candidate_id}: {type(exc).__name__}: {exc}"
                )

        result.est_llm_usd = round(spent, 6)
        if result.ungraded and result.stopped_by_budget:
            result.honest_nulls["budget"] = (
                f"stopped: NIGHTLY_MAX_LLM_USD={max_llm_usd} was exhausted "
                f"after {graded} adviser call(s); {result.ungraded} candidate(s) "
                "remain ungraded"
            )
        if result.excluded:
            result.honest_nulls["excluded"] = (
                "not_queued: artifacts have no operation, and assertions whose "
                "endpoints are still aliases need entity resolution"
            )
        if result.review_items_skipped:
            result.honest_nulls["idempotent_skips"] = (
                f"already_queued: {result.review_items_skipped} candidate(s) "
                "already had a ReviewItem for this run and were not re-graded"
            )
        return result
    finally:
        stores.close()


def update_pipeline_run_from_stage(
    result: CurateResult,
    *,
    review_store: Any,
    runs_dir: Optional[str | Path] = None,
    runs_bucket: Optional[str] = None,
    repository: Any = None,
) -> Any:
    """Project the stage's facts onto the run's ``:PipelineRun`` node.

    The counts are recomputed from the review store rather than added to the
    previous values, so a retry after a partial run cannot double-count.
    """
    from agentic_kg.knowledge_graph.models.pipeline_run import PipelineFailure
    from agentic_kg.knowledge_graph.pipeline_runs import (
        get_pipeline_run,
        save_pipeline_run,
    )
    from agentic_kg.pipeline.report import write_report_json

    try:
        run = get_pipeline_run(result.run_id, repository=repository)
    except Exception as exc:  # noqa: BLE001 - reported, not fatal to the queue
        logger.warning("could not load PipelineRun %s: %s", result.run_id, exc)
        result.failures.append(f"pipeline_run: {type(exc).__name__}: {exc}")
        return None

    run.review_queue_size = review_store.count_items(
        status="pending", run_id=result.run_id
    )
    run.adviser_verdicts = review_store.verdict_counts(result.run_id)
    run.budget.est_llm_usd = round(
        run.budget.est_llm_usd + result.est_llm_usd, 6
    )
    if result.stopped_by_budget:
        run.budget.stopped_by_budget = True

    if result.failures or result.stopped_by_budget:
        message = "; ".join(result.failures) or (
            f"adviser stopped by NIGHTLY_MAX_LLM_USD; {result.ungraded} "
            "candidate(s) ungraded"
        )
        run.failures = [f for f in run.failures if f.step != "adviser"]
        run.failures.append(PipelineFailure(step="adviser", message=message))
        if run.status == "succeeded":
            run.status = "partial"

    save_pipeline_run(run, repository=repository)
    if runs_dir:
        try:
            write_report_json(run, runs_dir=runs_dir, runs_bucket=runs_bucket)
        except Exception as exc:  # noqa: BLE001 - Neo4j is the API source
            logger.warning("could not rewrite the JSON report: %s", exc)
    return run


def _read_latest_run_id(runs_dir: str | Path) -> Optional[str]:
    marker = Path(runs_dir) / "nightly" / LATEST_RUN_ID_FILENAME
    if not marker.is_file():
        return None
    value = marker.read_text(encoding="utf-8").strip()
    return value or None


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m agentic_kg.pipeline.curate",
        description="Grade a nightly run's deferred candidates into the review queue.",
    )
    parser.add_argument("--run-id", default=None, help="Nightly run id to grade.")
    parser.add_argument("--namespace", default=None, help="Canonical namespace.")
    parser.add_argument("--ledger-dir", default=None, help="The run's KGIS ledger directory.")
    parser.add_argument(
        "--max-llm-usd", type=float, default=None,
        help="Estimated-USD adviser budget; <= 0 disables the cap.",
    )
    parser.add_argument(
        "--max-items", type=int, default=None,
        help="Maximum candidates to grade in this invocation.",
    )
    parser.add_argument("--runs-dir", default=None, help="Directory holding the run reports.")
    parser.add_argument("--runs-bucket", default=None, help="Bucket for the gs:// report URL.")
    parser.add_argument(
        "--no-update-run", action="store_true",
        help="Do not update the PipelineRun node (queue writes only).",
    )
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    namespace = (
        args.namespace
        or os.environ.get(ENV_NAMESPACE)
        or "canon"
    )
    runs_dir = args.runs_dir or os.environ.get(ENV_RUNS_DIR)
    runs_bucket = args.runs_bucket or os.environ.get(ENV_RUNS_BUCKET)
    run_id = args.run_id or os.environ.get(ENV_RUN_ID)
    if not run_id and runs_dir:
        run_id = _read_latest_run_id(runs_dir)
    if not run_id:
        logger.error(
            "no run id: pass --run-id, set %s, or provide a runs directory "
            "containing nightly/%s",
            ENV_RUN_ID,
            LATEST_RUN_ID_FILENAME,
        )
        return 2

    ledger_dir = (
        args.ledger_dir
        or os.environ.get(ENV_LEDGER_DIR)
        or os.environ.get("INGEST_LEDGER_DIR")
    )
    max_llm_usd = (
        args.max_llm_usd
        if args.max_llm_usd is not None
        else float(os.environ.get(ENV_MAX_LLM_USD, "0"))
    )
    max_items = (
        args.max_items
        if args.max_items is not None
        else int(os.environ.get(ENV_MAX_ITEMS, str(DEFAULT_MAX_ITEMS)))
    )

    from agentic_kg.knowledge_graph.repository import get_repository
    from agentic_kg.knowledge_graph.review_items import ReviewItemRepository

    repository = get_repository()
    review_store = ReviewItemRepository(repository, namespace=namespace)

    result = run_curation_stage(
        run_id=run_id,
        namespace=namespace,
        ledger_dir=ledger_dir,
        review_store=review_store,
        max_llm_usd=max_llm_usd,
        max_items=max_items,
    )

    if not args.no_update_run:
        update_pipeline_run_from_stage(
            result,
            review_store=review_store,
            runs_dir=runs_dir,
            runs_bucket=runs_bucket,
            repository=repository,
        )

    print(json.dumps(result.to_dict(), sort_keys=True))
    if result.failures:
        return 2
    if result.stopped_by_budget:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())


__all__ = [
    "CurateResult",
    "DEFAULT_MAX_ITEMS",
    "EST_USD_PER_ADVISER_CALL",
    "LATEST_RUN_ID_FILENAME",
    "main",
    "run_curation_stage",
    "update_pipeline_run_from_stage",
]
