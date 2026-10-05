"""Synthetic candidates for the paths the real corpus cannot reach.

Kept in one module, and used sparingly, because a synthetic candidate proves
something about the *seam* and nothing about the pipeline. Two paths need one:

* a candidate that routes ``AUTO`` under the unmodified contract policy — the
  real corpus has none, which is itself a measured finding (``test_policy.py``),
  so the executor and arm-export seams would otherwise be untestable at their
  default configuration;
* an ``ATTACH_ASSERTION``, the only operation the v1 vocabulary can actually
  compensate. The corpus's assertion candidates all carry alias subjects and
  are deferred, so a rollback round trip needs a subject that is already a
  minted identity id.

Scores are set explicitly rather than defaulted: ``identity_confidence`` is the
field the whole routing question turns on, and a factory default would hide it.
"""

from __future__ import annotations

from kg_contracts.candidates import (
    AttributeAssertionCandidate,
    EntityCandidate,
    SourceCoordinates,
)
from kg_contracts.identity import EntityRef, new_identity_id
from kg_contracts.testing.factories import make_scores

#: The graph the curation engine is bound to; a candidate from any other graph
#: is rejected at validation, which is a property `test_policy.py` exercises.
GRAPH_ID = "research"

#: cskg's DOI, in the locator form `doi_from_locator` parses. Taken from the
#: corpus table rather than invented: the arm export joins papers by DOI, so a
#: made-up one would produce a record attributed to no paper and the arm test
#: would pass over an empty grouping.
CSKG_LOCATOR = "paper://doi/10.1007/978-3-031-19433-7_39"

#: A scored gold Topic of cskg (`reconciled/paper_cskg.gold.yml`).
CSKG_GOLD_TOPIC = "Knowledge Graphs"


def auto_routing_scores():
    """Scores that clear every unmodified ``ConfidencePolicy`` AUTO threshold."""
    return make_scores(
        extraction_confidence=0.99,
        source_reliability=0.99,
        identity_confidence=0.99,
        policy_risk=0.0,
    )


def graded_entity_candidate(
    *,
    surface: str = CSKG_GOLD_TOPIC,
    entity_type: str = "Topic",
    locator: str = CSKG_LOCATOR,
    key: str = "kg",
) -> EntityCandidate:
    """An ``AUTO``-routing entity candidate of a graded type, joined to cskg."""
    return EntityCandidate(
        graph_id=GRAPH_ID,
        producer="test-producer",
        producer_run_id="run-synthetic",
        ontology_version="1",
        source_coordinates=SourceCoordinates(source_type="paper", locator=locator),
        semantic_key=f"{entity_type.lower()}/{key}",
        scores=auto_routing_scores(),
        entity_type=entity_type,
        aliases=(EntityRef(entity_type=entity_type, namespace="surface", key=surface),),
        display_name=surface,
        properties={},
    )


def attachable_attribute_candidate(
    *, subject: str | None = None, attribute: str = "title", value: object = "CS-KG"
) -> AttributeAssertionCandidate:
    """An ``AUTO``-routing attribute assertion about an already-minted identity."""
    return AttributeAssertionCandidate(
        graph_id=GRAPH_ID,
        producer="test-producer",
        producer_run_id="run-synthetic",
        ontology_version="1",
        source_coordinates=SourceCoordinates(source_type="paper", locator=CSKG_LOCATOR),
        semantic_key=f"paper/cskg/{attribute}",
        scores=auto_routing_scores(),
        subject=subject if subject is not None else new_identity_id(GRAPH_ID),
        attribute=attribute,
        value=value,
        valid_period=None,
    )
