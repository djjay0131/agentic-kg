#!/usr/bin/env python3
"""
CLI for the Agentic Knowledge Graph extraction pipeline.

Usage:
    # Extract problems from a local PDF
    python -m agentic_kg.cli extract --file paper.pdf

    # Extract from a PDF URL
    python -m agentic_kg.cli extract --url https://arxiv.org/pdf/2401.12345.pdf

    # Extract with metadata
    python -m agentic_kg.cli extract --file paper.pdf --title "My Paper" --doi "10.1234/test"

    # Batch extract from a JSON/CSV file
    python -m agentic_kg.cli extract --batch papers.json

    # Extract with JSON output
    python -m agentic_kg.cli extract --file paper.pdf --json
"""

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path
from typing import Optional

from agentic_kg.extraction.batch import BatchConfig, BatchJob, get_batch_processor
from agentic_kg.extraction.pipeline import (
    PaperProcessingPipeline,
    PaperProcessingResult,
    PipelineConfig,
    get_pipeline,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def print_result(result: PaperProcessingResult, as_json: bool = False) -> None:
    """Print extraction result to stdout."""
    if as_json:
        output = {
            "success": result.success,
            "paper_title": result.paper_title,
            "paper_doi": result.paper_doi,
            "sections": result.section_count,
            "problems": result.problem_count,
            "relations": result.relation_count,
            "duration_ms": round(result.total_duration_ms, 1),
            "stages": [
                {
                    "stage": s.stage,
                    "success": s.success,
                    "duration_ms": round(s.duration_ms, 1),
                    "error": s.error,
                }
                for s in result.stages
            ],
            "extracted_problems": [
                {
                    "statement": p.statement,
                    "domain": p.domain,
                    "confidence": p.confidence,
                    "quoted_text": p.quoted_text[:200],
                }
                for p in result.get_problems()
            ],
        }
        print(json.dumps(output, indent=2))
        return

    # Human-readable output
    status = "SUCCESS" if result.success else "FAILED"
    print(f"\n{'='*60}")
    print(f"Extraction Result: {status}")
    print(f"{'='*60}")

    if result.paper_title:
        print(f"  Paper: {result.paper_title}")
    if result.paper_doi:
        print(f"  DOI:   {result.paper_doi}")

    print(f"\n  Sections identified: {result.section_count}")
    print(f"  Problems extracted:  {result.problem_count}")
    print(f"  Relations found:     {result.relation_count}")
    print(f"  Total time:          {result.total_duration_ms:.0f}ms")

    # Stage details
    print(f"\n  Pipeline Stages:")
    for stage in result.stages:
        icon = "+" if stage.success else "x"
        print(f"    [{icon}] {stage.stage} ({stage.duration_ms:.0f}ms)")
        if stage.error:
            print(f"        Error: {stage.error}")

    # Problems
    problems = result.get_problems()
    if problems:
        print(f"\n  Extracted Problems:")
        for i, p in enumerate(problems, 1):
            conf_bar = "#" * int(p.confidence * 10)
            print(f"\n    {i}. [{p.confidence:.2f}] {conf_bar}")
            print(f"       {p.statement[:120]}{'...' if len(p.statement) > 120 else ''}")
            if p.domain:
                print(f"       Domain: {p.domain}")

    print()


async def extract_from_file(
    file_path: str,
    title: Optional[str],
    doi: Optional[str],
    authors: Optional[list[str]],
    config: PipelineConfig,
    as_json: bool,
) -> None:
    """Extract problems from a local PDF file."""
    path = Path(file_path)
    if not path.exists():
        print(f"Error: File not found: {file_path}", file=sys.stderr)
        sys.exit(1)

    if not as_json:
        print(f"Extracting from: {path.name}")

    pipeline = get_pipeline(config=config)
    result = await pipeline.process_pdf_file(
        file_path=path,
        paper_title=title or path.stem,
        paper_doi=doi,
        authors=authors or [],
    )
    print_result(result, as_json=as_json)


async def extract_from_url(
    url: str,
    title: Optional[str],
    doi: Optional[str],
    authors: Optional[list[str]],
    config: PipelineConfig,
    as_json: bool,
) -> None:
    """Extract problems from a PDF URL."""
    if not as_json:
        print(f"Extracting from URL: {url}")

    pipeline = get_pipeline(config=config)
    result = await pipeline.process_pdf_url(
        url=url,
        paper_title=title,
        paper_doi=doi,
        authors=authors or [],
    )
    print_result(result, as_json=as_json)


async def extract_from_text(
    text: str,
    title: Optional[str],
    doi: Optional[str],
    config: PipelineConfig,
    as_json: bool,
) -> None:
    """Extract problems from raw text (read from stdin or argument)."""
    if not as_json:
        print(f"Extracting from text ({len(text)} chars)")

    pipeline = get_pipeline(config=config)
    result = await pipeline.process_text(
        text=text,
        paper_title=title or "Direct text input",
        paper_doi=doi,
    )
    print_result(result, as_json=as_json)


async def extract_batch(
    file_path: str,
    config: PipelineConfig,
    batch_config: BatchConfig,
    as_json: bool,
) -> None:
    """Extract problems from multiple papers listed in a file."""
    path = Path(file_path)
    if not path.exists():
        print(f"Error: File not found: {file_path}", file=sys.stderr)
        sys.exit(1)

    # Parse batch file
    papers = []
    if path.suffix == ".json":
        with open(path) as f:
            data = json.load(f)
            if isinstance(data, list):
                papers = data
            elif isinstance(data, dict) and "papers" in data:
                papers = data["papers"]
            else:
                print("Error: JSON must be a list or have a 'papers' key", file=sys.stderr)
                sys.exit(1)
    elif path.suffix == ".csv":
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    parts = line.split(",")
                    if len(parts) >= 1:
                        entry = parts[0].strip().strip('"')
                        if entry.lower() not in ("url", "path", "doi", "file"):
                            papers.append({"url": entry} if entry.startswith("http") else {"path": entry})
    elif path.suffix == ".txt":
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    papers.append({"url": line} if line.startswith("http") else {"path": line})
    else:
        print(f"Error: Unsupported file format: {path.suffix}", file=sys.stderr)
        sys.exit(1)

    if not papers:
        print("No papers found in file", file=sys.stderr)
        sys.exit(1)

    if not as_json:
        print(f"Found {len(papers)} papers to process")

    # Process each paper
    pipeline = get_pipeline(config=config)
    results = []
    for i, paper in enumerate(papers, 1):
        paper_url = paper.get("url")
        paper_path = paper.get("path")
        paper_title = paper.get("title")
        paper_doi = paper.get("doi")
        paper_authors = paper.get("authors", [])

        if not as_json:
            source = paper_url or paper_path or "unknown"
            print(f"\n[{i}/{len(papers)}] Processing: {source}")

        if paper_url:
            result = await pipeline.process_pdf_url(
                url=paper_url,
                paper_title=paper_title,
                paper_doi=paper_doi,
                authors=paper_authors,
            )
        elif paper_path:
            result = await pipeline.process_pdf_file(
                file_path=paper_path,
                paper_title=paper_title or Path(paper_path).stem,
                paper_doi=paper_doi,
                authors=paper_authors,
            )
        else:
            if not as_json:
                print(f"  Skipped: no url or path")
            continue

        results.append(result)

        if not as_json:
            status = "OK" if result.success else "FAILED"
            print(f"  [{status}] {result.problem_count} problems extracted")

    # Summary
    total = len(results)
    succeeded = sum(1 for r in results if r.success)
    total_problems = sum(r.problem_count for r in results)

    if as_json:
        output = {
            "total": total,
            "succeeded": succeeded,
            "failed": total - succeeded,
            "total_problems": total_problems,
            "results": [
                {
                    "paper_title": r.paper_title,
                    "success": r.success,
                    "problems": r.problem_count,
                }
                for r in results
            ],
        }
        print(json.dumps(output, indent=2))
    else:
        print(f"\n{'='*60}")
        print(f"Batch Complete")
        print(f"{'='*60}")
        print(f"  Total:    {total}")
        print(f"  Succeeded: {succeeded}")
        print(f"  Failed:    {total - succeeded}")
        print(f"  Problems:  {total_problems}")


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser."""
    parser = argparse.ArgumentParser(
        prog="agentic-kg",
        description="Agentic Knowledge Graph - Research Problem Extraction",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # Extract command
    extract = subparsers.add_parser(
        "extract",
        help="Extract research problems from papers",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    # Input options (mutually exclusive)
    input_group = extract.add_mutually_exclusive_group(required=True)
    input_group.add_argument("--file", help="Path to a local PDF file")
    input_group.add_argument("--url", help="URL to a PDF file")
    input_group.add_argument("--text", help="Raw text to extract from (use - for stdin)")
    input_group.add_argument("--batch", help="Batch file (JSON, CSV, or TXT) with paper list")

    # Paper metadata
    extract.add_argument("--title", help="Paper title")
    extract.add_argument("--doi", help="Paper DOI")
    extract.add_argument("--authors", nargs="+", help="Paper authors")

    # Pipeline configuration
    extract.add_argument(
        "--min-confidence", type=float, default=0.0,
        help="Minimum confidence threshold for reported problems (default: 0.0)",
    )
    extract.add_argument(
        "--skip-relations", action="store_true",
        help="Skip relation extraction between problems",
    )
    extract.add_argument(
        "--min-section-length", type=int, default=100,
        help="Minimum section length in characters (default: 100)",
    )

    # Batch options
    extract.add_argument(
        "--max-concurrent", type=int, default=3,
        help="Maximum concurrent extractions for batch (default: 3)",
    )

    # Output options
    extract.add_argument(
        "--json", action="store_true", dest="json_output",
        help="Output results as JSON",
    )
    extract.add_argument(
        "-v", "--verbose", action="store_true",
        help="Enable verbose logging",
    )

    # Ingest command
    ingest = subparsers.add_parser(
        "ingest",
        help="Search for papers and ingest into knowledge graph",
    )
    ingest.add_argument(
        "--query", help="Search query for paper discovery",
    )
    ingest.add_argument(
        "--limit", type=int, default=20,
        help="Maximum papers to fetch (default: 20)",
    )
    ingest.add_argument(
        "--sources", nargs="+",
        help="API sources to search (e.g., semantic_scholar arxiv openalex)",
    )
    ingest.add_argument(
        "--dry-run", action="store_true",
        help="Search only — don't extract or write to KG",
    )
    ingest.add_argument(
        "--no-agent-workflow", action="store_true",
        help="Disable agent workflows for MEDIUM/LOW confidence matches",
    )
    ingest.add_argument(
        "--sanity-check-only", action="store_true",
        help="Run sanity checks against existing graph (no ingestion)",
    )
    ingest.add_argument(
        "--min-confidence", type=float, default=0.5,
        help="Minimum extraction confidence to integrate (default: 0.5)",
    )
    ingest.add_argument(
        "--json", action="store_true", dest="json_output",
        help="Output results as JSON",
    )
    ingest.add_argument(
        "-v", "--verbose", action="store_true",
        help="Enable verbose logging",
    )

    # load-taxonomy command (E-1)
    load_taxonomy_cmd = subparsers.add_parser(
        "load-taxonomy",
        help="Load seed taxonomy YAML into the knowledge graph",
    )
    load_taxonomy_cmd.add_argument(
        "--file",
        help="Path to a taxonomy YAML file (defaults to bundled seed_taxonomy.yml)",
    )
    load_taxonomy_cmd.add_argument(
        "--skip-embeddings", action="store_true",
        help="Skip embedding generation (faster, useful for tests)",
    )
    load_taxonomy_cmd.add_argument(
        "-v", "--verbose", action="store_true", help="Enable verbose logging",
    )

    # export-taxonomy command (E-1)
    export_taxonomy_cmd = subparsers.add_parser(
        "export-taxonomy",
        help="Export current taxonomy from the KG to YAML",
    )
    export_taxonomy_cmd.add_argument(
        "--file", required=True, help="Output YAML file path",
    )
    export_taxonomy_cmd.add_argument(
        "-v", "--verbose", action="store_true", help="Enable verbose logging",
    )

    # assign-topic command (E-1)
    assign_topic_cmd = subparsers.add_parser(
        "assign-topic",
        help="Assign a Problem/ProblemMention/ProblemConcept/Paper to a Topic",
    )
    assign_topic_cmd.add_argument(
        "--entity-id", required=True,
        help="Entity identifier (Paper uses its DOI)",
    )
    assign_topic_cmd.add_argument(
        "--topic-id", required=True, help="Target Topic id",
    )
    assign_topic_cmd.add_argument(
        "--entity-label", default="Problem",
        choices=["Problem", "ProblemMention", "ProblemConcept", "Paper"],
        help="Source node label (default: Problem)",
    )
    assign_topic_cmd.add_argument(
        "-v", "--verbose", action="store_true", help="Enable verbose logging",
    )

    # create-concept command (E-2)
    create_concept_cmd = subparsers.add_parser(
        "create-concept",
        help="Create a ResearchConcept (with embedding-based dedup)",
    )
    create_concept_cmd.add_argument(
        "--name", required=True, help="Concept name (>=2 chars)",
    )
    create_concept_cmd.add_argument(
        "--description", default=None,
        help="Optional description for richer embeddings",
    )
    create_concept_cmd.add_argument(
        "--aliases", default=None,
        help="Comma-separated list of alternative names",
    )
    create_concept_cmd.add_argument(
        "--threshold", type=float, default=None,
        help="Override the cosine dedup threshold (default 0.90)",
    )
    create_concept_cmd.add_argument(
        "-v", "--verbose", action="store_true", help="Enable verbose logging",
    )

    # link-concept command (E-2)
    link_concept_cmd = subparsers.add_parser(
        "link-concept",
        help="Link a ProblemConcept or Paper to a ResearchConcept",
    )
    link_concept_cmd.add_argument(
        "--concept-id", required=True, help="Target ResearchConcept id",
    )
    link_concept_cmd.add_argument(
        "--entity-id", required=True,
        help="ProblemConcept id for INVOLVES_CONCEPT, Paper DOI for DISCUSSES",
    )
    link_concept_cmd.add_argument(
        "--rel-type", default="INVOLVES_CONCEPT",
        choices=["INVOLVES_CONCEPT", "DISCUSSES"],
        help="Relationship type (default: INVOLVES_CONCEPT)",
    )
    link_concept_cmd.add_argument(
        "-v", "--verbose", action="store_true", help="Enable verbose logging",
    )

    return parser


def print_ingestion_result(result, as_json: bool = False) -> None:
    """Print ingestion result to stdout."""
    from agentic_kg.ingestion import IngestionResult

    if as_json:
        output = result.model_dump(exclude={"dry_run_papers"} if not result.dry_run_papers else set())
        print(json.dumps(output, indent=2, default=str))
        return

    status = result.status.upper()
    print(f"\n{'='*60}")
    print(f"Ingestion Result: {status}")
    print(f"{'='*60}")
    print(f"  Query:   {result.query}")
    print(f"  Trace:   {result.trace_id}")

    if result.status == "dry_run":
        print(f"\n  Papers found: {result.papers_found}")
        print(f"\n  Papers that would be ingested:")
        for p in result.dry_run_papers:
            pdf = "PDF available" if p.get("pdf_url") else "No PDF"
            print(f"    - {p.get('doi', 'no-doi')}: {p.get('title', 'Untitled')} ({pdf})")
    else:
        print(f"\n  Phase 1 - Search & Import:")
        print(f"    Papers found:    {result.papers_found}")
        print(f"    Papers imported: {result.papers_imported}")
        print(f"\n  Phase 2 - Extraction:")
        print(f"    Papers extracted:     {result.papers_extracted}")
        print(f"    Papers skipped (no PDF): {result.papers_skipped_no_pdf}")
        if result.extraction_errors:
            print(f"    Extraction errors:    {len(result.extraction_errors)}")
            for doi, err in result.extraction_errors.items():
                print(f"      {doi}: {err}")
        print(f"\n  Phase 3 - Integration:")
        print(f"    Total problems:    {result.total_problems}")
        print(f"    Concepts created:  {result.concepts_created}")
        print(f"    Concepts linked:   {result.concepts_linked}")

    if result.sanity_checks:
        print(f"\n  Phase 4 - Sanity Checks:")
        for check in result.sanity_checks:
            icon = "+" if check.passed else "x"
            print(f"    [{icon}] {check.name}: {check.description}")

    if result.error:
        print(f"\n  ERROR: {result.error}")

    print()


def print_sanity_checks(checks, as_json: bool = False) -> None:
    """Print sanity check results to stdout."""
    if as_json:
        output = [c.model_dump() for c in checks]
        print(json.dumps(output, indent=2))
        return

    print(f"\n{'='*60}")
    print(f"Sanity Checks")
    print(f"{'='*60}")
    all_passed = True
    for check in checks:
        icon = "+" if check.passed else "x"
        print(f"  [{icon}] {check.name}: {check.description}")
        if not check.passed:
            all_passed = False
            print(f"       Violations: {check.count}")

    status = "ALL PASSED" if all_passed else "FAILURES DETECTED"
    print(f"\n  Result: {status}")
    print()


async def run_ingest(args) -> None:
    """Run the ingest command."""
    from agentic_kg.ingestion import ingest_papers, run_sanity_checks

    if args.sanity_check_only:
        checks = run_sanity_checks()
        print_sanity_checks(checks, as_json=args.json_output)
        all_passed = all(c.passed for c in checks)
        sys.exit(0 if all_passed else 1)

    if not args.query:
        print("Error: --query is required (unless using --sanity-check-only)", file=sys.stderr)
        sys.exit(1)

    def on_progress(phase, doi, detail):
        if not args.json_output:
            if doi:
                print(f"  [{phase}] {doi}: {detail}")
            else:
                print(f"  [{phase}] {detail}")

    result = await ingest_papers(
        query=args.query,
        limit=args.limit,
        sources=args.sources,
        dry_run=args.dry_run,
        enable_agent_workflow=not args.no_agent_workflow,
        min_extraction_confidence=args.min_confidence,
        on_progress=on_progress,
    )

    print_ingestion_result(result, as_json=args.json_output)
    if result.status == "failed":
        sys.exit(1)


def run_load_taxonomy(args) -> None:
    """Load a taxonomy YAML into Neo4j via the repository."""
    from agentic_kg.knowledge_graph.repository import get_repository
    from agentic_kg.knowledge_graph.taxonomy import (
        DEFAULT_TAXONOMY_PATH,
        load_taxonomy,
    )

    source = args.file or DEFAULT_TAXONOMY_PATH
    repo = get_repository()
    stats = load_taxonomy(
        repo=repo,
        source=source,
        generate_embeddings=not args.skip_embeddings,
    )
    print(
        f"Loaded taxonomy from {source}: "
        f"{stats['created']} created, {stats['matched']} matched"
    )


def run_export_taxonomy(args) -> None:
    """Export the KG taxonomy to a YAML file."""
    from agentic_kg.knowledge_graph.repository import get_repository
    from agentic_kg.knowledge_graph.taxonomy import (
        dump_taxonomy_to_yaml,
        export_taxonomy,
    )

    repo = get_repository()
    taxonomy = export_taxonomy(repo)
    dump_taxonomy_to_yaml(taxonomy, args.file)
    print(f"Exported {_count_nodes(taxonomy)} topic(s) to {args.file}")


def _count_nodes(nodes: list) -> int:
    return sum(1 + _count_nodes(n.get("children", []) or []) for n in nodes)


def run_assign_topic(args) -> None:
    """Assign a single entity to a Topic."""
    from agentic_kg.knowledge_graph.repository import (
        NotFoundError,
        get_repository,
    )

    repo = get_repository()
    try:
        created = repo.assign_entity_to_topic(
            entity_id=args.entity_id,
            topic_id=args.topic_id,
            entity_label=args.entity_label,
        )
    except NotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(2)

    verb = "Created" if created else "Already present"
    print(
        f"{verb} edge: {args.entity_label} {args.entity_id} → Topic {args.topic_id}"
    )


def run_create_concept(args) -> None:
    """Create or dedup-merge a ResearchConcept."""
    from agentic_kg.knowledge_graph.repository import get_repository

    aliases: list[str] = []
    if args.aliases:
        aliases = [a.strip() for a in args.aliases.split(",") if a.strip()]

    repo = get_repository()
    concept, created = repo.create_or_merge_research_concept(
        name=args.name,
        description=args.description,
        aliases=aliases,
        threshold=args.threshold,
    )
    verb = "Created" if created else "Merged into existing concept"
    print(f"{verb}: {concept.name} (id={concept.id})")
    if concept.aliases:
        print(f"  Aliases: {', '.join(concept.aliases)}")


def run_link_concept(args) -> None:
    """Link a ProblemConcept or Paper to a ResearchConcept."""
    from agentic_kg.knowledge_graph.repository import (
        NotFoundError,
        get_repository,
    )

    repo = get_repository()
    try:
        if args.rel_type == "INVOLVES_CONCEPT":
            created = repo.link_problem_to_concept(
                problem_concept_id=args.entity_id,
                research_concept_id=args.concept_id,
            )
        else:  # DISCUSSES
            created = repo.link_paper_to_concept(
                paper_doi=args.entity_id,
                research_concept_id=args.concept_id,
            )
    except NotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(2)

    verb = "Created" if created else "Already present"
    print(
        f"{verb} edge: {args.entity_id} -{args.rel_type}-> "
        f"Concept {args.concept_id}"
    )


def main(argv: Optional[list[str]] = None) -> None:
    """CLI entry point."""
    parser = build_parser()
    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help()
        sys.exit(0)

    if getattr(args, "verbose", False):
        logging.getLogger().setLevel(logging.DEBUG)

    if args.command == "ingest":
        asyncio.run(run_ingest(args))
    elif args.command == "load-taxonomy":
        run_load_taxonomy(args)
    elif args.command == "export-taxonomy":
        run_export_taxonomy(args)
    elif args.command == "assign-topic":
        run_assign_topic(args)
    elif args.command == "create-concept":
        run_create_concept(args)
    elif args.command == "link-concept":
        run_link_concept(args)
    elif args.command == "extract":
        # Build pipeline config
        config = PipelineConfig(
            min_section_length=args.min_section_length,
            extract_relations=not args.skip_relations,
        )

        if args.file:
            asyncio.run(extract_from_file(
                args.file, args.title, args.doi, args.authors, config, args.json_output,
            ))
        elif args.url:
            asyncio.run(extract_from_url(
                args.url, args.title, args.doi, args.authors, config, args.json_output,
            ))
        elif args.text:
            text = sys.stdin.read() if args.text == "-" else args.text
            asyncio.run(extract_from_text(
                text, args.title, args.doi, config, args.json_output,
            ))
        elif args.batch:
            batch_config = BatchConfig(max_concurrent=args.max_concurrent)
            asyncio.run(extract_batch(
                args.batch, config, batch_config, args.json_output,
            ))


if __name__ == "__main__":
    main()
