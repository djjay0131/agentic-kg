#!/usr/bin/env python3
"""
Paper import CLI for Agentic Knowledge Graph.

Usage:
    # Import single paper by DOI
    python scripts/import_papers.py --doi "10.1038/nature12373"

    # Import single paper by arXiv ID
    python scripts/import_papers.py --arxiv "2106.01345"

    # Import papers from CSV/JSON file
    python scripts/import_papers.py --file papers.csv

    # Import all papers by an author
    python scripts/import_papers.py --author "1741101" --source semantic_scholar

    # Search and display papers (no import)
    python scripts/import_papers.py --search "transformer attention mechanism" --limit 5
"""

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

# Add packages to path for development
sys.path.insert(0, str(Path(__file__).parent.parent / "packages" / "core" / "src"))

from agentic_kg.data_acquisition import (
    PaperAggregator,
    PaperImporter,
    get_paper_aggregator,
    get_paper_importer,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def progress_callback(current: int, total: int, result: any) -> None:
    """Print progress for batch imports."""
    status = "created" if result.created else "updated" if result.updated else "skipped" if result.skipped else "failed"
    paper_info = result.paper.title[:50] + "..." if result.paper and len(result.paper.title) > 50 else (result.paper.title if result.paper else "N/A")
    print(f"[{current}/{total}] {status}: {paper_info}")


async def import_single_paper(
    identifier: str,
    sources: list[str] | None,
    update_existing: bool,
) -> None:
    """Import a single paper by identifier."""
    importer = get_paper_importer()

    print(f"Importing paper: {identifier}")
    result = await importer.import_paper(
        identifier,
        sources=sources,
        update_existing=update_existing,
    )

    if result.error:
        print(f"Error: {result.error}")
        return

    if result.paper:
        status = "Created" if result.created else "Updated" if result.updated else "Skipped (exists)"
        print(f"\n{status} paper:")
        print(f"  DOI: {result.paper.doi}")
        print(f"  Title: {result.paper.title}")
        print(f"  Year: {result.paper.year}")
        print(f"  Venue: {result.paper.venue}")
        print(f"  Authors: {', '.join(result.paper.authors[:5])}{'...' if len(result.paper.authors) > 5 else ''}")
        print(f"  Sources: {', '.join(result.sources)}")


async def import_from_file(
    file_path: str,
    sources: list[str] | None,
    update_existing: bool,
) -> None:
    """Import papers from a CSV or JSON file."""
    path = Path(file_path)

    if not path.exists():
        print(f"Error: File not found: {file_path}")
        return

    # Read identifiers from file
    identifiers = []

    if path.suffix == ".json":
        with open(path) as f:
            data = json.load(f)
            if isinstance(data, list):
                identifiers = data
            elif isinstance(data, dict) and "identifiers" in data:
                identifiers = data["identifiers"]
            else:
                print("Error: JSON must be a list or have 'identifiers' key")
                return

    elif path.suffix == ".csv":
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    # Handle CSV with or without header
                    if "," in line:
                        # Take first column
                        identifier = line.split(",")[0].strip().strip('"')
                    else:
                        identifier = line
                    if identifier and identifier.lower() not in ("doi", "identifier", "id"):
                        identifiers.append(identifier)

    elif path.suffix == ".txt":
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    identifiers.append(line)

    else:
        print(f"Error: Unsupported file format: {path.suffix}")
        return

    if not identifiers:
        print("No identifiers found in file")
        return

    print(f"Found {len(identifiers)} identifiers to import")

    importer = get_paper_importer()
    result = await importer.batch_import(
        identifiers,
        sources=sources,
        update_existing=update_existing,
        progress_callback=progress_callback,
    )

    print(f"\nBatch import complete:")
    print(f"  Total: {result.total}")
    print(f"  Created: {result.created}")
    print(f"  Updated: {result.updated}")
    print(f"  Skipped: {result.skipped}")
    print(f"  Failed: {result.failed}")

    if result.errors:
        print(f"\nErrors:")
        for identifier, error in list(result.errors.items())[:10]:
            print(f"  {identifier}: {error}")
        if len(result.errors) > 10:
            print(f"  ... and {len(result.errors) - 10} more")


async def import_author_papers(
    author_id: str,
    source: str,
    limit: int,
    update_existing: bool,
) -> None:
    """Import all papers by an author."""
    importer = get_paper_importer()

    print(f"Importing papers by author {author_id} from {source}")
    result = await importer.import_author_papers(
        author_id,
        source=source,
        limit=limit,
        update_existing=update_existing,
    )

    print(f"\nAuthor papers import complete:")
    print(f"  Total: {result.total}")
    print(f"  Created: {result.created}")
    print(f"  Updated: {result.updated}")
    print(f"  Skipped: {result.skipped}")
    print(f"  Failed: {result.failed}")


async def search_papers(
    query: str,
    sources: list[str] | None,
    limit: int,
) -> None:
    """Search for papers without importing."""
    aggregator = get_paper_aggregator()

    print(f"Searching for: {query}")
    result = await aggregator.search_papers(
        query,
        sources=sources,
        limit=limit,
    )

    print(f"\nFound {len(result.papers)} papers:")
    print(f"  Total by source: {result.total_by_source}")

    for i, paper in enumerate(result.papers[:limit], 1):
        print(f"\n{i}. {paper.title}")
        print(f"   DOI: {paper.doi or 'N/A'}")
        print(f"   Year: {paper.year or 'N/A'}")
        print(f"   Source: {paper.source}")
        authors = ", ".join(a.name for a in paper.authors[:3])
        if len(paper.authors) > 3:
            authors += f" (+{len(paper.authors) - 3} more)"
        print(f"   Authors: {authors}")

    if result.errors:
        print(f"\nSource errors: {result.errors}")


async def fetch_paper(
    identifier: str,
    sources: list[str] | None,
) -> None:
    """Fetch and display paper data without importing."""
    aggregator = get_paper_aggregator()

    print(f"Fetching paper: {identifier}")
    try:
        result = await aggregator.get_paper(identifier, sources=sources)

        paper = result.paper
        print(f"\nPaper data (from {', '.join(result.sources)}):")
        print(f"  Title: {paper.title}")
        print(f"  DOI: {paper.doi or 'N/A'}")
        print(f"  Year: {paper.year or 'N/A'}")
        print(f"  Venue: {paper.venue or 'N/A'}")
        print(f"  Citation count: {paper.citation_count or 'N/A'}")
        print(f"  Open access: {paper.is_open_access}")
        print(f"  PDF URL: {paper.pdf_url or 'N/A'}")

        print(f"\n  Authors ({len(paper.authors)}):")
        for author in paper.authors[:10]:
            affiliations = ", ".join(author.affiliations) if author.affiliations else "N/A"
            print(f"    - {author.name} ({affiliations})")
        if len(paper.authors) > 10:
            print(f"    ... and {len(paper.authors) - 10} more")

        print(f"\n  Fields: {', '.join(paper.fields_of_study[:5])}")

        if paper.abstract:
            abstract = paper.abstract[:300] + "..." if len(paper.abstract) > 300 else paper.abstract
            print(f"\n  Abstract: {abstract}")

        print(f"\n  External IDs: {paper.external_ids}")

    except Exception as e:
        print(f"Error: {str(e)}")


def main():
    parser = argparse.ArgumentParser(
        description="Import papers into the Agentic Knowledge Graph",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    # Input options (mutually exclusive)
    input_group = parser.add_mutually_exclusive_group(required=True)
    input_group.add_argument("--doi", help="Import paper by DOI")
    input_group.add_argument("--arxiv", help="Import paper by arXiv ID")
    input_group.add_argument("--openalex", help="Import paper by OpenAlex ID")
    input_group.add_argument("--file", help="Import papers from file (CSV, JSON, or TXT)")
    input_group.add_argument("--author", help="Import papers by author ID")
    input_group.add_argument("--search", help="Search for papers (no import)")
    input_group.add_argument("--fetch", help="Fetch paper data without importing")

    # Source options
    parser.add_argument(
        "--sources",
        nargs="+",
        choices=["semantic_scholar", "arxiv", "openalex"],
        help="Sources to fetch from (default: auto-detect)",
    )
    parser.add_argument(
        "--source",
        choices=["semantic_scholar", "openalex"],
        default="semantic_scholar",
        help="Source for author lookup (default: semantic_scholar)",
    )

    # Import options
    parser.add_argument(
        "--update",
        action="store_true",
        help="Update existing papers instead of skipping",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=100,
        help="Maximum papers to import/search (default: 100)",
    )

    # Output options
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output as JSON",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable verbose logging",
    )

    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    # Run async main
    if args.doi:
        asyncio.run(import_single_paper(args.doi, args.sources, args.update))
    elif args.arxiv:
        asyncio.run(import_single_paper(args.arxiv, ["arxiv", "semantic_scholar"], args.update))
    elif args.openalex:
        asyncio.run(import_single_paper(args.openalex, ["openalex"], args.update))
    elif args.file:
        asyncio.run(import_from_file(args.file, args.sources, args.update))
    elif args.author:
        asyncio.run(import_author_papers(args.author, args.source, args.limit, args.update))
    elif args.search:
        asyncio.run(search_papers(args.search, args.sources, args.limit))
    elif args.fetch:
        asyncio.run(fetch_paper(args.fetch, args.sources))


if __name__ == "__main__":
    main()
