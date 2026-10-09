"""The ontology declaration, and the validator that must actually be wired.

Both halves matter and they fail differently. A term set left empty makes
`Ontology` *unconstrained* rather than strict; a validator left at the
`ExtractionPipeline` default (`OntologyCandidateValidator(None)`) makes the
whole check a no-op. Either produces a run that looks strictly validated and
validates nothing, which is why neither is asserted by reading the source.
"""

from __future__ import annotations

import pytest
from agentic_kg.migration.ingestion.ontology import (
    ATTRIBUTES,
    ENTITY_TYPES,
    FORBIDDEN_RELATION_TYPES,
    GRAPH_ID,
    RELATION_TYPES,
    RESEARCH_ONTOLOGY,
    research_candidate_validator,
)
from kg_contracts.candidates import EntityCandidate, RelationCandidate
from kg_contracts.identity import EntityRef
from kg_contracts.testing.factories import make_coords, make_scores

#: The six domain types this PR's mapping approves, plus the two endpoints the
#: relation vocabulary needs.
REQUIRED_ENTITY_TYPES = frozenset(
    {"Paper", "Topic", "ResearchConcept", "Model", "Method", "Problem"}
)


def _entity(entity_type: str) -> EntityCandidate:
    alias = EntityRef(entity_type=entity_type, namespace="surface", key="x")
    return EntityCandidate(
        graph_id=GRAPH_ID,
        producer="test",
        producer_run_id="run",
        ontology_version=RESEARCH_ONTOLOGY.version,
        source_coordinates=make_coords(),
        semantic_key=f"{entity_type.lower()}/surface/x",
        scores=make_scores(),
        entity_type=entity_type,
        aliases=(alias,),
    )


@pytest.mark.parametrize(
    "name,terms",
    [
        ("entity_types", RESEARCH_ONTOLOGY.entity_types),
        ("relation_types", RESEARCH_ONTOLOGY.relation_types),
        ("attributes", RESEARCH_ONTOLOGY.attributes),
    ],
)
def test_every_term_set_is_non_empty(name: str, terms: frozenset[str]) -> None:
    """An empty set means *unconstrained*, not *forbidden*.

    `kgis/ontology.py` reads `not self.entity_types or term in self.entity_types`
    on all three axes. Declaring entity types while leaving relation types empty
    would silently admit any relation at all — a run that reported strict
    validation while checking two of three axes.
    """
    assert terms, f"{name} is empty, which means UNCONSTRAINED in kgis.Ontology"


def test_the_approved_domain_types_are_declared() -> None:
    missing = REQUIRED_ENTITY_TYPES - ENTITY_TYPES
    assert not missing, f"approved domain types missing from the ontology: {missing}"


def test_the_forbidden_relation_types_are_absent() -> None:
    """`SOLVED_BY` and `HAS_TOPIC` appear in no ontology declaration (AC-15).

    Kept as a negative assertion deliberately: AC-15 names such guards as the
    *enforcement* of the rule rather than a violation of it, and PR #66 paid for
    the `HAS_TOPIC` one.
    """
    assert FORBIDDEN_RELATION_TYPES
    assert not (FORBIDDEN_RELATION_TYPES & RELATION_TYPES)


def test_instance_of_is_not_a_relation() -> None:
    """Under the mapping it ceases to be a relation and becomes merge lineage."""
    assert "INSTANCE_OF" not in RELATION_TYPES


def test_graph_id_satisfies_the_identity_grammar() -> None:
    """`^[a-z][a-z0-9-]*$`, exercised against the upstream predicate.

    Asserted with KGCS's own `is_well_formed_graph_id` rather than a local
    regex: a local copy would prove this module's restatement matches itself
    and would stay green through an upstream change (§9.0 obligation 3). KGIS
    does not validate `graph_id`; KGCS rejects a malformed one as `BAD_DATA`,
    one system downstream of where it was introduced.
    """
    from kgcs.ids import is_well_formed_graph_id

    assert is_well_formed_graph_id(GRAPH_ID)
    assert not is_well_formed_graph_id("agentic_kg"), (
        "the upstream predicate accepts underscores, so this test is not "
        "checking what it claims to"
    )


def test_a_declared_entity_type_is_admitted() -> None:
    validator = research_candidate_validator(strict=True)
    for entity_type in sorted(REQUIRED_ENTITY_TYPES):
        assert validator.validate(_entity(entity_type)).valid, entity_type


def test_an_undeclared_entity_type_is_rejected() -> None:
    """The validator is strict, and this is what proves it.

    The defect: building the pipeline without passing `candidate_validator=`.
    `ExtractionPipeline` defaults to `OntologyCandidateValidator(None)`, which
    admits every term, so a run would look validated and check nothing (AC-14).
    The companion assertion in `test_shadow_run.py` checks the *pipeline* is
    wired with this validator; this one checks the validator has teeth.
    """
    decision = research_candidate_validator(strict=True).validate(_entity("Sandwich"))
    assert not decision.valid
    assert decision.failure_kind is not None
    assert "Sandwich" in " ".join(decision.reasons)


def test_a_forbidden_relation_type_is_rejected() -> None:
    validator = research_candidate_validator(strict=True)
    candidate = RelationCandidate(
        graph_id=GRAPH_ID,
        producer="test",
        producer_run_id="run",
        ontology_version=RESEARCH_ONTOLOGY.version,
        source_coordinates=make_coords(),
        semantic_key="paper/doi/a/HAS_TOPIC/topic/taxonomy/b",
        scores=make_scores(),
        relation_type="HAS_TOPIC",
        subject=EntityRef(entity_type="Paper", namespace="doi", key="a"),
        object=EntityRef(entity_type="Topic", namespace="taxonomy", key="b"),
    )
    assert not validator.validate(candidate).valid


def test_the_attribute_vocabulary_covers_what_the_extractors_emit() -> None:
    """Every attribute any configured extractor can assert is declared.

    Derived from the extractor configuration rather than transcribed, so adding
    an attribute field to an extractor without declaring it turns this red
    instead of producing `UNSUPPORTED_ONTOLOGY` rejections at run time that the
    report would record as a count nobody reads.
    """
    from agentic_kg.migration.ingestion.extractors import research_extractors

    configs = research_extractors()
    assert configs
    declared_by_builders: set[str] = set()
    for config in configs:
        builder = config.builder
        inner = getattr(builder, "_inner", builder)
        for sub in getattr(inner, "builders", ()):
            fields = getattr(sub, "_attribute_fields", ())
            declared_by_builders.update(fields)
    assert declared_by_builders, "no extractor declares any attribute field"
    missing = declared_by_builders - ATTRIBUTES
    assert not missing, f"attributes emitted but not declared: {sorted(missing)}"
