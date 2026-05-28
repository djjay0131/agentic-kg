"""E-8 Unit 12 — purge-then-rewrite (AC-13).

Tests the ``purge_paper_extraction`` function that clears a paper's
extraction footprint before re-ingestion. The annotation guardrail
refuses to proceed if any Problem node attributable to the paper has
been touched by a non-extraction edge (manual SOLVED_BY, human curation)
unless ``--force-rewrite`` is set.
"""

from unittest.mock import MagicMock

import pytest
from agentic_kg.extraction.re_ingestion import (
    PurgeBlocked,
    PurgeReport,
    purge_paper_extraction,
)


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
        assert "DISCUSSES" in all_queries
        assert "INVOLVES_CONCEPT" in all_queries
        assert "EXTRACTED_FROM" in all_queries

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
