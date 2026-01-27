"""
Unit tests for the extraction CLI.
"""

import json
from unittest.mock import AsyncMock, patch

import pytest

from agentic_kg.cli import build_parser, main


class TestBuildParser:
    """Tests for CLI argument parser construction."""

    def test_parser_creation(self):
        """Parser builds without error."""
        parser = build_parser()
        assert parser is not None

    def test_extract_file_args(self):
        """Parse extract --file arguments."""
        parser = build_parser()
        args = parser.parse_args(["extract", "--file", "paper.pdf"])
        assert args.command == "extract"
        assert args.file == "paper.pdf"

    def test_extract_url_args(self):
        """Parse extract --url arguments."""
        parser = build_parser()
        args = parser.parse_args(["extract", "--url", "https://example.com/paper.pdf"])
        assert args.command == "extract"
        assert args.url == "https://example.com/paper.pdf"

    def test_extract_text_args(self):
        """Parse extract --text arguments."""
        parser = build_parser()
        args = parser.parse_args(["extract", "--text", "Some research text"])
        assert args.command == "extract"
        assert args.text == "Some research text"

    def test_extract_batch_args(self):
        """Parse extract --batch arguments."""
        parser = build_parser()
        args = parser.parse_args(["extract", "--batch", "papers.json"])
        assert args.command == "extract"
        assert args.batch == "papers.json"

    def test_mutually_exclusive_input(self):
        """Cannot specify both --file and --url."""
        parser = build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["extract", "--file", "a.pdf", "--url", "http://b.pdf"])

    def test_metadata_args(self):
        """Parse metadata arguments."""
        parser = build_parser()
        args = parser.parse_args([
            "extract", "--file", "paper.pdf",
            "--title", "My Paper",
            "--doi", "10.1234/test",
            "--authors", "Alice", "Bob",
        ])
        assert args.title == "My Paper"
        assert args.doi == "10.1234/test"
        assert args.authors == ["Alice", "Bob"]

    def test_pipeline_config_args(self):
        """Parse pipeline configuration arguments."""
        parser = build_parser()
        args = parser.parse_args([
            "extract", "--file", "paper.pdf",
            "--min-confidence", "0.7",
            "--skip-relations",
            "--min-section-length", "200",
        ])
        assert args.min_confidence == 0.7
        assert args.skip_relations is True
        assert args.min_section_length == 200

    def test_output_args(self):
        """Parse output options."""
        parser = build_parser()
        args = parser.parse_args([
            "extract", "--file", "paper.pdf",
            "--json", "-v",
        ])
        assert args.json_output is True
        assert args.verbose is True

    def test_no_command_shows_help(self):
        """No command exits with 0."""
        with pytest.raises(SystemExit) as exc_info:
            main([])
        assert exc_info.value.code == 0

    def test_extract_requires_input(self):
        """Extract without input source fails."""
        with pytest.raises(SystemExit):
            main(["extract"])
