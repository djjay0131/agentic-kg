"""
Integration tests for Topic repository operations (E-1, Unit 3).

These tests require a running Neo4j instance (see conftest's `neo4j_repository`
fixture). They use TEST_ prefixed Topic names and UUID ids; the fixture cleans
up any node whose `name` starts with `TEST_` after each test.

Embeddings are supplied manually or disabled so tests do not depend on the
OpenAI API.
"""

import uuid

import pytest
from agentic_kg.knowledge_graph.models import (
    Paper,
    Problem,
    Topic,
    TopicLevel,
)
from agentic_kg.knowledge_graph.repository import (
    DuplicateError,
    NotFoundError,
)

pytestmark = pytest.mark.integration


def _test_name(label: str) -> str:
    """Build a TEST_ prefixed unique Topic name so cleanup catches it."""
    return f"TEST_{label}_{uuid.uuid4().hex[:8]}"


def _fake_embedding(seed: float = 0.1) -> list[float]:
    """A deterministic 1536-dim vector for hermetic vector-search tests."""
    return [seed] * 1536


class TestTopicCRUD:
    """Basic create/read/update/delete for Topic."""

    def test_create_and_get_topic(self, neo4j_repository):
        topic = Topic(
            name=_test_name("CreateGet"),
            level=TopicLevel.DOMAIN,
            source="manual",
        )
        created = neo4j_repository.create_topic(topic, generate_embedding=False)

        assert created.id == topic.id

        retrieved = neo4j_repository.get_topic(topic.id)
        assert retrieved.name == topic.name
        assert retrieved.level == TopicLevel.DOMAIN
        assert retrieved.parent_id is None

    def test_get_nonexistent_topic_raises(self, neo4j_repository):
        with pytest.raises(NotFoundError):
            neo4j_repository.get_topic("TEST_nonexistent-topic-id")

    def test_create_duplicate_id_raises(self, neo4j_repository):
        topic = Topic(
            name=_test_name("Dup"),
            level=TopicLevel.AREA,
        )
        neo4j_repository.create_topic(topic, generate_embedding=False)
        with pytest.raises(DuplicateError):
            neo4j_repository.create_topic(topic, generate_embedding=False)

    def test_update_topic(self, neo4j_repository):
        topic = Topic(
            name=_test_name("Update"),
            level=TopicLevel.AREA,
            description="Initial description",
        )
        neo4j_repository.create_topic(topic, generate_embedding=False)

        topic.description = "Updated description"
        neo4j_repository.update_topic(topic, regenerate_embedding=False)

        retrieved = neo4j_repository.get_topic(topic.id)
        assert retrieved.description == "Updated description"

    def test_update_nonexistent_raises(self, neo4j_repository):
        topic = Topic(name=_test_name("Missing"), level=TopicLevel.AREA)
        with pytest.raises(NotFoundError):
            neo4j_repository.update_topic(topic, regenerate_embedding=False)

    def test_delete_topic(self, neo4j_repository):
        topic = Topic(name=_test_name("Delete"), level=TopicLevel.AREA)
        neo4j_repository.create_topic(topic, generate_embedding=False)

        assert neo4j_repository.delete_topic(topic.id) is True
        with pytest.raises(NotFoundError):
            neo4j_repository.get_topic(topic.id)

    def test_delete_nonexistent_raises(self, neo4j_repository):
        with pytest.raises(NotFoundError):
            neo4j_repository.delete_topic("TEST_never-existed")


class TestTopicMerge:
    """Idempotent upsert via merge_topic (used by taxonomy loader)."""

    def test_merge_creates_when_missing(self, neo4j_repository):
        name = _test_name("MergeNew")
        topic = Topic(name=name, level=TopicLevel.AREA)
        merged = neo4j_repository.merge_topic(topic, generate_embedding=False)

        assert merged.name == name
        retrieved = neo4j_repository.get_topic(merged.id)
        assert retrieved.name == name

    def test_merge_returns_existing(self, neo4j_repository):
        name = _test_name("MergeSame")
        first = Topic(name=name, level=TopicLevel.AREA)
        first_merged = neo4j_repository.merge_topic(first, generate_embedding=False)

        # Second merge with the same (name, level, parent_id) returns the
        # pre-existing node rather than creating a new one.
        second = Topic(name=name, level=TopicLevel.AREA)
        second_merged = neo4j_repository.merge_topic(second, generate_embedding=False)

        assert second_merged.id == first_merged.id

    def test_merge_is_scoped_by_level(self, neo4j_repository):
        name = _test_name("SameNameDiffLevel")
        area = Topic(name=name, level=TopicLevel.AREA)
        neo4j_repository.merge_topic(area, generate_embedding=False)

        parent_id = area.id
        subtopic = Topic(name=name, level=TopicLevel.SUBTOPIC, parent_id=parent_id)
        sub_merged = neo4j_repository.merge_topic(subtopic, generate_embedding=False)

        assert sub_merged.id != area.id
        assert sub_merged.level == TopicLevel.SUBTOPIC


class TestTopicHierarchy:
    """SUBTOPIC_OF edges, parent linking, tree traversal."""

    def test_link_topic_parent(self, neo4j_repository):
        parent = Topic(name=_test_name("Parent"), level=TopicLevel.DOMAIN)
        child = Topic(name=_test_name("Child"), level=TopicLevel.AREA)
        neo4j_repository.create_topic(parent, generate_embedding=False)
        neo4j_repository.create_topic(child, generate_embedding=False)

        neo4j_repository.link_topic_parent(child.id, parent.id)

        children = neo4j_repository.get_topic_children(parent.id)
        assert child.id in {c.id for c in children}

        retrieved = neo4j_repository.get_topic(child.id)
        assert retrieved.parent_id == parent.id

    def test_link_topic_parent_is_idempotent(self, neo4j_repository):
        parent = Topic(name=_test_name("IdemParent"), level=TopicLevel.DOMAIN)
        child = Topic(name=_test_name("IdemChild"), level=TopicLevel.AREA)
        neo4j_repository.create_topic(parent, generate_embedding=False)
        neo4j_repository.create_topic(child, generate_embedding=False)

        neo4j_repository.link_topic_parent(child.id, parent.id)
        neo4j_repository.link_topic_parent(child.id, parent.id)

        children = neo4j_repository.get_topic_children(parent.id)
        # Only one child despite two links.
        matching = [c for c in children if c.id == child.id]
        assert len(matching) == 1

    def test_link_topic_parent_missing_node_raises(self, neo4j_repository):
        child = Topic(name=_test_name("OnlyChild"), level=TopicLevel.AREA)
        neo4j_repository.create_topic(child, generate_embedding=False)

        with pytest.raises(NotFoundError):
            neo4j_repository.link_topic_parent(child.id, "TEST_no-such-parent")

    def test_create_topic_with_parent_id_auto_links(self, neo4j_repository):
        parent = Topic(name=_test_name("AutoParent"), level=TopicLevel.DOMAIN)
        neo4j_repository.create_topic(parent, generate_embedding=False)

        child = Topic(
            name=_test_name("AutoChild"),
            level=TopicLevel.AREA,
            parent_id=parent.id,
        )
        neo4j_repository.create_topic(child, generate_embedding=False)

        children = neo4j_repository.get_topic_children(parent.id)
        assert child.id in {c.id for c in children}

    def test_get_topic_tree_from_root(self, neo4j_repository):
        domain = Topic(name=_test_name("TreeDomain"), level=TopicLevel.DOMAIN)
        neo4j_repository.create_topic(domain, generate_embedding=False)

        area = Topic(
            name=_test_name("TreeArea"),
            level=TopicLevel.AREA,
            parent_id=domain.id,
        )
        neo4j_repository.create_topic(area, generate_embedding=False)

        sub = Topic(
            name=_test_name("TreeSub"),
            level=TopicLevel.SUBTOPIC,
            parent_id=area.id,
        )
        neo4j_repository.create_topic(sub, generate_embedding=False)

        trees = neo4j_repository.get_topic_tree(root_id=domain.id)
        assert len(trees) == 1
        root = trees[0]
        assert root["id"] == domain.id
        assert len(root["children"]) == 1
        assert root["children"][0]["id"] == area.id
        assert root["children"][0]["children"][0]["id"] == sub.id

    def test_get_topics_by_level(self, neo4j_repository):
        d = Topic(name=_test_name("LvlDom"), level=TopicLevel.DOMAIN)
        a = Topic(name=_test_name("LvlArea"), level=TopicLevel.AREA)
        neo4j_repository.create_topic(d, generate_embedding=False)
        neo4j_repository.create_topic(a, generate_embedding=False)

        domains = neo4j_repository.get_topics_by_level(TopicLevel.DOMAIN)
        assert d.id in {t.id for t in domains}
        assert a.id not in {t.id for t in domains}


class TestTopicEmbeddingSearch:
    """Vector search over topic_embedding_idx."""

    def test_search_returns_highest_similarity_first(self, neo4j_repository):
        close = Topic(
            name=_test_name("Close"),
            level=TopicLevel.AREA,
            embedding=_fake_embedding(0.5),
        )
        far = Topic(
            name=_test_name("Far"),
            level=TopicLevel.AREA,
            embedding=_fake_embedding(-0.5),
        )
        neo4j_repository.create_topic(close, generate_embedding=False)
        neo4j_repository.create_topic(far, generate_embedding=False)

        results = neo4j_repository.search_topics_by_embedding(
            _fake_embedding(0.5), limit=10
        )
        result_ids = [t.id for t, _ in results]
        # Both should be present, and `close` should be ranked first.
        assert close.id in result_ids
        assert far.id in result_ids
        assert result_ids.index(close.id) < result_ids.index(far.id)

    def test_search_respects_level_filter(self, neo4j_repository):
        area_topic = Topic(
            name=_test_name("AreaSearch"),
            level=TopicLevel.AREA,
            embedding=_fake_embedding(0.3),
        )
        sub_topic = Topic(
            name=_test_name("SubSearch"),
            level=TopicLevel.SUBTOPIC,
            parent_id=area_topic.id,
            embedding=_fake_embedding(0.3),
        )
        neo4j_repository.create_topic(area_topic, generate_embedding=False)
        neo4j_repository.create_topic(sub_topic, generate_embedding=False)

        results = neo4j_repository.search_topics_by_embedding(
            _fake_embedding(0.3), limit=20, level=TopicLevel.SUBTOPIC
        )
        levels = {t.level for t, _ in results}
        assert levels.issubset({TopicLevel.SUBTOPIC})


class TestTopicAssignment:
    """BELONGS_TO / RESEARCHES edge creation with transactional count delta."""

    def test_assign_problem_increments_count(
        self, neo4j_repository, sample_problem_data
    ):
        topic = Topic(name=_test_name("AssignProblem"), level=TopicLevel.AREA)
        neo4j_repository.create_topic(topic, generate_embedding=False)

        problem = Problem(
            id=f"TEST_{uuid.uuid4().hex[:16]}",
            **sample_problem_data,
        )
        neo4j_repository.create_problem(problem, generate_embedding=False)

        created = neo4j_repository.assign_entity_to_topic(
            problem.id, topic.id, entity_label="Problem"
        )
        assert created is True

        refreshed = neo4j_repository.get_topic(topic.id)
        assert refreshed.problem_count == 1
        assert refreshed.paper_count == 0

    def test_assign_is_idempotent(
        self, neo4j_repository, sample_problem_data
    ):
        topic = Topic(name=_test_name("AssignIdem"), level=TopicLevel.AREA)
        neo4j_repository.create_topic(topic, generate_embedding=False)
        problem = Problem(
            id=f"TEST_{uuid.uuid4().hex[:16]}",
            **sample_problem_data,
        )
        neo4j_repository.create_problem(problem, generate_embedding=False)

        first = neo4j_repository.assign_entity_to_topic(
            problem.id, topic.id, entity_label="Problem"
        )
        second = neo4j_repository.assign_entity_to_topic(
            problem.id, topic.id, entity_label="Problem"
        )
        assert first is True
        assert second is False

        # Count stays at 1 — no double-increment.
        refreshed = neo4j_repository.get_topic(topic.id)
        assert refreshed.problem_count == 1

    def test_assign_paper_uses_researches_edge_and_paper_count(
        self, neo4j_repository, sample_paper_data
    ):
        topic = Topic(name=_test_name("AssignPaper"), level=TopicLevel.AREA)
        neo4j_repository.create_topic(topic, generate_embedding=False)
        paper = Paper(**sample_paper_data)
        neo4j_repository.create_paper(paper)

        neo4j_repository.assign_entity_to_topic(
            paper.doi, topic.id, entity_label="Paper"
        )

        refreshed = neo4j_repository.get_topic(topic.id)
        assert refreshed.paper_count == 1
        assert refreshed.problem_count == 0

    def test_assign_unknown_label_raises(self, neo4j_repository):
        topic = Topic(name=_test_name("AssignBad"), level=TopicLevel.AREA)
        neo4j_repository.create_topic(topic, generate_embedding=False)
        with pytest.raises(ValueError):
            neo4j_repository.assign_entity_to_topic(
                "anything", topic.id, entity_label="Author"
            )

    def test_unassign_removes_edge_and_decrements(
        self, neo4j_repository, sample_problem_data
    ):
        topic = Topic(name=_test_name("Unassign"), level=TopicLevel.AREA)
        neo4j_repository.create_topic(topic, generate_embedding=False)
        problem = Problem(
            id=f"TEST_{uuid.uuid4().hex[:16]}",
            **sample_problem_data,
        )
        neo4j_repository.create_problem(problem, generate_embedding=False)
        neo4j_repository.assign_entity_to_topic(
            problem.id, topic.id, entity_label="Problem"
        )

        removed = neo4j_repository.unassign_entity_from_topic(
            problem.id, topic.id, entity_label="Problem"
        )
        assert removed is True

        refreshed = neo4j_repository.get_topic(topic.id)
        assert refreshed.problem_count == 0


class TestTopicCountReconciliation:
    """Periodic sanity check that corrects denormalized count drift."""

    def test_reconcile_corrects_drift(
        self, neo4j_repository, sample_problem_data
    ):
        topic = Topic(
            name=_test_name("Reconcile"),
            level=TopicLevel.AREA,
            problem_count=42,  # Artificially inflated: no real edges yet.
        )
        neo4j_repository.create_topic(topic, generate_embedding=False)

        drift = neo4j_repository.reconcile_topic_counts()
        drift_ids = {row["id"] for row in drift}
        assert topic.id in drift_ids

        refreshed = neo4j_repository.get_topic(topic.id)
        assert refreshed.problem_count == 0

    def test_reconcile_no_op_when_consistent(
        self, neo4j_repository, sample_problem_data
    ):
        topic = Topic(name=_test_name("ReconOK"), level=TopicLevel.AREA)
        neo4j_repository.create_topic(topic, generate_embedding=False)
        problem = Problem(
            id=f"TEST_{uuid.uuid4().hex[:16]}",
            **sample_problem_data,
        )
        neo4j_repository.create_problem(problem, generate_embedding=False)
        neo4j_repository.assign_entity_to_topic(
            problem.id, topic.id, entity_label="Problem"
        )

        drift = neo4j_repository.reconcile_topic_counts()
        # The counts on our topic are already correct, so it must not appear
        # in the drift report.
        drift_ids = {row["id"] for row in drift}
        assert topic.id not in drift_ids
