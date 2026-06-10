"""E-4 AC-9 + AC-11 — integration tests against testcontainers Neo4j.

Two test classes:

- ``TestDoneDemo`` — AC-9 verify gate. Creates 5 Methods + 5 synthetic
  Papers, links them, and confirms ``get_papers_for_method`` returns
  them and the denormalized usage_count is correct.
- ``TestDedupSmokeTest`` — AC-11 threshold-regression sentinel. A single
  test: create "fine-tuning" then "Fine Tuning" at default threshold and
  assert they merge. Catches threshold-inversion bugs and absurd-value
  bumps without paying for a full eval set.

Uses deterministic-by-name embeddings so dedup is fully exercised without
OpenAI. After each test class, every Method node is purged so cross-test
contamination doesn't bleed (lesson from E-3 done-demo).
"""

from __future__ import annotations

import uuid

import pytest
from agentic_kg.knowledge_graph.models import Paper

pytestmark = pytest.mark.integration


@pytest.fixture
def _patch_embeddings(monkeypatch):
    """Deterministic embeddings: same name → same slot, different names
    → orthogonal vectors below the 0.90 threshold."""
    from agentic_kg.knowledge_graph import embeddings

    seen: dict[str, int] = {}

    def _det_emb(name: str, description=None) -> list[float]:
        slot = seen.setdefault(name, len(seen))
        v = [0.0] * 1536
        v[slot % 1536] = 1.0
        return v

    monkeypatch.setattr(embeddings, "generate_method_embedding", _det_emb)


@pytest.fixture
def loaded_repo(neo4j_repository, _patch_embeddings):
    """Repository with deterministic embeddings; auto-purges Method nodes
    on teardown so the testcontainers instance stays clean for siblings.
    """
    yield neo4j_repository

    with neo4j_repository.session() as session:
        session.run("MATCH (m:Method) DETACH DELETE m")


def _test_doi() -> str:
    return f"10.1/TEST-{uuid.uuid4().hex[:8]}"


def _make_paper(repo, doi: str, title_suffix: str) -> Paper:
    paper = Paper(
        doi=doi,
        title=f"TEST {title_suffix}",
        authors=[],
        year=2024,
    )
    repo.create_paper(paper)
    return paper


# =============================================================================
# AC-9 — Testcontainers "done demo" (the verify gate)
# =============================================================================


class TestDoneDemo:
    """AC-9: 5 synthetic Papers + 5 Method create-or-merge calls + linking +
    inverse traversal. End-to-end proof v1 actually does something useful.
    """

    def test_create_link_query_round_trip(self, loaded_repo):
        targets = [
            ("fine-tuning", "PEFT survey"),
            ("contrastive learning", "self-supervised vision"),
            ("knowledge distillation", "model compression"),
            ("data augmentation", "augmentation strategies"),
            ("few-shot learning", "in-context examples"),
        ]

        for method_name, title_suffix in targets:
            doi = _test_doi()
            _make_paper(loaded_repo, doi, title_suffix)

            # First call creates.
            method, created = loaded_repo.create_or_merge_method(
                name=method_name,
            )
            assert created is True

            # Second call on the same name merges (idempotency).
            method2, created2 = loaded_repo.create_or_merge_method(
                name=method_name,
            )
            assert created2 is False
            assert method2.id == method.id

            # Link → inverse traversal returns the paper.
            link_created = loaded_repo.link_paper_to_method(
                paper_doi=doi, method_id=method.id,
            )
            assert link_created is True

            papers = loaded_repo.get_papers_for_method(method.id, limit=20)
            assert any(p["doi"] == doi for p in papers), (
                f"linked Paper {doi} not returned for Method {method_name!r}"
            )

    def test_usage_count_per_method_matches_link_count(self, loaded_repo):
        """Each Method's denormalized usage_count tracks its inbound
        APPLIES_METHOD edges exactly."""
        doi1 = _test_doi()
        doi2 = _test_doi()
        _make_paper(loaded_repo, doi1, "paper one")
        _make_paper(loaded_repo, doi2, "paper two")

        method, _ = loaded_repo.create_or_merge_method(name="active learning")
        assert method.usage_count == 0

        loaded_repo.link_paper_to_method(paper_doi=doi1, method_id=method.id)
        loaded_repo.link_paper_to_method(paper_doi=doi2, method_id=method.id)

        refreshed = loaded_repo.get_method(method.id)
        assert refreshed.usage_count == 2

    def test_threshold_escape_valve_creates_distinct_nodes(self, loaded_repo):
        """QA Q2 review: threshold=1.01 forces a distinct node even when
        the embedding would otherwise merge. The end-to-end CLI / API
        path is covered by unit tests; this verifies the repository
        behavior against live Neo4j as part of the done demo."""
        # First call — creates with default threshold.
        method_a, created_a = loaded_repo.create_or_merge_method(
            name="curriculum learning",
        )
        assert created_a is True

        # Same name with threshold=1.01 → bypass dedup, distinct node.
        method_b, created_b = loaded_repo.create_or_merge_method(
            name="curriculum learning",
            threshold=1.01,
        )
        assert created_b is True
        assert method_b.id != method_a.id


# =============================================================================
# AC-11 — Dedup smoke test (threshold-regression sentinel)
# =============================================================================


class TestDedupSmokeTest:
    """Single sentinel: with the default threshold (0.90) and a real-ish
    embedding similarity setup, a case-variant should merge.

    This is intentionally minimal — it's not an eval set. The job is to
    catch the threshold-inversion and absurd-value classes of bug:

    - If someone bumps DEFAULT_METHOD_DEDUP_THRESHOLD to 9.0, this fails.
    - If someone inverts ``score >= threshold`` to ``<``, this fails.
    - If someone breaks vector indexing on the Method label, this fails.

    Precision/recall validation across many pairs is deferred to E-8 V2.
    """

    @pytest.fixture(autouse=True)
    def _identical_emb_for_matching_pairs(self, monkeypatch):
        """For this smoke test only: make exact name matches and case
        variants embed to identical vectors so the dedup decision depends
        entirely on the threshold comparator. Different distinct names
        get orthogonal vectors."""
        from agentic_kg.knowledge_graph import embeddings

        canonical_form: dict[str, str] = {}

        def _det_emb(name: str, description=None) -> list[float]:
            key = name.strip().lower()
            slot_for_key = canonical_form.setdefault(
                key, str(len(canonical_form))
            )
            slot = int(slot_for_key)
            v = [0.0] * 1536
            v[slot % 1536] = 1.0
            return v

        monkeypatch.setattr(embeddings, "generate_method_embedding", _det_emb)

    def test_case_variant_merges_at_default_threshold(self, neo4j_repository):
        # Cleanup any leftover Methods so this test runs deterministically.
        with neo4j_repository.session() as session:
            session.run("MATCH (m:Method) DETACH DELETE m")

        # First call — creates.
        a, created_a = neo4j_repository.create_or_merge_method(
            name="fine-tuning",
        )
        assert created_a is True

        # Case variant — must merge into the same node at default
        # threshold. If this fails, dedup is broken at the threshold,
        # comparator, or vector-index level.
        b, created_b = neo4j_repository.create_or_merge_method(
            name="Fine-Tuning",
        )
        assert created_b is False, (
            "AC-11 sentinel failed: case-variant did not merge at default "
            "threshold. Check DEFAULT_METHOD_DEDUP_THRESHOLD, the dedup "
            "comparator, and the method_embedding_idx."
        )
        assert b.id == a.id

        # Teardown.
        with neo4j_repository.session() as session:
            session.run("MATCH (m:Method) DETACH DELETE m")
