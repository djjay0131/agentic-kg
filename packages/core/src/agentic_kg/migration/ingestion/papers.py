"""Paper candidates — the structured arm (spec §3.3, `producer="kgis.structured"`).

A Paper is not extracted. Its title, DOI, year and venue are a deterministic
read of a source record, so it takes the structured path and carries
`extraction_confidence=1.0` — *we are certain we read the row correctly* —
while its `source_reliability` reflects the curated identifier it came from.
An LLM never sees it, which is why this module has no prompt and no client.

Two legacy properties are re-modelled here, both per spec §3.3:

* **`is_stub` disappears as a flag.** A stub is an `EntityCandidate` carrying
  its alias and no attribute assertions, so "stub" becomes derivable ("has no
  `title` assertion") and the legacy tri-state (`true` / `false` / absent) goes
  away. This module emits no stubs — the committed corpus has full records —
  but it emits nothing that would have to be unwound to support one.
* **`citation_count` stops being one name with two meanings.** Today
  `repository.py:3092` writes the in-graph inbound degree and `importer.py:224`
  overwrites it with the source API's global count. Here the source value would
  be `source_citation_count`; the committed importer output does not record it,
  so no such assertion is emitted rather than a zero being invented.

Evidence for a Paper is `present_evidence` over the source record, with an
explicit `evidence_id` derived through `stable_suffix`. The `Evidence` default
is a random ULID, and taking it would destroy re-collection idempotency: the
same record read twice would pile up two evidence rows that nothing could tell
apart.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from agentic_kg.migration.ingestion._contracts import (
    AttributeAssertionCandidate,
    BuildContext,
    Candidate,
    EntityCandidate,
    EntityRef,
    Evidence,
    EvidenceRef,
    EvidenceRelationship,
    Provenance,
    SourceCoordinates,
    present_evidence,
    stable_suffix,
)
from agentic_kg.migration.ingestion.corpus import CorpusPaper
from agentic_kg.migration.ingestion.extractors import STRUCTURED_SCORING
from agentic_kg.migration.ingestion.identity import normalize_doi

#: Producer for the structured arm. Distinct from `kgis.extraction:*` so a
#: consumer can tell a deterministic field read from a model's reading.
STRUCTURED_PRODUCER = "kgis.structured"

#: `source_type` for the source record, as opposed to the paper's PDF text.
RECORD_SOURCE_TYPE = "importer_record"


def paper_alias(doi: str) -> EntityRef:
    """`Paper:doi:<lowercased DOI>` — the primary alias (spec §3.3).

    Non-empty aliases are mandatory on an `EntityCandidate`: "an identity is
    made of its aliases" (`kg_contracts/identity.py`). The arXiv / OpenAlex /
    Semantic Scholar aliases the spec also lists are emitted when the source
    supplies them; the committed corpus supplies only DOIs.
    """
    return EntityRef(entity_type="Paper", namespace="doi", key=normalize_doi(doi))


def paper_semantic_key(doi: str) -> str:
    """`paper/doi/<doi>` — never a UUID (spec §3.1, AC-11)."""
    return f"paper/doi/{normalize_doi(doi)}"


def record_coordinates(paper: CorpusPaper) -> SourceCoordinates:
    """Where this paper's identity was read from.

    The locator names the committed importer-output file relative to the repo,
    not an absolute path: an absolute path is a property of one checkout and
    would make the coordinate unreproducible anywhere else.
    """
    return SourceCoordinates(
        source_type=RECORD_SOURCE_TYPE,
        locator=f"docs/ground-truth/importer-output/{paper.importer_path.name}",
        fragment="paper",
    )


def paper_evidence_id(paper: CorpusPaper) -> str:
    """Deterministic evidence id for one paper's source record.

    Explicit, via `stable_suffix`, for the reason the module docstring gives:
    the `Evidence` default is a random ULID and re-collection would duplicate.
    The documented `source:key@window` id scheme is **not implemented** upstream
    (it is described in a docstring and appears nowhere in the code), so this
    follows the scheme KGIS's own extraction path uses instead.
    """
    return "ev_" + stable_suffix(
        RECORD_SOURCE_TYPE, normalize_doi(paper.doi), paper.importer_path.name
    )


def build_paper_evidence(paper: CorpusPaper, *, observed_at: datetime) -> Evidence:
    coords = record_coordinates(paper)
    return present_evidence(
        evidence_id=paper_evidence_id(paper),
        source_type=coords.source_type,
        source_locator=f"{coords.locator}#{coords.fragment}",
        observed_at=observed_at,
        provenance=Provenance(
            source=coords.locator, source_ref=coords.fragment, actor=STRUCTURED_PRODUCER
        ),
        content=f"{paper.title} ({paper.year}) doi:{paper.doi}",
        payload_hash="b2:" + stable_suffix(paper.title, str(paper.year), paper.doi),
    )


def build_paper_candidates(paper: CorpusPaper, context: BuildContext) -> list[Candidate]:
    """One `EntityCandidate` plus one assertion per known attribute.

    Attributes are emitted only when the record carries them: "a null property
    is not a property", and asserting `year=None` would be asserting that the
    paper has no year rather than that we do not know it.

    Every attribute is untimed. Spec §3.3 makes `source_citation_count` the one
    exception — it is time-varying and carries a `ValidPeriod` — but the
    committed importer output records no citation count, so that assertion is
    absent here rather than emitted with a fabricated window.
    """
    alias = paper_alias(paper.doi)
    semantic_key = paper_semantic_key(paper.doi)
    coords = record_coordinates(paper)
    ref = EvidenceRef(
        evidence_id=paper_evidence_id(paper),
        relationship=EvidenceRelationship.DERIVED_FROM,
    )
    common = {
        "graph_id": context.graph_id,
        "producer": context.producer,
        "producer_run_id": context.producer_run_id,
        "ontology_version": context.ontology_version,
        "source_coordinates": coords,
        "scores": STRUCTURED_SCORING.to_scores(),
        "created_at": context.clock.now(),
        "evidence_refs": (ref,),
    }

    entity = EntityCandidate(
        candidate_id=context.ids.candidate_id(
            graph_id=context.graph_id, candidate_kind="entity", semantic_key=semantic_key
        ),
        trace_id=context.ids.trace_id(
            run_id=context.producer_run_id,
            graph_id=context.graph_id,
            candidate_kind="entity",
            semantic_key=semantic_key,
        ),
        semantic_key=semantic_key,
        entity_type="Paper",
        aliases=(alias,),
        display_name=paper.title,
        content_hash="b2:" + stable_suffix(alias.render(), paper.title),
        **common,
    )

    candidates: list[Candidate] = [entity]
    attributes: Sequence[tuple[str, object]] = tuple(
        (name, value)
        for name, value in (("title", paper.title), ("year", paper.year))
        if value is not None
    )
    for name, value in attributes:
        attr_key = f"{semantic_key}/{name}"
        candidates.append(
            AttributeAssertionCandidate(
                candidate_id=context.ids.candidate_id(
                    graph_id=context.graph_id,
                    candidate_kind="attribute_assertion",
                    semantic_key=attr_key,
                ),
                trace_id=context.ids.trace_id(
                    run_id=context.producer_run_id,
                    graph_id=context.graph_id,
                    candidate_kind="attribute_assertion",
                    semantic_key=attr_key,
                ),
                semantic_key=attr_key,
                subject=alias,
                attribute=name,
                value=value,
                content_hash="b2:" + stable_suffix(attr_key, str(value)),
                **common,
            )
        )
    return candidates


__all__ = [
    "RECORD_SOURCE_TYPE",
    "STRUCTURED_PRODUCER",
    "build_paper_candidates",
    "build_paper_evidence",
    "paper_alias",
    "paper_evidence_id",
    "paper_semantic_key",
    "record_coordinates",
]
