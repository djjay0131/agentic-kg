#!/usr/bin/env python3
"""
CLI tool for fetching paper metadata and PDFs.

Usage:
    python fetch_paper.py <identifier> [options]

Examples:
    python fetch_paper.py 10.1038/nature12373
    python fetch_paper.py 2301.12345
    python fetch_paper.py arxiv:2301.12345 --download --output ./papers
    python fetch_paper.py "10.1038/nature12373" --json
"""

import argparse
import json
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(
        description="Fetch paper metadata and PDFs from various sources",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s 10.1038/nature12373              # Fetch by DOI
  %(prog)s 2301.12345                       # Fetch by arXiv ID
  %(prog)s --search "attention transformer" # Search for papers
  %(prog)s 2301.12345 --download            # Download PDF
  %(prog)s 2301.12345 --json                # Output as JSON
  %(prog)s 2301.12345 --sync                # Sync to Knowledge Graph
        """,
    )
    parser.add_argument(
        "identifier",
        nargs="?",
        help="Paper identifier (DOI, arXiv ID, URL, or S2 ID)",
    )
    parser.add_argument(
        "--search", "-s",
        metavar="QUERY",
        help="Search for papers by query instead of fetching by ID",
    )
    parser.add_argument(
        "--download", "-d",
        action="store_true",
        help="Download PDF if available",
    )
    parser.add_argument(
        "--output", "-o",
        type=Path,
        default=Path("."),
        help="Output directory for downloaded PDFs (default: current dir)",
    )
    parser.add_argument(
        "--json", "-j",
        action="store_true",
        help="Output as JSON",
    )
    parser.add_argument(
        "--embedding",
        action="store_true",
        help="Include SPECTER2 embedding in output",
    )
    parser.add_argument(
        "--sync",
        action="store_true",
        help="Sync paper to Knowledge Graph",
    )
    parser.add_argument(
        "--limit", "-l",
        type=int,
        default=10,
        help="Maximum results for search (default: 10)",
    )

    args = parser.parse_args()

    # Validate arguments
    if not args.identifier and not args.search:
        parser.error("Either identifier or --search is required")

    # Import here to avoid slow startup if just showing help
    try:
        from agentic_kg.data_acquisition import (
            PaperAcquisitionLayer,
            detect_identifier_type,
            sync_paper_to_kg,
        )
    except ImportError as e:
        print(f"Error: Could not import agentic_kg module: {e}", file=sys.stderr)
        print("Make sure agentic_kg is installed: pip install -e packages/core", file=sys.stderr)
        sys.exit(1)

    acquisition = PaperAcquisitionLayer()

    if args.search:
        # Search mode
        results = acquisition.search(args.search, limit=args.limit)
        if not results:
            print("No papers found", file=sys.stderr)
            sys.exit(1)

        if args.json:
            output = [paper.model_dump_json_safe() for paper in results]
            print(json.dumps(output, indent=2))
        else:
            print(f"Found {len(results)} papers:\n")
            for i, paper in enumerate(results, 1):
                print(f"{i}. {paper.title}")
                if paper.doi:
                    print(f"   DOI: {paper.doi}")
                if paper.arxiv_id:
                    print(f"   arXiv: {paper.arxiv_id}")
                if paper.year:
                    print(f"   Year: {paper.year}")
                if paper.authors:
                    authors = ", ".join(a.name for a in paper.authors[:3])
                    if len(paper.authors) > 3:
                        authors += f" et al. ({len(paper.authors)} authors)"
                    print(f"   Authors: {authors}")
                print(f"   Source: {paper.source.value}")
                print()

    else:
        # Fetch mode
        id_type = detect_identifier_type(args.identifier)
        print(f"Identifier type: {id_type.value}", file=sys.stderr)

        metadata = acquisition.get_paper_metadata(
            args.identifier,
            include_embedding=args.embedding,
        )

        if metadata is None:
            print(f"Paper not found: {args.identifier}", file=sys.stderr)
            sys.exit(1)

        if args.json:
            output = metadata.model_dump_json_safe()
            print(json.dumps(output, indent=2))
        else:
            print_paper_metadata(metadata)

        # Download PDF if requested
        if args.download:
            print(f"\nDownloading PDF to {args.output}...", file=sys.stderr)
            result = acquisition.get_pdf(args.identifier, args.output)

            if result.is_success():
                print(f"Downloaded: {result.file_path}")
                print(f"Size: {result.file_size:,} bytes")
                print(f"Source: {result.source.value}")
            else:
                print(f"Download failed: {result.error_message}", file=sys.stderr)
                sys.exit(1)

        # Sync to Knowledge Graph if requested
        if args.sync:
            print("\nSyncing to Knowledge Graph...", file=sys.stderr)
            try:
                sync_result = sync_paper_to_kg(metadata)
                if sync_result.success:
                    print(f"Synced: {sync_result.papers_created} created, "
                          f"{sync_result.papers_updated} updated")
                    print(f"Authors: {sync_result.authors_created} created, "
                          f"{sync_result.authors_updated} updated")
                    print(f"Relations: {sync_result.relations_created} created")
                else:
                    print(f"Sync errors: {sync_result.errors}", file=sys.stderr)
            except Exception as e:
                print(f"Sync failed: {e}", file=sys.stderr)
                print("Note: Neo4j must be running for KG sync", file=sys.stderr)


def print_paper_metadata(metadata):
    """Print paper metadata in human-readable format."""
    print(f"\n{'='*60}")
    print(f"Title: {metadata.title}")
    print(f"{'='*60}\n")

    # Identifiers
    print("Identifiers:")
    if metadata.doi:
        print(f"  DOI: {metadata.doi}")
    if metadata.arxiv_id:
        print(f"  arXiv: {metadata.arxiv_id}")
    if metadata.s2_id:
        print(f"  S2 ID: {metadata.s2_id}")
    if metadata.openalex_id:
        print(f"  OpenAlex: {metadata.openalex_id}")

    # Publication info
    print(f"\nPublication:")
    if metadata.year:
        print(f"  Year: {metadata.year}")
    if metadata.venue:
        print(f"  Venue: {metadata.venue}")
    if metadata.publication_date:
        print(f"  Date: {metadata.publication_date.strftime('%Y-%m-%d')}")

    # Authors
    if metadata.authors:
        print(f"\nAuthors ({len(metadata.authors)}):")
        for author in metadata.authors:
            affiliations = f" ({', '.join(author.affiliations)})" if author.affiliations else ""
            orcid = f" [ORCID: {author.orcid}]" if author.orcid else ""
            print(f"  - {author.name}{affiliations}{orcid}")

    # Abstract
    if metadata.abstract:
        print(f"\nAbstract:")
        # Word wrap abstract
        words = metadata.abstract.split()
        line = "  "
        for word in words:
            if len(line) + len(word) > 78:
                print(line)
                line = "  "
            line += word + " "
        if line.strip():
            print(line)

    # Fields of study
    if metadata.fields_of_study:
        print(f"\nFields: {', '.join(metadata.fields_of_study)}")

    # Citation metrics
    if metadata.citation_count is not None:
        print(f"\nCitations: {metadata.citation_count:,}")
        if metadata.influential_citation_count:
            print(f"Influential: {metadata.influential_citation_count:,}")

    # Access info
    print(f"\nAccess:")
    print(f"  Open Access: {'Yes' if metadata.is_open_access else 'No'}")
    if metadata.pdf_url:
        print(f"  PDF URL: {metadata.pdf_url}")
    if metadata.url:
        print(f"  Paper URL: {metadata.url}")

    # Source info
    print(f"\nSource: {metadata.source.value}")
    print(f"Retrieved: {metadata.retrieved_at.strftime('%Y-%m-%d %H:%M:%S UTC')}")

    # Embedding info
    if metadata.embedding:
        print(f"\nEmbedding: {len(metadata.embedding)} dimensions (SPECTER2)")


if __name__ == "__main__":
    main()
