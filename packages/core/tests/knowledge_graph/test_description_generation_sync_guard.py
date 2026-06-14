"""Unit tests for the sync-method guard (E-6 Unit 4, AC-5).

Pure unit tests against the sync ``create_or_merge_X`` methods —
no Neo4j needed, no real LLM. The guard short-circuits with
NotImplementedError BEFORE any DB work.
"""


import pytest
from agentic_kg.knowledge_graph.repository import Neo4jRepository


def _make_repo() -> Neo4jRepository:
    """Bare repo instance — we never reach the DB because the guard fires first."""
    repo = Neo4jRepository.__new__(Neo4jRepository)
    return repo


class TestCreateOrMergeMethodSyncGuard:
    def test_raises_when_generate_description_true(self):
        repo = _make_repo()
        with pytest.raises(NotImplementedError, match="acreate_or_merge_method"):
            repo.create_or_merge_method(
                name="fine-tuning", generate_description=True,
            )

    def test_no_raise_when_default_false(self):
        """The default kwarg value (False) must not change existing behavior.
        We let the call attempt to reach the DB path — Neo4j will raise its
        own error, which is NOT NotImplementedError."""
        repo = _make_repo()
        try:
            repo.create_or_merge_method(name="fine-tuning")
        except NotImplementedError:
            pytest.fail("Default-False path should not raise NotImplementedError")
        except Exception:
            # Any other error (likely AttributeError from uninitialized _driver) is fine.
            pass

    def test_explicit_description_with_generate_true_still_raises(self):
        """Per AC-5, sync raises regardless of whether description is provided.
        The "explicit description wins" semantic lives on the async sibling."""
        repo = _make_repo()
        with pytest.raises(NotImplementedError):
            repo.create_or_merge_method(
                name="fine-tuning",
                description="A real description",
                generate_description=True,
            )


class TestCreateOrMergeModelSyncGuard:
    def test_raises_when_generate_description_true(self):
        repo = _make_repo()
        with pytest.raises(NotImplementedError, match="acreate_or_merge_model"):
            repo.create_or_merge_model(
                name="BERT", generate_description=True,
            )


class TestCreateOrMergeResearchConceptSyncGuard:
    def test_raises_when_generate_description_true(self):
        repo = _make_repo()
        with pytest.raises(
            NotImplementedError, match="acreate_or_merge_research_concept"
        ):
            repo.create_or_merge_research_concept(
                name="attention", generate_description=True,
            )


# Topic doesn't have a create_or_merge today; it has merge_topic.
# We add the guard only on the four entities that have create_or_merge.
# Spec: "Add an opt-in `generate_description: bool = False` kwarg to
# `create_or_merge_topic`, ...". Repository has `merge_topic` for E-1 instead.
# Treating that as a structural deviation flagged at implementation time.
