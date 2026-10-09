# Frozen segmenter output — the committed-corpus pin

One JSON per ground-truth paper, holding exactly what `SectionSegmenter`
produces when run over the committed `../ground_truth_chain/paper_<slug>.txt`.

**These are generated. Do not hand-edit them.**

```
python scripts/measure_segmentation.py --freeze          # regenerate
python scripts/measure_segmentation.py --corpus committed # check for drift
pytest packages/core/tests/extraction/test_segmenter_frozen_corpus.py
```

## What a fixture holds

| Field | Asserted by the drift test | Purpose |
|---|---|---|
| `source_sha256`, `source_chars` | **yes** | pins the input, so the output cannot be compared against text that moved |
| `sections[]` — `type`, `title`, `chars` | **yes** | catches a resegmentation that preserves the character total |
| `extractor_input` | **yes** | the exact string the entity extractors would receive |
| `extractor_input_sha256`, `extractor_input_chars` | **yes** | the same fact in a form a CI log can print |
| `segmenter_sha256` | no | provenance; reported on failure |
| `keeplist`, `keeplist_sha256` | no | provenance; reported on failure |

`extractor_input` is stored **in full and in the clear**, not as a hash alone.
A hash tells a reviewer that something moved and nothing about what, and this is
the artifact that both arms of a legacy-vs-new comparison have to agree on — so
it is the artifact that gets reviewed.

## Why output and not a commit sha

PyMuPDF and the PDF bytes drift independently of this repo. Pinning a commit
would be too strict (a pure refactor goes red) and too weak (a matching commit
proves nothing about the text). `segmenter_sha256` is therefore recorded but
never asserted; when output *does* drift, the drift report says whether the
segmenter source changed too, so the reviewer knows whether the change was
intentional.

## Provenance of the inputs

The `paper_<slug>.txt` files are **not** raw PDF text. They are hand-verified,
keep-list-filtered gold extractor input, produced by
`scripts/segment_ground_truth.py` from the gitignored PDFs. What that buys and
what it hides is written up in `docs/ground-truth/corpus-readiness.md`.
