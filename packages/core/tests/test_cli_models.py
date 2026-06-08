"""CLI argparse + handler-dispatch tests for the E-3 Model commands.

Drives the parser layer and verifies the three new subcommands (
``load-models``, ``create-model``, ``link-model``) reach their handlers
with the expected namespace. Repository calls are mocked so no Neo4j is
needed.
"""

from unittest.mock import MagicMock, patch

from agentic_kg.cli import build_parser, main


class TestLoadModelsArgparse:
    def test_file_default_none(self):
        parser = build_parser()
        ns = parser.parse_args(["load-models"])
        assert ns.command == "load-models"
        assert ns.file is None

    def test_file_override(self):
        parser = build_parser()
        ns = parser.parse_args(["load-models", "--file", "/tmp/custom.yml"])
        assert ns.file == "/tmp/custom.yml"


class TestCreateModelArgparse:
    def test_required_name(self):
        parser = build_parser()
        ns = parser.parse_args(["create-model", "--name", "BERT"])
        assert ns.name == "BERT"
        assert ns.is_canonical is False  # default
        assert ns.architecture is None

    def test_full_flags(self):
        parser = build_parser()
        ns = parser.parse_args([
            "create-model",
            "--name", "BERT",
            "--description", "transformer LM",
            "--aliases", "bert-base,bert-large",
            "--architecture", "transformer",
            "--model-type", "language_model",
            "--year-introduced", "2018",
            "--canonical",
            "--threshold", "0.97",
        ])
        assert ns.is_canonical is True
        assert ns.architecture == "transformer"
        assert ns.model_type == "language_model"
        assert ns.year_introduced == 2018
        assert ns.threshold == 0.97


class TestLinkModelArgparse:
    def test_required_flags(self):
        parser = build_parser()
        ns = parser.parse_args([
            "link-model",
            "--paper-doi", "10.1/abc",
            "--model-id", "m-1",
        ])
        assert ns.paper_doi == "10.1/abc"
        assert ns.model_id == "m-1"


# =============================================================================
# Dispatch — confirm main() routes the new commands to their handlers
# =============================================================================


class TestDispatch:
    def test_load_models_dispatch(self):
        with patch("agentic_kg.cli.run_load_models") as handler:
            main(["load-models"])
            handler.assert_called_once()

    def test_create_model_dispatch(self):
        with patch("agentic_kg.cli.run_create_model") as handler:
            main(["create-model", "--name", "BERT"])
            handler.assert_called_once()

    def test_link_model_dispatch(self):
        with patch("agentic_kg.cli.run_link_model") as handler:
            main([
                "link-model",
                "--paper-doi", "10.1/abc",
                "--model-id", "m-1",
            ])
            handler.assert_called_once()


# =============================================================================
# Handler smoke tests — mock the repository singleton
# =============================================================================


class TestRunCreateModel:
    @patch("agentic_kg.knowledge_graph.repository.get_repository")
    def test_calls_create_or_merge(self, get_repo, capsys):
        repo = MagicMock()
        merged_model = MagicMock(
            id="m-1", name="BERT", is_canonical=True, aliases=["bert-base"]
        )
        repo.create_or_merge_model.return_value = (merged_model, False)
        get_repo.return_value = repo

        main([
            "create-model",
            "--name", "BERT",
            "--canonical",
        ])

        repo.create_or_merge_model.assert_called_once()
        kwargs = repo.create_or_merge_model.call_args.kwargs
        assert kwargs["name"] == "BERT"
        assert kwargs["is_canonical"] is True
        out = capsys.readouterr().out
        assert "Merged" in out


class TestRunLinkModel:
    @patch("agentic_kg.knowledge_graph.repository.get_repository")
    def test_calls_link_paper_to_model(self, get_repo, capsys):
        repo = MagicMock()
        repo.link_paper_to_model.return_value = True
        get_repo.return_value = repo

        main([
            "link-model",
            "--paper-doi", "10.1/abc",
            "--model-id", "m-1",
        ])

        repo.link_paper_to_model.assert_called_once_with(
            paper_doi="10.1/abc", model_id="m-1",
        )
        out = capsys.readouterr().out
        assert "USES_MODEL" in out


class TestRunLoadModels:
    @patch("agentic_kg.knowledge_graph.seed_models.load_seed_models")
    @patch("agentic_kg.knowledge_graph.repository.get_repository")
    def test_invokes_loader_with_default_path(
        self, get_repo, load_fn, capsys
    ):
        get_repo.return_value = MagicMock()
        load_fn.return_value = {"created": 5, "merged": 0}

        main(["load-models"])

        load_fn.assert_called_once()
        out = capsys.readouterr().out
        assert "5 new" in out
