---
title: Ground-Truth Curation
nav_exclude: true
---

# Ground-Truth Curation — Importer Validation Set

**Owner:** Victoria
**Status:** Started
**Created:** 2026-07-21

## Why this task exists

The entity-expansion importer (`ingest_papers` → Topic / ResearchConcept / Model / Method /
Problem / Citation extraction) is being deployed to staging. Before we trust its output, we need a
**human-verified ground-truth set** — a small, well-understood collection of papers where we already
know, by hand, what the importer *should* produce. When the importer runs live on these same papers,
we diff its output against this answer key.

We are deliberately choosing papers that **cite each other**, so that a single concept recurs across a
citation chain and accumulates evidence — letting us watch a concept "build up score" and escalate
into a full `Problem` node. That cross-paper accumulation is a behavior we can only validate with a
connected set, not with isolated papers.

## The two outputs of this task

- **Output A — a place to store the results.** Decide and set up where the reviews and the reconciled
  ground truth live (spreadsheet vs. structured files in the repo vs. a separate "gold" Neo4j). See
  [Step 2](#step-2--decide-storage-output-a).
- **Output B — reviewed ground truth for the 5–10 papers.** For each paper, a human review and an
  independent Claude review, reconciled into one agreed answer key. See
  [Step 3](#step-3--per-paper-dual-review-output-b) and [Step 4](#step-4--reconcile).

## The review model: two reviews, then reconcile

Each paper gets **two independent reviews**:

1. **Human review** — Victoria reads the paper and fills in the field schema below.
2. **Claude review** — a Claude session reads the same paper and fills in the same schema, *without*
   seeing the human review first (independence matters — it's the whole point of having two).

Then the two are **reconciled**: compare them side by side, resolve every disagreement deliberately,
and record the agreed ground truth for that paper — plus a short note on anything that was disputed or
genuinely ambiguous (those notes are valuable; they tell us where the importer's job is hard).

---

## Step 0 — Kick off

1. **Start a fresh Claude session** in the `agentic-kg` repo. Keep this brief open in that session.
2. Have Claude read the entity model so both reviews use the real schema (not an invented one):
   - `llm/features/topic-research-area-entities.md` (E-1) — the `Topic` hierarchy
     (**domain → area → subtopic**, `SUBTOPIC_OF`), and `BELONGS_TO` edges.
   - `llm/features/research-concept-entities.md` (E-2) — `ResearchConcept` nodes and how they attach
     to Topics (`BELONGS_TO`), Problems (`INVOLVES_CONCEPT`), and Papers (`DISCUSSES`).
   - `packages/core/src/agentic_kg/extraction/schemas.py` — the exact extracted-entity fields
     (name, aliases, `quoted_text`, confidence) the importer emits. **Match these field names.**
   - `packages/core/tests/extraction/fixtures/e8_eval/SELECTION.md` — the repo's *existing*
     gold-label convention. Prefer to extend this rather than invent a new format.
3. **Materialize the PDFs.** The papers live in OneDrive as online-only placeholders. In Finder,
   select the folder → right-click → **"Always keep on this device"** before reviewing, or Claude/tools
   won't be able to open them.

## Step 1 — Get the papers (identifiers + chain already verified)

**This step is largely done** — see [The verified citation chain](#the-verified-citation-chain-curated-2026-07-25)
below. The 8 papers are resolved to DOIs and the citation edges among them were confirmed against
OpenAlex (10 edges, one connected component). What's left for you:

1. **Acquire the PDFs.** Three are already in your library (marked ✅ in the chain table); fetch the
   other five by DOI (publisher or `https://doi.org/<doi>`; arXiv/open-access where available).
2. **Sanity-check the ✅ mappings.** Confirm your `Public/Large_Scale_KG_CS.pdf`,
   `Completing_Scientific_Facts_...pdf`, and `KG-EmpiRE` PDF are in fact the papers listed (open them,
   check title/DOI). The KG-EmpiRE PDF should map to the **2023 "Divide and Conquer the EmpiRE"** version.
3. **(Optional) Re-derive edges from your storage** once loaded, as a cross-check — for each paper,
   confirm its reference list contains the others as the chain table claims. Semantic Scholar
   (`https://api.semanticscholar.org/graph/v1/paper/DOI:<doi>`) is the source the importer itself uses
   for `CITES`; OpenAlex was used for the curation. Minor source-to-source edge differences are normal —
   record whichever the importer will actually populate.

## Step 2 — Decide storage (Output A)

Discuss the options with Claude in the new session and pick one. Trade-offs:

| Option | Pros | Cons |
|---|---|---|
| **Spreadsheet** (Sheets/xlsx) — human & Claude columns side by side | Easiest dual-entry + eyeball reconciliation; zero setup | Relations (`CITES`, hierarchy) are awkward to represent; not directly test-consumable |
| **Structured files in repo** (YAML/JSON per paper) | Version-controlled, diffable, and the eval harness can load them directly; matches the existing `e8_eval` gold-YAML pattern | Less friendly for freehand human entry |
| **Separate "gold" Neo4j** | Mirrors the target graph exactly; enables graph-level diff against importer output | Heavy setup; overkill at *labeling* time for 10 papers |

**Recommended split (discuss before committing):** capture the two raw reviews in whatever's fastest to
type into (a **spreadsheet** is fine), but commit the **reconciled** ground truth as
**version-controlled YAML/JSON in the repo**, in/alongside
`packages/core/tests/extraction/fixtures/e8_eval/`, so the existing eval/test harness can consume it.
Optionally project the reconciled set into a gold Neo4j later if we want graph-level diffing.

## Step 3 — Per-paper dual review (Output B)

For **each** paper, both the human and Claude independently fill this schema. Field names mirror the
importer's entity model so the answer key lines up 1:1 with what gets extracted.

```yaml
paper:
  filename: "<onedrive filename>"
  title: "<exact title>"
  doi: "<10.xxxx/...>"
  s2_paper_id: "<semantic scholar paperId>"
  year: <yyyy>

# Topic hierarchy (domain -> area -> subtopic). BELONGS_TO points entities at the deepest applicable Topic.
topic:
  domain: "<e.g. Computer Science>"
  area: "<e.g. Knowledge Representation>"
  subtopic: "<e.g. Automated Knowledge Graph Construction>"

# ResearchConcepts the paper discusses (DISCUSSES). These are the recurring "spine" candidates.
research_concepts:
  - name: "<concept>"
    aliases: ["<alt name>", ...]
    quoted_text: "<>=10-char verbatim span grounding it>"

# Models the paper introduces or uses (USES_MODEL). NOT architectures-in-general.
models:
  - name: "<model>"
    aliases: []
    quoted_text: "<>"

# Methods the paper applies (APPLIES_METHOD). NOT generic activities like "training".
methods:
  - name: "<method>"
    aliases: []
    quoted_text: "<>"

# The core Problem the paper addresses (the escalation target; INVOLVES_CONCEPT -> concepts above).
problem:
  statement: "<one-sentence problem this paper tackles>"
  involves_concepts: ["<concept name>", ...]

# CITES targets *within this set only* (from Step 1 verification).
cites_within_set: ["<filename or s2_paper_id>", ...]

reviewer: "human" | "claude"
notes: "<anything ambiguous, low-confidence, or judgment-call>"
```

**Extraction rules to apply consistently (from the importer's prompts):**
- A concept must be **grounded in a verbatim quote** — no concept without supporting text.
- `Model` ≠ architecture-in-general ("transformer architecture" is not a model instance).
- `Method` ≠ generic activity ("training" is not a method).
- Prefer the paper's own terminology for names; put paraphrases in `aliases`.

## Step 4 — Reconcile

For each paper, put the human and Claude reviews side by side:

1. **Agreements** → copy straight into the reconciled record.
2. **Disagreements** → decide deliberately (re-read the relevant span). Record the resolution.
3. **Only-one-caught-it** items → judge whether it's a real miss or a spurious extraction; keep or drop.
4. Save the reconciled record to the storage chosen in Step 2, and write a one-line `notes` on any item
   that was disputed or ambiguous.

The disagreement notes are a deliverable in their own right — they map exactly where the live importer
is most likely to be wrong, and become targeted assertions for the eval harness.

## Done criteria

- [x] Output A: storage set up and documented (with the rationale for the choice). Located in /packages/core/tests/extraction/fixtures/ground_truth_chain.
- [x] Step 1: all papers resolved to title + DOI + S2 id; citation edges among the set verified.
- [ ] Output B: 5–10 papers each have a human review **and** a Claude review **and** a reconciled record.
- [ ] Disagreement/ambiguity notes captured.

---

## The verified citation chain (curated 2026-07-25)

Concept spine: **automated construction of scholarly knowledge graphs → validation →
downstream reasoning (hypothesis / `Problem` generation).** This set was **verified against
OpenAlex**: the 8 papers form **one connected component with 10 real citation edges** and it is
multi-hop (not just a star), so cross-paper concept accumulation actually has citation structure to
ride on. **DOIs are resolved** — no guessing needed. Five of the eight are from the same research
lineage (Dessì / Osborne / Salatino group), so concepts genuinely recur and evolve across the set.

| # | Paper | Year | DOI | Role in the chain | In your folder? |
|---|---|---|---|---|---|
| 1 | CS-KG: A Large-Scale Knowledge Graph of Research Entities and Claims in Computer Science | 2022 | `10.1007/978-3-031-19433-7_39` | **Anchor / hub** — automated scholarly KG | ✅ `Public/Large_Scale_KG_CS.pdf` |
| 2 | CS-KG 2.0: A Large-scale Knowledge Graph of Computer Science | 2025 | `10.1038/s41597-025-05200-8` | Direct successor to the anchor | — (fetch) |
| 3 | Construction of Knowledge Graphs: Current State and Challenges | 2023 | `10.2139/ssrn.4605059` | KG-construction survey | — (fetch) |
| 4 | Large Language Models for Scholarly Ontology Generation | 2025 | `10.1016/j.ipm.2025.104262` | LLM-based construction | — (fetch) |
| 5 | Completing Scientific Facts in Knowledge Graphs of Research Concepts | 2022 | `10.1109/access.2022.3220241` | Fact / relation completion | ✅ `Completing_Scientific_Facts_...pdf` |
| 6 | Knowledge Graph Validation by Integrating LLMs and Human-in-the-Loop | 2025 | `10.1016/j.ipm.2025.104145` | Validation + human review (ties to the review-queue goal) | — (fetch) |
| 7 | Research Hypothesis Generation over Scientific Knowledge Graphs | 2025 | `10.1016/j.knosys.2025.113280` | **Downstream: KG → hypotheses** — the `Problem`-node escalation target | — (fetch) |
| 8 | Divide and Conquer the EmpiRE: A Community-Maintainable KG of Empirical Research in Requirements Engineering | 2023 | `10.1109/esem56168.2023.10304795` | Domain KG application | ✅ your `KG-EmpiRE` PDF (this is the fuller 2023 version) |

**Verified edges (A cites B), from OpenAlex `referenced_works`:**

```
CS-KG 2.0                      → CS-KG,  Completing Scientific Facts
Construction of KGs (survey)   → CS-KG
LLM Scholarly Ontology Gen     → CS-KG,  KG Validation (HITL)
KG Validation (HITL)           → CS-KG,  Completing Scientific Facts
Research Hypothesis Generation → CS-KG,  Completing Scientific Facts
Divide and Conquer the EmpiRE  → CS-KG
```

**Download pointers for the 5 "fetch" papers (all open access, verified 2026-07-25).** Prefer the
publisher/DOI link; the repository mirror is the fallback if you hit a paywall or a bot block.

| # | Paper | Primary (DOI) | Direct PDF / repository mirror |
|---|---|---|---|
| 2 | CS-KG 2.0 | `https://doi.org/10.1038/s41597-025-05200-8` | **PDF (verified live):** `https://www.nature.com/articles/s41597-025-05200-8.pdf` (gold OA) |
| 3 | Construction of KGs: State & Challenges | `https://doi.org/10.2139/ssrn.4605059` | Qucosa repo: `https://ul.qucosa.de/id/qucosa:102513` — SSRN preprint; a fuller version also appeared in *Information* (2024), worth confirming which the PDF is |
| 4 | LLMs for Scholarly Ontology Generation | `https://doi.org/10.1016/j.ipm.2025.104262` | Milano-Bicocca BOA: `https://hdl.handle.net/10281/567741` (hybrid OA) |
| 6 | KG Validation w/ LLMs + Human-in-the-Loop | `https://doi.org/10.1016/j.ipm.2025.104145` | Open Research Online PDF: `https://oro.open.ac.uk/103792/1/103792.pdf` (opens in a browser; 403s to scripts) |
| 7 | Research Hypothesis Generation over Sci KGs | `https://doi.org/10.1016/j.knosys.2025.113280` | HAL: `https://hal.science/hal-05052350` (hybrid OA) |

The three ✅ papers you already own (CS-KG, Completing Scientific Facts, KG-EmpiRE/Divide-and-Conquer)
are in your OneDrive library — just materialize them per Step 0.3.

**How this was derived (and why it replaces the first-pass list):** the original shortlist was built
from your *paraphrased folder filenames*. On verification (2026-07-25), only 4 of those 10 resolved
confidently by title search, and the 4 that did (CS-KG, Completing Scientific Facts, KG-EmpiRE,
Personal Research KGs) turned out to have **zero direct citation edges** among them — a topically
grouped, same-era folder rarely forms a citation chain. So the chain was **rebuilt by construction**:
anchor on CS-KG (which you own and which resolved cleanly), then pull papers that *actually cite it*
via OpenAlex forward-citations, keeping those that also cite each other. Three of your original PDFs
survive into this set (marked ✅); the other five are fetched by DOI. If you'd rather stay strictly
inside your existing library, materialize the PDFs and we can re-run the edge check on those exact
files — but expect a sparser graph.

**Tighter core (if 8 is too many):** #1, #2, #5, #7 give the anchor → successor → completion →
hypothesis-escalation spine with every edge verified.

## Gotchas

- **PDFs are OneDrive online-only placeholders** — materialize them (Step 0.3) or nothing can open them.
  (This is why curation used DOIs/OpenAlex rather than the PDFs directly.)
- **Don't fabricate DOIs.** The chain table's DOIs are resolved; confirm each against the actual PDF.
- **Independence of the two reviews is the point** — don't let the Claude review see the human review (or
  vice versa) until reconciliation.
- **The chain is verified but not immutable** — if you drop or add a paper, re-run the edge check so the
  set stays connected. A connected set is the requirement, not this exact list.
- **`CITES` source differs by tool** — curation used OpenAlex; the importer uses Semantic Scholar. Expect
  small edge differences; the ground truth should reflect what the *importer* will populate.
