"""CLI argparse + handler tests for the E-5 citation-graph command."""

from unittest.mock import MagicMock, patch

import pytest
from agentic_kg.cli import build_parser, main


def _stub_paper(**overrides):
    p = MagicMock()
    p.doi = overrides.get("doi", "10.1/anchor")
    p.title = overrides.get("title", "Anchor paper title")
    p.is_stub = overrides.get("is_stub", False)
    p.citation_count = overrides.get("citation_count", 0)
    p.reference_count = overrides.get("reference_count", 0)
    return p


class TestArgparse:
    def test_required_paper_doi(self):
        parser = build_parser()
        ns = parser.parse_args([
            "citation-graph", "--paper-doi", "10.1/x",
        ])
        assert ns.paper_doi == "10.1/x"
        assert ns.depth == 1  # default
        assert ns.direction == "out"  # default

    def test_full_flags(self):
        parser = build_parser()
        ns = parser.parse_args([
            "citation-graph",
            "--paper-doi", "10.1/x",
            "--depth", "3",
            "--direction", "both",
            "--limit", "5",
        ])
        assert ns.depth == 3
        assert ns.direction == "both"
        assert ns.limit == 5

    def test_invalid_direction_rejected(self):
        parser = build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args([
                "citation-graph", "--paper-doi", "10.1/x",
                "--direction", "sideways",
            ])


class TestDispatch:
    def test_citation_graph_dispatch(self):
        with patch("agentic_kg.cli.run_citation_graph") as handler:
            main(["citation-graph", "--paper-doi", "10.1/x"])
            handler.assert_called_once()


class TestRunCitationGraph:
    @patch("agentic_kg.knowledge_graph.repository.get_repository")
    def test_traverses_out_edges_to_depth_one(self, get_repo, capsys):
        repo = MagicMock()
        repo.get_paper.return_value = _stub_paper(
            doi="10.1/anchor", title="Anchor", reference_count=2,
        )
        repo.get_references.return_value = [
            {"doi": "10.1/r1", "title": "Ref 1", "is_stub": False},
            {"doi": "10.1/r2", "title": "Ref 2", "is_stub": True},
        ]
        get_repo.return_value = repo

        main([
            "citation-graph", "--paper-doi", "10.1/anchor", "--depth", "1",
        ])

        out = capsys.readouterr().out
        assert "Anchor" in out
        assert "10.1/r1" in out
        assert "10.1/r2" in out
        assert "[stub]" in out  # stub tag on r2

    @patch("agentic_kg.knowledge_graph.repository.get_repository")
    def test_direction_in_calls_get_citing_papers(self, get_repo, capsys):
        repo = MagicMock()
        repo.get_paper.return_value = _stub_paper(doi="10.1/anchor")
        repo.get_citing_papers.return_value = [
            {"doi": "10.1/c1", "title": "Citer 1", "is_stub": False},
        ]
        get_repo.return_value = repo

        main([
            "citation-graph", "--paper-doi", "10.1/anchor", "--direction", "in",
        ])
        out = capsys.readouterr().out
        assert "10.1/c1" in out
        assert "<-" in out  # in-edge arrow
        repo.get_references.assert_not_called()

    @patch("agentic_kg.knowledge_graph.repository.get_repository")
    def test_direction_both_calls_both_traversals(self, get_repo, capsys):
        repo = MagicMock()
        repo.get_paper.return_value = _stub_paper(doi="10.1/anchor")
        repo.get_references.return_value = [
            {"doi": "10.1/r1", "title": "Out", "is_stub": False},
        ]
        repo.get_citing_papers.return_value = [
            {"doi": "10.1/c1", "title": "In", "is_stub": False},
        ]
        get_repo.return_value = repo

        main([
            "citation-graph", "--paper-doi", "10.1/anchor", "--direction", "both",
        ])
        out = capsys.readouterr().out
        assert "10.1/r1" in out
        assert "10.1/c1" in out

    @patch("agentic_kg.knowledge_graph.repository.get_repository")
    def test_visited_set_prevents_cycle_explosion(self, get_repo, capsys):
        """Cycle: anchor -> A -> anchor. Depth 3 must not revisit anchor."""
        repo = MagicMock()
        repo.get_paper.return_value = _stub_paper(doi="10.1/anchor")

        def refs_side_effect(doi, limit=50):
            if doi == "10.1/anchor":
                return [{"doi": "10.1/A", "title": "A", "is_stub": False}]
            if doi == "10.1/A":
                return [{"doi": "10.1/anchor", "title": "Anchor", "is_stub": False}]
            return []

        repo.get_references.side_effect = refs_side_effect
        get_repo.return_value = repo

        main([
            "citation-graph", "--paper-doi", "10.1/anchor", "--depth", "3",
        ])
        out = capsys.readouterr().out
        # 10.1/A should appear exactly once (not re-expanded via the cycle).
        assert out.count("10.1/A") == 1

    @patch("agentic_kg.knowledge_graph.repository.get_repository")
    def test_in_direction_visited_skip(self, get_repo, capsys):
        """Cover the in-direction visited-set skip (line 1111).

        Setup: depth 2, direction=in. anchor's citers include X. X's
        citers include the anchor (cycle). The visited check must prevent
        the anchor from being re-expanded.
        """
        repo = MagicMock()
        repo.get_paper.return_value = _stub_paper(doi="10.1/anchor")

        def citing_side_effect(doi, limit=50):
            if doi == "10.1/anchor":
                return [{"doi": "10.1/X", "title": "X", "is_stub": False}]
            if doi == "10.1/X":
                return [{"doi": "10.1/anchor", "title": "Anchor", "is_stub": False}]
            return []

        repo.get_citing_papers.side_effect = citing_side_effect
        get_repo.return_value = repo

        main([
            "citation-graph", "--paper-doi", "10.1/anchor",
            "--depth", "2", "--direction", "in",
        ])
        out = capsys.readouterr().out
        # X appears once (depth 1). Anchor's re-visit at depth 2 is skipped.
        assert out.count("10.1/X") == 1

    @patch("agentic_kg.knowledge_graph.repository.get_repository")
    def test_not_found_exits_nonzero(self, get_repo, capsys):
        from agentic_kg.knowledge_graph.repository import NotFoundError

        repo = MagicMock()
        repo.get_paper.side_effect = NotFoundError("missing")
        get_repo.return_value = repo

        with pytest.raises(SystemExit) as exc:
            main(["citation-graph", "--paper-doi", "10.1/missing"])
        assert exc.value.code == 1
        err = capsys.readouterr().err
        assert "missing" in err
