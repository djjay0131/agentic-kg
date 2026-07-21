# Ground-Truth Curation — Importer Validation Set

**Owner:** Victoria
**Status:** Not started
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

## Step 1 — Resolve the papers and verify the chain

The shortlist below is identified by **filename** (paraphrased) — resolve each to hard identifiers first:

1. Open each PDF, record its **exact title, authors, year, and DOI** (the DOI is usually on page 1 or
   in the references header). Do **not** guess DOIs — read them off the paper.
2. Look each up on **Semantic Scholar** (`https://api.semanticscholar.org/graph/v1/paper/DOI:<doi>`)
   to get its `paperId`. This is the same source the importer uses to populate `CITES`.
3. **Verify the citation edges** among the set: for each paper, check its references/citations for the
   other papers in the set. Record which papers actually cite which. This confirms (or corrects) the
   intended chain — if some papers turn out disconnected, swap them for connected ones from the same
   `Knowledge Graphs/` folder.

Record the resolved identifiers + edge list wherever you land on storage (Step 2).

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

- [ ] Output A: storage set up and documented (with the rationale for the choice).
- [ ] Step 1: all papers resolved to title + DOI + S2 id; citation edges among the set verified.
- [ ] Output B: 5–10 papers each have a human review **and** a Claude review **and** a reconciled record.
- [ ] Disagreement/ambiguity notes captured.

---

## The shortlist (KG-construction spine)

Concept spine to watch as it recurs and escalates: **"LLM-based scientific entity & relation
extraction."** All papers are in
`OneDrive/.../Research.AI/Literature/Human Selected Literature/Knowledge Graphs/` unless noted.
Ordered as the *intended* chain — **verify the real edges in Step 1** and reorder/swap as needed.

| # | Paper (folder filename) | Intended role in the chain |
|---|---|---|
| 1 | `Public/Large_Scale_KG_CS.pdf` (likely CS-KG / AI-KG, Dessì et al.) | Foundational hub — earlier automated KG-of-science that later work cites |
| 2 | `Automated KG construction.pdf` | Core construction method |
| 3 | `Constructing KGs with LLMs.pdf` | LLM-based construction (spine anchor) |
| 4 | `A_Knowledge_Graph-based_RAG_for_Cross-Document_Information_Extraction.pdf` | Cross-document extraction |
| 5 | `Completing_Scientific_Facts_in_Knowledge_Graphs_of_Research_Concepts.pdf` | Fact/relation completion |
| 6 | `KG-EmpiRE_...Requirements_Engineering.pdf` | Domain KG application (community-maintainable lit-review KG) |
| 7 | `Personal Research Knowledge Graphs.pdf` | Per-researcher KG application |
| 8 | `Assessing Research Papers with Knowlege Graphs.pdf` | Evaluation / downstream use of the KG |
| 9 | `General Research Automation (idea generation, etc.)/Idea Generation using Knowledge Graphs + LLMs.pdf` | Downstream: KG → new ideas |
| 10 | `General Research Automation (idea generation, etc.)/Problem-Solution Dataset for Automated Extraction.pdf` | Directly exercises the `Problem` node — the escalation target |

**Tighter core (if 10 is too many):** #1–#5 form the pure KG-construction chain. #6–#10 add breadth and
the problem-escalation angle.

## Gotchas

- **PDFs are OneDrive online-only placeholders** — materialize them (Step 0.3) or nothing can open them.
- **Don't fabricate DOIs.** Read them off the paper; confirm on Semantic Scholar.
- **Independence of the two reviews is the point** — don't let the Claude review see the human review (or
  vice versa) until reconciliation.
- If a shortlisted paper turns out **not** to connect to the others by citation, swap it — a connected
  set is the requirement, not this exact list.
