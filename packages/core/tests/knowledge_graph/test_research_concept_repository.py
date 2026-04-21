"""
Integration tests for ResearchConcept repository operations (E-2, Unit 3).

Requires a running Neo4j instance (see conftest's ``neo4j_repository``
fixture). Uses ``TEST_`` prefixed names so the shared-instance cleanup
sweep removes them after each test. Embeddings are supplied manually
or disabled so tests do not depend on the OpenAI API.
"""

import uuid

import pytest

from agentic_kg.knowledge_graph.models import (
    Paper,
    ProblemConcept,
    ProblemStatus,
    ResearchConcept,
)
from agentic_kg.knowledge_graph.repository import (
    DuplicateError,
    NotFoundError,
)

pytestmark = pytest.mark.integration


def _test_name(label: str) -> str:
    return f"TEST_{label}_{uuid.uuid4().hex[:8]}"


def _fake_embedding(seed: float = 0.1) -> list[float]:
    """Deterministic 1536-dim vector so vector search is reproducible."""
    return [seed] * 1536


def _make_problem_concept(neo4j_repository, name_suffix: str) -> ProblemConcept:
    """Create a ProblemConcept directly via Cypher (models don't hit repo here)."""
    pc_id = f"TEST_{uuid.uuid4().hex[:16]}"
    with neo4j_repository.session() as session:
        session.run(
            """
            CREATE (pc:ProblemConcept {
                id: $id,
                canonical_statement: $statement,
                status: 'open',
                mention_count: 0,
                paper_count: 0
            })
            """,
            id=pc_id,
            statement=f"TEST canonical statement for {name_suffix}",
        )
    return ProblemConcept(
        id=pc_id,
        canonical_statement=f"TEST canonical statement for {name_suffix}",
        status=ProblemStatus.OPEN,
    )


# =============================================================================
# Basic CRUD
# =============================================================================


class TestResearchConceptCRUD:
    def test_create_and_get(self, neo4j_repository):
        concept = ResearchConcept(
            name=_test_name("CreateGet"),
            description="a sample concept",
            embedding=_fake_embedding(0.2),
        )
        neo4j_repository.create_research_concept(concept, generate_embedding=False)

        retrieved = neo4j_repository.get_research_concept(concept.id)
        assert retrieved.id == concept.id
        assert retrieved.name == concept.name
        assert retrieved.description == "a sample concept"
        assert retrieved.aliases == []

    def test_get_nonexistent_raises(self, neo4j_repository):
        with pytest.raises(NotFoundError):
            neo4j_repository.get_research_concept("TEST_never-existed")

    def test_create_duplicate_id_raises(self, neo4j_repository):
        concept = ResearchConcept(
            name=_test_name("Dup"), embedding=_fake_embedding(0.3)
        )
        neo4j_repository.create_research_concept(concept, generate_embedding=False)
        with pytest.raises(DuplicateError):
            neo4j_repository.create_research_concept(concept, generate_embedding=False)

    def test_update_description_and_aliases(self, neo4j_repository):
        concept = ResearchConcept(
            name=_test_name("Update"),
            description="before",
            aliases=["old"],
            embedding=_fake_embedding(0.4),
        )
        neo4j_repository.create_research_concept(concept, generate_embedding=False)

        updated = neo4j_repository.update_research_concept(
            concept.id,
            description="after",
            aliases=["old", "new"],
        )
        assert updated.description == "after"
        assert sorted(updated.aliases) == ["new", "old"]

        retrieved = neo4j_repository.get_research_concept(concept.id)
        assert retrieved.description == "after"
        assert sorted(retrieved.aliases) == ["new", "old"]

    def test_update_nonexistent_raises(self, neo4j_repository):
        with pytest.raises(NotFoundError):
            neo4j_repository.update_research_concept(
                "TEST_missing", description="x"
            )

    def test_delete(self, neo4j_repository):
        concept = ResearchConcept(
            name=_test_name("Delete"), embedding=_fake_embedding(0.5)
        )
        neo4j_repository.create_research_concept(concept, generate_embedding=False)

        assert neo4j_repository.delete_research_concept(concept.id) is True
        with pytest.raises(NotFoundError):
            neo4j_repository.get_research_concept(concept.id)

    def test_delete_nonexistent_raises(self, neo4j_repository):
        with pytest.raises(NotFoundError):
            neo4j_repository.delete_research_concept("TEST_nope")


# =============================================================================
# Embedding search
# =============================================================================


class TestResearchConceptEmbeddingSearch:
    def test_search_orders_by_similarity(self, neo4j_repository):
        close = ResearchConcept(
            name=_test_name("Close"), embedding=_fake_embedding(0.5)
        )
        far = ResearchConcept(
            name=_test_name("Far"),
            embedding=[1.0] + [0.0] * 1535,
        )
        neo4j_repository.create_research_concept(close, generate_embedding=False)
        neo4j_repository.create_research_concept(far, generate_embedding=False)

        results = neo4j_repository.search_research_concepts_by_embedding(
            embedding=_fake_embedding(0.5), top_k=10
        )
        ids = [c.id for c, _ in results]
        assert close.id in ids
        assert far.id in ids
        assert ids.index(close.id) < ids.index(far.id)

    def test_search_min_score_filters_results(self, neo4j_repository):
        match = ResearchConcept(
            name=_test_name("Match"), embedding=_fake_embedding(0.5)
        )
        other = ResearchConcept(
            name=_test_name("Other"),
            embedding=[1.0] + [0.0] * 1535,
        )
        neo4j_repository.create_research_concept(match, generate_embedding=False)
        neo4j_repository.create_research_concept(other, generate_embedding=False)

        results = neo4j_repository.search_research_concepts_by_embedding(
            embedding=_fake_embedding(0.5), top_k=10, min_score=0.99
        )
        ids = {c.id for c, _ in results}
        # orthogonal-ish `other` should be filtered; identical `match` should remain.
        assert match.id in ids
        assert other.id not in ids


# =============================================================================
# Create-or-merge dedup
# =============================================================================


class TestCreateOrMerge:
    def test_creates_when_no_match(self, neo4j_repository):
        name = _test_name("NoMatch")
        merged, created = neo4j_repository.create_or_merge_research_concept(
            name=name,
            description="first of its kind",
            embedding=_fake_embedding(0.42),
        )
        assert created is True
        assert merged.name == name

    def test_merges_when_above_threshold(self, neo4j_repository):
        existing_name = _test_name("ExistingCanonical")
        existing = ResearchConcept(
            name=existing_name,
            description="established canonical description",
            embedding=_fake_embedding(0.7),
        )
        neo4j_repository.create_research_concept(
            existing, generate_embedding=False
        )

        incoming_name = _test_name("IncomingVariant")
        merged, created = neo4j_repository.create_or_merge_research_concept(
            name=incoming_name,
            aliases=["alt surface form"],
            threshold=0.99,
            embedding=_fake_embedding(0.7),
        )
        assert created is False
        assert merged.id == existing.id
        assert incoming_name in merged.aliases
        assert "alt surface form" in merged.aliases

    def test_merge_does_not_duplicate_incoming_name_matching_existing_name(
        self, neo4j_repository
    ):
        """If the incoming name matches the survivor's name, don't re-add it."""
        canonical = _test_name("ExactMatch")
        concept = ResearchConcept(
            name=canonical, embedding=_fake_embedding(0.8)
        )
        neo4j_repository.create_research_concept(
            concept, generate_embedding=False
        )

        merged, created = neo4j_repository.create_or_merge_research_concept(
            name=canonical,
            aliases=["extra alias"],
            threshold=0.99,
            embedding=_fake_embedding(0.8),
        )
        assert created is False
        assert merged.id == concept.id
        # Canonical name must not appear in aliases.
        assert canonical not in merged.aliases
        assert "extra alias" in merged.aliases

    def test_create_when_below_threshold(self, neo4j_repository):
        anchor = ResearchConcept(
            name=_test_name("Anchor"), embedding=_fake_embedding(0.5)
        )
        neo4j_repository.create_research_concept(anchor, generate_embedding=False)

        new_name = _test_name("Different")
        merged, created = neo4j_repository.create_or_merge_research_concept(
            name=new_name,
            threshold=0.99,
            embedding=[1.0] + [0.0] * 1535,  # orthogonal-ish
        )
        assert created is True
        assert merged.id != anchor.id


# =============================================================================
# Linking to ProblemConcepts and Papers
# =============================================================================


class TestLinking:
    def test_link_problem_to_concept_idempotent_and_counts(
        self, neo4j_repository
    ):
        concept = ResearchConcept(
            name=_test_name("LinkPC"), embedding=_fake_embedding(0.3)
        )
        neo4j_repository.create_research_concept(concept, generate_embedding=False)
        pc = _make_problem_concept(neo4j_repository, "LinkProblem")

        created_first = neo4j_repository.link_problem_to_concept(pc.id, concept.id)
        created_second = neo4j_repository.link_problem_to_concept(pc.id, concept.id)
        assert created_first is True
        assert created_second is False

        refreshed = neo4j_repository.get_research_concept(concept.id)
        assert refreshed.mention_count == 1
        assert refreshed.paper_count == 0

    def test_link_paper_to_concept_idempotent_and_counts(
        self, neo4j_repository, sample_paper_data
    ):
        concept = ResearchConcept(
            name=_test_name("LinkPaper"), embedding=_fake_embedding(0.3)
        )
        neo4j_repository.create_research_concept(concept, generate_embedding=False)
        paper = Paper(**sample_paper_data)
        neo4j_repository.create_paper(paper)

        first = neo4j_repository.link_paper_to_concept(paper.doi, concept.id)
        second = neo4j_repository.link_paper_to_concept(paper.doi, concept.id)
        assert first is True
        assert second is False

        refreshed = neo4j_repository.get_research_concept(concept.id)
        assert refreshed.paper_count == 1
        assert refreshed.mention_count == 0

    def test_unlink_problem_decrements(self, neo4j_repository):
        concept = ResearchConcept(
            name=_test_name("UnlinkPC"), embedding=_fake_embedding(0.3)
        )
        neo4j_repository.create_research_concept(concept, generate_embedding=False)
        pc = _make_problem_concept(neo4j_repository, "Unlink")
        neo4j_repository.link_problem_to_concept(pc.id, concept.id)

        removed = neo4j_repository.unlink_problem_from_concept(pc.id, concept.id)
        assert removed is True

        refreshed = neo4j_repository.get_research_concept(concept.id)
        assert refreshed.mention_count == 0

    def test_unlink_paper_decrements(
        self, neo4j_repository, sample_paper_data
    ):
        concept = ResearchConcept(
            name=_test_name("UnlinkPaper"), embedding=_fake_embedding(0.3)
        )
        neo4j_repository.create_research_concept(concept, generate_embedding=False)
        paper = Paper(**sample_paper_data)
        neo4j_repository.create_paper(paper)
        neo4j_repository.link_paper_to_concept(paper.doi, concept.id)

        removed = neo4j_repository.unlink_paper_from_concept(paper.doi, concept.id)
        assert removed is True

        refreshed = neo4j_repository.get_research_concept(concept.id)
        assert refreshed.paper_count == 0

    def test_link_unknown_relationship_raises(self, neo4j_repository):
        concept = ResearchConcept(
            name=_test_name("BadRel"), embedding=_fake_embedding(0.3)
        )
        neo4j_repository.create_research_concept(concept, generate_embedding=False)
        with pytest.raises(ValueError):
            neo4j_repository._link_entity_to_concept(
                entity_id="x", concept_id=concept.id, relationship="NOT_A_REL"
            )

    def test_get_problems_for_concept(self, neo4j_repository):
        concept = ResearchConcept(
            name=_test_name("GetProblems"), embedding=_fake_embedding(0.3)
        )
        neo4j_repository.create_research_concept(concept, generate_embedding=False)
        pc1 = _make_problem_concept(neo4j_repository, "GP1")
        pc2 = _make_problem_concept(neo4j_repository, "GP2")
        neo4j_repository.link_problem_to_concept(pc1.id, concept.id)
        neo4j_repository.link_problem_to_concept(pc2.id, concept.id)

        problems = neo4j_repository.get_problems_for_concept(concept.id)
        ids = {p["id"] for p in problems}
        assert pc1.id in ids and pc2.id in ids

    def test_get_papers_for_concept(
        self, neo4j_repository, sample_paper_data
    ):
        concept = ResearchConcept(
            name=_test_name("GetPapers"), embedding=_fake_embedding(0.3)
        )
        neo4j_repository.create_research_concept(concept, generate_embedding=False)
        paper = Paper(**sample_paper_data)
        neo4j_repository.create_paper(paper)
        neo4j_repository.link_paper_to_concept(paper.doi, concept.id)

        papers = neo4j_repository.get_papers_for_concept(concept.id)
        assert any(p["doi"] == paper.doi for p in papers)


# =============================================================================
# Count reconciliation
# =============================================================================


class TestReconciliation:
    def test_reconcile_corrects_drift(self, neo4j_repository):
        concept = ResearchConcept(
            name=_test_name("Drift"),
            mention_count=99,  # artificially inflated
            paper_count=42,
            embedding=_fake_embedding(0.3),
        )
        neo4j_repository.create_research_concept(concept, generate_embedding=False)

        drift = neo4j_repository.reconcile_research_concept_counts()
        drift_ids = {row["id"] for row in drift}
        assert concept.id in drift_ids

        refreshed = neo4j_repository.get_research_concept(concept.id)
        assert refreshed.mention_count == 0
        assert refreshed.paper_count == 0

    def test_reconcile_no_op_when_consistent(self, neo4j_repository):
        concept = ResearchConcept(
            name=_test_name("Consistent"), embedding=_fake_embedding(0.3)
        )
        neo4j_repository.create_research_concept(concept, generate_embedding=False)
        pc = _make_problem_concept(neo4j_repository, "ReconOK")
        neo4j_repository.link_problem_to_concept(pc.id, concept.id)

        drift = neo4j_repository.reconcile_research_concept_counts()
        drift_ids = {row["id"] for row in drift}
        assert concept.id not in drift_ids
