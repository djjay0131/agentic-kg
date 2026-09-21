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
        if path.snippet not in source.read_text(encoding="utf-8"):
            offenders.append(f"{path.id}: snippet not found in {path.source_file}")
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
    unprobed = sorted(
        p.id for p in paths_for_class(CompatClass.PARITY) if p.id not in probed
    )
    assert unprobed == ["search.semantic"], (
        "every PARITY read path must have a deterministic probe. "
        f"Unprobed: {unprobed}. 'search.semantic' is the sole exception: it "
        "calls db.index.vector.queryNodes with an embedding from a live "
        "provider, so it is not deterministic and not free. Its contract "
        "(the index name) is asserted against the spec instead."
    )


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
