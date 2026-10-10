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

from collections.abc import Mapping, Sequence
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
from agentic_kg.migration.ingestion.extractors import STRUCTURED_SCORING
from agentic_kg.migration.ingestion.identity import normalize_doi
from agentic_kg.migration.ingestion.source_record import SourceRecord

#: Cross-identifier namespaces the paper arm turns into `Paper` aliases, in a
#: fixed order so the alias tuple is deterministic. `doi` is always the primary
#: alias and is handled separately; these are the secondary ones a live
#: discovery response supplies. The frozen corpus carries none of them.
EXTERNAL_ID_NAMESPACES: tuple[str, ...] = ("arxiv", "semantic_scholar", "openalex")

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


def aliases_for(
    doi: str, external_ids: Mapping[str, str] | None = None
) -> tuple[EntityRef, ...]:
    """Every `Paper` alias for a `(doi, external_ids)` pair: DOI first.

    Split out from :func:`paper_aliases` so a caller that has only the
    identifiers — the live dedupe step, before a record exists — builds the same
    alias set the candidate builder will. One definition, so dedupe and identity
    cannot disagree.
    """
    refs = [paper_alias(doi)]
    ids = external_ids or {}
    for namespace in EXTERNAL_ID_NAMESPACES:
        key = ids.get(namespace)
        if key and key.strip():
            refs.append(
                EntityRef(entity_type="Paper", namespace=namespace, key=key.strip())
            )
    return tuple(refs)


def paper_aliases(paper: SourceRecord) -> tuple[EntityRef, ...]:
    """Every `Paper` alias the record knows: DOI first, then cross-ids.

    The DOI is the primary identity (spec §3.3) and is always present on this
    path, which is why `run_migration` filters out papers without one. The
    secondary namespaces are emitted only when the source carried them — a null
    alias is not an alias, and inventing one would make the same paper appear
    under two keys. The frozen corpus supplies none, so its alias tuple is
    exactly the one-element tuple it has always been.
    """
    return aliases_for(paper.doi, paper.external_ids)


def paper_semantic_key(doi: str) -> str:
    """`paper/doi/<doi>` — never a UUID (spec §3.1, AC-11)."""
    return f"paper/doi/{normalize_doi(doi)}"


def record_coordinates(paper: SourceRecord) -> SourceCoordinates:
    """Where this paper's identity was read from.

    For the frozen corpus the locator names the committed importer-output file
    relative to the repo, not an absolute path: an absolute path is a property
    of one checkout and would make the coordinate unreproducible anywhere else.
    For a live paper the record owns its locator (the discovery-API reference),
    so the same accessor serves both producers.
    """
    return SourceCoordinates(
        source_type=paper.record_source_type,
        locator=paper.record_locator,
        fragment=paper.record_fragment,
    )


def paper_evidence_id(paper: SourceRecord) -> str:
    """Deterministic evidence id for one paper's source record.

    Explicit, via `stable_suffix`, for the reason the module docstring gives:
    the `Evidence` default is a random ULID and re-collection would duplicate.
    The documented `source:key@window` id scheme is **not implemented** upstream
    (it is described in a docstring and appears nowhere in the code), so this
    follows the scheme KGIS's own extraction path uses instead.
    """
    return "ev_" + stable_suffix(
        paper.record_source_type, normalize_doi(paper.doi), paper.record_identity
    )


def build_paper_evidence(paper: SourceRecord, *, observed_at: datetime) -> Evidence:
    coords = record_coordinates(paper)
    return present_evidence(
        evidence_id=paper_evidence_id(paper),
        source_type=coords.source_type,
        source_locator=f"{coords.locator}#{coords.fragment}",
        observed_at=observed_at,
        provenance=Provenance(
            source=coords.locator, source_ref=coords.fragment, actor=STRUCTURED_PRODUCER
        ),
        content=paper.evidence_content,
        payload_hash="b2:" + stable_suffix(*paper.evidence_hash_parts()),
    )


def build_paper_candidates(paper: SourceRecord, context: BuildContext) -> list[Candidate]:
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
    aliases = paper_aliases(paper)
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
        aliases=aliases,
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
    "EXTERNAL_ID_NAMESPACES",
    "aliases_for",
    "build_paper_candidates",
    "build_paper_evidence",
    "paper_alias",
    "paper_aliases",
    "paper_evidence_id",
    "paper_semantic_key",
    "record_coordinates",
]
