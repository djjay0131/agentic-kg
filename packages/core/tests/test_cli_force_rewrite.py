"""E-8 Unit 12 — CLI smoke test for the ``--force-rewrite`` flag.

The flag wires through to ``purge_paper_extraction`` at ingest time. The
full ingestion-orchestration wiring is a follow-up; this test confirms
the argparse surface so the flag is at least usable from operators'
scripts today.
"""

from agentic_kg.cli import main


class TestForceRewriteFlag:
    def test_help_mentions_force_rewrite(self, capsys):
        try:
            main(["ingest", "--help"])
        except SystemExit:
            pass
        out = capsys.readouterr().out
        assert "--force-rewrite" in out
        # The help text spells out what the operator is opting into.
        assert "AC-13" in out or "purge" in out.lower()

    def test_flag_parses_and_reaches_namespace(self, monkeypatch):
        """Drive only the argparse layer — confirm --force-rewrite lands on
        the parsed namespace. The full CLI execution path is exercised by
        the existing ingest tests; here we only validate the new surface.
        """
        from agentic_kg.cli import build_parser

        parser = build_parser()
        ns = parser.parse_args(
            ["ingest", "--query", "test", "--dry-run", "--force-rewrite"]
        )
        assert ns.force_rewrite is True

    def test_flag_defaults_false(self):
        from agentic_kg.cli import build_parser

        parser = build_parser()
        ns = parser.parse_args(["ingest", "--query", "test", "--dry-run"])
        assert ns.force_rewrite is False
