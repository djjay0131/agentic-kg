"""The read-path inventory is checked against the code it claims to describe.

An inventory is only useful if it cannot drift. Every
:class:`~agentic_kg.migration.compat.read_paths.ReadPath` carries a literal
``snippet`` that must occur in the file it cites, and this module asserts all of
them. ``test_the_snippet_check_can_fail`` points the same checker at a
deliberately wrong entry, because a scan that matches nothing and a scan with a
broken pattern look identical in a green run (§9.0 obligation 5).

The projection-contract checks below read the **mapping spec markdown itself**
rather than a local restatement of its 15 relationship types and 6 vector
indexes. §9.0 obligation 3: a criterion naming an upstream rule must exercise
the upstream artefact. A transcribed tuple here would pass forever after the
spec changed.

Needs no database.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest
from agentic_kg.migration.compat import (
    CYPHER_PROBES,
    READ_PATHS,
    SCOPED_OUT_SURFACES,
    CompatClass,
    ReadPath,
    labels_read,
    ordering_keys_read,
    paths_for_class,
    relationships_read,
    vector_indexes_read,
)

REPO_ROOT = Path(__file__).resolve().parents[5]
SPEC = REPO_ROOT / "llm" / "features" / "kgis-kgcs-ontology-mapping.md"


def _snippet_offenders(paths: tuple[ReadPath, ...]) -> list[str]:
    """Entries whose cited file is missing or does not contain their snippet."""
    offenders: list[str] = []
    for path in paths:
        source = REPO_ROOT / path.source_file
        if not source.is_file():
            offenders.append(f"{path.id}: no such file {path.source_file}")
            continue
        text = source.read_text(encoding="utf-8")
        if path.snippet not in text:
            offenders.append(f"{path.id}: snippet not found in {path.source_file}")
        for extra in path.also:
            if extra not in text:
                offenders.append(f"{path.id}: `also` anchor not found: {extra!r}")
    return offenders


# ---------------------------------------------------------------------------
# The registry is real
# ---------------------------------------------------------------------------


def test_the_inventory_is_not_empty() -> None:
    """§9.0 obligation 1 for every quantified assertion in this module."""
    assert len(READ_PATHS) >= 25, f"expected the full read-path inventory, got {len(READ_PATHS)}"
    assert len(SCOPED_OUT_SURFACES) >= 3
    assert len({p.id for p in READ_PATHS}) == len(READ_PATHS), "duplicate read-path id"


def test_every_snippet_occurs_in_the_file_it_cites() -> None:
    """The anti-drift anchor. Red the moment the inventory and the code diverge."""
    assert _snippet_offenders(READ_PATHS) == []


def test_the_snippet_check_can_fail() -> None:
    """Obligation 5 for the check above: it detects an entry that does not hold.

    Two ways an entry can be wrong -- a file that is not there, and a snippet
    that is not in it -- and both must be caught. Without this, a checker whose
    `in` test had been inverted, or whose path join was broken, would report a
    clean inventory forever.
    """
    real = READ_PATHS[0]
    wrong_snippet = ReadPath(
        id="fabricated.snippet",
        surface="does not exist",
        source_file=real.source_file,
        snippet="this string is not in any source file in this repository",
        labels=(),
        relationships=(),
        properties=(),
    )
    wrong_file = ReadPath(
        id="fabricated.file",
        surface="does not exist",
        source_file="packages/api/src/agentic_kg_api/routers/no_such_module.py",
        snippet="anything",
        labels=(),
        relationships=(),
        properties=(),
    )
    offenders = _snippet_offenders((wrong_snippet, wrong_file, real))
    assert len(offenders) == 2
    assert "fabricated.snippet" in offenders[0]
    assert "fabricated.file" in offenders[1]


def test_scoped_out_surfaces_cite_files_that_exist() -> None:
    missing = [
        f"{s.id}: {fp}"
        for s in SCOPED_OUT_SURFACES
        for fp in (s.source_file, s.declaration_file)
        if not (REPO_ROOT / fp).is_file()
    ]
    assert missing == []


# ---------------------------------------------------------------------------
# Probes and the inventory agree with each other
# ---------------------------------------------------------------------------


def test_every_probe_names_a_read_path_in_the_inventory() -> None:
    known = {p.id for p in READ_PATHS}
    assert CYPHER_PROBES, "the probe set must not be empty"
    claimed = {rid for probe in CYPHER_PROBES for rid in probe.read_path_ids}
    assert claimed, "no probe claims to cover any read path"
    dangling = sorted(claimed - known)
    assert dangling == [], f"probes reference unknown read paths: {dangling}"


def test_every_probe_id_is_unique() -> None:
    ids = [p.id for p in CYPHER_PROBES]
    assert len(set(ids)) == len(ids)


def test_every_parity_read_path_is_probed_or_justified() -> None:
    """Coverage is asserted, not assumed.

    A PARITY read path with no probe is an unproven compatibility claim. Exactly
    one is allowed to be unprobed, and it is named here with its reason, so
    adding a second silently is a failure rather than an omission.
    """
    probed = {rid for probe in CYPHER_PROBES for rid in probe.read_path_ids}
    candidates = [p for p in paths_for_class(CompatClass.PARITY) if not p.vector_indexes]
    assert candidates, "no probeable PARITY read paths"
    unprobed = sorted(p.id for p in candidates if p.id not in probed)
    assert unprobed == [], (
        "every PARITY read path must have a deterministic probe unless it names "
        f"a vector index. Unprobed: {unprobed}."
    )


def test_only_vector_index_paths_are_exempt_from_probing() -> None:
    """The exemption is a rule, not a list, and it is narrow.

    A vector read calls ``db.index.vector.queryNodes`` with an embedding from a
    live provider: neither deterministic nor free, so a probe would be a
    flake generator. Its real contract is the index *name*, asserted against
    the spec. Stating the exemption as a property of the entry — rather than
    as a hardcoded id — means a new vector path is covered automatically while
    a new non-vector path still has to be probed.
    """
    probed = {rid for probe in CYPHER_PROBES for rid in probe.read_path_ids}
    exempt = sorted(p.id for p in READ_PATHS if p.vector_indexes and p.id not in probed)
    assert len(exempt) == 6, exempt
    assert all(paths_for_class(CompatClass.PARITY)), "class filter returned nothing"


def test_every_declared_change_read_path_is_probed() -> None:
    """DECLARED_CHANGE paths still need a probe -- to record what legacy does.

    They are not held to parity, but their legacy behaviour has to be captured
    or the declared change cannot be evidenced after cutover.
    """
    probed = {rid for probe in CYPHER_PROBES for rid in probe.read_path_ids}
    changed = paths_for_class(CompatClass.DECLARED_CHANGE)
    assert changed, "the DECLARED_CHANGE set must not be empty"
    unprobed = sorted(p.id for p in changed if p.id not in probed)
    assert unprobed == []


def test_no_probe_targets_a_scoped_out_read_path() -> None:
    """A probe over a path that raises before it queries would be vacuous."""
    scoped_out = {p.id for p in paths_for_class(CompatClass.SCOPED_OUT)}
    assert scoped_out, "the SCOPED_OUT set must not be empty"
    offenders = sorted(
        probe.id
        for probe in CYPHER_PROBES
        if scoped_out & set(probe.read_path_ids)
    )
    assert offenders == []


# ---------------------------------------------------------------------------
# The inventory's dependencies are inside the declared projection contract
# ---------------------------------------------------------------------------


def _spec_text() -> str:
    assert SPEC.is_file(), f"mapping spec not found at {SPEC}"
    return SPEC.read_text(encoding="utf-8")


def _spec_relationship_types() -> frozenset[str]:
    """The 15 relationship types §4.4 promises to project, parsed from the spec."""
    text = _spec_text()
    anchor = text.index("Fifteen distinct type names must be projected")
    end = text.index("(`REVIEWS` is dead", anchor)
    return frozenset(re.findall(r"`([A-Z][A-Z_]+)`", text[anchor:end]))


def _spec_vector_indexes() -> frozenset[str]:
    """The 6 vector index names §4.4 promises to recreate, parsed from the spec."""
    text = _spec_text()
    anchor = text.index("**Vector indexes — six, by name.**")
    end = text.index("`mention_embedding_idx`", anchor)
    return frozenset(re.findall(r"`([a-z_]+_idx)`", text[anchor:end]))


def test_the_spec_parse_found_what_the_spec_says_it_contains() -> None:
    """The parse is asserted before anything is concluded from it.

    A regex that silently matched nothing would make every subset check below
    pass trivially. The counts come from the spec's own prose -- 'Fifteen
    distinct type names', 'Vector indexes - six, by name'.
    """
    assert len(_spec_relationship_types()) == 15
    assert len(_spec_vector_indexes()) == 6


def test_every_relationship_the_application_reads_is_in_the_projection_contract() -> None:
    """A read path depending on an unprojected edge is a cutover gap.

    Quantified over PARITY and DECLARED_CHANGE only: the SCOPED_OUT
    ``/api/reviews/*`` surface traverses ``REVIEWS``, which §4.4 explicitly
    calls dead and does not project. Including it would make this assertion
    fail for a reason that is already recorded and accepted.
    """
    live = tuple(
        p
        for p in READ_PATHS
        if p.compat in (CompatClass.PARITY, CompatClass.DECLARED_CHANGE)
    )
    assert live, "no live read paths to check"
    needed = frozenset(rel for p in live for rel in p.relationships)
    assert needed, "the live read paths traverse no relationships - parse is broken"
    missing = sorted(needed - _spec_relationship_types())
    assert missing == [], (
        f"read paths traverse relationship types the projection does not "
        f"promise: {missing}"
    )


def test_reviews_is_the_only_relationship_outside_the_contract() -> None:
    """The counterpart: the exclusion above is narrow and named.

    Without this, widening a read path to traverse anything unprojected could be
    hidden by quietly marking it SCOPED_OUT.
    """
    all_rels = relationships_read()
    assert all_rels
    outside = sorted(all_rels - _spec_relationship_types())
    assert outside == ["REVIEWS"]


# ---------------------------------------------------------------------------
# Completeness — the direction the snippet anchor does not cover
# ---------------------------------------------------------------------------
#
# The snippet check proves every *entry* describes real code. It proves nothing
# about the reverse, and the reverse is where the gaps were: four CITES /
# USES_MODEL / APPLIES_METHOD reads, the eighth BELONGS_TO site the spec itself
# names, and four of the six vector indexes all had no entry at all. Three
# scans below close it. Each asserts it actually looked at something before
# concluding anything, and each has a companion proving it can fail.

APPLICATION_TREES = (
    REPO_ROOT / "packages" / "api" / "src" / "agentic_kg_api",
    REPO_ROOT / "packages" / "core" / "src" / "agentic_kg" / "agents",
)
#: Core modules outside the two application trees that nevertheless issue graph
#: reads the application reaches. ``relations.py`` and ``review_queue.py`` were
#: missing: review planted a ``[:SMUGGLED_EDGE]`` read with an ``ORDER BY`` in
#: ``relations.py``, called it from ``continuation.py``, and it passed all 69
#: inventory tests. A module in no scan set is a hole regardless of how good
#: the scans are.
EXTRA_READ_MODULES = tuple(
    REPO_ROOT / "packages" / "core" / "src" / "agentic_kg" / "knowledge_graph" / name
    for name in ("search.py", "relations.py", "review_queue.py")
)
ROUTERS = REPO_ROOT / "packages" / "api" / "src" / "agentic_kg_api" / "routers"
REPOSITORY = (
    REPO_ROOT / "packages" / "core" / "src" / "agentic_kg" / "knowledge_graph" / "repository.py"
)
INDEX_SCAN_ROOTS = (
    REPO_ROOT / "packages" / "core" / "src" / "agentic_kg" / "knowledge_graph",
    REPO_ROOT / "packages" / "api" / "src" / "agentic_kg_api",
)

#: Declared dead by spec §4.4 ("declared and never queried — dropped").
DEAD_VECTOR_INDEXES = frozenset({"mention_embedding_idx"})


#: Cypher write clauses. A literal carrying one of these is a mutation, and
#: mutations are out of this harness's scope by declaration (see
#: SCOPED_OUT_SURFACES and bound 1 in the PR body) -- leg A would otherwise
#: demand inventory entries for every CREATE/MERGE in the modules it scans,
#: including the ``MATCH ... CREATE`` prologue of a write.
_WRITE_CLAUSE = re.compile(r"\b(CREATE|MERGE|SET|DELETE|REMOVE)\b")


def _is_read(cypher: str) -> bool:
    return (
        "MATCH (" in cypher or "db.index.vector.queryNodes" in cypher
    ) and not _WRITE_CLAUSE.search(cypher)


def _cypher_literals(path: Path) -> list[tuple[int, int]]:
    """(start_line, end_line) of every string literal that issues a graph *read*."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    spans: list[tuple[int, int]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
            continue
        if _is_read(node.value):
            spans.append((node.lineno, node.end_lineno or node.lineno))
    return spans


def _snippet_lines(path: Path) -> dict[str, list[int]]:
    """For each inventory entry citing ``path``, the 1-based lines its anchors hit.

    ``also`` anchors count, so one entry can legitimately account for several
    query literals (``GET /api/stats`` issues four).
    """
    try:
        rel = str(path.relative_to(REPO_ROOT)).replace("\\", "/")
    except ValueError:
        # A file outside the repo (a tmp_path fixture) is cited by nothing.
        return {}
    lines = path.read_text(encoding="utf-8").splitlines()
    hits: dict[str, list[int]] = {}
    for entry in READ_PATHS:
        if entry.source_file != rel:
            continue
        anchors = [entry.snippet, *entry.also]
        found: list[int] = []
        for anchor in anchors:
            first = anchor.splitlines()[0]
            found.extend(i + 1 for i, line in enumerate(lines) if first in line)
        hits[entry.id] = found
    return hits


def _uncovered_cypher(paths: tuple[Path, ...]) -> list[str]:
    offenders: list[str] = []
    for path in paths:
        claimed = [line for lines in _snippet_lines(path).values() for line in lines]
        for start, end in _cypher_literals(path):
            if not any(start <= line <= end for line in claimed):
                rel = path.relative_to(REPO_ROOT)
                offenders.append(f"{rel}:{start}")
    return offenders


def _scanned_files() -> list[Path]:
    files: list[Path] = []
    for tree in APPLICATION_TREES:
        assert tree.is_dir(), f"missing application tree: {tree}"
        files.extend(sorted(tree.rglob("*.py")))
    files.extend(EXTRA_READ_MODULES)
    return files


def test_the_completeness_scan_looked_at_real_files_with_real_cypher() -> None:
    """Obligation 1 for the scan: it must have found something to check."""
    files = _scanned_files()
    assert len(files) >= 20, f"scanned only {len(files)} files"
    literals = sum(len(_cypher_literals(path)) for path in files)
    assert literals >= 10, f"found only {literals} Cypher literals - the scan is broken"


def test_every_inline_cypher_read_has_an_inventory_entry() -> None:
    """Leg A: no graph read in the application trees may be un-inventoried."""
    uncovered = _uncovered_cypher(tuple(_scanned_files()))
    assert uncovered == [], (
        "these graph reads have no entry in READ_PATHS, so the inventory is "
        f"incomplete and every 'all read paths' claim overstates coverage: {uncovered}"
    )


def test_the_completeness_scan_can_fail(tmp_path: Path) -> None:
    """Obligation 5 for leg A: an un-inventoried read is detected.

    Points the same extractor at a module holding a Cypher read that no
    inventory entry cites. Without this, a broken AST walk reports a complete
    inventory forever.
    """
    rogue = tmp_path / "rogue_router.py"
    rogue.write_text(
        'QUERY = """\nMATCH (p:Problem)-[:INVENTED_EDGE]->(x:Nowhere)\nRETURN p\n"""\n',
        encoding="utf-8",
    )
    spans = _cypher_literals(rogue)
    assert len(spans) == 1, "the extractor did not see the rogue read"
    # Nothing in READ_PATHS cites this file, so it is claimed by nothing.
    assert _snippet_lines(rogue) == {}


def test_relations_and_review_queue_are_in_the_scan_set() -> None:
    """The smuggling path review demonstrated: a module in no scan set.

    A ``[:SMUGGLED_EDGE]`` read with an ``ORDER BY`` was planted in
    ``relations.py``, called from ``continuation.py``, and it passed all 69
    inventory tests -- because ``relations.py`` was scanned by neither leg.
    Both modules are now scanned, and both are asserted to actually contain
    Cypher so the fix cannot be a path that matches nothing.
    """
    scanned = {path.name for path in _scanned_files()}
    assert {"relations.py", "review_queue.py", "search.py"} <= scanned, sorted(scanned)
    for name in ("relations.py", "review_queue.py"):
        path = next(p for p in _scanned_files() if p.name == name)
        assert _cypher_literals(path), f"{name} is scanned but yields no read literals"


def test_a_smuggled_read_in_a_scanned_module_is_caught(tmp_path: Path) -> None:
    """Obligation 5 for the widened scan set, using review's exact shape."""
    smuggled = tmp_path / "relations_like.py"
    smuggled.write_text(
        'QUERY = """\n'
        "MATCH (p:Problem {id: $id})-[:SMUGGLED_EDGE]->(x:Problem)\n"
        "RETURN x ORDER BY x.name\n"
        '"""\n',
        encoding="utf-8",
    )
    spans = _cypher_literals(smuggled)
    assert len(spans) == 1, "leg A did not see the smuggled read"
    assert _snippet_lines(smuggled) == {}, "nothing in READ_PATHS claims it"


def test_an_untyped_traversal_carries_a_contract() -> None:
    """Hardening 2, and the case that made it necessary.

    The criterion required a typed ``-[:TYPE``, so every untyped form was
    silently exempt -- including the two queries this harness calls its widest
    leak surface. They were safe only because they sit inline in a router,
    where leg A has no exemption. Now the rule is what makes them safe.
    """
    for cypher in (
        "MATCH (p:Problem)-[r]->(p2:Problem) RETURN p",          # graph.py:86
        "MATCH (p:Problem) OPTIONAL MATCH (p)-[r]-(n) RETURN r",  # graph.py:235
        "MATCH (a)--(b) RETURN a",
        "MATCH (a)-->(b) RETURN a",
        "MATCH (a)<--(b) RETURN a",
        "MATCH (a)-[*1..3]->(b) RETURN a",
    ):
        assert _carries_a_compatibility_contract(cypher), cypher


def test_unordered_pagination_and_aggregates_carry_a_contract() -> None:
    """The other silently-exempt shapes review named."""
    assert _carries_a_compatibility_contract("MATCH (p:Paper) RETURN p LIMIT $limit")
    assert _carries_a_compatibility_contract("MATCH (p:Paper) RETURN p SKIP $o")
    assert _carries_a_compatibility_contract("MATCH (p:Problem) RETURN count(p)")
    assert _carries_a_compatibility_contract("CALL apoc.path.expand(n, '>', '', 1, 3)")


def test_a_single_node_key_fetch_is_still_exempt() -> None:
    """The exemption must stay narrow, not vanish.

    A widened rule that demanded everything would make leg B noise, and noise
    is how a completeness check stops being read.
    """
    assert not _carries_a_compatibility_contract("MATCH (p:Problem {id: $id}) RETURN p")
    assert not _carries_a_compatibility_contract("MATCH (t:Topic {name: $n}) RETURN t")


def test_the_criterion_reads_cypher_not_python_source() -> None:
    """``->`` in a return annotation and ``--`` in a docstring are not traversals.

    Widening the pattern made the input matter: run it over Python source and
    every annotated function becomes a false positive.
    """
    module = ast.parse(
        'def f(x) -> None:\n'
        '    """Notes -- see above."""\n'
        '    return tx.run("MATCH (p:Problem {id: $id}) RETURN p")\n'
    )
    func = next(n for n in ast.walk(module) if isinstance(n, ast.FunctionDef))
    assert not _carries_a_compatibility_contract(_cypher_in(func))


def _router_repo_calls() -> set[str]:
    """Every ``repo.X(...)`` / ``repository.X(...)`` method name used by a router."""
    names: set[str] = set()
    for path in sorted(ROUTERS.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id in {"repo", "repository"}
            ):
                names.add(node.func.attr)
    return names


#: A repository read carries a compatibility contract when it traverses a
#: relationship, orders a page, or names a vector index. Anything else is a
#: ``MATCH (n:Label {key})`` fetch by primary key, which the projection cannot
#: reorder or re-shape and which the probes could not express anything about.
#:
#: This *is* the exemption rule. An earlier version stated the same criterion in
#: a docstring and then hand-maintained a 30-name allow-list beside it, and the
#: list did not obey its own criterion: review found ``get_topic_children``
#: (traverses ``SUBTOPIC_OF``, orders on ``c.name``) and ``get_topic_tree``
#: (orders on ``t.name``) inside it, and auditing the rest turned up three more
#: (``get_topic_by_name``'s ``CASE t.level`` tie-break, ``get_model_by_name``,
#: ``get_method_by_name``), one redundant entry (``list_problems``, already
#: inventoried), and two names that are not repository methods at all
#: (``get_citation_counts``, ``list_topics``) and so exempted nothing.
#:
#: The scan was sound; the hand-maintained list was the leak. Computing the
#: exemption from the criterion removes the leak and every future variant of
#: it: there is no longer a place to write an exemption that the criterion does
#: not justify.
#: Any Cypher relationship syntax, typed or not.
#:
#: The first version required a typed ``-[:TYPE``, which silently exempted
#: ``(p)--(n)``, ``-->``, ``-[r]-`` and variable-length forms — and the two
#: queries this harness itself calls its widest leak surface
#: (``GET /api/graph``'s ``-[r]->`` and ``/graph/node/{id}``'s
#: ``-[r]-(neighbor)``) are untyped. They were safe only because they sit
#: inline in a router where leg A has no exemption at all: safety by
#: coincidence of placement, not by the rule. Widened so the rule is what makes
#: them safe.
_RELATIONSHIP_PATTERN = re.compile(r"-\[|\]-|<--|-->|--(?!\s*$)")

#: Multi-row shapes that carry a contract even with no ORDER BY: pagination
#: (spec U-8 — the order is incidental, but the *page* is still a contract),
#: aggregates, and apoc procedures.
_MULTI_ROW_PATTERN = re.compile(
    r"\bLIMIT\b|\bSKIP\b|\b(count|collect|sum|avg|min|max)\s*\(|apoc\.", re.IGNORECASE
)


def _carries_a_compatibility_contract(body: str) -> bool:
    """True unless the query is a single-node fetch by primary key.

    Stated as "what is exempt" rather than "what counts", because the
    exemption is the narrow half: a ``MATCH (n:Label {key}) RETURN n`` cannot
    be reordered, re-paginated or re-shaped by the projection, so the probes
    could express nothing about it. Everything else can.
    """
    return bool(
        _RELATIONSHIP_PATTERN.search(body)
        or "ORDER BY" in body.upper()
        or "db.index.vector.queryNodes" in body
        or _MULTI_ROW_PATTERN.search(body)
    )


def _non_docstring_strings(node: ast.AST) -> str:
    """Every string literal in a function except docstrings, joined.

    Write detection must see *all* of them. A query assembled from f-string
    fragments can put ``MATCH`` in one literal and ``CREATE``/``SET`` in
    another -- ``assign_entity_to_topic`` does exactly that -- so filtering to
    Cypher-looking literals first would hide the write clause and
    misclassify a mutation as a read.
    """
    docstrings = {
        id(inner.body[0].value)
        for inner in ast.walk(node)
        if isinstance(inner, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Module))
        and inner.body
        and isinstance(inner.body[0], ast.Expr)
        and isinstance(inner.body[0].value, ast.Constant)
        and isinstance(inner.body[0].value.value, str)
    }
    return "\n".join(
        child.value
        for child in ast.walk(node)
        if isinstance(child, ast.Constant)
        and isinstance(child.value, str)
        and id(child) not in docstrings
    )


def _repository_reads() -> dict[str, bool]:
    """Method name -> whether its body issues a graph read at all."""
    source = REPOSITORY.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(REPOSITORY))
    out: dict[str, bool] = {}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        out[node.name] = _is_read(_non_docstring_strings(node))
    return out


def _cypher_in(node: ast.AST) -> str:
    """Every string literal inside a function, joined.

    The criterion runs over the *Cypher*, not the Python source: a ``->`` in a
    return annotation and a ``--`` in a docstring would both false-positive
    against the widened relationship pattern.
    """
    docstrings = {
        id(inner.body[0].value)
        for inner in ast.walk(node)
        if isinstance(inner, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Module))
        and inner.body
        and isinstance(inner.body[0], ast.Expr)
        and isinstance(inner.body[0].value, ast.Constant)
        and isinstance(inner.body[0].value.value, str)
    }
    return "\n".join(
        child.value
        for child in ast.walk(node)
        if isinstance(child, ast.Constant)
        and isinstance(child.value, str)
        and id(child) not in docstrings
        # Only Cypher: prose that happens to contain "--" is not a traversal,
        # and the widened pattern makes that distinction load-bearing.
        and ("MATCH (" in child.value or "db.index.vector.queryNodes" in child.value)
    )


def _repository_reads_carrying_a_contract() -> set[str]:
    """The subset that must be inventoried, computed from the criterion."""
    source = REPOSITORY.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(REPOSITORY))
    out: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        # Write detection over every literal; the contract criterion over the
        # Cypher-looking ones only. Writes are out of scope by declaration, and
        # a mutation's `MATCH ... DELETE` prologue would otherwise demand a
        # read-path entry for a surface the harness deliberately does not
        # certify.
        if _is_read(_non_docstring_strings(node)) and _carries_a_compatibility_contract(
            _cypher_in(node)
        ):
            out.add(node.name)
    return out


def test_the_router_call_scan_found_calls() -> None:
    calls = _router_repo_calls()
    assert len(calls) >= 15, f"only {len(calls)} repository calls found in routers"
    assert any(_repository_reads().values()), "no repository method reads the graph"
    contract = _repository_reads_carrying_a_contract()
    assert contract, "the criterion matched nothing - the classifier is broken"


def test_the_exemption_criterion_discriminates() -> None:
    """The criterion must separate the two classes, not wave everything through.

    Obligation 5 for the rule that replaced the allow-list: a predicate that
    returned True for everything would make leg B demand the world, and one
    returning False for everything would make it demand nothing. Both are
    checked against real repository bodies.
    """
    reads = {name for name, is_read in _repository_reads().items() if is_read}
    contract = _repository_reads_carrying_a_contract()
    assert contract < reads, "every read allegedly carries a contract - rule too broad"
    assert len(contract) >= 10, f"only {len(contract)} reads carry a contract - too narrow"
    # A key fetch is exempt; a traversal and an ordered page are not.
    assert not _carries_a_compatibility_contract("MATCH (p:Problem {id: $id}) RETURN p")
    assert _carries_a_compatibility_contract("MATCH (c:Topic)-[:SUBTOPIC_OF]->(p) RETURN c")
    assert _carries_a_compatibility_contract("MATCH (t:Topic) RETURN t ORDER BY t.name")
    assert _carries_a_compatibility_contract("CALL db.index.vector.queryNodes('x', 1, $e)")


def test_every_repository_read_reached_from_a_router_is_inventoried() -> None:
    """Leg B: the gap that hid CITES, USES_MODEL and APPLIES_METHOD.

    A router calling ``repo.get_references`` issues a ``CITES`` traversal with
    no Cypher anywhere in the router, so leg A cannot see it. ``ReadPath.via``
    is where such a read is claimed.
    """
    claimed = {name for entry in READ_PATHS for name in entry.via}
    reached = _router_repo_calls() & _repository_reads_carrying_a_contract()
    assert reached, "no router-reached repository read found - the scan is broken"
    missing = sorted(reached - claimed)
    assert missing == [], (
        "these repository methods traverse a relationship, order a page or name "
        "a vector index, are called from an API router, and no READ_PATHS entry "
        f"names them in `via`: {missing}"
    )


def test_every_vector_index_named_anywhere_is_inventoried() -> None:
    """Leg C: the check that was quantifying over a one-element set.

    Four of the six live indexes are named in ``repository.py`` and reached
    from ``/search`` routes, so scanning only the inventory's own entries said
    nothing. This scans the source for the literal names instead.
    """
    found: set[str] = set()
    for root in INDEX_SCAN_ROOTS:
        for path in sorted(root.rglob("*.py")):
            if "migration/compat" in str(path).replace("\\", "/"):
                continue  # the inventory itself is not evidence about the app
            found.update(re.findall(r"'([a-z_]+_embedding_idx)'", path.read_text(encoding="utf-8")))
    assert len(found) >= 5, f"index scan found only {sorted(found)}"
    missing = sorted(found - DEAD_VECTOR_INDEXES - vector_indexes_read())
    assert missing == [], (
        f"vector indexes the code names but the inventory does not record: {missing}"
    )


def test_the_inventory_now_covers_all_six_live_vector_indexes() -> None:
    """The positive form, so a shrinking inventory is a failure not a silence."""
    assert len(vector_indexes_read()) == 6, sorted(vector_indexes_read())
    assert vector_indexes_read() == _spec_vector_indexes()


def test_every_vector_index_the_application_names_is_recreated_by_the_projection() -> None:
    named = vector_indexes_read()
    assert named, "the inventory records no vector index - the registry is broken"
    missing = sorted(named - _spec_vector_indexes())
    assert missing == []


def test_the_ordering_keys_include_all_four_denormalised_counters() -> None:
    """Pagination order is part of the contract, and counters drive four of it.

    Spec §5.1: the counters are ORDER BY keys and legacy has no reconciler for
    Model.usage_count or Method.usage_count at all. If the projection recomputes
    them, these are the sorts that move.
    """
    keys = ordering_keys_read()
    assert keys
    counter_keys = sorted(k for k in keys if "count" in k)
    assert counter_keys == [
        "m.usage_count DESC",
        "pc.mention_count DESC",
        "rc.mention_count DESC",
    ], counter_keys


def test_labels_read_cover_the_agents_and_the_routers() -> None:
    labels = labels_read()
    assert {"Problem", "Paper", "Topic", "Author", "Model", "Method"} <= labels


@pytest.mark.parametrize("read_path", READ_PATHS, ids=lambda p: p.id)
def test_every_scoped_out_or_changed_entry_records_why(read_path: ReadPath) -> None:
    """A classification without a reason is a decision nobody can review."""
    if read_path.compat is not CompatClass.PARITY:
        assert read_path.note.strip(), f"{read_path.id} is {read_path.compat.value} with no note"
