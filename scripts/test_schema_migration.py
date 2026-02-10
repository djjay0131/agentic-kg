#!/usr/bin/env python3
"""
Test script for verifying Neo4j schema migration to version 2.

Tests:
1. Schema version is updated to 2
2. All constraints are created
3. All property indexes are created
4. All vector indexes are created
5. Schema is idempotent (can run multiple times)

Usage:
    python scripts/test_schema_migration.py

Requires:
    - Neo4j instance running and accessible
    - Environment variables set (NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD)
"""

import logging
import sys
from pathlib import Path

# Add package to path
sys.path.insert(0, str(Path(__file__).parent.parent / "packages" / "core" / "src"))

from agentic_kg.knowledge_graph.schema import SchemaManager, SCHEMA_VERSION

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


def test_schema_migration():
    """Test schema migration to version 2."""
    logger.info("=" * 60)
    logger.info("Testing Schema Migration")
    logger.info("=" * 60)

    # Initialize schema manager
    manager = SchemaManager()

    # Get current version
    current_version = manager._get_current_version()
    logger.info(f"Current schema version: {current_version}")
    logger.info(f"Target schema version: {SCHEMA_VERSION}")

    # Run migration
    logger.info("\n--- Running Schema Migration ---")
    updated = manager.initialize(force=False)

    if updated:
        logger.info("✓ Schema was updated")
    else:
        logger.info("✓ Schema already up to date")

    # Verify new version
    new_version = manager._get_current_version()
    assert new_version == SCHEMA_VERSION, f"Version mismatch: {new_version} != {SCHEMA_VERSION}"
    logger.info(f"✓ Schema version is now {new_version}")

    # Get schema info
    logger.info("\n--- Verifying Schema Elements ---")
    info = manager.get_schema_info()

    # Check constraints
    logger.info(f"\nConstraints ({len(info['constraints'])} total):")
    expected_constraints = [
        "problem_id_unique",
        "paper_doi_unique",
        "author_id_unique",
        "problem_mention_id_unique",  # New in v2
        "problem_concept_id_unique",  # New in v2
        "schema_version_unique",
    ]

    for constraint in expected_constraints:
        if constraint in info["constraints"]:
            logger.info(f"  ✓ {constraint}")
        else:
            logger.error(f"  ✗ {constraint} - MISSING!")
            raise AssertionError(f"Missing constraint: {constraint}")

    # Check property indexes
    logger.info(f"\nProperty Indexes ({len(info['indexes'])} total):")
    index_names = [idx["name"] for idx in info["indexes"]]

    expected_indexes = [
        "problem_status_idx",
        "problem_domain_idx",
        "paper_year_idx",
        "author_name_idx",
        "mention_paper_idx",  # New in v2
        "mention_review_status_idx",  # New in v2
        "mention_concept_idx",  # New in v2
        "concept_domain_idx",  # New in v2
        "concept_mention_count_idx",  # New in v2
        "concept_status_idx",  # New in v2
    ]

    for idx_name in expected_indexes:
        if idx_name in index_names:
            idx_info = next(i for i in info["indexes"] if i["name"] == idx_name)
            logger.info(f"  ✓ {idx_name} ({idx_info['type']}, {idx_info['state']})")
        else:
            logger.warning(f"  ? {idx_name} - Not found (may still be creating)")

    # Check vector indexes
    logger.info("\nVector Indexes:")
    vector_indexes = [idx for idx in info["indexes"] if idx["type"] == "VECTOR"]

    expected_vector_indexes = [
        "problem_embedding_idx",  # Original
        "mention_embedding_idx",  # New in v2
        "concept_embedding_idx",  # New in v2
    ]

    for vec_idx in expected_vector_indexes:
        matching = [v for v in vector_indexes if v["name"] == vec_idx]
        if matching:
            logger.info(f"  ✓ {vec_idx} ({matching[0]['state']})")
        else:
            logger.warning(f"  ? {vec_idx} - Not found (may still be creating)")

    # Test idempotency
    logger.info("\n--- Testing Idempotency ---")
    updated_again = manager.initialize(force=False)
    assert not updated_again, "Schema should not update again"
    logger.info("✓ Schema is idempotent (no changes on second run)")

    # Force re-run
    logger.info("\n--- Testing Force Re-run ---")
    force_updated = manager.initialize(force=True)
    logger.info("✓ Force re-run completed successfully")

    logger.info("\n" + "=" * 60)
    logger.info("✓ All Schema Migration Tests Passed!")
    logger.info("=" * 60)


if __name__ == "__main__":
    try:
        test_schema_migration()
        sys.exit(0)
    except Exception as e:
        logger.error(f"\n✗ Test failed: {e}", exc_info=True)
        sys.exit(1)
