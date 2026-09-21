"""One configured `ExtractorConfig` per domain type — call-site data, no KGIS edits.

KGIS's own framing is that "an extractor = (entity schema, prompt template,
model config) registered per graph", and an `ExtractorConfig` is exactly that
tuple. Everything in this module is therefore *configuration*: the builders are
`kgis.builders` classes used verbatim, the parser is the shipped
`JsonItemsParser` default, and the prompts ask for the JSON shape that parser
already accepts.

**What this PR configures, and what it deliberately does not.**

| Type | Kind | Producer |
|---|---|---|
| `ResearchConcept` | `EntityCandidate` + 1 assertion | `kgis.extraction:research_concept` |
| `Model` | `EntityCandidate` + 4 assertions | `kgis.extraction:model` |
| `Method` | `EntityCandidate` + 2 assertions | `kgis.extraction:method` |
| `Topic` | `EntityCandidate` in the `taxonomy` namespace | `kgis.extraction:topic` |
| `Problem` | `EntityCandidate` keyed on `K` + 3 assertions | `kgis.extraction:problem` |
| `Paper` | `EntityCandidate` + 3 assertions, **structured** | `kgis.structured` |

Not configured here, each for a stated reason rather than by omission:

* **`Author`.** The corpus text files are keep-listed to abstract /
  introduction / methods / experiments; the author block is not in them, so an
  Author extractor would have nothing to read. Spec §3.3's `paper_local`
  identity needs the source *record*, which is the structured path's input, and
  this PR's structured path carries only what the committed importer output
  supplies. Declaring `Author` in the ontology while configuring no extractor is
  the honest state.
* **Relations** (`RESEARCHES`, `CITES`, `EXTRACTED_FROM`). A
  `RelationCandidate` needs both endpoints as `EntityRef`s in one record, and
  the LLM's per-item JSON carries only one side. Emitting them means either
  a second pass over the ledger or teaching the prompt to repeat the paper's
  DOI on every item — the latter invites the model to hallucinate an identity,
  which is the one thing identity must never depend on. Left to a later PR.

**`MIN_*_CONFIDENCE` thresholds are not applied here.** Today they are module
constants in `kg_integration_v2.py` that `continue` past an assignment,
silently. Under the mapping they become `ConfidencePolicy` fields and a dropped
assignment becomes a *routed* one with a recorded reason. Re-implementing the
silent drop in the shadow path would bake in the behaviour the adoption exists
to remove, so every extracted item becomes a candidate and the confidence rides
on `extraction_confidence` for a policy to read later.

**No candidate carries a `confidence=` kwarg** (AC-10). `CandidateScores` is
`extra="forbid"`, so one would be a `ValidationError` rather than a silent
pass-through; `SourceScoring` supplies `extraction_confidence` and
`source_reliability` on every candidate, and the parser lifts a model-reported
per-item confidence onto the former.
"""

from __future__ import annotations

from collections.abc import Sequence

from agentic_kg.migration.ingestion._contracts import (
    AttributeCandidateBuilder,
    BuildContext,
    Candidate,
    CompositeCandidateBuilder,
    EntityCandidateBuilder,
    ExtractorConfig,
    NormalizedRecord,
    SourceScoring,
)
from agentic_kg.migration.ingestion.documents import doi_from_locator
from agentic_kg.migration.ingestion.identity import join_key

#: The model the committed replay fixture was captured against. Recorded on
#: every candidate's `source_passage` representation and inside every evidence
#: id, so a re-extraction under a different model produces *different* evidence
#: rather than silently overwriting the old.
MODEL_ID = "gpt-4o"
MODEL_VERSION = "2024-08-06"

#: Reliability of a paper's own full text as a source, per extractor arm. One
#: number for the whole arm because every extractor reads the same PDF text —
#: what differs between them is how confidently the model read it, which is the
#: other axis. Spec §8 U-2 records that the initial value is a policy choice and
#: is not derivable from legacy data; it is deliberately not 1.0, which would
#: assert that an LLM reading of a paper is as reliable as a DOI lookup.
EXTRACTION_SOURCE_RELIABILITY = 0.75

#: A deterministic field read of a committed source record. 1.0 on both axes is
#: honest here and only here: we are certain we read the row correctly, and the
#: row is a curated identifier rather than a judgment.
STRUCTURED_SCORING = SourceScoring(source_reliability=1.0, extraction_confidence=1.0)

EXTRACTION_SCORING = SourceScoring(
    source_reliability=EXTRACTION_SOURCE_RELIABILITY,
    # The floor for an item whose JSON omits `confidence`. Mirrors the legacy
    # schemas' `confidence: float = Field(ge=0, le=1, default=0.8)`, so an item
    # the model declined to score lands where the legacy path would have put it
    # rather than at a fabricated 1.0.
    extraction_confidence=0.8,
)

_PROMPT_TAIL = (
    "\n\nReturn ONLY a JSON array. Each element is an object with the keys named "
    "above and a `confidence` between 0 and 1. Return [] if the passage contains "
    "none.\n\nPassage:\n{text}"
)


class PaperScopedKeyBuilder:
    """Injects a paper-scoped `K` onto a record, then delegates to a real builder.

    `EntityCandidateBuilder` reads its key from a field of the record, and the
    record's fields are whatever the model's JSON carried — which cannot include
    the paper's DOI, because asking a model to restate an identifier is asking
    it to hallucinate one. The DOI *is* available, on
    `record.coordinates.locator` (see `PaperDocument.locator`), which is the one
    channel that survives the trip from document to builder.

    So this is a `CandidateBuilder` (the Protocol KGIS explicitly invites
    implementations of) that computes `K` from the coordinates plus two fields
    of the record, writes it into a synthetic field, and hands the enriched
    record to a normal `EntityCandidateBuilder`. It reimplements no KGIS
    behaviour — construction, semantic keys and contract validation all still
    happen in `kgis.builders`.

    `K` is computed by :func:`join_key`, the same function §6.4.1's migration
    join calls. That is what "the join key and the identity key agree by
    construction" means operationally: not two functions kept in step, one
    function called twice.
    """

    #: The synthetic field `K` lands in. Leading underscore so it cannot collide
    #: with a key the model emitted.
    KEY_FIELD = "_paper_span_key"

    def __init__(
        self,
        *,
        entity_type: str,
        surface_field: str,
        quote_field: str,
        display_name_field: str,
        property_fields: Sequence[str] = (),
        attribute_fields: Sequence[str] = (),
    ) -> None:
        self._entity_type = entity_type
        self._surface_field = surface_field
        self._quote_field = quote_field
        entity = EntityCandidateBuilder(
            namespace="paper_span",
            key_field=self.KEY_FIELD,
            entity_type=entity_type,
            display_name_field=display_name_field,
            property_fields=property_fields,
        )
        self._entity = entity
        builders: list[object] = [entity]
        if attribute_fields:
            builders.append(
                AttributeCandidateBuilder(
                    subject=entity, attribute_fields=tuple(attribute_fields)
                )
            )
        self._inner = CompositeCandidateBuilder(builders)  # type: ignore[arg-type]

    @property
    def required_fields(self) -> tuple[str, ...]:
        """The fields the *model* must supply.

        `KEY_FIELD` is excluded on purpose: it is synthesised by this builder,
        so declaring it required would make the pipeline demand a field no
        source can ever provide.
        """
        return (self._surface_field, self._quote_field)

    def build(self, record: NormalizedRecord, context: BuildContext) -> Sequence[Candidate]:
        doi = doi_from_locator(record.coordinates.locator)
        surface = record.values.get(self._surface_field)
        quote = record.values.get(self._quote_field)
        if not isinstance(surface, str) or not surface.strip():
            raise ValueError(f"{self._surface_field!r} is missing or empty")
        if not isinstance(quote, str) or not quote.strip():
            raise ValueError(f"{self._quote_field!r} is missing or empty")
        key = join_key(
            entity_type=self._entity_type, doi=doi, surface=surface, quoted_text=quote
        )
        enriched = record.model_copy(
            update={"values": {**record.values, self.KEY_FIELD: key.paper_span}}
        )
        return self._inner.build(enriched, context)


def _surface_entity_builder(
    *,
    entity_type: str,
    property_fields: Sequence[str] = (),
    attribute_fields: Sequence[str] = (),
    namespace: str = "surface",
) -> CompositeCandidateBuilder:
    """Entity keyed on its normalized surface form, plus its attributes.

    Spec §3.3: legacy identity for `ResearchConcept` / `Model` / `Method` is
    "cosine >= 0.90 against whichever node happened to arrive first", which
    depends on ingestion order, embedding-model version and the threshold of the
    day. The replacement is the normalized surface form, which is reproducible
    from the source text. Deciding that "attention mechanism" and
    "self-attention" are one entity becomes a KGCS ER decision that is audited,
    replayable and compensable — and that substitution is the point of the whole
    adoption.

    The prompts therefore ask for a `key` field holding `NORM(name)`; see
    :func:`_normalized_key_builder` for why the normalization does not happen in
    the prompt.
    """
    entity = EntityCandidateBuilder(
        namespace=namespace,
        key_field="_surface_key",
        entity_type=entity_type,
        display_name_field="name",
        property_fields=property_fields,
    )
    builders: list[object] = [entity]
    if attribute_fields:
        builders.append(
            AttributeCandidateBuilder(
                subject=entity, attribute_fields=tuple(attribute_fields)
            )
        )
    return CompositeCandidateBuilder(builders)  # type: ignore[arg-type]


class NormalizedSurfaceBuilder:
    """Writes `NORM(name)` into `_surface_key`, then delegates.

    The normalization is done here rather than asked of the model for the same
    reason `NORM` has one definition: a prompt instructing a model to NFKC-fold,
    rejoin line-broken hyphens, collapse whitespace and casefold is a *second*
    implementation of `NORM`, evaluated by a language model, on every call. It
    would disagree with :func:`norm` at some rate nobody measures, and every
    disagreement is a candidate whose identity key and migration join key differ
    — the exact 13% loss §3.1 documents.
    """

    def __init__(self, inner: CompositeCandidateBuilder, *, name_field: str = "name") -> None:
        self._inner = inner
        self._name_field = name_field

    @property
    def required_fields(self) -> tuple[str, ...]:
        return (self._name_field,)

    def build(self, record: NormalizedRecord, context: BuildContext) -> Sequence[Candidate]:
        from agentic_kg.migration.ingestion.identity import norm

        name = record.values.get(self._name_field)
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"{self._name_field!r} is missing or empty")
        enriched = record.model_copy(
            update={"values": {**record.values, "_surface_key": norm(name)}}
        )
        return self._inner.build(enriched, context)


def research_concept_extractor() -> ExtractorConfig:
    return ExtractorConfig(
        extractor_id="research_concept",
        target_type="ResearchConcept",
        builder=NormalizedSurfaceBuilder(
            _surface_entity_builder(
                entity_type="ResearchConcept", attribute_fields=("description",)
            )
        ),
        prompt_template=(
            "You are cataloguing the research concepts a computer-science paper "
            "discusses. For each distinct concept the passage names, emit an object "
            "with `name` (the surface form as written), `description` (one sentence, "
            "or null), and `quoted_text` (the verbatim sentence naming it)."
            + _PROMPT_TAIL
        ),
        model_id=MODEL_ID,
        model_version=MODEL_VERSION,
        extractor_version="1",
        prompt_version="1",
        scoring=EXTRACTION_SCORING,
    )


def model_extractor() -> ExtractorConfig:
    return ExtractorConfig(
        extractor_id="model",
        target_type="Model",
        builder=NormalizedSurfaceBuilder(
            _surface_entity_builder(
                entity_type="Model",
                attribute_fields=(
                    "description",
                    "architecture",
                    "model_type",
                    "year_introduced",
                ),
            )
        ),
        prompt_template=(
            "You are cataloguing the named machine-learning models a computer-science "
            "paper uses or introduces. For each, emit an object with `name`, "
            "`description`, `architecture`, `model_type`, `year_introduced` (integer "
            "or null) and `quoted_text`." + _PROMPT_TAIL
        ),
        model_id=MODEL_ID,
        model_version=MODEL_VERSION,
        extractor_version="1",
        prompt_version="1",
        scoring=EXTRACTION_SCORING,
    )


def method_extractor() -> ExtractorConfig:
    return ExtractorConfig(
        extractor_id="method",
        target_type="Method",
        builder=NormalizedSurfaceBuilder(
            _surface_entity_builder(
                entity_type="Method", attribute_fields=("description", "method_type")
            )
        ),
        prompt_template=(
            "You are cataloguing the methods and techniques a computer-science paper "
            "applies. For each, emit an object with `name`, `description`, "
            "`method_type` and `quoted_text`." + _PROMPT_TAIL
        ),
        model_id=MODEL_ID,
        model_version=MODEL_VERSION,
        extractor_version="1",
        prompt_version="1",
        scoring=EXTRACTION_SCORING,
    )


def topic_extractor() -> ExtractorConfig:
    """Topic assignment as an `EntityCandidate` in the `taxonomy` namespace.

    Spec §3.3 models the *assignment* as a `RelationCandidate(RESEARCHES)` from
    the paper to the topic. This PR emits the topic identity only, and not the
    edge, for the reason given in the module docstring: the relation needs the
    paper endpoint in the same record and the model's JSON cannot carry it
    without being invited to invent an identifier. The topic entity is the half
    that is well-defined without the edge, and it is the half the evaluation
    runner's `topics` bucket grades.
    """
    return ExtractorConfig(
        extractor_id="topic",
        target_type="Topic",
        builder=NormalizedSurfaceBuilder(
            _surface_entity_builder(
                entity_type="Topic", namespace="taxonomy", attribute_fields=("level",)
            )
        ),
        prompt_template=(
            "You are assigning a computer-science paper to research topics. For each "
            "topic the passage shows the paper researches, emit an object with `name` "
            "(the topic), `level` (one of domain, area, subtopic) and `quoted_text`."
            + _PROMPT_TAIL
        ),
        model_id=MODEL_ID,
        model_version=MODEL_VERSION,
        extractor_version="1",
        prompt_version="1",
        scoring=EXTRACTION_SCORING,
    )


def problem_extractor() -> ExtractorConfig:
    """Problems, keyed on the full join key `K` (spec §6.4.1).

    `section` is an attribute, never part of the key: it is a segmenter output
    and the segmenter is under active change (SEG-1/3/4/6), so keying identity
    on it would churn every problem's identity each time segmentation improves.
    """
    return ExtractorConfig(
        extractor_id="problem",
        target_type="Problem",
        builder=PaperScopedKeyBuilder(
            entity_type="Problem",
            surface_field="statement",
            quote_field="quoted_text",
            display_name_field="statement",
            attribute_fields=("statement", "quoted_text", "section"),
        ),
        prompt_template=(
            "You are cataloguing the research problems a computer-science paper "
            "identifies. For each distinct problem, emit an object with `statement` "
            "(the problem in one sentence), `quoted_text` (the verbatim sentence "
            "evidencing it) and `section` (the section it appears in)." + _PROMPT_TAIL
        ),
        model_id=MODEL_ID,
        model_version=MODEL_VERSION,
        extractor_version="1",
        prompt_version="1",
        scoring=EXTRACTION_SCORING,
    )


#: Every extractor, in a fixed order. Order decides which of two candidates
#: sharing a semantic key survives intra-run dedup (first wins), so it must not
#: depend on dict iteration or a set.
def research_extractors() -> tuple[ExtractorConfig, ...]:
    return (
        topic_extractor(),
        research_concept_extractor(),
        model_extractor(),
        method_extractor(),
        problem_extractor(),
    )


__all__ = [
    "EXTRACTION_SCORING",
    "EXTRACTION_SOURCE_RELIABILITY",
    "MODEL_ID",
    "MODEL_VERSION",
    "STRUCTURED_SCORING",
    "NormalizedSurfaceBuilder",
    "PaperScopedKeyBuilder",
    "method_extractor",
    "model_extractor",
    "problem_extractor",
    "research_concept_extractor",
    "research_extractors",
    "topic_extractor",
]
