#!/usr/bin/env python3
"""Record the SYNTHETIC admission fixtures CI replays against.

The adjudication stage consults a bounded adviser over the entity candidates the
deterministic policy deferred to ``LLM_ASSESS``. In CI the adviser runs against a
``RecordedCompletionClient``, so the questions it will ask must be recorded. This
script asks the deterministic :class:`SyntheticAdmissionOracle` — **not a model,
no network** — for a response to every eligible question and writes them keyed by
KGCS's ``CompletionRequest.request_hash``.

    python scripts/record_admission_fixtures.py            # write
    python scripts/record_admission_fixtures.py --check    # fail if stale

The output is labelled SYNTHETIC in its filename and envelope. It validates the
wiring (question → request key → parse → gate → plan); it must never be read as a
measurement of model quality. Live-model numbers are pending a staging run
against the OpenAI adapter.

This needs the opt-in ``migration`` extra. It is a developer tool, not product
code; the recordings it writes are a committed test fixture.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
#: Committed alongside the other migration test fixtures. The `.synthetic.`
#: infix is load-bearing: a reader (or a grep) can tell at a glance this is not
#: a recording of a live model.
DEFAULT_OUTPUT = (
    REPO
    / "packages/core/tests/migration/curation/fixtures"
    / "admission_fixtures.synthetic.json"
)


def _load_and_route():
    """The shadow candidates and the deterministic routing over the corpus."""
    from agentic_kg.migration.config import MigrationConfig
    from agentic_kg.migration.curation import CONTRACT_DEFAULT_POLICY, run_curation
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
    candidates = (*shadow.paper_candidates, *shadow.candidates)
    # A plan-only run: the engine's decisions are all the recorder needs, and a
    # store would only add a write surface this tool does not use.
    result = run_curation(
        candidates,
        config=MigrationConfig(use_kgis_ingestion=True, use_kgcs_resolution=True),
        confidence_policy=CONTRACT_DEFAULT_POLICY,
    )
    return candidates, result.engine


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--check",
        action="store_true",
        help="exit non-zero if the committed recording differs",
    )
    args = parser.parse_args(argv[1:])

    from agentic_kg.migration.curation.clients import record_admission_fixtures

    candidates, engine_result = _load_and_route()

    if args.check:
        import json

        if not args.output.is_file():
            print(f"missing committed recording: {args.output}", file=sys.stderr)
            return 1
        before = json.loads(args.output.read_text(encoding="utf-8"))
        tmp = args.output.with_suffix(".check.json")
        record_admission_fixtures(candidates, engine_result, out_path=tmp)
        after = json.loads(tmp.read_text(encoding="utf-8"))
        tmp.unlink()
        if before != after:
            print(
                f"{args.output} is stale; run scripts/record_admission_fixtures.py",
                file=sys.stderr,
            )
            return 1
        print(f"{args.output} is current")
        return 0

    record_admission_fixtures(candidates, engine_result, out_path=args.output)
    print(f"wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
