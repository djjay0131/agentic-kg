"""
Tests for Topic CLI subcommands (E-1, Unit 8):

- load-taxonomy
- export-taxonomy
- assign-topic
"""

from unittest.mock import MagicMock, patch

import pytest

from agentic_kg.cli import build_parser, main
from agentic_kg.knowledge_graph.repository import NotFoundError


# =============================================================================
# Parser-level tests
# =============================================================================


class TestLoadTaxonomyParser:
    def test_subcommand_registered(self):
        parser = build_parser()
        args = parser.parse_args(["load-taxonomy"])
        assert args.command == "load-taxonomy"
        assert args.file is None
        assert args.skip_embeddings is False

    def test_accepts_file(self):
        parser = build_parser()
        args = parser.parse_args(["load-taxonomy", "--file", "/tmp/x.yml"])
        assert args.file == "/tmp/x.yml"

    def test_skip_embeddings_flag(self):
        parser = build_parser()
        args = parser.parse_args(["load-taxonomy", "--skip-embeddings"])
        assert args.skip_embeddings is True


class TestExportTaxonomyParser:
    def test_requires_file(self):
        parser = build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["export-taxonomy"])

    def test_accepts_file(self):
        parser = build_parser()
        args = parser.parse_args(["export-taxonomy", "--file", "/tmp/out.yml"])
        assert args.command == "export-taxonomy"
        assert args.file == "/tmp/out.yml"


class TestAssignTopicParser:
    def test_requires_entity_and_topic(self):
        parser = build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["assign-topic"])
        with pytest.raises(SystemExit):
            parser.parse_args(["assign-topic", "--entity-id", "p1"])

    def test_default_label_is_problem(self):
        parser = build_parser()
        args = parser.parse_args(
            ["assign-topic", "--entity-id", "p1", "--topic-id", "t1"]
        )
        assert args.entity_label == "Problem"

    def test_custom_label(self):
        parser = build_parser()
        args = parser.parse_args(
            [
                "assign-topic",
                "--entity-id",
                "10.1/x",
                "--topic-id",
                "t1",
                "--entity-label",
                "Paper",
            ]
        )
        assert args.entity_label == "Paper"

    def test_rejects_unknown_label(self):
        parser = build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(
                [
                    "assign-topic",
                    "--entity-id",
                    "p1",
                    "--topic-id",
                    "t1",
                    "--entity-label",
                    "Author",
                ]
            )


# =============================================================================
# Command dispatch tests
# =============================================================================


class TestLoadTaxonomyDispatch:
    def test_calls_load_taxonomy_with_default_path(self, capsys):
        mock_repo = MagicMock()
        with patch(
            "agentic_kg.knowledge_graph.repository.get_repository",
            return_value=mock_repo,
        ), patch(
            "agentic_kg.knowledge_graph.taxonomy.load_taxonomy",
            return_value={"created": 3, "matched": 0},
        ) as mock_load:
            main(["load-taxonomy"])

        mock_load.assert_called_once()
        call = mock_load.call_args
        assert call.kwargs["repo"] is mock_repo
        assert call.kwargs["generate_embeddings"] is True

        captured = capsys.readouterr().out
        assert "3 created" in captured
        assert "0 matched" in captured

    def test_skip_embeddings_flag_propagates(self):
        mock_repo = MagicMock()
        with patch(
            "agentic_kg.knowledge_graph.repository.get_repository",
            return_value=mock_repo,
        ), patch(
            "agentic_kg.knowledge_graph.taxonomy.load_taxonomy",
            return_value={"created": 0, "matched": 1},
        ) as mock_load:
            main(["load-taxonomy", "--skip-embeddings", "--file", "/tmp/x.yml"])

        assert mock_load.call_args.kwargs["generate_embeddings"] is False
        assert mock_load.call_args.kwargs["source"] == "/tmp/x.yml"


class TestExportTaxonomyDispatch:
    def test_writes_yaml_and_reports_count(self, tmp_path, capsys):
        mock_repo = MagicMock()
        taxonomy = [
            {
                "name": "Computer Science",
                "level": "domain",
                "children": [
                    {"name": "NLP", "level": "area", "children": []},
                ],
            }
        ]

        out = tmp_path / "out.yml"
        with patch(
            "agentic_kg.knowledge_graph.repository.get_repository",
            return_value=mock_repo,
        ), patch(
            "agentic_kg.knowledge_graph.taxonomy.export_taxonomy",
            return_value=taxonomy,
        ), patch(
            "agentic_kg.knowledge_graph.taxonomy.dump_taxonomy_to_yaml"
        ) as mock_dump:
            main(["export-taxonomy", "--file", str(out)])

        mock_dump.assert_called_once_with(taxonomy, str(out))
        captured = capsys.readouterr().out
        assert "2 topic" in captured  # 1 domain + 1 area


class TestAssignTopicDispatch:
    def test_calls_repo_and_reports_created(self, capsys):
        mock_repo = MagicMock()
        mock_repo.assign_entity_to_topic.return_value = True

        with patch(
            "agentic_kg.knowledge_graph.repository.get_repository",
            return_value=mock_repo,
        ):
            main(
                [
                    "assign-topic",
                    "--entity-id",
                    "prob-1",
                    "--topic-id",
                    "topic-1",
                ]
            )

        mock_repo.assign_entity_to_topic.assert_called_once_with(
            entity_id="prob-1",
            topic_id="topic-1",
            entity_label="Problem",
        )
        captured = capsys.readouterr().out
        assert "Created" in captured

    def test_already_present_prints_correctly(self, capsys):
        mock_repo = MagicMock()
        mock_repo.assign_entity_to_topic.return_value = False

        with patch(
            "agentic_kg.knowledge_graph.repository.get_repository",
            return_value=mock_repo,
        ):
            main(
                [
                    "assign-topic",
                    "--entity-id",
                    "prob-1",
                    "--topic-id",
                    "topic-1",
                ]
            )

        captured = capsys.readouterr().out
        assert "Already present" in captured

    def test_paper_uses_doi(self):
        mock_repo = MagicMock()
        mock_repo.assign_entity_to_topic.return_value = True

        with patch(
            "agentic_kg.knowledge_graph.repository.get_repository",
            return_value=mock_repo,
        ):
            main(
                [
                    "assign-topic",
                    "--entity-id",
                    "10.1234/x",
                    "--topic-id",
                    "topic-1",
                    "--entity-label",
                    "Paper",
                ]
            )

        mock_repo.assign_entity_to_topic.assert_called_once_with(
            entity_id="10.1234/x",
            topic_id="topic-1",
            entity_label="Paper",
        )

    def test_not_found_exits_with_code_1(self, capsys):
        mock_repo = MagicMock()
        mock_repo.assign_entity_to_topic.side_effect = NotFoundError("nope")

        with patch(
            "agentic_kg.knowledge_graph.repository.get_repository",
            return_value=mock_repo,
        ), pytest.raises(SystemExit) as exc_info:
            main(
                [
                    "assign-topic",
                    "--entity-id",
                    "missing",
                    "--topic-id",
                    "topic-1",
                ]
            )

        assert exc_info.value.code == 1
        assert "nope" in capsys.readouterr().err

    def test_value_error_exits_with_code_2(self, capsys):
        mock_repo = MagicMock()
        mock_repo.assign_entity_to_topic.side_effect = ValueError("bad label")

        with patch(
            "agentic_kg.knowledge_graph.repository.get_repository",
            return_value=mock_repo,
        ), pytest.raises(SystemExit) as exc_info:
            main(
                [
                    "assign-topic",
                    "--entity-id",
                    "p1",
                    "--topic-id",
                    "topic-1",
                ]
            )

        assert exc_info.value.code == 2
