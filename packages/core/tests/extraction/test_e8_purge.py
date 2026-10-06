"""E-8 Unit 12 — purge-then-rewrite (AC-13).

Tests the ``purge_paper_extraction`` function that clears a paper's
extraction footprint before re-ingestion. The annotation guardrail
refuses to proceed if any Problem node attributable to the paper has
been touched by a non-extraction edge (manual SOLVED_BY, human curation)
unless ``--force-rewrite`` is set.
"""

import re
from unittest.mock import MagicMock

import pytest
from agentic_kg.extraction.re_ingestion import (
    PurgeBlocked,
    PurgeReport,
    purge_paper_extraction,
)
from agentic_kg.ingestion import _can_skip_entity_extraction


@pytest.fixture
def mock_repo():
    repo = MagicMock()
    session = MagicMock()
    session.__enter__ = lambda self: session
    session.__exit__ = lambda self, *a: None
    repo.session.return_value = session
    return repo


def _stub_session_results(session, queries_to_results: dict):
    """Configure session.run to return different results based on a substring
    match of the Cypher query — keeps tests readable.
    """

    def _run(query, *args, **kwargs):
        for substring, result in queries_to_results.items():
            if substring in query:
                return result
        # Default: empty result.
        empty = MagicMock()
        empty.__iter__ = lambda self: iter([])
        empty.single.return_value = None
        return empty

    session.run.side_effect = _run


# =============================================================================
# Guardrail: refuse on non-extraction edges (without --force-rewrite)
# =============================================================================


class TestGuardrail:
    def test_refuses_when_non_extraction_edges_present(self, mock_repo):
        session = mock_repo.session.return_value
        # Pretend a SOLVED_BY edge exists on one of this paper's problems.
        blocking_result = MagicMock()
        blocking_result.__iter__ = lambda self: iter(
            [
                {
                    "problem_id": "p-1",
                    "relationship_type": "SOLVED_BY",
                    "other_node": "another-doi",
                }
            ]
        )
        _stub_session_results(
            session,
            {"non_extraction_edges": blocking_result},
        )

        with pytest.raises(PurgeBlocked) as exc:
            purge_paper_extraction(
                mock_repo, paper_doi="10.1/abc", force_rewrite=False
            )
        # The blocking edges are listed in the exception so the operator
        # can audit them.
        assert "SOLVED_BY" in str(exc.value)
        # No DELETE was issued.
        deletes = [
            c for c in session.run.call_args_list if "DELETE" in c.args[0]
        ]
        assert not deletes

    def test_force_rewrite_overrides_guardrail(self, mock_repo):
        session = mock_repo.session.return_value
        # Same blocking edge state.
        blocking_result = MagicMock()
        blocking_result.__iter__ = lambda self: iter(
            [
                {
                    "problem_id": "p-1",
                    "relationship_type": "SOLVED_BY",
                    "other_node": "another-doi",
                }
            ]
        )
        # All other queries return empty single() rows.
        empty = MagicMock()
        empty.single.return_value = {"count": 0}
        empty.__iter__ = lambda self: iter([])

        def _run(query, *args, **kwargs):
            if "non_extraction_edges" in query:
                return blocking_result
            return empty

        session.run.side_effect = _run

        report = purge_paper_extraction(
            mock_repo, paper_doi="10.1/abc", force_rewrite=True
        )
        assert isinstance(report, PurgeReport)
        # Collateral edges were reported as forced-loss.
        assert report.collateral_edge_loss
        # The DELETE statements were issued.
        deletes = [
            c for c in session.run.call_args_list if "DELETE" in c.args[0]
        ]
        assert deletes


# =============================================================================
# Happy path: no blocking edges, full purge proceeds
# =============================================================================


class TestPurgeHappyPath:
    def test_purges_problems_and_mentions(self, mock_repo):
        session = mock_repo.session.return_value
        empty = MagicMock()
        empty.single.return_value = {"deleted_problems": 3, "deleted_mentions": 5}
        empty.__iter__ = lambda self: iter([])
        session.run.return_value = empty

        report = purge_paper_extraction(
            mock_repo, paper_doi="10.1/abc", force_rewrite=False
        )
        assert isinstance(report, PurgeReport)
        assert report.paper_doi == "10.1/abc"
        # Cypher queries issued cover: problems, mentions, all E-8 edges.
        all_queries = " ".join(c.args[0] for c in session.run.call_args_list)
        assert "Problem" in all_queries
        assert "ProblemMention" in all_queries
        assert "BELONGS_TO" in all_queries
        assert "RESEARCHES" in all_queries
        assert "DISCUSSES" in all_queries
        assert "INVOLVES_CONCEPT" in all_queries
        assert "EXTRACTED_FROM" in all_queries

    def test_paper_topic_purge_targets_researches_not_belongs_to(self, mock_repo):
        """Paper → Topic is RESEARCHES; Problem-side → Topic is BELONGS_TO.

        The purge matched BELONGS_TO for the Paper edge, so it deleted nothing
        and every re-ingestion left the prior run's topic edges behind. Assert
        on the Paper-anchored query specifically — a bare "BELONGS_TO in
        queries" check passes on the buggy version, because the Problem-side
        purge legitimately uses it.
        """
        session = mock_repo.session.return_value
        empty = MagicMock()
        empty.single.return_value = {"deleted_problems": 0, "deleted_mentions": 0}
        empty.__iter__ = lambda self: iter([])
        session.run.return_value = empty

        purge_paper_extraction(mock_repo, paper_doi="10.1/abc", force_rewrite=False)

        queries = [c.args[0] for c in session.run.call_args_list]
        paper_topic = [
            q for q in queries
            if "(p:Paper {doi: $doi})-[r:" in q and ":Topic)" in q
        ]
        assert paper_topic, "no Paper→Topic purge query was issued"
        joined = " ".join(paper_topic)
        assert "RESEARCHES" in joined
        assert "BELONGS_TO" not in joined

    def test_problem_side_topic_purge_uses_belongs_to_not_has_topic(self, mock_repo):
        """Problem-side → Topic is BELONGS_TO. HAS_TOPIC is written nowhere in
        the codebase — vocabulary from a spec that never shipped — so the old
        query could never match."""
        session = mock_repo.session.return_value
        empty = MagicMock()
        empty.single.return_value = {"deleted_problems": 0, "deleted_mentions": 0}
        empty.__iter__ = lambda self: iter([])
        session.run.return_value = empty

        purge_paper_extraction(mock_repo, paper_doi="10.1/abc", force_rewrite=False)

        all_queries = " ".join(c.args[0] for c in session.run.call_args_list)
        assert "HAS_TOPIC" not in all_queries
        problem_topic = [
            q for q in (c.args[0] for c in session.run.call_args_list)
            if "ProblemConcept" in q and ":Topic)" in q
        ]
        assert problem_topic, "no Problem-side→Topic purge query was issued"
        assert "BELONGS_TO" in " ".join(problem_topic)

    def test_researches_is_declared_extraction_footprint(self):
        """RESEARCHES is written by the extraction pipeline, so it must count
        as footprint — otherwise the guardrail could treat a paper's own topic
        edges as foreign and refuse to purge."""
        from agentic_kg.extraction.re_ingestion import _EXTRACTION_EDGE_TYPES

        assert "RESEARCHES" in _EXTRACTION_EDGE_TYPES
        assert "BELONGS_TO" in _EXTRACTION_EDGE_TYPES
        # dead vocabulary — nothing writes it
        assert "HAS_TOPIC" not in _EXTRACTION_EDGE_TYPES

    def test_shared_topic_and_concept_nodes_not_deleted(self, mock_repo):
        """AC-13 critical: shared Topic and ResearchConcept nodes must NOT
        be deleted — they may be referenced by other papers."""
        session = mock_repo.session.return_value
        empty = MagicMock()
        empty.single.return_value = {"count": 0}
        empty.__iter__ = lambda self: iter([])
        session.run.return_value = empty

        purge_paper_extraction(mock_repo, paper_doi="10.1/abc", force_rewrite=False)
        all_queries = " ".join(c.args[0] for c in session.run.call_args_list)
        # No DELETE on Topic or ResearchConcept node labels.
        # (DETACH DELETE on edges to those nodes is fine; node-level DELETE
        # of those labels is forbidden.)
        assert "DELETE (t:Topic)" not in all_queries
        assert "DELETE (rc:ResearchConcept)" not in all_queries
        assert "DETACH DELETE t" not in all_queries
        # Yes, this is a string check — the integration test in Phase 6
        # confirms the actual behavior against live Neo4j.

    def test_clears_extraction_incomplete_state(self, mock_repo):
        """After a successful purge, the Paper node's extraction-status
        properties are also reset so the re-extraction starts clean."""
        session = mock_repo.session.return_value
        empty = MagicMock()
        empty.single.return_value = {"count": 0}
        empty.__iter__ = lambda self: iter([])
        session.run.return_value = empty

        purge_paper_extraction(mock_repo, paper_doi="10.1/abc", force_rewrite=False)
        all_queries = " ".join(c.args[0] for c in session.run.call_args_list)
        assert "extraction_incomplete" in all_queries
        assert "extraction_failed_extractors" in all_queries

    def test_clears_taxonomy_hash_state(self, mock_repo):
        """AC-23: the purge must clear ``Paper.taxonomy_hash``.

        Otherwise the re-ingest skip check (``_can_skip_entity_extraction``)
        still sees a matching hash after the purge and short-circuits the
        rewrite — so the RESEARCHES Paper→Topic edges the purge just deleted
        are never restored. "topics identified and then not stored."
        """
        session = mock_repo.session.return_value
        empty = MagicMock()
        empty.single.return_value = {"count": 0}
        empty.__iter__ = lambda self: iter([])
        session.run.return_value = empty

        purge_paper_extraction(mock_repo, paper_doi="10.1/abc", force_rewrite=False)
        all_queries = " ".join(c.args[0] for c in session.run.call_args_list)
        assert "taxonomy_hash" in all_queries


# =============================================================================
# AC-23: purge → skip-check composition (no Neo4j)
# =============================================================================


class _FakeResult:
    """Result stub: empty iterable, single ``.single()`` row."""

    def __init__(self, single=None):
        self._single = single

    def __iter__(self):
        return iter(())

    def single(self):
        return self._single


class _FakePurgeRepository:
    """Stateful Paper-node stub for the purge → skip composition.

    Models only the Paper extraction-status properties the composition
    reads, and applies whatever ``SET p.<prop> = <literal>`` assignments a
    query actually carries. That way the test exercises the real purge
    Cypher instead of a hand-rolled expectation of it.
    """

    def __init__(self, taxonomy_hash: str = "hash-v1"):
        self.paper = {
            "taxonomy_hash": taxonomy_hash,
            "extraction_incomplete": False,
            "extraction_failed_extractors": "",
        }

    def session(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def run(self, query, *args, **kwargs):
        if "SET p." in query:
            for prop, raw in re.findall(
                r"p\.(\w+)\s*=\s*('[^']*'|true|false)", query
            ):
                if raw in ("true", "false"):
                    self.paper[prop] = raw == "true"
                else:
                    self.paper[prop] = raw.strip("'")
        if "AS taxonomy_hash" in query:
            return _FakeResult(dict(self.paper))
        if "deleted_mentions" in query:
            return _FakeResult({"deleted_mentions": 0})
        if "deleted_problems" in query:
            return _FakeResult({"deleted_problems": 0})
        return _FakeResult()


class TestPurgeSkipComposition:
    def test_purge_clears_hash_so_next_ingest_is_not_skipped(self):
        """A purged paper must be re-extracted, not skipped.

        This is the persistence half of the Topic bug: the purge deletes
        Paper→Topic ``RESEARCHES`` edges, so a matching ``taxonomy_hash``
        surviving the purge makes the skip check swallow the rewrite and
        the topic edges stay gone.
        """
        repo = _FakePurgeRepository(taxonomy_hash="hash-v1")
        # Before the purge, the paper is complete under this taxonomy.
        assert _can_skip_entity_extraction(repo, "10.1/abc", "hash-v1") is True

        purge_paper_extraction(repo, paper_doi="10.1/abc", force_rewrite=False)

        # AC-23: purge resets the hash → the skip check now fails →
        # extraction (and the RESEARCHES rewrite) runs next batch.
        assert _can_skip_entity_extraction(repo, "10.1/abc", "hash-v1") is False


# =============================================================================
# Report shape
# =============================================================================


class TestPurgeReport:
    def test_default_report_shape(self):
        r = PurgeReport(paper_doi="10.1/abc")
        assert r.paper_doi == "10.1/abc"
        assert r.problems_deleted == 0
        assert r.mentions_deleted == 0
        assert r.edges_deleted == 0
        assert r.collateral_edge_loss == []
