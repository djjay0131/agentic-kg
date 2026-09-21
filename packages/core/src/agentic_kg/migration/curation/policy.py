"""The adopter's declared curation policy — config, and stated as such.

`ConfidencePolicy` is a *frozen contract model whose thresholds are data*
(`kg_contracts.policy`: "automating a decision later is a policy config change,
never a code change"). Adopting KGCS therefore means **declaring** a policy, in
one place, with the consequences written down — not sprinkling literals through
a pipeline.

Two policies are declared here, and which one a caller passes is a real
decision, not a default to drift into.

The measured deadlock
---------------------
:data:`CONTRACT_DEFAULT_POLICY` is ``ConfidencePolicy()`` unchanged. Under it,
**no candidate the KGIS shadow path produces can ever route ``AUTO``**, so the
deterministic core plans nothing and the executor commits nothing — on any
corpus, forever. The cause is not a threshold this repo could tune:

* ``ConfidencePolicy.require_identity_confidence_for_auto`` defaults to
  ``True``, and ``AUTO`` then additionally requires
  ``identity_confidence >= auto_min_identity_confidence`` (0.95). A **missing**
  ``identity_confidence`` deliberately does not pass — honest null blocks
  ``AUTO`` rather than defaulting to good enough.
* Nothing in the chain ever sets it. ``kgis.builders`` starts the three
  optional scores at ``None`` and no KGIS extractor, no KGCS stage, and no
  ``kg_eval`` component writes one; ``kgcs.er`` computes *link* probabilities
  for identity pairs and produces an ``ErDecision``, never a
  ``CandidateScores.identity_confidence``.

So the field the default policy gates on has **no producer anywhere in the
KGIS/KGCS chain**. ``test_policy.py`` pins that as a measurement rather than a
claim, in ``test_the_contract_default_policy_auto_routes_nothing_kgis_produces``.
It is reported upstream, not worked around here: a fix belongs in the platform
(either a producer for ``identity_confidence``, or a contract decision that a
*new-identity* candidate is not gated on a *resolution* confidence), and the
corrected commit re-pinned.

The opt-in alternative
----------------------
:data:`STRUCTURED_IDENTITY_POLICY` changes exactly one field —
``require_identity_confidence_for_auto=False`` — and **nothing else**. Both
extraction thresholds keep their contract values (``auto_min_extraction=0.95``,
``auto_min_source_reliability=0.90``), deliberately: lowering those until the
LLM extractor's 0.8/0.75 candidates cleared the bar would be tuning the policy
until the arm looked good, which is the one thing a comparison harness must
never do.

What it costs, stated plainly: the identity gate is the fail-closed guard that
says "do not auto-mint an identity when no entity resolution has told you this
is a new one". With it off, two candidates naming the same real-world entity
mint two identities (``DerivedIdFactory.identity_id`` keys on the candidate id),
and nothing dedupes them — irreversibly, because ``CREATE_IDENTITY`` has no
inverse. An independent reviewer demonstrated exactly that: two ``Topic``
candidates for one concept, ``identity_confidence=None``, two identities minted,
``roll_back`` returning ``plan=None`` with both operations non-compensable.

That is acceptable only where identity is carried by a **registered
identifier** rather than inferred — which is what the *structured* arm's
`Paper` candidates have, keyed by DOI.

**And that justification is now enforced, not merely written down.** A
``ConfidencePolicy`` is batch-wide; the argument for relaxing it was
candidate-level, and the same reviewer pointed out that nothing held the line
except the LLM extractor's 0.8/0.75 scores happening to keep graded entities
away from ``AUTO``. So :func:`~agentic_kg.migration.curation.pipeline.run_curation`
refuses — before anything is executed — any run in which the identity gate is
off *and* a new identity would be minted for an entity that no registered
identifier keys (see :data:`REGISTERED_IDENTIFIER_NAMESPACES`). The refusal
keys on the ``require_identity_confidence_for_auto`` **field**, not on this
constant, so an ad-hoc policy with the gate off is guarded identically.

The guard's seam is ``run_curation``. A caller that drives
:func:`curation_engine` directly, builds its own ``PlanExecutor`` and applies
the plan itself is outside it — stated here rather than left to be discovered,
because "enforced" should name where.

What it does **not** buy: any graded research entity. `Topic`, `ResearchConcept`,
`Model` and `Method` candidates come from the LLM extractor at
``extraction_confidence=0.8`` / ``source_reliability=0.75``, below both
unchanged ``AUTO`` thresholds, so they route ``LLM_ASSESS`` under this policy
too. Auto-applying them needs the bounded adviser / review stage, which is not
wired. `test_policy.py` measures that as well.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime

from agentic_kg.migration.curation._contracts import (
    AuditSink,
    ConfidencePolicy,
    CurationEngine,
    DerivedIdFactory,
    FixedClock,
    IdFactory,
)
from agentic_kg.migration.ingestion.ontology import GRAPH_ID

#: The graph the curated candidates belong to. Imported from the ingestion
#: ontology rather than restated, so the validator's ``graph_id`` binding and
#: the candidates' own ``graph_id`` cannot drift into a silent mismatch — a
#: cross-graph candidate is rejected at validation, and a second literal here
#: would turn that safety check into a whole-corpus rejection.
CURATION_GRAPH_ID = GRAPH_ID

#: A fixed instant for the curation run. Only ``AuditRecord.recorded_at`` and
#: ``ExecutionRecord.recorded_at`` read the clock — a ``CurationPlan`` carries
#: no timestamp at all — but those records are the audit trail, and a wall
#: clock would make two identical runs differ in every one of them. The value
#: is arbitrary and only has to be stable.
RUN_INSTANT = datetime(2026, 9, 18, tzinfo=UTC)

#: ``ConfidencePolicy()`` unchanged. Fail-closed, and — see the module
#: docstring — currently a hard deadlock against every KGIS-produced candidate,
#: because the ``identity_confidence`` it gates on has no producer in the chain.
#: This is the default everywhere in this subpackage: an adopter that wants
#: auto-application must say so.
CONTRACT_DEFAULT_POLICY = ConfidencePolicy()

#: The one-field opt-in described in the module docstring. Not a default.
STRUCTURED_IDENTITY_POLICY = ConfidencePolicy(require_identity_confidence_for_auto=False)

#: How a DOI is spelled. Not decoration: review demonstrated that checking the
#: *namespace* alone admitted ``namespace="doi", key="banana"`` and
#: ``key="  not-a-doi  "``. A namespace is a claim about which registry settles
#: an identity; a key that registry could never issue is not that identity.
DOI_PATTERN = re.compile(r"^10\.[0-9]{4,9}/\S+$")


@dataclass(frozen=True)
class RegisteredIdentifier:
    """A registry that settles identity, and the limits of what it settles.

    Three fields because review showed that one is not enough. The first
    version of this guard was a bare namespace allowlist, and an independent
    reviewer walked through it four ways: a ``Topic`` with a ``doi``-namespaced
    key of ``"banana"``; the same with whitespace; a ``Topic`` whose *second*
    alias was a DOI; and — the one that mattered — two candidates carrying the
    **identical** DOI minting two irreversible identities.

    * ``namespace`` — which registry the alias claims.
    * ``identifies`` — what that registry can identify. A DOI names a *work*;
      it says nothing about whether two ``Topic`` candidates are one topic. An
      alias whose registry cannot identify the candidate's type is not a
      registered identifier for that candidate, however it is namespaced.
    * ``pattern`` — what a key this registry could have issued looks like.

    Together these make "carries a registered identifier" checkable instead of
    assertable. Uniqueness — the fourth hole — cannot live on this record,
    because it is a property of a *batch* rather than of a candidate; it is
    enforced in ``pipeline.unkeyed_new_identities``' sibling,
    ``duplicate_registered_identities``.
    """

    namespace: str
    identifies: frozenset[str]
    pattern: re.Pattern[str]

    def keys(self, candidate_entity_type: str, alias_namespace: str, alias_key: str) -> bool:
        """Does this alias actually identify a candidate of that type?"""
        return (
            alias_namespace == self.namespace
            and candidate_entity_type in self.identifies
            and bool(self.pattern.match(alias_key))
        )

    def canonical(self, alias_key: str) -> str:
        """The form two spellings of one identifier must share to compare equal.

        DOIs are case-insensitive and this corpus exercises it — the importer
        emitted ``10.1109/ACCESS...`` where the curation table says
        ``10.1109/access...``. Two candidates whose DOIs differ only in case are
        the same paper, and the duplicate check has to see that.
        """
        return " ".join(alias_key.split()).casefold()


#: The registries this repo accepts as settling identity without entity
#: resolution, and exactly what each settles.
#:
#: This is the whole content of the claim "no entity resolution is needed here":
#: two candidates carrying the same DOI are the same paper because the DOI
#: system says so. Note what that sentence commits to — that the pipeline
#: actually *treats* them as one paper. It did not; see
#: ``pipeline.duplicate_registered_identities``, which is the part that makes
#: the sentence true rather than merely written down.
#:
#: A future ``Author`` keyed by ORCID is added here with its own pattern and its
#: own ``identifies`` set, and gets the same argument on its own evidence.
REGISTERED_IDENTIFIERS: dict[str, RegisteredIdentifier] = {
    "doi": RegisteredIdentifier(
        namespace="doi",
        identifies=frozenset({"Paper"}),
        pattern=DOI_PATTERN,
    )
}

#: Derived view, kept because it reads well at call sites and in error messages.
#: Membership in it is necessary and — as review demonstrated — **not
#: sufficient**; use :func:`registered_identifier_for`.
REGISTERED_IDENTIFIER_NAMESPACES: frozenset[str] = frozenset(REGISTERED_IDENTIFIERS)


def registered_identifier_for(entity_type: str, alias) -> tuple[str, str] | None:
    """``(namespace, canonical key)`` if this alias really identifies, else ``None``.

    The single place the four checks are applied together, so no caller can
    perform three of them.
    """
    registry = REGISTERED_IDENTIFIERS.get(getattr(alias, "namespace", ""))
    if registry is None:
        return None
    if not registry.keys(entity_type, alias.namespace, alias.key):
        return None
    return registry.namespace, registry.canonical(alias.key)

#: The empty-graph snapshot, ``kgcs.policy``'s own default. A plan stamped with
#: it applies only while the graph is still at epoch 0, so a pipeline that never
#: passes anything else can commit exactly once in the lifetime of a graph — see
#: ``pipeline.run_curation``, which reads the store's current epoch instead.
DEFAULT_SNAPSHOT_VERSION = "0"


def curation_engine(
    *,
    confidence_policy: ConfidencePolicy | None = None,
    id_factory: IdFactory | None = None,
    instant: datetime | None = None,
    graph_id: str = CURATION_GRAPH_ID,
    snapshot_version: str = DEFAULT_SNAPSHOT_VERSION,
    audit_sink: AuditSink | None = None,
) -> CurationEngine:
    """Wire a deterministic :class:`CurationEngine` for the adopted path.

    ``CurationEngine.create`` threads one ``IdFactory`` and one snapshot /
    policy version through resolution, planning and audit, so a plan's snapshot
    and an audit record's policy version cannot disagree. This helper adds the
    two bindings the adopted path always wants and neither of which
    ``create`` defaults usefully:

    * ``graph_id`` is bound, so a candidate from another graph is **rejected at
      validation** instead of being curated into this one. ``create``'s
      ``graph_id`` defaults to ``None``, which accepts any graph.
    * the clock is a ``FixedClock``, not ``SystemClock``. Determinism is the
      property that makes a replayed run comparable to the one before it.

    ``confidence_policy`` defaults to :data:`CONTRACT_DEFAULT_POLICY`, which
    auto-applies nothing. Passing :data:`STRUCTURED_IDENTITY_POLICY` is a
    deliberate, single-field loosening whose cost is stated in the module
    docstring.
    """
    return CurationEngine.create(
        graph_id=graph_id,
        confidence_policy=confidence_policy or CONTRACT_DEFAULT_POLICY,
        id_factory=id_factory or DerivedIdFactory(),
        clock=FixedClock(instant or RUN_INSTANT),
        snapshot_version=snapshot_version,
        audit_sink=audit_sink,
    )


__all__ = [
    "CONTRACT_DEFAULT_POLICY",
    "CURATION_GRAPH_ID",
    "RUN_INSTANT",
    "STRUCTURED_IDENTITY_POLICY",
    "curation_engine",
]
