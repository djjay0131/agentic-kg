"""The research ontology, declared as call-site data (spec §3.1).

The ontology seam is data, not a KGIS edit: `Ontology(...)` plus configured
builders plus an `ExtractorConfig` per type. Nothing here patches, subclasses
or otherwise reaches into `kgis`.

**All three term sets are non-empty, and that is a correctness requirement.**
`Ontology.declares_entity_type` is `not self.entity_types or term in
self.entity_types` (`kgis/ontology.py:67,70,73`) — an **empty set means
unconstrained, not forbidden**. Declaring `entity_types` while leaving
`relation_types` empty would silently admit any relation at all, and the
resulting run would look strictly validated while checking two-thirds of
nothing. `test_ontology.py` asserts all three are non-empty for exactly that
reason.

**The extraction path is ontology-unchecked by default and must be told
otherwise.** `ExtractionPipeline` takes no `ontology=` argument; its default
`candidate_validator` is `OntologyCandidateValidator(None)`, which admits every
term, while `IngestPipeline` defaults to `ontology_strict=True`. The asymmetry
is the easiest thing in the adoption to get wrong, so
:func:`research_candidate_validator` exists and the pipeline builder in
`pipeline.py` has no way to construct a run without it (AC-14).
"""

from __future__ import annotations

from agentic_kg.migration.ingestion._contracts import Ontology, OntologyCandidateValidator

#: Spec §3.1: `^[a-z][a-z0-9-]*$`. **Not** `agentic_kg` — underscores are
#: illegal, and while KGIS itself does not validate `graph_id`, KGCS rejects a
#: malformed one as `BAD_DATA` (`kgcs/validation.py`, `GraphIdWellFormedRule`).
#: Getting this wrong surfaces one system later than it is introduced, which is
#: why it is a constant here rather than a caller's string.
GRAPH_ID = "research"

#: Bumped on any term-set change. Rides every candidate.
ONTOLOGY_VERSION = "2026-09-18.1"

#: The six domain types the mapping spec approves for this path, plus the two
#: the corpus's relations need as endpoints.
#:
#: `Author` is declared but this PR configures no Author extractor — see the
#: module note in `extractors.py`. Declaring it costs nothing and leaving it out
#: would make a later Author candidate fail validation for a reason unrelated to
#: the bug that produced it.
ENTITY_TYPES = frozenset(
    {"Paper", "Author", "Topic", "ResearchConcept", "Model", "Method", "Problem"}
)

#: Spec §3.1. `SOLVED_BY` and `HAS_TOPIC` are **deliberately absent** (§5.5,
#: AC-15); `INSTANCE_OF` is absent because under the mapping it ceases to be a
#: relation and becomes merge lineage (§3.6).
RELATION_TYPES = frozenset(
    {
        "AUTHORED_BY",
        "CITES",
        "RESEARCHES",
        "BELONGS_TO",
        "SUBTOPIC_OF",
        "DISCUSSES",
        "INVOLVES_CONCEPT",
        "USES_MODEL",
        "APPLIES_METHOD",
        "EXTRACTED_FROM",
        "EXTENDS",
        "CONTRADICTS",
        "DEPENDS_ON",
        "REFRAMES",
    }
)

#: Spec §3.1. Note `source_citation_count`, not `citation_count`: today
#: `repository.py:3092` writes the in-graph inbound degree under that name and
#: `importer.py:224` overwrites it with the source API's global count — one name
#: carrying two meanings. Under the mapping the source API's value is this
#: attribute and the in-graph degree is computed by the projector.
ATTRIBUTES = frozenset(
    {
        "title",
        "abstract",
        "year",
        "venue",
        "pdf_url",
        "arxiv_id",
        "source_citation_count",
        "level",
        "description",
        "architecture",
        "model_type",
        "year_introduced",
        "introducing_paper_doi",
        "method_type",
        "statement",
        "quoted_text",
        "section",
        "assumption",
        "constraint",
        "dataset",
        "metric",
        "baseline",
    }
)

RESEARCH_ONTOLOGY = Ontology(
    version=ONTOLOGY_VERSION,
    entity_types=ENTITY_TYPES,
    relation_types=RELATION_TYPES,
    attributes=ATTRIBUTES,
)

#: Terms the spec forbids from appearing in any write vocabulary, ontology
#: declaration, Cypher string or guardrail list (§5.5, AC-15). Held here so the
#: regression guard has one place to read them from.
FORBIDDEN_RELATION_TYPES = frozenset({"SOLVED_BY", "HAS_TOPIC"})


def research_candidate_validator(*, strict: bool = True) -> OntologyCandidateValidator:
    """The validator every shadow run must be constructed with (AC-14).

    `strict=True` rejects an undeclared term as `UNSUPPORTED_ONTOLOGY`, which is
    a distinct failure kind from `BAD_DATA` precisely so it can be alerted and
    retried differently: the data may be perfect and the ontology simply behind.
    """
    return OntologyCandidateValidator(RESEARCH_ONTOLOGY, strict=strict)


__all__ = [
    "ATTRIBUTES",
    "ENTITY_TYPES",
    "FORBIDDEN_RELATION_TYPES",
    "GRAPH_ID",
    "ONTOLOGY_VERSION",
    "RELATION_TYPES",
    "RESEARCH_ONTOLOGY",
    "research_candidate_validator",
]
