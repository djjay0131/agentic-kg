"""Integration tests for Method repository operations (E-4, Units 4-6).

Requires a running Neo4j instance (see conftest's ``neo4j_repository``
fixture). Uses ``TEST_`` prefixed names so the shared-instance cleanup
sweep removes them after each test. Embeddings are supplied manually or
disabled so tests do not depend on the OpenAI API.

Layout:
- ``TestMethodCRUD`` — Unit 4 (create / get / get_by_name / update / delete / DETACH DELETE)
- ``TestSearchMethodsByEmbedding`` — Unit 4 (vector search)
- ``TestLinkPaperToMethod`` — Unit 5 (APPLIES_METHOD via generalized helper)
- ``TestCreateOrMergeMethod`` — Unit 6 (E-2-shape alias merge, no canonical)
- ``TestE3ModelLinkingRegression`` — Unit 5 (E-3 Model link still works)
- ``TestE2ConceptLinkingRegression`` — Unit 5 (E-2 Concept link still works)
"""

import uuid

import pytest
from agentic_kg.knowledge_graph.models import Method, Paper
from agentic_kg.knowledge_graph.repository import (
    DuplicateError,
    NotFoundError,
)

pytestmark = pytest.mark.integration


def _test_name(label: str) -> str:
    return f"TEST_{label}_{uuid.uuid4().hex[:8]}"


def _fake_embedding(seed: float = 0.1) -> list[float]:
    return [seed] * 1536


def _orthogonal_embedding(slot: int) -> list[float]:
    v = [0.0] * 1536
    v[slot % 1536] = 1.0
    return v


def _make_paper(neo4j_repository, doi: str) -> Paper:
    paper = Paper(
        doi=doi, title=f"TEST title for {doi}", authors=[], year=2024,
    )
    neo4j_repository.create_paper(paper)
    return paper


# =============================================================================
# Unit 4 — Basic CRUD
# =============================================================================


class TestMethodCRUD:
    def test_create_and_get(self, neo4j_repository):
        m = Method(
            name=_test_name("CreateGet"),
            description="a test method",
            embedding=_fake_embedding(0.2),
        )
        neo4j_repository.create_method(m, generate_embedding=False)

        retrieved = neo4j_repository.get_method(m.id)
        assert retrieved.id == m.id
        assert retrieved.name == m.name
        assert retrieved.description == "a test method"
        assert retrieved.aliases == []
        assert retrieved.usage_count == 0

    def test_get_nonexistent_raises(self, neo4j_repository):
        with pytest.raises(NotFoundError):
            neo4j_repository.get_method("TEST_nonexistent_id")

    def test_create_duplicate_id_raises(self, neo4j_repository):
        m = Method(name=_test_name("Dup"), embedding=_fake_embedding(0.3))
        neo4j_repository.create_method(m, generate_embedding=False)
        with pytest.raises(DuplicateError):
            neo4j_repository.create_method(m, generate_embedding=False)

    def test_get_by_name(self, neo4j_repository):
        name = _test_name("ByName")
        m = Method(name=name, embedding=_fake_embedding(0.4))
        neo4j_repository.create_method(m, generate_embedding=False)

        retrieved = neo4j_repository.get_method_by_name(name)
        assert retrieved.id == m.id

    def test_get_by_name_missing_raises(self, neo4j_repository):
        with pytest.raises(NotFoundError):
            neo4j_repository.get_method_by_name("TEST_does-not-exist")

    def test_get_by_name_deterministic_tie_break(self, neo4j_repository):
        """Spec: when two Methods share a name, alphabetically-first id wins.
        No is_canonical to tiebreak on (E-3 had that branch; E-4 doesn't)."""
        name = _test_name("Tied")
        first = Method(name=name, id="aaa-tie-" + uuid.uuid4().hex[:8],
                       embedding=_fake_embedding(0.5))
        second = Method(name=name, id="zzz-tie-" + uuid.uuid4().hex[:8],
                        embedding=_fake_embedding(0.6))
        neo4j_repository.create_method(first, generate_embedding=False)
        neo4j_repository.create_method(second, generate_embedding=False)

        retrieved = neo4j_repository.get_method_by_name(name)
        # Alphabetically earlier id wins.
        assert retrieved.id == first.id

    def test_update_method(self, neo4j_repository):
        m = Method(
            name=_test_name("Update"),
            description="Initial",
            embedding=_fake_embedding(0.7),
        )
        neo4j_repository.create_method(m, generate_embedding=False)

        neo4j_repository.update_method(
            m.id,
            description="Updated description",
            aliases=["alias1", "alias2"],
            method_type="training",
        )

        retrieved = neo4j_repository.get_method(m.id)
        assert retrieved.description == "Updated description"
        assert retrieved.aliases == ["alias1", "alias2"]
        assert retrieved.method_type == "training"

    def test_update_nonexistent_raises(self, neo4j_repository):
        with pytest.raises(NotFoundError):
            neo4j_repository.update_method("TEST_missing_id", description="x")

    def test_delete_method(self, neo4j_repository):
        m = Method(name=_test_name("Delete"), embedding=_fake_embedding(0.8))
        neo4j_repository.create_method(m, generate_embedding=False)

        neo4j_repository.delete_method(m.id)
        with pytest.raises(NotFoundError):
            neo4j_repository.get_method(m.id)

    def test_delete_nonexistent_raises(self, neo4j_repository):
        with pytest.raises(NotFoundError):
            neo4j_repository.delete_method("TEST_no_such_method")

    def test_delete_with_inbound_applies_method_edge_detaches(
        self, neo4j_repository
    ):
        """DETACH DELETE semantics — drop node + all APPLIES_METHOD edges
        in one shot. Same shape as E-3 Model delete. **No force flag** since
        Method has no is_canonical."""
        doi = f"10.1/TEST-{uuid.uuid4().hex[:6]}"
        _make_paper(neo4j_repository, doi)
        m = Method(name=_test_name("Detach"), embedding=_fake_embedding(0.92))
        neo4j_repository.create_method(m, generate_embedding=False)
        neo4j_repository.link_paper_to_method(paper_doi=doi, method_id=m.id)

        neo4j_repository.delete_method(m.id)
        with pytest.raises(NotFoundError):
            neo4j_repository.get_method(m.id)
        # Paper survives.
        assert neo4j_repository.get_paper(doi).doi == doi


# =============================================================================
# Unit 4 — Vector search
# =============================================================================


class TestSearchMethodsByEmbedding:
    def test_finds_exact_match(self, neo4j_repository):
        m = Method(name=_test_name("Search"), embedding=_fake_embedding(0.5))
        neo4j_repository.create_method(m, generate_embedding=False)

        results = neo4j_repository.search_methods_by_embedding(
            embedding=_fake_embedding(0.5), top_k=5
        )
        ids = [method.id for method, _ in results]
        assert m.id in ids

    def test_orthogonal_below_threshold(self, neo4j_repository):
        """Neo4j scales cosine to [0,1] via (1+cos)/2; orthogonal vectors
        score 0.5. A min_score of 0.6 excludes them."""
        m = Method(
            name=_test_name("Ortho"),
            embedding=_orthogonal_embedding(0),
        )
        neo4j_repository.create_method(m, generate_embedding=False)

        results = neo4j_repository.search_methods_by_embedding(
            embedding=_orthogonal_embedding(1),
            top_k=5,
            min_score=0.6,
        )
        assert all(method.id != m.id for method, _ in results)


# =============================================================================
# Unit 5 — APPLIES_METHOD via generalized helper
# =============================================================================


class TestLinkPaperToMethod:
    def test_link_creates_edge_and_increments_count(self, neo4j_repository):
        doi = f"10.1/TEST-{uuid.uuid4().hex[:6]}"
        _make_paper(neo4j_repository, doi)
        m = Method(name=_test_name("LinkInc"), embedding=_fake_embedding(0.1))
        neo4j_repository.create_method(m, generate_embedding=False)

        created = neo4j_repository.link_paper_to_method(
            paper_doi=doi, method_id=m.id,
        )
        assert created is True

        retrieved = neo4j_repository.get_method(m.id)
        assert retrieved.usage_count == 1

    def test_link_idempotent_no_double_increment(self, neo4j_repository):
        doi = f"10.1/TEST-{uuid.uuid4().hex[:6]}"
        _make_paper(neo4j_repository, doi)
        m = Method(name=_test_name("LinkIdem"), embedding=_fake_embedding(0.2))
        neo4j_repository.create_method(m, generate_embedding=False)

        first = neo4j_repository.link_paper_to_method(paper_doi=doi, method_id=m.id)
        second = neo4j_repository.link_paper_to_method(paper_doi=doi, method_id=m.id)

        assert first is True
        assert second is False  # edge already existed
        retrieved = neo4j_repository.get_method(m.id)
        assert retrieved.usage_count == 1

    def test_unlink_removes_edge_and_decrements_count(self, neo4j_repository):
        doi = f"10.1/TEST-{uuid.uuid4().hex[:6]}"
        _make_paper(neo4j_repository, doi)
        m = Method(name=_test_name("Unlink"), embedding=_fake_embedding(0.3))
        neo4j_repository.create_method(m, generate_embedding=False)
        neo4j_repository.link_paper_to_method(paper_doi=doi, method_id=m.id)

        removed = neo4j_repository.unlink_paper_from_method(
            paper_doi=doi, method_id=m.id,
        )
        assert removed is True

        retrieved = neo4j_repository.get_method(m.id)
        assert retrieved.usage_count == 0

    def test_get_papers_for_method(self, neo4j_repository):
        doi1 = f"10.1/TEST-{uuid.uuid4().hex[:6]}"
        doi2 = f"10.1/TEST-{uuid.uuid4().hex[:6]}"
        _make_paper(neo4j_repository, doi1)
        _make_paper(neo4j_repository, doi2)
        m = Method(name=_test_name("Papers"), embedding=_fake_embedding(0.4))
        neo4j_repository.create_method(m, generate_embedding=False)
        neo4j_repository.link_paper_to_method(paper_doi=doi1, method_id=m.id)
        neo4j_repository.link_paper_to_method(paper_doi=doi2, method_id=m.id)

        papers = neo4j_repository.get_papers_for_method(m.id, limit=10)
        dois = {p["doi"] for p in papers}
        assert {doi1, doi2}.issubset(dois)

    def test_unlink_when_no_edge_returns_false(self, neo4j_repository):
        """Adversarial QA: unlink should be tolerant of "no edge to remove"
        — return False, do not decrement usage_count, do not raise."""
        doi = f"10.1/TEST-{uuid.uuid4().hex[:6]}"
        _make_paper(neo4j_repository, doi)
        m = Method(name=_test_name("UnlinkNoEdge"), embedding=_fake_embedding(0.35))
        neo4j_repository.create_method(m, generate_embedding=False)

        removed = neo4j_repository.unlink_paper_from_method(
            paper_doi=doi, method_id=m.id,
        )
        assert removed is False
        retrieved = neo4j_repository.get_method(m.id)
        assert retrieved.usage_count == 0

    def test_link_missing_paper_raises(self, neo4j_repository):
        m = Method(name=_test_name("MissingP"), embedding=_fake_embedding(0.5))
        neo4j_repository.create_method(m, generate_embedding=False)

        with pytest.raises(NotFoundError):
            neo4j_repository.link_paper_to_method(
                paper_doi="10.1/TEST-doesnotexist", method_id=m.id,
            )

    def test_link_missing_method_raises(self, neo4j_repository):
        doi = f"10.1/TEST-{uuid.uuid4().hex[:6]}"
        _make_paper(neo4j_repository, doi)

        with pytest.raises(NotFoundError):
            neo4j_repository.link_paper_to_method(
                paper_doi=doi, method_id="TEST_missing_method_id",
            )


# =============================================================================
# Unit 6 — create_or_merge_method (E-2 shape, no canonical protection)
# =============================================================================


class TestCreateOrMergeMethod:
    def test_new_node_when_no_candidate(self, neo4j_repository):
        """AC-4: empty graph + new name → new node, created=True."""
        name = _test_name("NewNode")
        method, created = neo4j_repository.create_or_merge_method(
            name=name,
            embedding=_orthogonal_embedding(50),
        )
        assert created is True
        assert method.name == name

    def test_merge_when_above_threshold(self, neo4j_repository):
        """E-2-shape merge: existing name wins, incoming joins aliases."""
        emb = _fake_embedding(0.5)
        first_name = _test_name("MergeBase")
        m1, created1 = neo4j_repository.create_or_merge_method(
            name=first_name, embedding=emb,
        )
        assert created1 is True

        second_name = _test_name("MergeIncoming")
        m2, created2 = neo4j_repository.create_or_merge_method(
            name=second_name, embedding=emb,
        )
        assert created2 is False
        assert m2.id == m1.id  # merged
        assert m2.name == first_name  # existing wins
        assert second_name in m2.aliases

    def test_merge_fills_description_when_existing_is_none(self, neo4j_repository):
        emb = _fake_embedding(0.55)
        first_name = _test_name("DescBase")
        neo4j_repository.create_or_merge_method(
            name=first_name, description=None, embedding=emb,
        )

        second_name = _test_name("DescIncoming")
        merged, _ = neo4j_repository.create_or_merge_method(
            name=second_name,
            description="incoming description",
            embedding=emb,
        )
        assert merged.description == "incoming description"

    def test_merge_preserves_existing_description(self, neo4j_repository):
        emb = _fake_embedding(0.56)
        first_name = _test_name("DescPreserve")
        neo4j_repository.create_or_merge_method(
            name=first_name, description="existing wins", embedding=emb,
        )

        second_name = _test_name("DescOverwriteAttempt")
        merged, _ = neo4j_repository.create_or_merge_method(
            name=second_name,
            description="incoming losers",
            embedding=emb,
        )
        assert merged.description == "existing wins"

    def test_merge_fills_method_type_when_existing_is_none(self, neo4j_repository):
        emb = _fake_embedding(0.57)
        first_name = _test_name("MtypeBase")
        neo4j_repository.create_or_merge_method(
            name=first_name, embedding=emb,
        )

        second_name = _test_name("MtypeIncoming")
        merged, _ = neo4j_repository.create_or_merge_method(
            name=second_name, method_type="training", embedding=emb,
        )
        assert merged.method_type == "training"

    def test_threshold_1_01_bypasses_dedup(self, neo4j_repository):
        """QA Q2 review: operator escape valve. threshold=1.01 disables
        dedup because no cosine score can clear 1.0."""
        emb = _fake_embedding(0.6)
        first_name = _test_name("Escape1")
        m1, _ = neo4j_repository.create_or_merge_method(
            name=first_name, embedding=emb,
        )

        second_name = _test_name("Escape2")
        m2, created2 = neo4j_repository.create_or_merge_method(
            name=second_name, embedding=emb, threshold=1.01,
        )
        assert created2 is True
        assert m2.id != m1.id  # distinct nodes despite identical embeddings

    def test_alias_cap_overflow_via_merge_raises(self, neo4j_repository):
        """Spec Edge Case + Tech Lead Q3: when a heavily-used Method has
        its alias list near the cap, a merge that would push past the
        cap surfaces as a Pydantic ValidationError (loud, not silent).
        Operator workaround documented in the spec: ``update_method``
        with a trimmed alias list. Pin the documented failure mode."""
        from pydantic import ValidationError

        emb = _fake_embedding(0.41)
        existing = Method(
            name=_test_name("AliasCap"),
            aliases=[f"alias_{i}" for i in range(20)],  # at the cap
            embedding=emb,
        )
        neo4j_repository.create_method(existing, generate_embedding=False)

        # Incoming call has a distinct name that would merge — pushing
        # the alias set past 20 entries.
        with pytest.raises(ValidationError):
            neo4j_repository.create_or_merge_method(
                name=_test_name("AliasCapOverflow"),
                embedding=emb,
            )

    def test_embedding_failure_creates_without_embedding(
        self, neo4j_repository, monkeypatch
    ):
        """AC-12: embedding service down → fallback to create-without-
        embedding, no crash. Dedup is skipped for that call."""
        from agentic_kg.knowledge_graph import embeddings as emb_mod

        def boom(*a, **kw):
            raise RuntimeError("embedding service down")

        monkeypatch.setattr(emb_mod, "generate_method_embedding", boom)

        name = _test_name("NoEmb")
        method, created = neo4j_repository.create_or_merge_method(name=name)
        assert created is True
        retrieved = neo4j_repository.get_method(method.id)
        assert retrieved.name == name


# =============================================================================
# Unit 5 — E-3 Model + E-2 Concept regression after one-line APPLIES_METHOD add
# =============================================================================


class TestE3ModelLinkingRegression:
    """The Unit 5 change adds a single APPLIES_METHOD entry to
    _NODE_LINK_RELATIONSHIPS. The existing USES_MODEL call path must
    still work."""

    def test_link_paper_to_model_still_works(self, neo4j_repository):
        from agentic_kg.knowledge_graph.models import Model

        doi = f"10.1/TEST-{uuid.uuid4().hex[:6]}"
        _make_paper(neo4j_repository, doi)
        model = Model(
            name=_test_name("Mdl"),
            embedding=_fake_embedding(0.71),
        )
        neo4j_repository.create_model(model, generate_embedding=False)

        created = neo4j_repository.link_paper_to_model(
            paper_doi=doi, model_id=model.id,
        )
        assert created is True
        retrieved = neo4j_repository.get_model(model.id)
        assert retrieved.usage_count == 1


class TestE2ConceptLinkingRegression:
    """E-2 ResearchConcept links must still work."""

    def test_link_paper_to_concept_still_works(self, neo4j_repository):
        from agentic_kg.knowledge_graph.models import ResearchConcept

        doi = f"10.1/TEST-{uuid.uuid4().hex[:6]}"
        _make_paper(neo4j_repository, doi)
        concept = ResearchConcept(
            name=_test_name("Concept"),
            embedding=_fake_embedding(0.99),
        )
        neo4j_repository.create_research_concept(
            concept, generate_embedding=False
        )

        created = neo4j_repository.link_paper_to_concept(
            paper_doi=doi, research_concept_id=concept.id,
        )
        assert created is True
