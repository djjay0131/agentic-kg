---
title: Named Resources in the Entity Model
nav_exclude: true
---

# Named resources have no home in the E-8 entity model

**Owner:** Victoria
**Status:** Open — awaiting colleague input (raised 2026-08-03)
**Blocks:** diffing importer output against `ground_truth_chain/reconciled/`

## The problem

The extraction schema offers four buckets for paper content: `Topic`, `ResearchConcept`,
`Model`, `Method`. A large class of entities that scholarly-KG papers talk about
constantly fits none of them:

| Kind | Examples in our 8-paper set |
|---|---|
| Knowledge graphs | CS-KG, AI-KG, ORKG, Wikidata, DBpedia, KG-EmpiRE |
| Ontologies / taxonomies | CSO, SKOS, PROV-O, VerbNet |
| Corpora / catalogues | MAG, OpenAlex, Semantic Scholar |
| Lexical resources | WordNet |
| Frameworks / libraries | SentenceTransformers, Stanford CoreNLP |
| Publishing formats | Nanopublications |

None is a `Model` (no weights, no architecture family). None is a `Method` (not a recipe
you apply). Calling them `ResearchConcept`s is a stretch — they are *instances*, not
techniques, theories or named ideas — and it puts "Wikidata" in the same bucket as
"retrieval augmented generation".

This is not a corner case for our validation set. The set was chosen for citation
connectivity around scholarly-KG construction, so **every paper in it is dense with named
resources**. On `cskg` alone, both reviewers independently flagged eleven of them.

## Why it blocks the diff

Because a gold file is binary, the decision changes the score in both directions:

- **If gold omits them** and the importer emits "CS-KG" as a concept, that is a false
  positive dragging `concept_precision` down on every paper in the set.
- **If gold includes them** as concepts, the importer *must* emit them or lose
  `concept_recall` — and we will have defined "Wikidata" as a research concept in the
  graph schema by the back door.

Either way the number we get out measures our labeling choice more than the importer's
behaviour. On `cskg` they are currently parked in `acceptable_extras` (neither scored nor
penalised), which is a holding position, not an answer.

## Options

**A. Score them as `ResearchConcept`s.** Zero code change; the importer probably already
does this. Cost: the concept layer silently becomes a mixed bag of techniques and named
artifacts, which degrades every downstream use of it — concept accumulation across the
citation chain (the whole point of this set) would count "CS-KG appears in 6 papers" as a
concept building evidence.

**B. Exclude them from gold and treat emissions as false positives.** Keeps the concept
layer clean and honest. Cost: we knowingly score correct-looking extractions as errors,
and the precision numbers will look bad for reasons that are not the importer's fault.
Needs the extractor prompt updated to say "do not extract named resources" or the penalty
is unfair.

**C. Add a `Resource` node type (E-9?).** Models the domain correctly, and for a
scholarly-KG project a `Resource` node is arguably a first-class citizen — it is what the
papers in this domain are *about*. Cost: new extractor, new prompt, new merge logic, new
gold key, and a taxonomy question of its own (is `CSO` a Resource that is also a Topic
source?). Largest change, best end state.

**D. Keep the `acceptable_extras` holding pattern indefinitely.** Unblocks the diff now at
the cost of leaving a documented hole: these entities are simply not validated, so we
learn nothing about how the importer handles the most common entity class in the set.

## Recommendation

**B for the current validation round, C as the target** — provided the extractor prompts
get the matching "not a named resource" instruction before we score precision, otherwise B
is unfair to the importer and the numbers are noise.

Reasoning: the reason for this validation set is watching a *concept* accumulate evidence
across a citation chain and escalate into a `Problem`. Option A directly corrupts that
signal, which is the one thing we cannot afford. C is right but is a feature, not a
labeling decision, and should not gate this round. B is the honest interim: it keeps the
concept layer clean, and the resulting precision hit is itself the argument for C.

## What is needed to close this

- [ ] Decision on A / B / C / D.
- [ ] If B: extractor prompt updated to exclude named resources, then re-run before scoring.
- [ ] If C: `Resource` spec in `llm/features/`, and a new `expected_resources` key in `SCHEMA.md`.
- [ ] Apply the outcome uniformly to all 8 reconciled files — `cskg`'s `acceptable_extras`
      block is the current inventory for that paper and each remaining paper will need its own.

## Where this is recorded

- `packages/core/tests/extraction/fixtures/ground_truth_chain/reconciled/paper_cskg.gold.yml`
  — `acceptable_extras.named_resources` (the eleven entities, with grounding rationale)
- `packages/core/tests/extraction/fixtures/ground_truth_chain/SCHEMA.md` — the
  `acceptable_extras` contract
