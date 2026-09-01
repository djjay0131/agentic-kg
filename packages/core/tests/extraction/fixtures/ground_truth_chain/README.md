# Ground-Truth Chain — Importer Validation Set (Output A)

**Owner:** Victoria
**Created:** 2026-07-27
**Task brief:** `docs/ground-truth/README.md`

Human-verified answer key for the entity-expansion importer
(`ingest_papers` → Topic / ResearchConcept / Model / Method / Problem / Citation),
built over an 8-paper citation chain so a single concept recurs across the chain and
accumulates evidence. When the importer runs live on these same papers, its output is
diffed against the `reconciled/` records here.

## Why a sibling of `e8_eval/` and not part of it

`e8_eval/` and this directory share a *format* but not a *purpose*, and merging them
would break the former.

| | `e8_eval/` | `ground_truth_chain/` (here) |
|---|---|---|
| Purpose | Gate extractor precision/recall (AC-12 / AC-17) | Validate the live importer end-to-end |
| Selection rule | **Exactly one paper per area** — NLP, CV, IR, ML, DM/Agents | **Citation connectivity** — 8 papers, 10 verified edges, one connected component |
| Topical spread | Deliberately wide | Deliberately narrow (all scholarly-KG construction) |

`test_e8_eval.py::_discover_eval_fixtures()` globs `FIXTURE_DIR.glob("paper_*.gold.yml")`
against `e8_eval/` only. That glob is non-recursive, so this directory is invisible to it —
which is the point. Dropping our 8 knowledge-representation papers into `e8_eval/` would
silently sweep them into the AC-12 gate and destroy its one-per-area contract.

## Why YAML in the repo, and in this shape

Step 2 of the brief weighed spreadsheet vs. repo files vs. a gold Neo4j. Repo YAML wins
because it is version-controlled and diffable, and because the scoring functions in
`tests/extraction/test_e8_eval.py` already speak this schema — matching it means that when
the eval runner is written, no fixture migration is needed.

**Caveat, recorded honestly:** those scoring *functions* (`topic_precision`,
`concept_precision`, `concept_recall`, `model_precision`, `method_precision`,
`model_method_recall`) are implemented and unit-tested, but the *runner* is not.
`test_e8_eval_gates` skips unconditionally, there is no YAML loader (the module's
`import yaml` is unused), and there is no cross-paper aggregation. Gold files are
necessary but not sufficient to make any gate run.

## Layout

```
ground_truth_chain/
├── README.md                        # this file
├── SCHEMA.md                        # field-by-field labeling convention
├── human/paper_<slug>.gold.yml      # Victoria's independent review
├── claude/paper_<slug>.gold.yml     # Claude's independent review
└── reconciled/paper_<slug>.gold.yml # the agreed answer key + disagreement notes
```

The three subdirectories keep the two reviews **independent**, which is the whole point of
having two. Neither reviewer reads the other's file until reconciliation.

Only `reconciled/` is the answer key. `human/` and `claude/` are retained as evidence —
the disagreements between them are a deliverable in their own right, since they map where
the importer's job is hardest.

## The 8 papers

Slugs are stable identifiers; `cites_within_set` refers to them.

| Slug | Paper | Year | DOI | PDF (verified 2026-07-27) |
|---|---|---|---|---|
| `cskg` | CS-KG: A Large-Scale Knowledge Graph of Research Entities and Claims in Computer Science | 2022 | `10.1007/978-3-031-19433-7_39` | `Large_Scale_KG_CS.pdf` |
| `cskg2` | CS-KG 2.0: A Large-scale Knowledge Graph of Computer Science | 2025 | `10.1038/s41597-025-05200-8` | `CS_KG_2.0_2025.pdf` |
| `kg_construction_survey` | Construction of Knowledge Graphs: Current State and Challenges | 2023 | `10.2139/ssrn.4605059` | `Current_State_Challenges.pdf` |
| `llm_ontology_gen` | Large Language Models for Scholarly Ontology Generation | 2025 | `10.1016/j.ipm.2025.104262` | `LLMs_for_Scholarly_Ontology_Generation.pdf` |
| `fact_completion` | Completing Scientific Facts in Knowledge Graphs of Research Concepts | 2022 | `10.1109/access.2022.3220241` | `Completing_Scientific_Facts_in_Knowledge_Graphs_of_Research_Concepts.pdf` |
| `kg_validation_hitl` | Knowledge Graph Validation by Integrating LLMs and Human-in-the-Loop | 2025 | `10.1016/j.ipm.2025.104145` | `KG_Validation_HumanInTheLoop.pdf` |
| `hypothesis_generation` | Research Hypothesis Generation over Scientific Knowledge Graphs | 2025 | `10.1016/j.knosys.2025.113280` | `Research_Hypothesis_Generation.pdf` |
| `empire` | Divide and Conquer the EmpiRE: A Community-Maintainable KG of Empirical Research in Requirements Engineering | 2023 | `10.1109/esem56168.2023.10304795` | `KG-EmpiRE.pdf` |

PDFs live in `ground-truth-papers/` at the repo root (not committed). All eight were
confirmed against their first page on 2026-07-27 — title and, where printed, DOI.

**Verified citation edges** (A cites B, from OpenAlex `referenced_works`; 10 edges, one
connected component):

```
cskg2                  → cskg, fact_completion
kg_construction_survey → cskg
llm_ontology_gen       → cskg, kg_validation_hitl
kg_validation_hitl     → cskg, fact_completion
hypothesis_generation  → cskg, fact_completion
empire                 → cskg
```

Curation used OpenAlex; the importer uses Semantic Scholar for `CITES`. Expect small
source-to-source differences — `cites_within_set` should record what the *importer* will
populate, so re-check against Semantic Scholar before treating an edge diff as a bug.

## Conventions

See `SCHEMA.md` for the field-by-field contract. The two rules most likely to be
violated:

1. **`expected_topics[].name` is a closed set.** It must be one of the 29 names in
   `packages/core/src/agentic_kg/knowledge_graph/data/seed_taxonomy.yml`. The extractor
   binds a pydantic `Literal` to that snapshot and is structurally incapable of emitting
   anything else — a free-text topic in gold scores every prediction as a miss.
2. **Matching is case-insensitive exact string equality.** No stemming, no fuzzy match, no
   embeddings. `"knowledge graphs"` does not match `"knowledge graph"`. Enumerate surface
   variants, including singular/plural, in `acceptable_aliases`.
