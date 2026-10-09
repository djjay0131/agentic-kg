"""The one surface this harness still excludes, plus the repaired ones's proofs.

Excluding something from a compatibility contract is a claim, and an unchecked
claim rots. Each entry in
:data:`~agentic_kg.migration.compat.read_paths.SCOPED_OUT_SURFACES` says "this
code does not reach the graph", and every one of those is asserted here against
the real call signatures.

Why assert that a defect is *present*? Because the exclusion is only justified
while it is. If someone repairs the remaining ``ReviewQueueService`` surface,
the harness must grow to cover it -- and the way to make that decision happen
rather than be forgotten is for this file to go red on the day of the fix. Each
assertion says so in its message.

Two earlier members of that set have been repaired and their tripwires
inverted:

* ``PUT /api/problems/{id}`` -- #110 repaired it (the router resolves a
  canonical view, then writes through ``update_problem_concept`` for a concept
  or ``update_problem(problem)`` for a legacy row). Its test is now
  ``test_put_problem_binds_its_arguments_correctly``.
* ``SynthesisAgent``'s write-back and ``ContinuationAgent``'s related-problem
  read -- #115 repaired both (real ``Problem``/relation writes; the continuation
  read now binds and consumes ``(Problem, ProblemRelation)`` tuples). Their
  tripwires are now the positive tests below.

Signature-level throughout: no HTTP client, no Neo4j, no LLM. The defects are
argument-binding errors, and ``inspect.signature`` sees them without running
anything. ``packages/api`` is imported only where the defect lives there, and
the import is guarded, because the ``migration-canonical-adapter`` CI job
installs ``packages/core`` alone.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest
from agentic_kg.migration.compat import SCOPED_OUT_SURFACES

REPO_ROOT = Path(__file__).resolve().parents[5]


def test_the_scoped_out_set_is_not_empty() -> None:
    # One surface remains: /api/reviews/*. The synthesis write-back and the
    # continuation related-problem read were repaired by #115 and retired here.
    assert len(SCOPED_OUT_SURFACES) == 1
    assert len({s.id for s in SCOPED_OUT_SURFACES}) == 1


# ---------------------------------------------------------------------------
# 1. PUT /api/problems/{id} -- repaired by #110, now a positive test
# ---------------------------------------------------------------------------


def test_put_problem_binds_its_arguments_correctly() -> None:
    """The former tripwire, inverted because #110 fixed the route.

    Before #110 the router made the positional call
    ``repo.update_problem(problem_id, problem)`` against
    ``update_problem(problem: Problem, regenerate_embedding: bool = False)``:
    the str landed on ``problem`` and the Problem on ``regenerate_embedding``,
    so the body raised ``AttributeError`` and the route answered 500. The router
    now resolves a canonical view and dispatches: a concept is written through
    ``repo.update_problem_concept(...)``, a legacy row through the correctly
    bound ``repo.update_problem(problem)``. The router is read as source rather
    than imported, so this test does not require ``packages/api`` to be
    installed.
    """
    from agentic_kg.knowledge_graph.repository import Neo4jRepository

    params = list(inspect.signature(Neo4jRepository.update_problem).parameters)
    assert params == ["self", "problem", "regenerate_embedding"], (
        f"update_problem's signature changed to {params}; re-check the PUT "
        f"/api/problems/{{id}} dispatch against it."
    )
    concept_params = list(
        inspect.signature(Neo4jRepository.update_problem_concept).parameters
    )
    assert concept_params == ["self", "concept_id", "status", "statement"], (
        f"update_problem_concept's signature changed to {concept_params}; "
        f"re-check the concept branch of PUT /api/problems/{{id}}."
    )

    router = REPO_ROOT / "packages/api/src/agentic_kg_api/routers/problems.py"
    source = router.read_text(encoding="utf-8")
    assert "repo.update_problem(problem_id, problem)" not in source, (
        "PUT /api/problems/{id} has reintroduced the positional misbind that "
        "made the route answer 500. Re-pin the defect."
    )
    assert "repo.update_problem(problem)" in source, (
        "PUT /api/problems/{id} no longer writes the legacy :Problem through "
        "the correctly bound one-argument call; re-check the route."
    )
    assert "repo.update_problem_concept(" in source, (
        "PUT /api/problems/{id} no longer routes concept updates through "
        "update_problem_concept; re-check the route."
    )


# ---------------------------------------------------------------------------
# 2. /api/reviews/*
# ---------------------------------------------------------------------------


def test_the_review_queue_calls_repository_methods_that_do_not_exist() -> None:
    """``ReviewQueueService`` awaits ``repo.write_transaction`` /
    ``read_transaction``. ``Neo4jRepository`` defines neither, so every
    ``/api/reviews/*`` route raises AttributeError before any Cypher runs.
    """
    from agentic_kg.knowledge_graph.repository import Neo4jRepository

    expected = ("write_transaction", "read_transaction")
    missing = [name for name in expected if not hasattr(Neo4jRepository, name)]
    present = sorted(set(expected) - set(missing))
    assert missing == list(expected), (
        f"Neo4jRepository now provides {present}. The human-review surface may "
        f"be live; remove 'write.api.reviews' from SCOPED_OUT_SURFACES and give "
        f"PendingReview/REVIEWS a real contract."
    )

    queue = REPO_ROOT / "packages/core/src/agentic_kg/knowledge_graph/review_queue.py"
    source = queue.read_text(encoding="utf-8")
    assert "self._repo.write_transaction(" in source
    assert "self._repo.read_transaction(" in source


# ---------------------------------------------------------------------------
# 3. SynthesisAgent's writes -- repaired by #115, now positive tests
# ---------------------------------------------------------------------------


def test_synthesis_agent_binds_its_repository_writes_correctly() -> None:
    """The former tripwire, inverted because #115 fixed the write-back.

    Before #115 ``_apply_graph_updates`` called
    ``repo.create_problem(id=, statement=, status=)`` against
    ``create_problem(problem: Problem, ...)`` and updated the source status via
    ``repo.update_problem(id, status=)``. Both raised ``TypeError``, swallowed
    by ``logger.warning``, so no synthesis output ever reached the graph. The
    agent now builds a real ``Problem`` and uses the label-agnostic
    ``set_problem_status``; this pins that the arguments bind.
    """
    from agentic_kg.knowledge_graph.models import Problem, ProblemStatus
    from agentic_kg.knowledge_graph.repository import Neo4jRepository

    create_params = list(inspect.signature(Neo4jRepository.create_problem).parameters)
    assert create_params[1] == "problem", (
        f"create_problem's first parameter is {create_params[1]!r}, not "
        f"'problem'; re-check SynthesisAgent._apply_graph_updates."
    )
    # A real Problem now binds positionally.
    inspect.signature(Neo4jRepository.create_problem).bind(
        object(),
        Problem(
            id="x",
            statement="A synthesized candidate problem statement.",
            status=ProblemStatus.OPEN,
        ),
    )
    # Status goes through the label-agnostic setter, not update_problem(id, status=).
    inspect.signature(Neo4jRepository.set_problem_status).bind(
        object(), "x", ProblemStatus.IN_PROGRESS
    )

    source = (REPO_ROOT / "packages/core/src/agentic_kg/agents/synthesis.py").read_text(
        encoding="utf-8"
    )
    assert "self.repo.create_problem(new_problem)" in source, (
        "SynthesisAgent no longer creates a real Problem through "
        "create_problem(problem); re-check _apply_graph_updates."
    )
    assert "self.repo.set_problem_status(" in source, (
        "SynthesisAgent no longer writes source status through "
        "set_problem_status; re-check _apply_graph_updates."
    )


def test_synthesis_agent_binds_its_create_relation_call_correctly() -> None:
    """The relation half: ``from_problem_id`` / ``to_problem_id`` / ``RelationType``.

    Before #115 the agent passed ``source_id=`` / ``target_id=`` /
    ``relation_type="EXTENDS"`` against
    ``create_relation(from_problem_id, to_problem_id, relation_type, ...)`` --
    another swallowed ``TypeError``. The real signature now accepts the call,
    and the old keyword names still would not.
    """
    from agentic_kg.knowledge_graph.models import RelationType
    from agentic_kg.knowledge_graph.relations import RelationService

    signature = inspect.signature(RelationService.create_relation)
    params = list(signature.parameters)
    assert params[1:4] == ["from_problem_id", "to_problem_id", "relation_type"], params
    signature.bind(object(), "a", "b", RelationType.EXTENDS)
    with pytest.raises(TypeError):
        signature.bind(
            object(), source_id="a", target_id="b", relation_type=RelationType.EXTENDS
        )

    source = (REPO_ROOT / "packages/core/src/agentic_kg/agents/synthesis.py").read_text(
        encoding="utf-8"
    )
    assert "source_id=" not in source, (
        "the old create_relation(source_id=...) misbind has been reintroduced; "
        "re-check SynthesisAgent._apply_graph_updates."
    )


def test_synthesis_write_handlers_report_failures_without_raising() -> None:
    """Why a write failure is still safe, now that the writes bind.

    #115 kept a per-write ``try`` around the graph updates, so one bad relation
    cannot drop the rest of the report. Those handlers must downgrade to a log
    line rather than raise. The writes they guard now go through the real API
    (asserted above), so this is defence -- not the old silent no-op where every
    call failed and "Applied 0 graph updates" was the only signal.
    """
    source = (REPO_ROOT / "packages/core/src/agentic_kg/agents/synthesis.py").read_text(
        encoding="utf-8"
    )
    tree = ast.parse(source)
    apply_updates = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "_apply_graph_updates"
    )

    # The writes now name real repository / relation methods.
    called = {
        node.func.attr
        for node in ast.walk(apply_updates)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert {
        "create_problem",
        "create_derived_from",
        "create_relation",
        "set_problem_status",
    } <= called, f"a real write-back method is no longer called: {sorted(called)}"

    handlers = [
        handler
        for node in ast.walk(apply_updates)
        if isinstance(node, ast.Try)
        for handler in node.handlers
    ]
    assert len(handlers) == 4, f"expected four write handlers, found {len(handlers)}"
    for handler in handlers:
        calls = [
            n
            for n in ast.walk(handler)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute)
            and n.func.attr in {"warning", "info", "debug"}
        ]
        assert calls, (
            "a SynthesisAgent write handler no longer downgrades its failure to "
            "a log line. If the writes now surface, revisit "
            "'write.agent.synthesis' in SCOPED_OUT_SURFACES."
        )
        assert not any(isinstance(n, ast.Raise) for n in ast.walk(handler))


# ---------------------------------------------------------------------------
# 4. ContinuationAgent's related-problem context -- repaired by #115
# ---------------------------------------------------------------------------


def test_continuation_agent_binds_its_relation_context_call() -> None:
    """D-1, repaired by #115.

    ``continuation.py`` used to call
    ``get_related_problems(problem_id, direction='both', limit=10)`` against a
    method declaring ``(self, problem_id, relation_type=None, direction='both')``.
    The ``TypeError`` was swallowed by ``logger.warning``, so the
    related-problem leg of the continuation prompt was unconditionally empty.
    The accepted call now binds.
    """
    from agentic_kg.knowledge_graph.relations import RelationService

    signature = inspect.signature(RelationService.get_related_problems)
    assert "limit" not in signature.parameters, (
        "get_related_problems now accepts `limit`; revisit the continuation "
        "call site and the probe for 'agent.continuation.related_problems'."
    )
    # The real call binds...
    signature.bind(object(), "problem-id", direction="both")
    # ...and the old misbind would still raise.
    with pytest.raises(TypeError):
        signature.bind(object(), "problem-id", direction="both", limit=10)

    source = (REPO_ROOT / "packages/core/src/agentic_kg/agents/continuation.py").read_text(
        encoding="utf-8"
    )
    assert "limit=10" not in source, "the D-1 misbind has been reintroduced"
    assert 'direction="both"' in source, (
        "the continuation call no longer requests both directions; re-check "
        "_load_problem_context."
    )


def test_continuation_agent_reads_the_relation_service_tuples() -> None:
    """D-2, repaired by #115.

    ``get_related_problems`` returns ``list[tuple[Problem, ProblemRelation]]``.
    Before #115 the caller did ``rel.get('type', 'RELATED')`` on each entry; the
    ``AttributeError`` was swallowed and the prompt stayed empty. The caller now
    unpacks the tuples.
    """
    from agentic_kg.knowledge_graph.relations import RelationService

    returns = inspect.signature(RelationService.get_related_problems).return_annotation
    assert "tuple" in str(returns), (
        f"return annotation is now {returns!r}; re-check whether the "
        f"ContinuationAgent's tuple unpacking is still correct"
    )

    source = (REPO_ROOT / "packages/core/src/agentic_kg/agents/continuation.py").read_text(
        encoding="utf-8"
    )
    assert "for related_problem, relation in related" in source, (
        "continuation no longer unpacks the (Problem, ProblemRelation) tuples "
        "returned by get_related_problems; re-check _load_problem_context."
    )
    assert "relation.relation_type.value" in source
    assert 'rel.get("type", "RELATED")' not in source
    assert "rel.get('type', 'RELATED')" not in source


# ---------------------------------------------------------------------------
# The remaining exclusion is documented where a reader will find it
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("surface", SCOPED_OUT_SURFACES, ids=lambda s: s.id)
def test_every_exclusion_names_its_failure_and_its_reason(surface: object) -> None:
    assert getattr(surface, "failure").strip()
    assert getattr(surface, "reason").strip()
    assert getattr(surface, "call").strip()
    assert getattr(surface, "declaration").strip()
