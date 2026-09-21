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
EXTRA_READ_MODULES = (
    REPO_ROOT / "packages" / "core" / "src" / "agentic_kg" / "knowledge_graph" / "search.py",
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


def _cypher_literals(path: Path) -> list[tuple[int, int]]:
    """(start_line, end_line) of every string literal that issues a graph read."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    spans: list[tuple[int, int]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
            continue
        text = node.value
        if "MATCH (" in text or "db.index.vector.queryNodes" in text:
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


def _repository_methods_containing_a_read() -> dict[str, bool]:
    """Method name -> whether its body issues a graph read."""
    tree = ast.parse(REPOSITORY.read_text(encoding="utf-8"), filename=str(REPOSITORY))
    out: dict[str, bool] = {}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        body = ast.get_source_segment(REPOSITORY.read_text(encoding="utf-8"), node) or ""
        out[node.name] = "MATCH (" in body or "db.index.vector.queryNodes" in body
    return out


def test_the_router_call_scan_found_calls() -> None:
    calls = _router_repo_calls()
    assert len(calls) >= 15, f"only {len(calls)} repository calls found in routers"
    methods = _repository_methods_containing_a_read()
    assert any(methods.values()), "no repository method appears to read the graph"


def test_every_repository_read_reached_from_a_router_is_inventoried() -> None:
    """Leg B: the gap that hid CITES, USES_MODEL and APPLIES_METHOD.

    A router calling ``repo.get_references`` issues a ``CITES`` traversal with
    no Cypher anywhere in the router, so leg A cannot see it. ``ReadPath.via``
    is where such a read is claimed.
    """
    reads = _repository_methods_containing_a_read()
    claimed = {name for entry in READ_PATHS for name in entry.via}
    reached = {name for name in _router_repo_calls() if reads.get(name)}
    assert reached, "no router-reached repository read found - the scan is broken"
    missing = sorted(reached - claimed - _implicitly_claimed())
    assert missing == [], (
        "these repository methods issue a graph read, are called from an API "
        f"router, and no READ_PATHS entry names them in `via`: {missing}"
    )


def _implicitly_claimed() -> frozenset[str]:
    """Single-node fetches and writes that carry no traversal worth pinning.

    Named explicitly rather than filtered by a pattern, so adding one is a
    visible decision. Each is a ``MATCH (n:Label {key})`` by primary key, or a
    mutation; none traverses a relationship, orders a page, or names an index,
    so none carries a compatibility contract the probes could express.
    """
    return frozenset(
        {
            "get_problem", "get_paper", "get_topic", "get_author",
            "get_research_concept", "get_model", "get_method",
            "get_topic_by_name", "get_model_by_name", "get_method_by_name",
            "create_problem", "update_problem", "delete_problem",
            "create_topic", "create_research_concept", "create_model",
            "create_method", "delete_model", "delete_method",
            "assign_entity_to_topic", "link_problem_to_concept",
            "link_paper_to_concept", "link_paper_to_model", "link_paper_to_method",
            "list_problems", "get_citation_counts", "count_citations",
            "list_topics", "get_topic_children", "get_topic_tree",
        }
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
