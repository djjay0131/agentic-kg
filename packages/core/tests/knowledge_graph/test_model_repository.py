"""
Integration tests for Model repository operations (E-3, Units 3-5).

Requires a running Neo4j instance (see conftest's ``neo4j_repository``
fixture). Uses ``TEST_`` prefixed names so the shared-instance cleanup
sweep removes them after each test. Embeddings are supplied manually
or disabled so tests do not depend on the OpenAI API.

Test layout:
- ``TestModelCRUD`` — Unit 3 (create / get / get_by_name / update / delete)
- ``TestSearchModelsByEmbedding`` — Unit 3 (vector search)
- ``TestLinkPaperToModel`` — Unit 4 (USES_MODEL via generalized helper)
- ``TestCreateOrMergeModel`` — Unit 5 (canonical-protected dedup)
- ``TestConceptLinkingRegression`` — Unit 4 (E-2 concept links must still work)
"""

import uuid

import pytest
from agentic_kg.knowledge_graph.models import Model, Paper
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


def _orthogonal_embedding(slot: int) -> list[float]:
    """Sparse 1-hot 1536-dim vector — orthogonal pairs have cosine=0."""
    v = [0.0] * 1536
    v[slot % 1536] = 1.0
    return v


def _make_paper(neo4j_repository, doi: str) -> Paper:
    """Create a Paper directly so we can test USES_MODEL edges."""
    paper = Paper(
        doi=doi,
        title=f"TEST title for {doi}",
        authors=[],
        year=2024,
    )
    neo4j_repository.create_paper(paper)
    return paper


# =============================================================================
# Unit 3 — Basic CRUD
# =============================================================================


class TestModelCRUD:
    def test_create_and_get(self, neo4j_repository):
        m = Model(
            name=_test_name("CreateGet"),
            description="a test model",
            embedding=_fake_embedding(0.2),
        )
        neo4j_repository.create_model(m, generate_embedding=False)

        retrieved = neo4j_repository.get_model(m.id)
        assert retrieved.id == m.id
        assert retrieved.name == m.name
        assert retrieved.description == "a test model"
        assert retrieved.aliases == []
        assert retrieved.is_canonical is False
        assert retrieved.usage_count == 0

    def test_get_nonexistent_raises(self, neo4j_repository):
        with pytest.raises(NotFoundError):
            neo4j_repository.get_model("TEST_nonexistent_id")

    def test_create_duplicate_id_raises(self, neo4j_repository):
        m = Model(name=_test_name("Dup"), embedding=_fake_embedding(0.3))
        neo4j_repository.create_model(m, generate_embedding=False)
        with pytest.raises(DuplicateError):
            neo4j_repository.create_model(m, generate_embedding=False)

    def test_get_by_name(self, neo4j_repository):
        name = _test_name("ByName")
        m = Model(name=name, embedding=_fake_embedding(0.4))
        neo4j_repository.create_model(m, generate_embedding=False)

        retrieved = neo4j_repository.get_model_by_name(name)
        assert retrieved.id == m.id

    def test_get_by_name_missing_raises(self, neo4j_repository):
        with pytest.raises(NotFoundError):
            neo4j_repository.get_model_by_name("TEST_does-not-exist")

    def test_get_by_name_prefers_canonical_on_tie(self, neo4j_repository):
        """Spec: when two Models share a name, canonical wins."""
        name = _test_name("Tied")
        non_canonical = Model(name=name, embedding=_fake_embedding(0.5))
        canonical = Model(
            name=name, is_canonical=True, embedding=_fake_embedding(0.6)
        )
        neo4j_repository.create_model(non_canonical, generate_embedding=False)
        neo4j_repository.create_model(canonical, generate_embedding=False)

        retrieved = neo4j_repository.get_model_by_name(name)
        assert retrieved.id == canonical.id

    def test_update_model(self, neo4j_repository):
        m = Model(
            name=_test_name("Update"),
            description="Initial",
            embedding=_fake_embedding(0.7),
        )
        neo4j_repository.create_model(m, generate_embedding=False)

        neo4j_repository.update_model(
            m.id,
            description="Updated description",
            aliases=["alias1", "alias2"],
            architecture="transformer",
        )

        retrieved = neo4j_repository.get_model(m.id)
        assert retrieved.description == "Updated description"
        assert retrieved.aliases == ["alias1", "alias2"]
        assert retrieved.architecture == "transformer"

    def test_update_nonexistent_raises(self, neo4j_repository):
        with pytest.raises(NotFoundError):
            neo4j_repository.update_model("TEST_missing_id", description="x")

    def test_delete_model(self, neo4j_repository):
        m = Model(name=_test_name("Delete"), embedding=_fake_embedding(0.8))
        neo4j_repository.create_model(m, generate_embedding=False)

        neo4j_repository.delete_model(m.id)
        with pytest.raises(NotFoundError):
            neo4j_repository.get_model(m.id)

    def test_delete_canonical_without_force_raises(self, neo4j_repository):
        m = Model(
            name=_test_name("Canon"),
            is_canonical=True,
            embedding=_fake_embedding(0.9),
        )
        neo4j_repository.create_model(m, generate_embedding=False)

        with pytest.raises(ValueError, match="canonical"):
            neo4j_repository.delete_model(m.id, force=False)

    def test_delete_canonical_with_force_succeeds(self, neo4j_repository):
        m = Model(
            name=_test_name("ForceDel"),
            is_canonical=True,
            embedding=_fake_embedding(0.91),
        )
        neo4j_repository.create_model(m, generate_embedding=False)

        neo4j_repository.delete_model(m.id, force=True)
        with pytest.raises(NotFoundError):
            neo4j_repository.get_model(m.id)

    def test_delete_with_inbound_uses_model_edge_detaches(self, neo4j_repository):
        """DETACH DELETE semantics — Q4 review decision: drop the node and
        all incident USES_MODEL edges in one shot, no audit log."""
        doi = f"10.1/TEST-{uuid.uuid4().hex[:6]}"
        _make_paper(neo4j_repository, doi)
        m = Model(name=_test_name("Detach"), embedding=_fake_embedding(0.92))
        neo4j_repository.create_model(m, generate_embedding=False)
        neo4j_repository.link_paper_to_model(paper_doi=doi, model_id=m.id)

        # Delete with force=False (not canonical — should still succeed).
        neo4j_repository.delete_model(m.id)
        with pytest.raises(NotFoundError):
            neo4j_repository.get_model(m.id)
        # Paper still exists, only the edge is gone.
        assert neo4j_repository.get_paper(doi).doi == doi


# =============================================================================
# Unit 3 — Vector search
# =============================================================================


class TestSearchModelsByEmbedding:
    def test_finds_exact_match(self, neo4j_repository):
        m = Model(name=_test_name("Search"), embedding=_fake_embedding(0.5))
        neo4j_repository.create_model(m, generate_embedding=False)

        results = neo4j_repository.search_models_by_embedding(
            embedding=_fake_embedding(0.5), top_k=5
        )
        ids = [model.id for model, _ in results]
        assert m.id in ids

    def test_orthogonal_below_threshold(self, neo4j_repository):
        """Neo4j scales cosine to [0,1] via (1+cos)/2; orthogonal vectors
        score 0.5. A min_score of 0.6 excludes them."""
        m = Model(
            name=_test_name("Ortho"),
            embedding=_orthogonal_embedding(0),
        )
        neo4j_repository.create_model(m, generate_embedding=False)

        results = neo4j_repository.search_models_by_embedding(
            embedding=_orthogonal_embedding(1),  # different slot
            top_k=5,
            min_score=0.6,
        )
        assert all(model.id != m.id for model, _ in results)


# =============================================================================
# Unit 4 — link_paper_to_model + USES_MODEL via generalized helper
# =============================================================================


class TestLinkPaperToModel:
    def test_link_creates_edge_and_increments_count(self, neo4j_repository):
        doi = f"10.1/TEST-{uuid.uuid4().hex[:6]}"
        _make_paper(neo4j_repository, doi)
        m = Model(name=_test_name("LinkInc"), embedding=_fake_embedding(0.1))
        neo4j_repository.create_model(m, generate_embedding=False)

        created = neo4j_repository.link_paper_to_model(
            paper_doi=doi, model_id=m.id
        )
        assert created is True

        retrieved = neo4j_repository.get_model(m.id)
        assert retrieved.usage_count == 1

    def test_link_idempotent_no_double_increment(self, neo4j_repository):
        doi = f"10.1/TEST-{uuid.uuid4().hex[:6]}"
        _make_paper(neo4j_repository, doi)
        m = Model(name=_test_name("LinkIdem"), embedding=_fake_embedding(0.2))
        neo4j_repository.create_model(m, generate_embedding=False)

        first = neo4j_repository.link_paper_to_model(paper_doi=doi, model_id=m.id)
        second = neo4j_repository.link_paper_to_model(paper_doi=doi, model_id=m.id)

        assert first is True
        assert second is False  # edge already existed
        retrieved = neo4j_repository.get_model(m.id)
        assert retrieved.usage_count == 1

    def test_unlink_removes_edge_and_decrements_count(self, neo4j_repository):
        doi = f"10.1/TEST-{uuid.uuid4().hex[:6]}"
        _make_paper(neo4j_repository, doi)
        m = Model(name=_test_name("Unlink"), embedding=_fake_embedding(0.3))
        neo4j_repository.create_model(m, generate_embedding=False)
        neo4j_repository.link_paper_to_model(paper_doi=doi, model_id=m.id)

        removed = neo4j_repository.unlink_paper_from_model(
            paper_doi=doi, model_id=m.id
        )
        assert removed is True

        retrieved = neo4j_repository.get_model(m.id)
        assert retrieved.usage_count == 0

    def test_get_papers_for_model(self, neo4j_repository):
        doi1 = f"10.1/TEST-{uuid.uuid4().hex[:6]}"
        doi2 = f"10.1/TEST-{uuid.uuid4().hex[:6]}"
        _make_paper(neo4j_repository, doi1)
        _make_paper(neo4j_repository, doi2)
        m = Model(name=_test_name("Papers"), embedding=_fake_embedding(0.4))
        neo4j_repository.create_model(m, generate_embedding=False)
        neo4j_repository.link_paper_to_model(paper_doi=doi1, model_id=m.id)
        neo4j_repository.link_paper_to_model(paper_doi=doi2, model_id=m.id)

        papers = neo4j_repository.get_papers_for_model(m.id, limit=10)
        dois = {p["doi"] for p in papers}
        assert {doi1, doi2}.issubset(dois)

    def test_link_missing_paper_raises(self, neo4j_repository):
        m = Model(name=_test_name("MissingP"), embedding=_fake_embedding(0.5))
        neo4j_repository.create_model(m, generate_embedding=False)

        with pytest.raises(NotFoundError):
            neo4j_repository.link_paper_to_model(
                paper_doi="10.1/TEST-doesnotexist", model_id=m.id
            )

    def test_link_missing_model_raises(self, neo4j_repository):
        doi = f"10.1/TEST-{uuid.uuid4().hex[:6]}"
        _make_paper(neo4j_repository, doi)

        with pytest.raises(NotFoundError):
            neo4j_repository.link_paper_to_model(
                paper_doi=doi, model_id="TEST_missing_model_id"
            )


# =============================================================================
# Unit 5 — create_or_merge_model with canonical protection
# =============================================================================


class TestCreateOrMergeModel:
    def test_new_node_when_no_candidate(self, neo4j_repository):
        """AC-4: empty graph + new name → new node, created=True."""
        name = _test_name("NewNode")
        model, created = neo4j_repository.create_or_merge_model(
            name=name,
            embedding=_orthogonal_embedding(50),
        )
        assert created is True
        assert model.name == name
        assert model.is_canonical is False

    def test_merge_when_above_threshold(self, neo4j_repository):
        """Two near-identical embeddings — second call merges into first."""
        emb = _fake_embedding(0.5)
        first_name = _test_name("MergeBase")
        m1, created1 = neo4j_repository.create_or_merge_model(
            name=first_name, embedding=emb,
        )
        assert created1 is True

        second_name = _test_name("MergeIncoming")
        m2, created2 = neo4j_repository.create_or_merge_model(
            name=second_name, embedding=emb,
        )
        assert created2 is False
        assert m2.id == m1.id  # merged into the first
        assert second_name in m2.aliases

    def test_canonical_protected_from_rename(self, neo4j_repository):
        """AC-3: incoming non-canonical does NOT rename existing canonical."""
        emb = _fake_embedding(0.6)
        canonical_name = _test_name("BERT")
        canon, _ = neo4j_repository.create_or_merge_model(
            name=canonical_name,
            is_canonical=True,
            embedding=emb,
        )

        # Incoming non-canonical lowercase variant.
        incoming = _test_name("bert").lower()
        merged, created = neo4j_repository.create_or_merge_model(
            name=incoming,
            is_canonical=False,
            embedding=emb,
        )
        assert created is False
        assert merged.id == canon.id
        assert merged.name == canonical_name  # NOT renamed
        assert merged.is_canonical is True
        assert incoming in merged.aliases

    def test_incoming_canonical_promotes_existing_non_canonical(
        self, neo4j_repository
    ):
        """AC-3 + Q1: incoming canonical promotes existing non-canonical.
        Existing name moves to aliases; incoming canonical name takes over.
        usage_count preserved."""
        emb = _fake_embedding(0.7)
        community_name = _test_name("llama2")
        community, _ = neo4j_repository.create_or_merge_model(
            name=community_name, embedding=emb,
        )

        # Seed canonical landing later.
        canonical_name = _test_name("Llama-2")
        promoted, created = neo4j_repository.create_or_merge_model(
            name=canonical_name,
            is_canonical=True,
            embedding=emb,
        )
        assert created is False
        assert promoted.id == community.id  # same node, promoted
        assert promoted.name == canonical_name  # renamed to canonical
        assert promoted.is_canonical is True
        assert community_name in promoted.aliases  # prior name preserved

    def test_canonical_promotion_preserves_usage_count(self, neo4j_repository):
        """Q1 review: usage_count survives the canonical-promotion rename."""
        doi = f"10.1/TEST-{uuid.uuid4().hex[:6]}"
        _make_paper(neo4j_repository, doi)

        emb = _fake_embedding(0.75)
        community_name = _test_name("gpt4")
        community, _ = neo4j_repository.create_or_merge_model(
            name=community_name, embedding=emb,
        )
        neo4j_repository.link_paper_to_model(paper_doi=doi, model_id=community.id)
        assert neo4j_repository.get_model(community.id).usage_count == 1

        # Promote.
        canonical_name = _test_name("GPT-4")
        promoted, _ = neo4j_repository.create_or_merge_model(
            name=canonical_name, is_canonical=True, embedding=emb,
        )
        assert promoted.usage_count == 1  # preserved through rename

    def test_canonical_canonical_collision_warns(
        self, neo4j_repository, caplog
    ):
        """Spec edge case: two canonical entries collide on embedding. The
        merge proceeds (idempotent), but a WARN is logged so seed-load
        review catches the probable curator mistake."""
        import logging

        emb = _fake_embedding(0.81)
        first = _test_name("Llama-2")
        neo4j_repository.create_or_merge_model(
            name=first, is_canonical=True, embedding=emb,
        )

        second = _test_name("Llama-2-7B")
        with caplog.at_level(logging.WARNING):
            neo4j_repository.create_or_merge_model(
                name=second, is_canonical=True, embedding=emb,
            )

        assert any(
            "Canonical-canonical merge" in r.message for r in caplog.records
        )

    def test_two_non_canonical_alias_merge(self, neo4j_repository):
        """Both non-canonical — standard E-2-style alias merge."""
        emb = _fake_embedding(0.8)
        a_name = _test_name("alpha")
        b_name = _test_name("beta")
        a, _ = neo4j_repository.create_or_merge_model(name=a_name, embedding=emb)
        merged, created = neo4j_repository.create_or_merge_model(
            name=b_name, embedding=emb,
        )
        assert created is False
        assert merged.id == a.id
        assert merged.name == a_name  # existing wins
        assert b_name in merged.aliases
        assert merged.is_canonical is False

    def test_embedding_failure_creates_without_embedding(
        self, neo4j_repository, monkeypatch
    ):
        """AC-13: embedding service down → fallback to create-without-embedding,
        no crash. Dedup is skipped for that call."""
        from agentic_kg.knowledge_graph import embeddings as emb_mod

        def boom(*a, **kw):
            raise RuntimeError("embedding service down")

        monkeypatch.setattr(emb_mod, "generate_model_embedding", boom)

        name = _test_name("NoEmb")
        model, created = neo4j_repository.create_or_merge_model(name=name)
        assert created is True
        retrieved = neo4j_repository.get_model(model.id)
        # The embedding property is absent or None in Neo4j.
        # We re-fetch via the model to confirm node exists.
        assert retrieved.name == name


# =============================================================================
# Unit 4 — E-2 regression after generalizing _link_entity_to_concept
# =============================================================================


class TestConceptLinkingRegression:
    """The Tech Lead Q5 decision was to generalize ``_link_entity_to_concept``
    by adding USES_MODEL to the relationships map. The existing E-2
    DISCUSSES (Paper → ResearchConcept) and INVOLVES_CONCEPT
    (ProblemConcept → ResearchConcept) call paths must still work.
    """

    def test_link_paper_to_concept_still_works(self, neo4j_repository):
        from agentic_kg.knowledge_graph.models import ResearchConcept

        doi = f"10.1/TEST-{uuid.uuid4().hex[:6]}"
        _make_paper(neo4j_repository, doi)
        concept = ResearchConcept(
            name=_test_name("Concept"), embedding=_fake_embedding(0.99),
        )
        neo4j_repository.create_research_concept(concept, generate_embedding=False)

        created = neo4j_repository.link_paper_to_concept(
            paper_doi=doi, research_concept_id=concept.id,
        )
        assert created is True
        retrieved = neo4j_repository.get_research_concept(concept.id)
        assert retrieved.paper_count == 1
