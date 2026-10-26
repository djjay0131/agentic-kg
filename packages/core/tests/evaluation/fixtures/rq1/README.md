# Frozen RQ1 fixtures

These files are **generated**, committed, and consumed by
`.github/workflows/rq1-eval.yml`. CI never calls an LLM and never mutates them.

| File | Source | Records |
|---|---|---|
| `gold.jsonl` | `packages/core/tests/extraction/fixtures/ground_truth_chain/<reviewer>/*.gold.yml` | annotation records + coverage records |
| `predictions.jsonl` | `docs/ground-truth/importer-output/*.yml` (frozen legacy importer output) | prediction records |
| `corpus.json` | the eight-paper chain in `docs/ground-truth/README.md` | corpus manifest |

Regenerate after changing a gold file or the importer output:

```bash
python -m agentic_kg.evaluation.rq1 convert \
  --gold-yaml packages/core/tests/extraction/fixtures/ground_truth_chain \
  --out packages/core/tests/evaluation/fixtures/rq1/gold.jsonl

python -m agentic_kg.evaluation.rq1 convert \
  --importer-output docs/ground-truth/importer-output \
  --out packages/core/tests/evaluation/fixtures/rq1/predictions.jsonl
```

`packages/core/tests/evaluation/test_annotations.py::TestCommittedFixtures`
fails if a committed file and a fresh conversion differ, so the frozen fixture
cannot silently drift from its source.

The answer key is selected per paper: `reconciled` > `human` > `claude`. All
three review sets are retained in `gold.jsonl` because inter-annotator agreement
is computed over the reviewers before that projection. Only the selected
annotator's records are used for precision/recall/F1.

**Current honest coverage:** four papers have annotations (`cskg`, `cskg2`,
`empire`, `fact_completion`); only the `problem` field is annotated. The other
four papers and the five structured fields are ungraded and reported as honest
nulls. See `docs/evaluation/rq1-annotation-guide.md`.
