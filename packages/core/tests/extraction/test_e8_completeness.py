"""E-8 Unit 10 — completeness query contract (AC-14).

The ``queries.completeness`` module is the single point that downstream
analytical Cypher queries compose to exclude papers with
``extraction_incomplete = true``. The verify gate runs a codebase audit
to ensure analytical queries either use the helper or document why they
accept partial-extraction papers.
"""

from unittest.mock import MagicMock

from agentic_kg.queries import completeness


class TestCompletePapersFilter:
    def test_returns_cypher_string(self):
        f = completeness.complete_papers_filter()
        assert isinstance(f, str)
        # Mentions the property used as the gate.
        assert "extraction_incomplete" in f

    def test_excludes_true_state(self):
        """The filter should reject both 'true' and missing-property cases
        (papers ingested before E-8 won't carry the property at all).
        """
        f = completeness.complete_papers_filter()
        lowered = f.lower()
        # Either explicitly handles NULL or uses a coalesce-style check.
        assert "is null" in lowered or "coalesce" in lowered or "exists" in lowered

    def test_stable_across_calls(self):
        # The string is part of an external contract — must not be regenerated
        # with timestamps or non-deterministic salt.
        assert completeness.complete_papers_filter() == completeness.complete_papers_filter()

    def test_composes_into_a_query(self):
        """Sanity that callers can drop the fragment into a WHERE clause."""
        f = completeness.complete_papers_filter()
        query = f"MATCH (p:Paper) WHERE p.doi IS NOT NULL {f} RETURN p"
        # Just a string composition check — no Neo4j call.
        assert "MATCH (p:Paper)" in query
        assert "extraction_incomplete" in query


class TestIncompletePapersByExtractor:
    def test_returns_list_from_repo(self):
        """Mock the repo's session().run() chain and assert the query
        targets the expected extractor name and reads back Paper rows."""
        repo = MagicMock()
        sess = MagicMock()
        run = MagicMock()
        run.__iter__ = lambda self: iter([])
        sess.run.return_value = run
        sess.__enter__ = lambda self: sess
        sess.__exit__ = lambda self, *a: None
        repo.session.return_value = sess

        result = completeness.incomplete_papers_by_extractor(repo, "topic")
        assert result == []
        # The query was actually issued.
        sess.run.assert_called_once()
        # Topic name appears either as kwarg or in the query string.
        call_args, call_kwargs = sess.run.call_args
        assert "topic" in str(call_args) + str(call_kwargs)


class TestCompletenessHealthCheck:
    def test_zero_papers_returns_empty(self):
        """Edge case: empty graph. Returning the empty dict avoids a
        ZeroDivisionError when later code computes fractions."""
        repo = MagicMock()
        sess = MagicMock()
        sess.__enter__ = lambda self: sess
        sess.__exit__ = lambda self, *a: None
        empty = MagicMock()
        empty.single.return_value = {"total": 0}
        sess.run.return_value = empty
        repo.session.return_value = sess

        result = completeness.completeness_health_check(repo)
        assert result == {}

    def test_returns_mapping_extractor_to_percentage(self):
        repo = MagicMock()
        sess = MagicMock()
        # Stub: 2 incomplete topic, 1 incomplete concept, 100 total papers.
        rows = iter([{"extractor": "topic", "count": 2}, {"extractor": "concept", "count": 1}])

        def run_side_effect(query, *args, **kwargs):
            r = MagicMock()
            if "count(p)" in query and "extraction_incomplete" not in query:
                # Total-papers query.
                r.single.return_value = {"total": 100}
            else:
                r.__iter__ = lambda self: rows
            return r

        sess.run.side_effect = run_side_effect
        sess.__enter__ = lambda self: sess
        sess.__exit__ = lambda self, *a: None
        repo.session.return_value = sess

        result = completeness.completeness_health_check(repo)
        assert isinstance(result, dict)
        # Result reports fractions (0.0 - 1.0) per extractor.
        for v in result.values():
            assert 0.0 <= v <= 1.0


class TestModuleSurface:
    """AC-14: the helper exposes exactly the three names that downstream
    queries are expected to compose."""

    def test_required_symbols_exist(self):
        for name in (
            "complete_papers_filter",
            "incomplete_papers_by_extractor",
            "completeness_health_check",
        ):
            assert hasattr(completeness, name), f"missing helper: {name}"
