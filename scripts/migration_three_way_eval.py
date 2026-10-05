#!/usr/bin/env python3
"""Render the Phase-5 legacy-vs-new-vs-gold report over the frozen corpus.

The evaluation runner (``agentic_kg.migration.evaluation.runner``) is a library
with no CLI, and the repository keeps the *rendered* report as a data-plane
artifact under ``docs/ground-truth/``. This script is the single command that
regenerates it, so the committed file is reproducible rather than hand-edited:

    python scripts/migration_three_way_eval.py            # write the report
    python scripts/migration_three_way_eval.py --check    # fail if it differs

The report is deterministic: the legacy arm is a committed recording, the new
arm is either a committed recording or an explicit ``ArmUnavailable``, and the
only randomness in ``kg_eval`` is the caller-seeded bootstrap. Two runs over the
same commit and the same pins produce byte-identical output.

What this script adds over the bare runner is the **curated-arm availability**
measurement: the KGCS path runs over the corpus and commits the eight structured
``Paper`` identities, but no *graded* research entity, so ``curated_arm`` returns
its honest null. The runner cannot show that — it is handed ``None`` — so the
numbers behind the null are rendered here instead, next to the report they
explain.
"""

from __future__ import annotations

import argparse
import json
import sys
from importlib import metadata
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CHAIN_ROOT = REPO / "packages/core/tests/extraction/fixtures/ground_truth_chain"
IMPORTER_OUTPUT = REPO / "docs/ground-truth/importer-output"
DEFAULT_OUTPUT = REPO / "docs/ground-truth/adoption-phase5-three-way-eval.md"

#: The second report: the same corpus with the bounded-adviser adjudication stage
#: ON, against the committed **SYNTHETIC** recordings. It is a separate file so
#: the deterministic-only report above stays a clean measurement, and every
#: number in it can carry the SYNTHETIC label without qualification.
ADVISER_SYNTHETIC_OUTPUT = (
    REPO / "docs/ground-truth/adoption-phase5-three-way-eval-adviser-synthetic.md"
)
RECORDINGS = (
    REPO
    / "packages/core/tests/migration/curation/fixtures"
    / "admission_fixtures.synthetic.json"
)

GENERATED_BEGIN = "<!-- BEGIN GENERATED REPORT -->"
GENERATED_END = "<!-- END GENERATED REPORT -->"


def _installed_commit(distribution: str) -> str:
    """The resolved commit of an installed git pin, or a loud honest null.

    Read from ``direct_url.json`` rather than the version string: both repos
    have shipped breaking changes without bumping their version, so the commit
    is the only identity that distinguishes two installs.
    """
    try:
        raw = metadata.distribution(distribution).read_text("direct_url.json")
    except metadata.PackageNotFoundError:
        return "not installed"
    if not raw:
        return "not a VCS install"
    return json.loads(raw).get("vcs_info", {}).get("commit_id", "unknown")


def curated_arm_availability() -> dict[str, object]:
    """Run the real curation path and report why the `new` arm is or is not graded.

    Imports the ingestion/curation stack, which only exists behind the opt-in
    ``migration`` extra. That is deliberate: this is the adopter's measurement,
    not a dependency of the evaluation package.
    """
    from agentic_kg.migration.config import MigrationConfig
    from agentic_kg.migration.curation import (
        CONTRACT_DEFAULT_POLICY,
        curated_arm,
        run_curation,
    )
    from agentic_kg.migration.ingestion import (
        ShadowStores,
        importer_replay_client,
        load_corpus,
        run_shadow_ingestion,
    )
    from kg_contracts.testing.memory import MemoryGraphStore

    papers = load_corpus()
    stores = ShadowStores.in_memory()
    try:
        shadow = run_shadow_ingestion(
            papers,
            config=MigrationConfig(use_kgis_ingestion=True, use_kgcs_resolution=False),
            client=importer_replay_client(papers),
            stores=stores,
        )
    finally:
        stores.close()

    candidates = (*shadow.paper_candidates, *shadow.candidates)
    doi_to_slug = {paper.doi.casefold(): paper.slug for paper in papers}
    result = run_curation(
        candidates,
        config=MigrationConfig(use_kgis_ingestion=True, use_kgcs_resolution=True),
        store=MemoryGraphStore(),
        confidence_policy=CONTRACT_DEFAULT_POLICY,
    )
    arm = curated_arm(result, candidates, doi_to_slug=doi_to_slug)
    return {
        "candidates": len(candidates),
        "route_counts": result.route_counts(),
        "operation_counts": result.operation_counts(),
        "committed_candidates": len(result.committed_candidate_ids),
        "deferred_candidates": len(result.deferred),
        "graded_committed": arm.graded_committed,
        "graded_deferred": arm.graded_deferred,
        "graded_rejected": arm.graded_rejected,
        "graded_uncommitted": arm.graded_uncommitted,
        "available": arm.available,
        "reason": arm.reason,
    }


def _runner_papers(arm_papers):
    """The export's payload as the runner's OWN ``ArmPaper`` / ``ArmEntity``.

    The local ``ShadowArmPaper`` is field-compatible today; constructing the
    runner's types explicitly is what makes an upstream rename fail loudly
    instead of silently grading the wrong thing.
    """
    from agentic_kg.migration.evaluation.corpus import ArmEntity, ArmPaper
    from agentic_kg.migration.ingestion.arm_export import as_arm_payload

    return tuple(
        ArmPaper(
            slug=payload["slug"],
            status=payload["status"],
            entities=tuple(ArmEntity(**entity) for entity in payload["entities"]),
            citations=tuple(payload["citations"]),
        )
        for payload in as_arm_payload(arm_papers)
    )


def _shadow_candidates_and_papers():
    """The shadow candidates and papers, from one deterministic replay run."""
    from agentic_kg.migration.config import MigrationConfig
    from agentic_kg.migration.ingestion import (
        ShadowStores,
        importer_replay_client,
        load_corpus,
        run_shadow_ingestion,
    )

    papers = load_corpus()
    stores = ShadowStores.in_memory()
    try:
        shadow = run_shadow_ingestion(
            papers,
            config=MigrationConfig(use_kgis_ingestion=True, use_kgcs_resolution=False),
            client=importer_replay_client(papers),
            stores=stores,
        )
    finally:
        stores.close()
    return (*shadow.paper_candidates, *shadow.candidates), papers


def render_adviser_synthetic_report() -> str:
    """The adjudication-ON report over the committed SYNTHETIC recordings.

    Every number here validates *wiring*: the oracle behind the recordings has
    not read a paper and is not a model. The prose says so before any table, and
    the pins and counts let a reader reproduce it. If the recordings are absent
    the arm is an honest null and the report says why, rather than grading an
    empty arm.
    """
    from agentic_kg.migration.config import MigrationConfig
    from agentic_kg.migration.curation import (
        CONTRACT_DEFAULT_POLICY,
        curated_arm,
        load_recorded_adviser,
        run_curation,
    )
    from agentic_kg.migration.evaluation.runner import run_evaluation
    from kg_contracts.testing.memory import MemoryGraphStore

    candidates, papers = _shadow_candidates_and_papers()
    doi_to_slug = {paper.doi.casefold(): paper.slug for paper in papers}

    if not RECORDINGS.is_file():
        return _adviser_report_prose(
            availability=None,
            report=None,
            missing=RECORDINGS,
        )

    result = run_curation(
        candidates,
        config=MigrationConfig(
            use_kgis_ingestion=True,
            use_kgcs_resolution=True,
            use_kgcs_adjudication=True,
        ),
        store=MemoryGraphStore(),
        confidence_policy=CONTRACT_DEFAULT_POLICY,
        adviser=load_recorded_adviser(RECORDINGS),
    )
    arm = curated_arm(result, candidates, doi_to_slug=doi_to_slug)
    report = run_evaluation(
        chain_root=CHAIN_ROOT,
        importer_output_dir=IMPORTER_OUTPUT,
        new_arm_papers=(
            _runner_papers(arm.arm_papers) if arm.arm_papers is not None else None
        ),
    )
    adjudication = result.adjudication
    availability = {
        "candidates": len(candidates),
        "route_counts": result.route_counts(),
        "operation_counts": result.operation_counts(),
        "committed_candidates": len(result.committed_candidate_ids),
        "deferred_candidates": len(result.deferred),
        "eligible": adjudication.eligible if adjudication else 0,
        "admitted": adjudication.admitted if adjudication else 0,
        "held": adjudication.held if adjudication else 0,
        "graded_committed": arm.graded_committed,
        "graded_deferred": arm.graded_deferred,
        "graded_rejected": arm.graded_rejected,
        "graded_uncommitted": arm.graded_uncommitted,
        "available": arm.available,
        "reason": arm.reason,
        "caveat": arm.caveat,
    }
    return _adviser_report_prose(availability=availability, report=report)


def _adviser_report_prose(*, availability, report, missing: Path | None = None) -> str:
    lines = [
        "---",
        "title: Phase 5 — bounded-adviser adjudication (SYNTHETIC recordings)",
        "nav_exclude: true",
        "---",
        "",
        "# Phase 5 — bounded-adviser adjudication: a SYNTHETIC measurement",
        "",
        "> **SYNTHETIC — not a model measurement.** The bounded-adviser stage is run",
        "> against committed recordings produced by a scripted, deterministic oracle",
        "> (`scripts/record_admission_fixtures.py`, `SyntheticAdmissionOracle`), which",
        "> admits every candidate it is shown and calls no model and no network. These",
        "> numbers validate the *wiring* — question → request key → assessment → gate →",
        "> plan → graph. They say nothing about whether an LLM would admit these",
        "> entities. Live-model numbers are pending a staging run against the",
        "> `OpenAICompletionClient` adapter.",
        ">",
        "> The deterministic-only report remains",
        "> `docs/ground-truth/adoption-phase5-three-way-eval.md` and is unchanged by",
        "> this file.",
        "",
        "> Generated by `scripts/migration_three_way_eval.py`; do not hand-edit.",
        "",
        "## Pins",
        "",
        f"- `agentic-kgis @ {_installed_commit('agentic-kgis')}`",
        f"- `agentic-kgcs @ {_installed_commit('agentic-kgcs')}`",
        f"- recordings: `{RECORDINGS.relative_to(REPO)}` (SYNTHETIC)",
        "",
    ]
    if missing is not None:
        lines += [
            "## Adjudication",
            "",
            f"**Not run.** the SYNTHETIC recordings are missing at `{missing}`. "
            "Regenerate them with `python scripts/record_admission_fixtures.py`.",
            "",
        ]
        return "\n".join(lines)

    lines += [
        "## Adjudication",
        "",
        "The bounded-adviser stage ran over the candidates the deterministic policy",
        "deferred to `LLM_ASSESS`:",
        "",
        f"- {availability['candidates']} candidates routed `{availability['route_counts']}`",
        f"- eligible entity candidates consulted: **{availability['eligible']}** "
        f"(admitted: **{availability['admitted']}**, held: {availability['held']})",
        f"- operations planned/applied: `{availability['operation_counts']}` "
        f"({availability['committed_candidates']} committed, "
        f"{availability['deferred_candidates']} deferred)",
        f"- graded entities committed: **{availability['graded_committed']}**; "
        f"graded entities deferred: **{availability['graded_deferred']}**",
        "",
    ]
    if not availability["available"]:
        lines += [
            f"> **The `new` arm is still unavailable.** {availability['reason']}",
            "",
        ]
    elif availability["caveat"]:
        lines += ["> " + availability["caveat"], ""]

    from agentic_kg.migration.evaluation.runner import render_report

    lines += [
        GENERATED_BEGIN,
        render_report(report).rstrip("\n"),
        GENERATED_END,
        "",
    ]
    return "\n".join(lines)


def render_committed_report() -> str:
    """The full committed artifact: provenance, curated-arm null, and the report."""
    from agentic_kg.migration.evaluation.runner import render_report, run_evaluation

    report = run_evaluation(chain_root=CHAIN_ROOT, importer_output_dir=IMPORTER_OUTPUT)
    availability = curated_arm_availability()

    lines = [
        "---",
        "title: Phase 5 — Legacy vs New vs Gold",
        "nav_exclude: true",
        "---",
        "",
        "# Phase 5 — legacy vs new vs gold: first graded measurement",
        "",
        "> Generated by `scripts/migration_three_way_eval.py`; do not hand-edit.",
        "> The section between the markers is exactly `render_report(",
        "> run_evaluation(...))` over the frozen corpus.",
        "",
        "## Pins",
        "",
        f"- `agentic-kgis @ {_installed_commit('agentic-kgis')}`",
        f"- `agentic-kgcs @ {_installed_commit('agentic-kgcs')}`",
        f"- reconciled papers: {len(report.papers)} ({', '.join(p.slug for p in report.papers)})",
        f"- scored gold entities: {report.scoreable_entities}",
        "",
        "## The `new` arm: a measured declination, not an empty arm",
        "",
        "The KGCS curation path runs end to end over the committed corpus under",
        "`CONTRACT_DEFAULT_POLICY` (the published default, no threshold lowered):",
        "",
        f"- {availability['candidates']} candidates routed `{availability['route_counts']}`",
        f"- operations planned/applied: `{availability['operation_counts']}` "
        f"({availability['committed_candidates']} committed, "
        f"{availability['deferred_candidates']} deferred to adjudication)",
        f"- graded entities committed: **{availability['graded_committed']}**; "
        f"graded entities deferred: **{availability['graded_deferred']}**",
        "",
        "Every committed identity is a structured `Paper`, and `Paper` is not one",
        "of the graded buckets. No `Method` / `Model` / `ResearchConcept` / `Topic`",
        "candidate reaches the graph: the LLM extractor's candidates carry",
        "`extraction_confidence=0.8`, below the unmoved `auto_min_extraction=0.95`,",
        "and that is a property of the replay fixture rather than of the platform.",
        "So `curated_arm` returns its honest null rather than an empty arm:",
        "",
        f"> {availability['reason']}",
        "",
        "Closing that gap needs the bounded adviser / review stage, not a lower",
        "threshold. Until then `new` is `INSUFFICIENT_EVIDENCE` in every ablation",
        "that involves it, and no metric about it is reported as a number.",
        "",
        GENERATED_BEGIN,
        render_report(report).rstrip("\n"),
        GENERATED_END,
        "",
    ]
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--adviser-output", type=Path, default=ADVISER_SYNTHETIC_OUTPUT
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="exit non-zero if the committed report differs instead of writing it",
    )
    args = parser.parse_args(argv[1:])

    rendered = {
        args.output: render_committed_report(),
        args.adviser_output: render_adviser_synthetic_report(),
    }
    if args.check:
        failed = False
        for path, content in rendered.items():
            if not path.is_file():
                print(f"missing committed report: {path}", file=sys.stderr)
                failed = True
            elif path.read_text(encoding="utf-8") != content:
                print(
                    f"{path} is stale; run scripts/migration_three_way_eval.py",
                    file=sys.stderr,
                )
                failed = True
            else:
                print(f"{path} is current")
        return 1 if failed else 0

    for path, content in rendered.items():
        path.write_text(content, encoding="utf-8")
        print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
