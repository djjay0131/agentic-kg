"""The legacy baseline: what the agents and the API retrieve before any LLM call.

Captured now, while the legacy graph is still authoritative, so that after
cutover the *same* probe set can be pointed at the canonical projection and the
difference read off rather than argued about.

What is compared, and what deliberately is not
----------------------------------------------
Deterministic retrieval only. Not one assertion here depends on model output:
the agent-level tests install an LLM client that raises if it is called at all,
so "this is the pre-LLM context" is enforced rather than asserted in a comment.
Comparing generated prose across a graph migration would be noise, and noise
that fails intermittently trains people to ignore the suite.

Regenerating the baseline
-------------------------
``AKG_COMPAT_WRITE_BASELINE=1 pytest packages/core/tests/migration/compat`` —
deliberately an explicit opt-in, not an auto-heal. A baseline that rewrites
itself on failure records the bug instead of catching it. Every regeneration
should show up as a reviewable diff in
``baseline/legacy_pre_llm_context.json``.
"""

from __future__ import annotations

import json
import os
from typing import Any

import pytest
from agentic_kg.migration.compat import (
    CYPHER_PROBES,
    ProbeResult,
    assert_parity,
    require_non_vacuous,
    run_all_probes,
)

from .conftest import BASELINE_FILE, FixtureGraph

pytestmark = pytest.mark.integration

WRITE_BASELINE = os.environ.get("AKG_COMPAT_WRITE_BASELINE") == "1"


class _RefusingLLM:
    """An LLM client that fails loudly if anything reaches it.

    Every attribute access raises, so it does not matter which method an agent
    happens to call. This is what turns "pre-LLM context" from a description
    into an enforced property: if a future refactor moves a graph read behind a
    model call, these tests error instead of quietly measuring something else.
    """

    def __getattr__(self, name: str) -> Any:
        raise AssertionError(
            f"the LLM client was called ({name!r}) while capturing deterministic "
            f"pre-LLM context. Nothing in this baseline may depend on model output."
        )


def _load_baseline() -> dict[str, list[dict[str, Any]]]:
    assert BASELINE_FILE.is_file(), (
        f"baseline missing at {BASELINE_FILE}. Regenerate with "
        f"AKG_COMPAT_WRITE_BASELINE=1."
    )
    data = json.loads(BASELINE_FILE.read_text(encoding="utf-8"))
    return data["probes"]


def _as_result(probe_id: str, rows: list[dict[str, Any]]) -> ProbeResult:
    return ProbeResult(probe_id=probe_id, surface="baseline", rows=tuple(rows))


# ---------------------------------------------------------------------------
# The baseline itself
# ---------------------------------------------------------------------------


def test_legacy_pre_llm_context_matches_the_committed_baseline(
    legacy_surface: Any, compat_graph: FixtureGraph
) -> None:
    """Every probe, against the legacy graph, compared to the committed rows.

    ``assert_parity`` is used rather than a bare equality precisely because it
    refuses an empty side. A probe whose read path stopped returning anything
    would otherwise match an empty baseline and report success.
    """
    observed = run_all_probes(
        legacy_surface, compat_graph.inputs, token=compat_graph.token
    )

    if WRITE_BASELINE:  # pragma: no cover - maintenance path
        BASELINE_FILE.parent.mkdir(parents=True, exist_ok=True)
        BASELINE_FILE.write_text(
            json.dumps(
                {
                    "_comment": (
                        "Deterministic pre-LLM retrieval captured from the LEGACY "
                        "graph before the KGIS/KGCS cutover. Regenerate with "
                        "AKG_COMPAT_WRITE_BASELINE=1. '<T>' is the per-run fixture "
                        "token, stripped so the rows are stable across runs."
                    ),
                    "surface": "legacy",
                    "probes": {
                        pid: [dict(row) for row in result.rows]
                        for pid, result in sorted(observed.items())
                    },
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )

    baseline = _load_baseline()
    assert set(baseline) == set(observed), (
        "the probe set and the baseline disagree about which probes exist; "
        f"baseline-only={sorted(set(baseline) - set(observed))}, "
        f"probe-only={sorted(set(observed) - set(baseline))}"
    )
    assert baseline, "the baseline file records no probes"

    for probe_id in sorted(observed):
        assert_parity(_as_result(probe_id, baseline[probe_id]), observed[probe_id])


def test_the_baseline_records_a_row_for_every_probe() -> None:
    """A baseline entry that is an empty list proves nothing about its read path.

    Needs no database: it reads the committed file. This is what stops the
    baseline silently degrading into a file full of ``[]`` that every future
    run trivially matches.
    """
    baseline = _load_baseline()
    assert len(baseline) == len(CYPHER_PROBES)
    empty = sorted(pid for pid, rows in baseline.items() if not rows)
    assert empty == [], f"baseline entries with no rows: {empty}"


def test_the_baseline_carries_no_per_run_identifiers() -> None:
    """Canonicalisation is asserted, not trusted.

    If the fixture token leaked into the committed file, the baseline would be
    pinned to one historical run and every subsequent run would fail for a
    reason that has nothing to do with compatibility.
    """
    raw = BASELINE_FILE.read_text(encoding="utf-8")
    assert "TEST_COMPAT_" not in raw, (
        "an un-canonicalised fixture token reached the baseline file; the "
        "committed rows are pinned to one historical run"
    )
    assert "<T>" in raw, "canonicalisation produced no placeholders - it did not run"


# ---------------------------------------------------------------------------
# The agents, exercised for real, stopped before the model
# ---------------------------------------------------------------------------


def test_ranking_agent_retrieves_the_baseline_candidates(
    neo4j_repository: Any, compat_graph: FixtureGraph
) -> None:
    """The real RankingAgent, the real SearchService, no model call.

    ``_query_candidates`` is the whole deterministic half of the agent: what it
    returns is exactly what gets formatted into the prompt. Comparing it to the
    ``ranking.candidates`` probe proves the probe models the agent rather than
    an idealised version of it.
    """
    from agentic_kg.agents.ranking import RankingAgent
    from agentic_kg.agents.state import create_initial_state
    from agentic_kg.knowledge_graph.search import SearchService

    search = SearchService(
        repository=neo4j_repository,
        embedding_service=object(),  # never used: structured_search does not embed
    )
    agent = RankingAgent(
        llm_client=_RefusingLLM(),
        repository=neo4j_repository,
        search_service=search,
    )

    # The state a real workflow starts from, built by the real factory, so the
    # baseline records what production does rather than what a hand-built dict
    # would make it do.
    state = create_initial_state(max_problems=20)
    candidates = agent._query_candidates(state)
    fixture_candidates = [
        c for c in candidates if c["id"].startswith(compat_graph.token)
    ]
    assert fixture_candidates, "the RankingAgent retrieved none of the fixture problems"

    ids = [c["id"].replace(compat_graph.token, "<T>") for c in fixture_candidates]
    baseline_ids = [row["id"] for row in _load_baseline()["ranking.candidates"]]
    assert ids == baseline_ids, (
        "the RankingAgent's candidate order diverged from the recorded baseline"
    )


def test_ranking_agent_cannot_apply_a_status_filter(
    neo4j_repository: Any, compat_graph: FixtureGraph
) -> None:
    """Defect D-4, pinned so the fix is detected rather than assumed.

    ``POST /api/agents/workflows`` accepts ``status_filter`` as a plain string
    (routers/agents.py:50). ``SearchService.structured_search`` evaluates
    ``status.value`` on it (search.py:208). The result is an AttributeError that
    ``RankingAgent.run`` swallows into a zero-candidate workflow.

    The test asserts the raise, not the swallow, so it is unambiguous about what
    is broken; when the argument type is fixed, this turns red and the
    ``ranking.candidates`` probe must be revisited because the agent will start
    issuing a filtered query.
    """
    from agentic_kg.agents.ranking import RankingAgent
    from agentic_kg.agents.state import create_initial_state
    from agentic_kg.knowledge_graph.search import SearchService

    agent = RankingAgent(
        llm_client=_RefusingLLM(),
        repository=neo4j_repository,
        search_service=SearchService(
            repository=neo4j_repository, embedding_service=object()
        ),
    )
    state = create_initial_state(status_filter="open", max_problems=20)
    with pytest.raises(AttributeError, match="'str' object has no attribute 'value'"):
        agent._query_candidates(state)


def test_continuation_agent_assembles_the_baseline_context(
    neo4j_repository: Any, compat_graph: FixtureGraph
) -> None:
    """The real ContinuationAgent context load, no model call.

    Also pins the two topic facts that matter for the cutover: the topic name
    is reachable today only because the fixture wrote BELONGS_TO by hand, and
    ``related_problems`` is empty despite an EXTENDS edge existing in the graph
    — defect D-1, asserted here so the fix is detected when it lands rather
    than assumed.
    """
    from agentic_kg.agents.continuation import ContinuationAgent
    from agentic_kg.knowledge_graph.relations import RelationService

    agent = ContinuationAgent(
        llm_client=_RefusingLLM(),
        repository=neo4j_repository,
        relation_service=RelationService(repository=neo4j_repository),
    )
    context = agent._load_problem_context(compat_graph.problem_ids[0])

    assert context["id"] == compat_graph.problem_ids[0]
    assert context["statement"].startswith(compat_graph.token)
    assert context["status"] == "open"
    assert context["topic"] == f"{compat_graph.token} natural language processing", (
        "the BELONGS_TO edge the fixture wrote is not reachable; the topic leg "
        "of the continuation prompt is not being exercised"
    )
    assert context["datasets"], "dataset context is empty"
    assert context["metrics"], "metric context is empty"
    assert context["constraints"], "constraint context is empty"

    # D-1. The EXTENDS edge exists -- the probe below proves it -- and the agent
    # still sees nothing, because get_related_problems is called with a `limit`
    # keyword it does not accept and the TypeError is swallowed.
    edges = require_non_vacuous(
        run_all_probes(
            _SurfaceOver(neo4j_repository), compat_graph.inputs, token=compat_graph.token
        )["api.graph.problem_relations"]
    )
    assert any(row["rel_type"] == "EXTENDS" for row in edges.rows)
    assert context["related_problems"] == [], (
        "related_problems is no longer empty, so defect D-1 "
        "(continuation.py:128 passes limit= to a method that has no such "
        "parameter) has been fixed. Remove the SCOPED_OUT classification on "
        "'agent.continuation.related_problems' and add a real probe for it."
    )


class _SurfaceOver:
    """Minimal inline surface, so this module needs no extra fixture."""

    def __init__(self, repository: Any) -> None:
        self._repo = repository

    @property
    def name(self) -> str:
        return "legacy"

    def run(self, cypher: str, /, **params: Any) -> list[dict[str, Any]]:
        with self._repo.session() as session:
            return session.execute_read(
                lambda tx: [dict(record) for record in tx.run(cypher, **params)]
            )


def test_evaluation_and_synthesis_read_the_same_problem_context(
    neo4j_repository: Any, compat_graph: FixtureGraph
) -> None:
    """Both agents' entire pre-LLM graph dependency is ``repo.get_problem``.

    Asserted here rather than taken on trust, because it is what licenses the
    ``continuation.problem_context`` probe covering three read paths at once.
    """
    problem = neo4j_repository.get_problem(compat_graph.problem_ids[0])
    # EvaluationAgent._generate_code formats exactly these two lists.
    assert [d.name for d in problem.datasets] == [f"{compat_graph.token} dataset 1"]
    assert [m.name for m in problem.metrics] == [f"{compat_graph.token} metric 1"]
    # SynthesisAgent._generate_report formats only the statement.
    assert problem.statement.startswith(compat_graph.token)
