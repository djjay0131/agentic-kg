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

    return parser


def main(argv: Optional[list[str]] = None) -> None:
    """CLI entry point."""
    parser = build_parser()
    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help()
        sys.exit(0)

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    # Build pipeline config
    config = PipelineConfig(
        min_section_length=args.min_section_length,
        extract_relations=not args.skip_relations,
    )

    if args.command == "extract":
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
