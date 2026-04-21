"""Tests for ResearchConcept CLI subcommands (E-2, Unit 5)."""

from unittest.mock import MagicMock, patch

import pytest
from agentic_kg.cli import build_parser, main
from agentic_kg.knowledge_graph.models import ResearchConcept
from agentic_kg.knowledge_graph.repository import NotFoundError


def _make_concept(**overrides) -> ResearchConcept:
    defaults = {
        "id": "concept-uuid-1",
        "name": "attention mechanism",
        "aliases": [],
    }
    defaults.update(overrides)
    return ResearchConcept(**defaults)


# =============================================================================
# Parser
# =============================================================================


class TestCreateConceptParser:
    def test_subcommand_requires_name(self):
        parser = build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["create-concept"])

    def test_minimal_invocation(self):
        parser = build_parser()
        args = parser.parse_args(["create-concept", "--name", "attention"])
        assert args.command == "create-concept"
        assert args.name == "attention"
        assert args.description is None
        assert args.aliases is None
        assert args.threshold is None

    def test_full_invocation(self):
        parser = build_parser()
        args = parser.parse_args(
            [
                "create-concept",
                "--name",
                "attention",
                "--description",
                "core transformer component",
                "--aliases",
                "self-attention, SDPA , scaled dot-product attention",
                "--threshold",
                "0.85",
            ]
        )
        assert args.description == "core transformer component"
        assert args.aliases == "self-attention, SDPA , scaled dot-product attention"
        assert args.threshold == 0.85


class TestLinkConceptParser:
    def test_subcommand_requires_ids(self):
        parser = build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["link-concept"])
        with pytest.raises(SystemExit):
            parser.parse_args(["link-concept", "--concept-id", "c1"])
        with pytest.raises(SystemExit):
            parser.parse_args(["link-concept", "--entity-id", "e1"])

    def test_default_rel_type_is_involves_concept(self):
        parser = build_parser()
        args = parser.parse_args(
            [
                "link-concept",
                "--concept-id",
                "c1",
                "--entity-id",
                "pc-1",
            ]
        )
        assert args.rel_type == "INVOLVES_CONCEPT"

    def test_discusses_accepted(self):
        parser = build_parser()
        args = parser.parse_args(
            [
                "link-concept",
                "--concept-id",
                "c1",
                "--entity-id",
                "10.1/x",
                "--rel-type",
                "DISCUSSES",
            ]
        )
        assert args.rel_type == "DISCUSSES"

    def test_unknown_rel_type_rejected(self):
        parser = build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(
                [
                    "link-concept",
                    "--concept-id",
                    "c1",
                    "--entity-id",
                    "e1",
                    "--rel-type",
                    "RELATED_TO",
                ]
            )


# =============================================================================
# Dispatch
# =============================================================================


class TestCreateConceptDispatch:
    def test_created_new(self, capsys):
        mock_repo = MagicMock()
        mock_repo.create_or_merge_research_concept.return_value = (
            _make_concept(),
            True,
        )
        with patch(
            "agentic_kg.knowledge_graph.repository.get_repository",
            return_value=mock_repo,
        ):
            main(["create-concept", "--name", "attention mechanism"])

        mock_repo.create_or_merge_research_concept.assert_called_once()
        call = mock_repo.create_or_merge_research_concept.call_args
        assert call.kwargs["name"] == "attention mechanism"
        assert call.kwargs["aliases"] == []
        captured = capsys.readouterr().out
        assert "Created" in captured

    def test_merged_into_existing(self, capsys):
        existing = _make_concept(aliases=["self-attention"])
        mock_repo = MagicMock()
        mock_repo.create_or_merge_research_concept.return_value = (existing, False)
        with patch(
            "agentic_kg.knowledge_graph.repository.get_repository",
            return_value=mock_repo,
        ):
            main(["create-concept", "--name", "SDPA"])
        captured = capsys.readouterr().out
        assert "Merged" in captured
        assert "self-attention" in captured

    def test_aliases_split_on_commas_and_trimmed(self):
        mock_repo = MagicMock()
        mock_repo.create_or_merge_research_concept.return_value = (_make_concept(), True)
        with patch(
            "agentic_kg.knowledge_graph.repository.get_repository",
            return_value=mock_repo,
        ):
            main(
                [
                    "create-concept",
                    "--name",
                    "attention",
                    "--aliases",
                    "self-attention, SDPA ,,scaled dot-product attention",
                ]
            )
        call = mock_repo.create_or_merge_research_concept.call_args
        assert call.kwargs["aliases"] == [
            "self-attention",
            "SDPA",
            "scaled dot-product attention",
        ]

    def test_threshold_forwarded(self):
        mock_repo = MagicMock()
        mock_repo.create_or_merge_research_concept.return_value = (_make_concept(), True)
        with patch(
            "agentic_kg.knowledge_graph.repository.get_repository",
            return_value=mock_repo,
        ):
            main(
                [
                    "create-concept",
                    "--name",
                    "attention",
                    "--threshold",
                    "0.92",
                ]
            )
        assert (
            mock_repo.create_or_merge_research_concept.call_args.kwargs["threshold"]
            == pytest.approx(0.92)
        )


class TestLinkConceptDispatch:
    def test_involves_concept_dispatches_problem_link(self, capsys):
        mock_repo = MagicMock()
        mock_repo.link_problem_to_concept.return_value = True

        with patch(
            "agentic_kg.knowledge_graph.repository.get_repository",
            return_value=mock_repo,
        ):
            main(
                [
                    "link-concept",
                    "--concept-id",
                    "c1",
                    "--entity-id",
                    "pc-1",
                ]
            )

        mock_repo.link_problem_to_concept.assert_called_once_with(
            problem_concept_id="pc-1", research_concept_id="c1"
        )
        mock_repo.link_paper_to_concept.assert_not_called()
        captured = capsys.readouterr().out
        assert "Created" in captured

    def test_discusses_dispatches_paper_link(self, capsys):
        mock_repo = MagicMock()
        mock_repo.link_paper_to_concept.return_value = False

        with patch(
            "agentic_kg.knowledge_graph.repository.get_repository",
            return_value=mock_repo,
        ):
            main(
                [
                    "link-concept",
                    "--concept-id",
                    "c1",
                    "--entity-id",
                    "10.1234/paper",
                    "--rel-type",
                    "DISCUSSES",
                ]
            )

        mock_repo.link_paper_to_concept.assert_called_once_with(
            paper_doi="10.1234/paper", research_concept_id="c1"
        )
        mock_repo.link_problem_to_concept.assert_not_called()
        captured = capsys.readouterr().out
        assert "Already present" in captured

    def test_not_found_exits_with_code_1(self, capsys):
        mock_repo = MagicMock()
        mock_repo.link_problem_to_concept.side_effect = NotFoundError("gone")
        with patch(
            "agentic_kg.knowledge_graph.repository.get_repository",
            return_value=mock_repo,
        ), pytest.raises(SystemExit) as exc_info:
            main(
                [
                    "link-concept",
                    "--concept-id",
                    "c1",
                    "--entity-id",
                    "missing",
                ]
            )
        assert exc_info.value.code == 1

    def test_value_error_exits_with_code_2(self):
        mock_repo = MagicMock()
        mock_repo.link_problem_to_concept.side_effect = ValueError("bad")
        with patch(
            "agentic_kg.knowledge_graph.repository.get_repository",
            return_value=mock_repo,
        ), pytest.raises(SystemExit) as exc_info:
            main(
                [
                    "link-concept",
                    "--concept-id",
                    "c1",
                    "--entity-id",
                    "pc-1",
                ]
            )
        assert exc_info.value.code == 2


# =============================================================================
# calibrate-concepts
# =============================================================================


class TestCalibrateConceptsDispatch:
    def _fake_report(self):
        from agentic_kg.knowledge_graph.calibration import (
            CalibrationReport,
            ThresholdResult,
        )

        return CalibrationReport(
            pairs_evaluated=2,
            positives=1,
            negatives=1,
            rows=[
                ThresholdResult(
                    threshold=0.90,
                    precision=1.0,
                    recall=1.0,
                    f1=1.0,
                    true_positive=1,
                    false_positive=0,
                    true_negative=1,
                    false_negative=0,
                ),
            ],
            recommended_threshold=0.90,
            recommended_f1=1.0,
        )

    def test_default_invocation_prints_report(self, capsys):
        with patch(
            "agentic_kg.knowledge_graph.calibration.run_calibration",
            return_value=self._fake_report(),
        ) as mock_run:
            main(["calibrate-concepts"])

        mock_run.assert_called_once()
        captured = capsys.readouterr().out
        assert "Pairs evaluated: 2" in captured
        assert "Recommended threshold: 0.90" in captured

    def test_forwards_pairs_and_thresholds(self):
        with patch(
            "agentic_kg.knowledge_graph.calibration.run_calibration",
            return_value=self._fake_report(),
        ) as mock_run:
            main(
                [
                    "calibrate-concepts",
                    "--pairs",
                    "/tmp/pairs.yml",
                    "--thresholds",
                    "0.80, 0.90 , 0.95",
                ]
            )
        call = mock_run.call_args
        assert call.kwargs["pairs_source"] == "/tmp/pairs.yml"
        assert call.kwargs["thresholds"] == [0.80, 0.90, 0.95]

    def test_invalid_thresholds_exit_code_2(self, capsys):
        with pytest.raises(SystemExit) as exc_info:
            main(["calibrate-concepts", "--thresholds", "not-a-float"])
        assert exc_info.value.code == 2
        assert "invalid --thresholds" in capsys.readouterr().err

    def test_calibration_error_exit_code_1(self, capsys):
        with patch(
            "agentic_kg.knowledge_graph.calibration.run_calibration",
            side_effect=RuntimeError("boom"),
        ), pytest.raises(SystemExit) as exc_info:
            main(["calibrate-concepts"])
        assert exc_info.value.code == 1
        assert "calibration failed" in capsys.readouterr().err
