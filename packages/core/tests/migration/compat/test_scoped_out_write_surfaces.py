"""The four surfaces this harness excludes, and the proof they are still broken.

Excluding something from a compatibility contract is a claim, and an unchecked
claim rots. Each entry in
:data:`~agentic_kg.migration.compat.read_paths.SCOPED_OUT_SURFACES` says "this
code does not reach the graph", and every one of those is asserted here against
the real call signatures.

Why assert that a defect is *present*? Because the exclusion is only justified
while it is. If someone repairs ``PUT /api/problems/{id}`` or the
``ContinuationAgent`` related-problem lookup, the surface becomes testable and
the harness must grow to cover it — and the way to make that decision happen
rather than be forgotten is for this file to go red on the day of the fix.
Each assertion says so in its message.

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
    assert len(SCOPED_OUT_SURFACES) == 4
    assert len({s.id for s in SCOPED_OUT_SURFACES}) == 4


# ---------------------------------------------------------------------------
# 1. PUT /api/problems/{id}
# ---------------------------------------------------------------------------


def test_put_problem_binds_its_arguments_to_the_wrong_parameters() -> None:
    """``repo.update_problem(problem_id, problem)`` against
    ``update_problem(problem: Problem, regenerate_embedding: bool = False)``.

    The str lands on ``problem`` and the Problem on ``regenerate_embedding``;
    the body then evaluates ``problem.id`` on a str. FastAPI has no handler for
    the AttributeError, so the route answers 500.

    The router is read as source rather than imported, so this test does not
    require ``packages/api`` to be installed.
    """
    from agentic_kg.knowledge_graph.repository import Neo4jRepository

    params = list(inspect.signature(Neo4jRepository.update_problem).parameters)
    assert params == ["self", "problem", "regenerate_embedding"], (
        f"update_problem's signature changed to {params}; re-check whether "
        f"PUT /api/problems/{{id}} still misbinds, and if it does not, remove "
        f"'write.api.put_problem' from SCOPED_OUT_SURFACES and cover the route."
    )

    router = REPO_ROOT / "packages/api/src/agentic_kg_api/routers/problems.py"
    source = router.read_text(encoding="utf-8")
    assert "repo.update_problem(problem_id, problem)" in source, (
        "PUT /api/problems/{id} no longer makes the positional call this "
        "harness scopes out. Re-evaluate the exclusion."
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
# 3. SynthesisAgent's four writes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("target", "kwargs"),
    (
        ("create_problem", {"id": "x", "statement": "y", "status": "open"}),
        ("update_problem", {"status": "in_progress"}),
    ),
)
def test_synthesis_agent_calls_the_repository_with_parameters_it_has_not_got(
    target: str, kwargs: dict[str, str]
) -> None:
    """``SynthesisAgent._apply_graph_updates`` passes keyword arguments that do
    not exist on the methods it calls. Every one raises TypeError, and every one
    is swallowed by ``logger.warning``, so the agent reports "Applied 0 graph
    updates" and nothing is written.

    ``Signature.bind`` is the check: it resolves the exact same way the
    interpreter does, so this cannot drift from real call behaviour.
    """
    from agentic_kg.knowledge_graph.repository import Neo4jRepository

    signature = inspect.signature(getattr(Neo4jRepository, target))
    with pytest.raises(TypeError):
        signature.bind(object(), **kwargs)


def test_synthesis_agent_calls_create_relation_with_parameters_it_has_not_got() -> None:
    """The relation half: ``source_id`` / ``target_id`` against
    ``from_problem_id`` / ``to_problem_id``.
    """
    from agentic_kg.knowledge_graph.relations import RelationService

    signature = inspect.signature(RelationService.create_relation)
    with pytest.raises(TypeError):
        signature.bind(object(), source_id="a", target_id="b", relation_type="EXTENDS")


def test_the_synthesis_failures_are_swallowed_rather_than_surfaced() -> None:
    """Why this is invisible in production rather than merely broken.

    Each write sits in its own ``try`` whose handler is ``logger.warning``, so
    the workflow completes successfully having written nothing. The AST walk
    asserts the handlers are there, because "the writes fail" and "the failures
    are reported" are different facts and only the first is obvious.
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
    handlers = [
        handler
        for node in ast.walk(apply_updates)
        if isinstance(node, ast.Try)
        for handler in node.handlers
    ]
    assert len(handlers) == 3, f"expected three swallowing handlers, found {len(handlers)}"
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
# 4. ContinuationAgent's related-problem context — NEW DEFECT D-1
# ---------------------------------------------------------------------------


def test_continuation_agent_passes_a_limit_that_the_relation_service_rejects() -> None:
    """D-1. ``continuation.py:128`` calls
    ``get_related_problems(problem_id, direction='both', limit=10)``;
    ``relations.py:261`` declares ``(self, problem_id, relation_type=None,
    direction='both')``. TypeError, swallowed by ``logger.warning``, so the
    related-problem leg of the continuation prompt is unconditionally empty.

    Found by this phase. Masked in the existing unit tests by a MagicMock
    (``packages/core/tests/agents/conftest.py:100``), which accepts any keyword
    and returns dicts.
    """
    from agentic_kg.knowledge_graph.relations import RelationService

    signature = inspect.signature(RelationService.get_related_problems)
    assert "limit" not in signature.parameters, (
        "get_related_problems now accepts `limit`, so defect D-1 is fixed. "
        "Reclassify 'agent.continuation.related_problems' out of SCOPED_OUT and "
        "add a probe for it."
    )
    with pytest.raises(TypeError):
        signature.bind(object(), "problem-id", direction="both", limit=10)

    source = (REPO_ROOT / "packages/core/src/agentic_kg/agents/continuation.py").read_text(
        encoding="utf-8"
    )
    assert "limit=10" in source, "the offending call site changed; re-verify D-1"


def test_the_relation_service_returns_tuples_the_continuation_agent_cannot_read() -> None:
    """D-2, the defect waiting behind D-1.

    ``get_related_problems`` returns ``list[tuple[Problem, ProblemRelation]]``.
    ``ContinuationAgent`` does ``rel.get('type', 'RELATED')``. Fixing the
    ``limit`` keyword alone converts a silent TypeError into a silent
    AttributeError — the prompt stays empty and the logged message changes.
    Recorded so the two are fixed together.
    """
    from agentic_kg.knowledge_graph.relations import RelationService

    returns = inspect.signature(RelationService.get_related_problems).return_annotation
    assert "tuple" in str(returns), (
        f"return annotation is now {returns!r}; re-check whether the "
        f"ContinuationAgent's rel.get(...) access is still wrong"
    )
    source = (REPO_ROOT / "packages/core/src/agentic_kg/agents/continuation.py").read_text(
        encoding="utf-8"
    )
    assert 'rel.get("type", "RELATED")' in source or "rel.get('type', 'RELATED')" in source


# ---------------------------------------------------------------------------
# The exclusions are documented where a reader will find them
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("surface", SCOPED_OUT_SURFACES, ids=lambda s: s.id)
def test_every_exclusion_names_its_failure_and_its_reason(surface: object) -> None:
    assert getattr(surface, "failure").strip()
    assert getattr(surface, "reason").strip()
    assert getattr(surface, "call").strip()
    assert getattr(surface, "declaration").strip()
