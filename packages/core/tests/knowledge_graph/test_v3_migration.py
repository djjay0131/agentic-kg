"""
Tests for the v2 → v3 Topic migration (E-1, Unit 5).

Unit tests cover the pure helpers (cosine similarity, canonical selection).
Integration tests (``pytest.mark.integration``) exercise the full pipeline
against Neo4j using TEST_ prefixed nodes so shared-instance cleanup works.
"""

import math
import uuid

import pytest

from agentic_kg.knowledge_graph.migrations.v3_topic_migration import (
    MERGE_THRESHOLD,
    MigrationReport,
    _cosine_similarity,
    _pick_canonical,
    dedup_migrated_topics,
    migrate_domains_to_topics,
    run_migration,
)
from agentic_kg.knowledge_graph.models import Problem


# =============================================================================
# Pure helpers (no Neo4j)
# =============================================================================


class TestCosineSimilarity:
    def test_identical_vectors(self):
        v = [1.0, 2.0, 3.0]
        assert _cosine_similarity(v, v) == pytest.approx(1.0)

    def test_orthogonal_vectors(self):
        assert _cosine_similarity([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)

    def test_opposite_vectors(self):
        assert _cosine_similarity([1.0, 0.0], [-1.0, 0.0]) == pytest.approx(-1.0)

    def test_partial_alignment(self):
        assert _cosine_similarity([1.0, 0.0], [1.0, 1.0]) == pytest.approx(
            1 / math.sqrt(2)
        )

    def test_zero_vector_returns_zero(self):
        assert _cosine_similarity([0.0, 0.0], [1.0, 1.0]) == 0.0

    def test_length_mismatch_raises(self):
        with pytest.raises(ValueError, match="length mismatch"):
            _cosine_similarity([1.0, 2.0], [1.0, 2.0, 3.0])


class TestPickCanonical:
    def test_longer_name_wins(self):
        a = {"name": "NLP", "created_at": "2024-01-01T00:00:00+00:00"}
        b = {
            "name": "Natural Language Processing",
            "created_at": "2024-06-01T00:00:00+00:00",
        }
        keep, drop = _pick_canonical(a, b)
        assert keep["name"] == "Natural Language Processing"
        assert drop["name"] == "NLP"

    def test_longer_name_wins_regardless_of_order(self):
        a = {
            "name": "Natural Language Processing",
            "created_at": "2024-06-01T00:00:00+00:00",
        }
        b = {"name": "NLP", "created_at": "2024-01-01T00:00:00+00:00"}
        keep, drop = _pick_canonical(a, b)
        assert keep["name"] == "Natural Language Processing"

    def test_equal_length_older_wins(self):
        a = {"name": "Alpha", "created_at": "2024-01-01T00:00:00+00:00"}
        b = {"name": "Beta", "created_at": "2024-06-01T00:00:00+00:00"}
        keep, drop = _pick_canonical(a, b)
        assert keep["name"] == "Alpha"
        assert drop["name"] == "Beta"


class TestThresholdValidation:
    """dedup_migrated_topics rejects invalid thresholds before hitting Neo4j."""

    def test_negative_threshold_raises(self):
        with pytest.raises(ValueError, match="threshold"):
            dedup_migrated_topics(repo=None, threshold=-0.1)

    def test_too_high_threshold_raises(self):
        with pytest.raises(ValueError, match="threshold"):
            dedup_migrated_topics(repo=None, threshold=1.5)


class TestConstants:
    def test_merge_threshold_is_conservative(self):
        # AC-12 calibration pending — starting value per spec is 0.9.
        assert MERGE_THRESHOLD == 0.90

    def test_migration_report_shape(self):
        r = MigrationReport(
            topics_touched=5,
            sources_migrated=42,
            dedup_merges=1,
            threshold=0.9,
        )
        assert r.topics_touched == 5
        assert r.sources_migrated == 42
        assert r.dedup_merges == 1
        assert r.threshold == 0.9


# =============================================================================
# Integration: full migration pipeline
# =============================================================================


pytestmark = []  # placeholder so per-class markers are explicit


def _test_domain(label: str) -> str:
    return f"TEST_{label}_{uuid.uuid4().hex[:6]}"


def _seed_problem(repo, sample_problem_data, domain_value: str):
    problem = Problem(
        id=f"TEST_{uuid.uuid4().hex[:16]}",
        **{**sample_problem_data, "domain": domain_value},
    )
    repo.create_problem(problem, generate_embedding=False)
    return problem


@pytest.mark.integration
class TestMigrationStep1:
    """migrate_domains_to_topics: Topic creation, BELONGS_TO, domain removal."""

    def test_creates_topic_and_belongs_to(
        self, neo4j_repository, sample_problem_data
    ):
        domain = _test_domain("D1")
        problem = _seed_problem(neo4j_repository, sample_problem_data, domain)

        counts = migrate_domains_to_topics(neo4j_repository)
        assert counts["topics_touched"] >= 1
        assert counts["sources_migrated"] >= 1

        # A Topic now exists for that domain
        result = neo4j_repository.driver.session(
            database=neo4j_repository._config.database
        ).run(
            "MATCH (t:Topic {name: $name, source: 'migrated'}) RETURN t",
            name=domain,
        )
        assert result.single() is not None

        # The problem has a BELONGS_TO edge
        with neo4j_repository.session() as session:
            rec = session.run(
                """
                MATCH (p:Problem {id: $id})-[:BELONGS_TO]->(t:Topic {name: $name})
                RETURN t.id AS tid
                """,
                id=problem.id,
                name=domain,
            ).single()
            assert rec is not None

        # The domain property is gone
        reloaded = neo4j_repository.get_problem(problem.id)
        assert reloaded.domain is None

    def test_is_idempotent(
        self, neo4j_repository, sample_problem_data
    ):
        domain = _test_domain("D2")
        _seed_problem(neo4j_repository, sample_problem_data, domain)

        first = migrate_domains_to_topics(neo4j_repository)
        second = migrate_domains_to_topics(neo4j_repository)

        assert first["sources_migrated"] >= 1
        # Nothing left with a .domain property, so step 1 is a no-op on rerun.
        assert second["sources_migrated"] == 0

    def test_multiple_problems_same_domain_share_topic(
        self, neo4j_repository, sample_problem_data
    ):
        domain = _test_domain("Shared")
        p1 = _seed_problem(neo4j_repository, sample_problem_data, domain)
        p2 = _seed_problem(neo4j_repository, sample_problem_data, domain)

        migrate_domains_to_topics(neo4j_repository)

        with neo4j_repository.session() as session:
            count = session.run(
                "MATCH (t:Topic {name: $name}) RETURN count(t) AS n",
                name=domain,
            ).single()["n"]
        assert count == 1

        with neo4j_repository.session() as session:
            refs = session.run(
                """
                MATCH (p:Problem)-[:BELONGS_TO]->(t:Topic {name: $name})
                WHERE p.id IN [$id1, $id2]
                RETURN count(p) AS n
                """,
                name=domain,
                id1=p1.id,
                id2=p2.id,
            ).single()["n"]
        assert refs == 2


@pytest.mark.integration
class TestMigrationDedup:
    """dedup_migrated_topics: merges embedding-close topics."""

    def test_identical_embeddings_merge(self, neo4j_repository):
        """Two migrated topics with identical embeddings merge."""
        from agentic_kg.knowledge_graph.models import Topic, TopicLevel

        emb = [0.1] * 1536
        short = Topic(
            name=_test_domain("NLP"),
            level=TopicLevel.AREA,
            source="migrated",
            embedding=emb,
        )
        long_name = Topic(
            name=_test_domain("Natural_Language_Processing"),
            level=TopicLevel.AREA,
            source="migrated",
            embedding=emb,
        )
        neo4j_repository.create_topic(short, generate_embedding=False)
        neo4j_repository.create_topic(long_name, generate_embedding=False)

        # Restrict to our test topics via narrow threshold + unique embedding.
        merges = dedup_migrated_topics(neo4j_repository, threshold=0.99)
        merged_ids = {
            (m["kept_id"], m["dropped_id"]) for m in merges
        }
        # The one with the longer name survives
        assert any(
            keep == long_name.id and drop == short.id
            for keep, drop in merged_ids
        )

        # The dropped topic is gone
        from agentic_kg.knowledge_graph.repository import NotFoundError
        with pytest.raises(NotFoundError):
            neo4j_repository.get_topic(short.id)

    def test_orthogonal_embeddings_do_not_merge(self, neo4j_repository):
        from agentic_kg.knowledge_graph.models import Topic, TopicLevel

        a = Topic(
            name=_test_domain("A"),
            level=TopicLevel.AREA,
            source="migrated",
            embedding=[1.0] + [0.0] * 1535,
        )
        b = Topic(
            name=_test_domain("B"),
            level=TopicLevel.AREA,
            source="migrated",
            embedding=[0.0, 1.0] + [0.0] * 1534,
        )
        neo4j_repository.create_topic(a, generate_embedding=False)
        neo4j_repository.create_topic(b, generate_embedding=False)

        merges = dedup_migrated_topics(neo4j_repository, threshold=0.9)
        merged_ids = {
            (m["kept_id"], m["dropped_id"]) for m in merges
        }
        # Neither A nor B was dropped — they're orthogonal.
        dropped = {d for _, d in merged_ids}
        assert a.id not in dropped
        assert b.id not in dropped


@pytest.mark.integration
class TestRunMigration:
    """run_migration orchestrates step 1 + step 2 + count reconciliation."""

    def test_returns_report_and_reconciles_counts(
        self, neo4j_repository, sample_problem_data
    ):
        domain = _test_domain("Full")
        _seed_problem(neo4j_repository, sample_problem_data, domain)
        _seed_problem(neo4j_repository, sample_problem_data, domain)

        report = run_migration(neo4j_repository)
        assert isinstance(report, MigrationReport)
        assert report.sources_migrated >= 2
        assert report.threshold == MERGE_THRESHOLD

        # problem_count is reconciled from the two BELONGS_TO edges
        with neo4j_repository.session() as session:
            record = session.run(
                """
                MATCH (t:Topic {name: $name})
                RETURN t.problem_count AS pc
                """,
                name=domain,
            ).single()
        assert record is not None
        assert record["pc"] == 2

    def test_rerun_is_no_op(
        self, neo4j_repository, sample_problem_data
    ):
        domain = _test_domain("Rerun")
        _seed_problem(neo4j_repository, sample_problem_data, domain)

        run_migration(neo4j_repository)
        rerun = run_migration(neo4j_repository)
        assert rerun.sources_migrated == 0
        assert rerun.dedup_merges == 0
