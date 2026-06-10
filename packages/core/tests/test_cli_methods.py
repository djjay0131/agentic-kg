"""CLI argparse + handler-dispatch tests for the E-4 Method commands.

Drives the parser layer and verifies the two new subcommands
(``create-method``, ``link-method``) reach their handlers with the
expected namespace. Repository calls are mocked so no Neo4j is needed.
"""

from unittest.mock import MagicMock, patch

import pytest
from agentic_kg.cli import build_parser, main


class TestCreateMethodArgparse:
    def test_required_name(self):
        parser = build_parser()
        ns = parser.parse_args(["create-method", "--name", "fine-tuning"])
        assert ns.name == "fine-tuning"
        assert ns.method_type is None
        assert ns.threshold is None

    def test_full_flags(self):
        parser = build_parser()
        ns = parser.parse_args([
            "create-method",
            "--name", "contrastive learning",
            "--description", "self-supervised pretraining",
            "--aliases", "contrastive loss,InfoNCE",
            "--method-type", "training",
            "--threshold", "1.01",
        ])
        assert ns.name == "contrastive learning"
        assert ns.method_type == "training"
        assert ns.threshold == 1.01


class TestLinkMethodArgparse:
    def test_required_flags(self):
        parser = build_parser()
        ns = parser.parse_args([
            "link-method",
            "--paper-doi", "10.1/abc",
            "--method-id", "m-1",
        ])
        assert ns.paper_doi == "10.1/abc"
        assert ns.method_id == "m-1"


class TestDispatch:
    def test_create_method_dispatch(self):
        with patch("agentic_kg.cli.run_create_method") as handler:
            main(["create-method", "--name", "fine-tuning"])
            handler.assert_called_once()

    def test_link_method_dispatch(self):
        with patch("agentic_kg.cli.run_link_method") as handler:
            main([
                "link-method",
                "--paper-doi", "10.1/abc",
                "--method-id", "m-1",
            ])
            handler.assert_called_once()


class TestRunCreateMethod:
    @patch("agentic_kg.knowledge_graph.repository.get_repository")
    def test_calls_create_or_merge(self, get_repo, capsys):
        repo = MagicMock()
        merged = MagicMock(
            id="m-1", name="fine-tuning", aliases=["FT", "PEFT"],
        )
        repo.create_or_merge_method.return_value = (merged, False)
        get_repo.return_value = repo

        main(["create-method", "--name", "fine-tuning"])

        repo.create_or_merge_method.assert_called_once()
        kwargs = repo.create_or_merge_method.call_args.kwargs
        assert kwargs["name"] == "fine-tuning"
        assert kwargs["method_type"] is None
        out = capsys.readouterr().out
        assert "Merged" in out
        assert "FT" in out  # aliases printed

    @patch("agentic_kg.knowledge_graph.repository.get_repository")
    def test_threshold_forwarded(self, get_repo, capsys):
        """QA Q2 escape valve: --threshold 1.01 flows to the repo call."""
        repo = MagicMock()
        repo.create_or_merge_method.return_value = (
            MagicMock(id="m-1", name="x", aliases=[]), True,
        )
        get_repo.return_value = repo

        main([
            "create-method", "--name", "fine-tuning", "--threshold", "1.01",
        ])
        kwargs = repo.create_or_merge_method.call_args.kwargs
        assert kwargs["threshold"] == 1.01

    @patch("agentic_kg.knowledge_graph.repository.get_repository")
    def test_aliases_comma_split(self, get_repo, capsys):
        repo = MagicMock()
        repo.create_or_merge_method.return_value = (
            MagicMock(id="m-1", name="fine-tuning", aliases=["FT", "PEFT"]),
            True,
        )
        get_repo.return_value = repo

        main([
            "create-method",
            "--name", "fine-tuning",
            "--aliases", "FT, PEFT , ,",
        ])

        kwargs = repo.create_or_merge_method.call_args.kwargs
        assert kwargs["aliases"] == ["FT", "PEFT"]


class TestRunLinkMethod:
    @patch("agentic_kg.knowledge_graph.repository.get_repository")
    def test_calls_link_paper_to_method(self, get_repo, capsys):
        repo = MagicMock()
        repo.link_paper_to_method.return_value = True
        get_repo.return_value = repo

        main([
            "link-method",
            "--paper-doi", "10.1/abc",
            "--method-id", "m-1",
        ])

        repo.link_paper_to_method.assert_called_once_with(
            paper_doi="10.1/abc", method_id="m-1",
        )
        out = capsys.readouterr().out
        assert "APPLIES_METHOD" in out

    @patch("agentic_kg.knowledge_graph.repository.get_repository")
    def test_not_found_exits_nonzero(self, get_repo, capsys):
        from agentic_kg.knowledge_graph.repository import NotFoundError

        repo = MagicMock()
        repo.link_paper_to_method.side_effect = NotFoundError("paper missing")
        get_repo.return_value = repo

        with pytest.raises(SystemExit) as exc:
            main([
                "link-method",
                "--paper-doi", "10.1/missing",
                "--method-id", "m-1",
            ])
        assert exc.value.code == 1
        err = capsys.readouterr().err
        assert "paper missing" in err
