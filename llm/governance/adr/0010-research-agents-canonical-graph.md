---
title: "ADR-0010: Research agents over the canonical graph"
nav_exclude: true
---

# ADR-0010: Research agents over the canonical graph

Status: Proposed
Date: 2026-10-26

## Context

The research agents (`packages/core/src/agentic_kg/agents`) — ranking,
continuation, evaluation, synthesis — were written against the legacy Neo4j
projection: `Neo4jRepository`, `SearchService`, `RelationService`, and direct
Cypher for topic lookup. The curated canonical graph (KGCS, `Canon__*`
identities and assertions, namespace per ADR-0004/ADR-0005) is now the source
of truth for ingested problems, but no agent could read it, and SynthesisAgent
still wrote directly to the legacy labels.

Two properties are required of an agent that reads canonical state:

1. **No canonical write surface.** ADR-0010/AC-1 makes this structural: an
   application object must not satisfy `GraphMutationStore`. Agents are
   application code.
2. **Provenance for every output.** The PA-AKG proposal's claim is that
   provenance is a typed artifact carried *with* an agent message, not a
   trace reconstructed afterwards. KGPS v0.5.0 (`ProvenanceEnvelope`,
   `ProvenanceService`) already defines that shape.

The legacy write-back also had signature bugs (fixed in #114). Carrying the
same direct-write shape into the canonical path would repeat that class of
bug with a much worse blast radius.

## Decision

**Agents consume graph objects through a `GraphPort`, and on the canonical
path they only produce review-queue candidates plus KGPS provenance.**

1. **One port, two adapters.** `agents/graph_port.py` defines
   `list_candidates()` and `load_context()`.
   `LegacyGraphPort` wraps the existing repository/search/relation calls
   unchanged, so default behaviour is byte-for-byte the historical one.
   `CanonicalGraphPort` reads `Canon__Problem` identities at the latest epoch
   through `migration/canonical_read.py`; it holds a canonical *reader* and no
   write surface. `AGENT_GRAPH_SOURCE=legacy|canonical` selects the adapter
   (`AgentConfig.graph_source`); an unknown value is a startup error, never a
   silent legacy fallback. Terraform owns the variable
   (`agent_graph_source`, default `legacy`) and injects it into the API
   service.
2. **Canonical writes go through the human gate.** On the canonical source,
   SynthesisAgent creates no `Identity`/`Assertion`. Each proposed new
   problem, refinement, resolved status, or relation becomes a
   `:Canon__ReviewItem` (the P2 queue) with `source="agent"`, the workflow
   run id, agent name, model and prompt version. Approval re-enters the
   existing `commit_candidate` -> `run_curation` -> `PlanExecutor` path
   (ADR-0010). A status change has no KGCS candidate operation in this slice,
   so it is queued `committable=false` rather than approximated.
3. **Every output carries a KGPS envelope.** Ranking (per problem),
   continuation, evaluation and synthesis outputs carry an `AgentProvenance`
   whose wire shape is `kgps.grounding.ProvenanceEnvelope`: sender/recipient
   role, kind, trace id, citations (canonical assertion ids, narrowed to
   evidence ids), abstention when nothing grounds it, and a `derived_from`
   list of upstream agent-output ids. A legacy output abstains explicitly —
   it has no canonical assertion ids — instead of pretending to be grounded.
   Outputs are stored in the runner's `AgentOutputStore` (an in-memory store
   in this slice, matching the runner's in-process `MemorySaver` checkpoints)
   and `GET /api/agents/outputs/{id}/provenance` returns the envelope plus KGPS
   `explain` payloads per cited assertion. Durable storage is a follow-up; the
   endpoint is honest about an unknown id (404) rather than inventing one.
4. **Ranking uses documented graph-native features.** On the canonical
   source the deterministic score is

   ```
   graph_score = 0.35 * evidence_strength
               + 0.25 * data_availability
               + 0.20 * connectivity
               + 0.20 * freshness
   ```

   where `evidence_strength` is distinct evidence ids per assertion,
   `data_availability` saturates at three dataset/metric/baseline assertions,
   `connectivity` saturates at ten assertions plus distinct object endpoints,
   and `freshness` is a one-year half-life on the identity's creation date.
   The LLM still reads the statements; the final score is
   `0.6 * llm + 0.4 * graph_score`. Both constants are code, not tuned
   per-run.
5. **No second write path.** `CanonicalGraphPort` is a reader;
   `canonical_write.py` only builds `kg_contracts` candidates and
   `ReviewItem`s; the executor remains the only object that mutates canonical
   state.

## Consequences

- Defaults are safe: without `AGENT_GRAPH_SOURCE=canonical` nothing changes,
  and a canonical run without the `migration` extra fails before any workflow
  starts.
- The canonical path is read + queue only. It cannot resolve aliases into
  identities, merge identities, or execute a status change; those items are
  queued with a stated blocker rather than guessed.
- Provenance on the legacy source is an abstaining envelope. That is honest
  but less useful than the canonical path; it is not back-filled with
  invented assertion ids.
- KGPS evidence *content* lives in the KGIS ledger, which is a different
  access path (KGIS ADR-0011). The provenance endpoint therefore reports
  unresolved evidence links as KGPS gaps instead of reading the ledger.
- The graph-native score is a v1 heuristic. It is deterministic and
  documented, so a later revision is a visible policy change rather than an
  implicit drift.

## Alternatives considered

- **Fork the agents per graph.** Rejected: two copies of ranking and
  synthesis would drift, and the legacy signature bug would have to be fixed
  twice.
- **Let SynthesisAgent write canonical state under an "agent" namespace.**
  Rejected: it re-creates a second write path and bypasses the human gate.
- **Reconstruct provenance from logs.** Rejected by the PA-AKG premise: the
  envelope must travel with the output, and a missing envelope must be
  visible as abstention.
