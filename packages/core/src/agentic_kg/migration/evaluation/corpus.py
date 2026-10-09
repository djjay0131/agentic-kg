"""Loading the ground-truth chain corpus into neutral, framework-free records.

This module knows the *fixture* format (`ground_truth_chain/reconciled/*.gold.yml`
and `docs/ground-truth/importer-output/*.yml`) and nothing about ``kg_eval``. The
separation is deliberate: the fixture contract is owned by ``SCHEMA.md`` and the
grading contract is owned by ``kg_eval``, and the only way to keep the adapter
honest is to make the translation between them one explicit, testable step
(:mod:`agentic_kg.migration.evaluation.adapter`) rather than a set of
assumptions smeared across a loader.

Three facts about this corpus drive every design choice here, and each was
re-verified against the files rather than taken on trust:

1. **Only two papers have reconciled gold** — ``cskg`` and ``cskg2``. ``human/``
   and ``claude/`` are independent *reviews*: evidence about what the answer key
   should say, never the answer key itself. This module refuses to read them, so
   no later caller can accidentally score against an unreconciled review.
   ``fact_completion`` has both reviews and no reconciliation; it is not
   scoreable, and pretending otherwise would manufacture ground truth.
2. **The same canonical appears in both papers 17 times** (e.g. ``"reification"``
   is scored gold in `cskg` *and* `cskg2`). A gold entity's identity is therefore
   ``(paper, type, canonical)``, not ``(type, canonical)`` — see
   :func:`ScoredEntity.scoped_key`. This is not a workaround for a ``kg_eval``
   restriction; it is what the fixture means. A recall obligation is an
   obligation *on one paper*, and `cskg2` failing to yield "reification" is a
   different miss from `cskg` failing to.
3. **``acceptable_extras`` uses two different YAML shapes** — ``{name, why}`` and
   ``{names: [...], why}``. Of the 61 extra surface forms in the reconciled
   files, 41 are in the ``{names: [...]}`` shape, all of them under the ad-hoc
   ``named_resources`` bucket. A loader that handles only ``{name, why}`` sees 20
   of 61 and silently readmits the other 41 into the precision denominator.
   :func:`_extra_names` handles both and is tested against the real counts.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

# --------------------------------------------------------------------------
# Buckets and entity types
# --------------------------------------------------------------------------

#: Scored gold key -> the bucket name used by ``acceptable_extras`` and by this
#: module's internal vocabulary. The two halves of a gold file name the same
#: four categories differently (``expected_topics`` vs ``topics``); mapping them
#: once here is what lets an extras entry be matched to the scored list it
#: qualifies, instead of being compared against every category.
GOLD_KEY_TO_BUCKET: Mapping[str, str] = {
    "expected_topics": "topics",
    "expected_concepts": "concepts",
    "expected_models": "models",
    "expected_methods": "methods",
}

#: Bucket -> the ``kg_contracts`` entity type the importer emits for it. These
#: must satisfy ``_ENTITY_TYPE_PATTERN`` (``^[A-Z][A-Za-z0-9]*$``) or
#: ``EntityCandidate`` construction fails at validation time.
BUCKET_TO_ENTITY_TYPE: Mapping[str, str] = {
    "topics": "Topic",
    "concepts": "ResearchConcept",
    "models": "Model",
    "methods": "Method",
}

#: Buckets that ``acceptable_extras`` may carry which are *not* one of the four
#: scored categories. ``SCHEMA.md`` allows a paper to add an ad-hoc bucket, and
#: `cskg`/`cskg2` both use ``named_resources`` for the entities blocked on the
#: open schema decision (docs/ground-truth/named-resources.md).
#:
#: An ad-hoc bucket has no entity type, so an extra recorded in one applies to
#: **every** category: nothing in the fixture says whether the importer will emit
#: "Wikidata" as a ResearchConcept or a Model, and guessing would make the filter
#: depend on the very decision it is waiting on.
AD_HOC_EXTRA_BUCKETS: frozenset[str] = frozenset({"named_resources"})

#: The bucket whose disposition is still open. Kept as a named constant because
#: three separate places need to agree on it: the extras filter, the
#: named-resource gate, and the report that explains why precision is null.
NAMED_RESOURCE_BUCKET = "named_resources"

#: Applies to every category — the sentinel used as a bucket key for extras that
#: came from an ad-hoc bucket.
ANY_BUCKET = "*"


# --------------------------------------------------------------------------
# Paper identity
# --------------------------------------------------------------------------

#: slug -> DOI for the eight papers of the validation set, transcribed from the
#: verified citation chain in ``docs/ground-truth/README.md``.
#:
#: This is a table of *identifiers*, not of labels, which is why it is allowed to
#: exist as a constant: a DOI is a fact about which paper a slug names, settled
#: by the curation step, and it carries no judgment that reconciliation could
#: overturn. It deliberately does **not** come from the gold files, for two
#: reasons. The reconciled directory only covers two of the eight, so it could
#: not resolve a citation edge pointing at any of the other six. And
#: ``human/paper_empire.gold.yml`` carries `cskg`'s DOI under slug ``empire`` — a
#: copy-paste error in an unreconciled review; deriving identity from those files
#: would import that error.
#:
#: :func:`check_paper_dois` asserts this table against every source in the repo
#: that independently states a DOI, so a transcription slip fails a test rather
#: than silently mis-scoring a citation edge.
PAPER_DOIS: Mapping[str, str] = {
    "cskg": "10.1007/978-3-031-19433-7_39",
    "cskg2": "10.1038/s41597-025-05200-8",
    "kg_construction_survey": "10.2139/ssrn.4605059",
    "llm_ontology_gen": "10.1016/j.ipm.2025.104262",
    "fact_completion": "10.1109/access.2022.3220241",
    "kg_validation_hitl": "10.1016/j.ipm.2025.104145",
    "hypothesis_generation": "10.1016/j.knosys.2025.113280",
    "empire": "10.1109/esem56168.2023.10304795",
}

#: The eight importer-output files, in chain order, mapped to the slug they
#: describe. Needed to attribute a legacy-arm output file to a gold paper: the
#: files are named by chain position and title, and carry ``paper.n``/``paper.doi``
#: but no slug.
IMPORTER_OUTPUT_SLUGS: Mapping[str, str] = {
    "1-cs-kg.yml": "cskg",
    "2-cs-kg-2-0.yml": "cskg2",
    "3-construction-of-kgs.yml": "kg_construction_survey",
    "4-llms-for-scholarly-ontology-generation.yml": "llm_ontology_gen",
    "5-completing-scientific-facts.yml": "fact_completion",
    "6-kg-validation-hitl.yml": "kg_validation_hitl",
    "7-research-hypothesis-generation.yml": "hypothesis_generation",
    "8-divide-and-conquer-the-empire.yml": "empire",
}


def normalize_doi(doi: str) -> str:
    """Case-fold and strip a DOI so two spellings of one identifier compare equal.

    DOIs are case-insensitive, and this corpus exercises that: the importer
    emitted ``10.1109/ACCESS.2022.3220241`` where the curation table and both
    gold reviews say ``10.1109/access.2022.3220241``. Without folding, the DOI
    resolves to no slug in the eight-paper set, so ``_resolve_cited_slugs``
    drops it: the one citation edge the legacy importer actually got right is
    never emitted as a candidate at all. Citation recall falls from 0.5 to 0.0
    (tp 0, fp 0, fn 2) purely on letter case.

    Note the failure is a *silent drop*, not a false positive — the edge
    disappears rather than being scored wrong, which is the harder kind to
    notice. Nothing in the report would look anomalous; recall would simply be
    lower, and the natural reading would be that the importer missed a citation
    it in fact found.
    """
    return doi.strip().casefold()


def normalize_surface(text: str) -> str:
    """Normalize an entity surface form for alias lookup.

    Whitespace is collapsed and case is folded. That is the whole of it — no
    stemming, no lemmatization, no edit distance, nothing tunable. The point of
    ``kg_eval``'s matching module is that a true positive is literal key
    equality rather than a similarity threshold someone could adjust until an
    arm looks good, and a normalizer with a knob in it would give that property
    back. Case and whitespace are safe because they are applied *identically* to
    both sides and neither can turn two genuinely different entities into one.

    It is nonetheless load-bearing: gold canonicals are authored lowercase
    ("information extraction pipeline") and the importer emits Title Case
    ("Information Extraction Pipeline"), so an exact-bytes comparison would score
    every correct extraction as a miss.
    """
    return " ".join(text.split()).casefold()


# --------------------------------------------------------------------------
# Neutral records
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class ScoredEntity:
    """One entry of a scored gold list — a recall obligation on one paper."""

    slug: str
    bucket: str
    canonical: str
    aliases: tuple[str, ...] = ()
    quoted_text: str | None = None
    confidence: float | None = None

    @property
    def entity_type(self) -> str:
        return BUCKET_TO_ENTITY_TYPE[self.bucket]

    def scoped_key(self) -> str:
        """The paper-scoped semantic key: ``"<slug>/<canonical>"``.

        Scoping by paper is required, not cosmetic. Seventeen canonicals are
        scored gold in *both* reconciled papers; an unscoped key would make
        ``GoldSet`` raise on duplicate match keys (it rejects them precisely
        because the second would be permanently unmatchable), and if it did not
        raise, `cskg2` finding "reification" would silently satisfy `cskg`'s
        obligation to find it.
        """
        return f"{self.slug}/{self.canonical}"

    def surfaces(self) -> tuple[str, ...]:
        """Every surface form that identifies this entity: canonical + aliases."""
        return (self.canonical, *self.aliases)


@dataclass(frozen=True)
class CitationEdge:
    """One ``cites_within_set`` obligation: ``citing`` must be found to cite ``cited``."""

    citing_slug: str
    cited_slug: str

    @property
    def cited_has_reconciled_gold(self) -> bool:
        """Does the cited paper itself have a reconciled answer key?

        ``False`` for `cskg2` -> `fact_completion`: the edge is a real obligation
        on `cskg2` (its own reviewers agreed the citation is there), but the
        endpoint is a paper we have no reconciled labels for. That does not
        invalidate the edge — it does mean a "citation recall over the chain"
        claim rests on one gold-on-both-ends edge, not two, and the report says so
        rather than letting a reader infer coverage the corpus does not have.
        """
        return self.cited_slug in RECONCILED_SLUGS


@dataclass(frozen=True)
class AcceptableExtra:
    """One ``acceptable_extras`` name: correct-if-emitted, never required.

    ``bucket`` is :data:`ANY_BUCKET` when the name came from an ad-hoc bucket.
    """

    slug: str
    bucket: str
    name: str
    why: str
    source_bucket: str


@dataclass(frozen=True)
class ReconciledPaper:
    """One reconciled answer key, in framework-neutral form."""

    slug: str
    doi: str
    title: str
    entities: tuple[ScoredEntity, ...]
    citations: tuple[CitationEdge, ...]
    extras: tuple[AcceptableExtra, ...]
    disagreement_count: int

    def entities_in(self, bucket: str) -> tuple[ScoredEntity, ...]:
        return tuple(e for e in self.entities if e.bucket == bucket)


@dataclass(frozen=True)
class ArmEntity:
    """One entity an arm emitted for one paper, before any grading."""

    slug: str
    bucket: str
    name: str
    aliases: tuple[str, ...] = ()
    quoted_text: str | None = None
    confidence: float | None = None


@dataclass(frozen=True)
class ArmPaper:
    """What an arm produced for one paper, plus whether it ran at all.

    ``status`` is the importer's own word: ``extracted`` means the pipeline
    completed, anything else (``stub``, ``metadata-only (PDF acquisition
    failed)``) means it did not. :attr:`succeeded` is the discriminator that
    keeps a pipeline failure from being read as an extraction of nothing — four
    of the eight importer-output files are in the second state, and counting
    their empty entity lists as zero recall would blame the extractor for a PDF
    that was never fetched.
    """

    slug: str
    status: str
    entities: tuple[ArmEntity, ...] = ()
    citations: tuple[CitationEdge, ...] = ()

    @property
    def succeeded(self) -> bool:
        return self.status.strip().lower() == "extracted"


@dataclass(frozen=True)
class CorpusCoverage:
    """How much of the corpus an arm actually processed, kept apart from grading.

    This is deliberately *not* folded into the graded arm's failure rate. The
    graded scope is the papers that have reconciled gold (two); the corpus is
    eight. An ``ArmOutput`` whose ``attempted`` said 8 while its candidates came
    from 2 papers would report a failure rate over a denominator its precision
    and recall do not share, and a reader comparing the two numbers would be
    comparing different populations.

    So both are reported, labelled: ``graded_*`` is the population the metrics
    describe, ``corpus_*`` is how representative that population is.
    """

    corpus_attempted: int
    corpus_failed: int
    graded_attempted: int
    graded_failed: int

    @property
    def corpus_failure_rate(self) -> float | None:
        if self.corpus_attempted == 0:
            return None
        return self.corpus_failed / self.corpus_attempted


# --------------------------------------------------------------------------
# Loading
# --------------------------------------------------------------------------

#: The only two papers with a reconciled answer key. Hard-coded as an assertion
#: target, not as a filter: :func:`load_reconciled_papers` reads whatever is in
#: the directory, and :func:`check_reconciled_slugs` fails loudly if the set has
#: changed, so a newly reconciled paper is a test failure that prompts a review
#: rather than a silent change in what the numbers cover.
RECONCILED_SLUGS: frozenset[str] = frozenset({"cskg", "cskg2"})

_RECONCILED_DIRNAME = "reconciled"


def _entry_surface(entry: Mapping[str, Any]) -> str:
    """The canonical surface of a scored entry.

    Topics use ``name``; concepts/models/methods use ``canonical``. Accepting
    both here rather than branching per bucket keeps one code path over a schema
    that is genuinely the same shape with one key renamed.
    """
    for key in ("canonical", "name"):
        value = entry.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    raise ValueError(f"scored gold entry has neither 'canonical' nor 'name': {entry!r}")


def _extra_names(entry: Mapping[str, Any]) -> tuple[str, ...]:
    """Every name an ``acceptable_extras`` entry covers, across both YAML shapes.

    ``SCHEMA.md`` documents ``{name, why}``, but both reconciled files also use
    ``{names: [...], why}`` for the ``named_resources`` bucket, and that is where
    the volume is: 41 of the 61 extra surface forms in the corpus are in the
    plural shape. Reading only ``name`` would leave those 41 in the precision
    denominator as false positives — which is the exact failure the extras
    mechanism exists to prevent.
    """
    if "names" in entry:
        names = entry.get("names") or ()
        if isinstance(names, str):  # a scalar where a list was meant
            return (names.strip(),)
        return tuple(str(n).strip() for n in names if str(n).strip())
    name = entry.get("name")
    if isinstance(name, str) and name.strip():
        return (name.strip(),)
    return ()


def _load_extras(slug: str, raw: Mapping[str, Any] | None) -> tuple[AcceptableExtra, ...]:
    out: list[AcceptableExtra] = []
    for source_bucket, items in (raw or {}).items():
        bucket = (
            ANY_BUCKET if source_bucket in AD_HOC_EXTRA_BUCKETS else str(source_bucket)
        )
        for item in items or ():
            why = str(item.get("why") or "").strip()
            for name in _extra_names(item):
                out.append(
                    AcceptableExtra(
                        slug=slug,
                        bucket=bucket,
                        name=name,
                        why=why,
                        source_bucket=str(source_bucket),
                    )
                )
    return tuple(out)


def load_reconciled_paper(path: Path) -> ReconciledPaper:
    """Parse one ``reconciled/paper_<slug>.gold.yml`` into a neutral record.

    Raises if the file's ``reviewer`` is not ``reconciled``. That check is the
    one guard against the single worst mistake available here — scoring against
    a ``human/`` or ``claude/`` review, which are two independent opinions about
    the answer, not the answer. The directory a file sits in is a convention;
    the ``reviewer`` key is the file's own claim about itself, and both must
    agree before anything is graded against it.
    """
    doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    reviewer = str(doc.get("reviewer") or "").strip()
    if reviewer != "reconciled":
        raise ValueError(
            f"{path} declares reviewer={reviewer!r}, not 'reconciled'. "
            "human/ and claude/ files are independent reviews, i.e. evidence about "
            "what the answer key should say — they are never ground truth and must "
            "not be scored against."
        )
    slug = str(doc["slug"]).strip()

    entities: list[ScoredEntity] = []
    for gold_key, bucket in GOLD_KEY_TO_BUCKET.items():
        for entry in doc.get(gold_key) or ():
            aliases = tuple(
                str(a).strip() for a in (entry.get("acceptable_aliases") or ()) if str(a).strip()
            )
            quote = entry.get("quoted_text")
            conf = entry.get("confidence")
            has_quote = isinstance(quote, str) and quote.strip()
            entities.append(
                ScoredEntity(
                    slug=slug,
                    bucket=bucket,
                    canonical=_entry_surface(entry),
                    aliases=aliases,
                    quoted_text=str(quote).strip() if has_quote else None,
                    confidence=float(conf) if isinstance(conf, (int, float)) else None,
                )
            )

    citations = tuple(
        CitationEdge(citing_slug=slug, cited_slug=str(cited).strip())
        for cited in doc.get("cites_within_set") or ()
    )

    return ReconciledPaper(
        slug=slug,
        doi=normalize_doi(str(doc["doi"])),
        title=str(doc.get("title") or slug),
        entities=tuple(entities),
        citations=citations,
        extras=_load_extras(slug, doc.get("acceptable_extras")),
        disagreement_count=len(doc.get("disagreements") or ()),
    )


def reconciled_dir(chain_root: Path) -> Path:
    return chain_root / _RECONCILED_DIRNAME


def load_reconciled_papers(chain_root: Path) -> tuple[ReconciledPaper, ...]:
    """Load every reconciled answer key, in deterministic (sorted) order."""
    directory = reconciled_dir(chain_root)
    if not directory.is_dir():
        raise FileNotFoundError(f"no reconciled gold directory at {directory}")
    return tuple(load_reconciled_paper(p) for p in sorted(directory.glob("*.gold.yml")))


def load_importer_output(directory: Path) -> tuple[ArmPaper, ...]:
    """Load the recorded legacy-importer output for all eight corpus papers.

    This is a *recorded* arm: a committed fixture of what the existing pipeline
    produced, which is what makes the whole runner replayable with no model call
    and no network. It is the legacy baseline, not a live re-run.
    """
    out: list[ArmPaper] = []
    for filename, slug in sorted(IMPORTER_OUTPUT_SLUGS.items()):
        path = directory / filename
        if not path.is_file():
            continue
        doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        paper = doc.get("paper") or {}
        entities: list[ArmEntity] = []
        for key, bucket in (
            ("research_concepts", "concepts"),
            ("models", "models"),
            ("methods", "methods"),
        ):
            for item in doc.get(key) or ():
                entities.append(
                    ArmEntity(
                        slug=slug,
                        bucket=bucket,
                        name=str(item.get("name") or "").strip(),
                        aliases=tuple(
                            str(a).strip() for a in (item.get("aliases") or ()) if str(a).strip()
                        ),
                    )
                )
        # `topic` is a single mapping of domain/area/subtopic, every level of
        # which is null across all eight files: the BELONGS_TO edge is never
        # persisted (the importer's own `_caveats` records this as a known bug).
        # Emitting nothing here is therefore accurate, and it is why topic
        # precision comes back insufficient rather than 0.0 — there are no topic
        # candidates to be wrong about.
        topic = doc.get("topic") or {}
        for level in ("domain", "area", "subtopic"):
            name = topic.get(level)
            if isinstance(name, str) and name.strip():
                entities.append(ArmEntity(slug=slug, bucket="topics", name=name.strip()))

        citations = tuple(
            CitationEdge(citing_slug=slug, cited_slug=cited_slug)
            for cited_slug in _resolve_cited_slugs(doc.get("cites_within_set") or ())
        )
        out.append(
            ArmPaper(
                slug=slug,
                status=str(paper.get("status") or "unknown"),
                entities=tuple(entities),
                citations=citations,
            )
        )
    return tuple(out)


_DOI_TO_SLUG: Mapping[str, str] = {normalize_doi(d): s for s, d in PAPER_DOIS.items()}


def _resolve_cited_slugs(values: Iterable[Any]) -> Iterator[str]:
    """Map the importer's ``cites_within_set`` entries onto corpus slugs.

    The gold files record slugs; the importer records DOIs (it populates CITES
    from Semantic Scholar, which speaks DOI). Both are accepted, and a DOI
    outside the eight-paper set is dropped rather than invented into a slug — an
    edge to a paper the corpus does not contain is not an error, it is simply
    not a ``cites_within_set`` edge and carries no obligation either way.
    """
    for value in values:
        text = str(value).strip()
        if not text:
            continue
        if text in PAPER_DOIS:
            yield text
            continue
        slug = _DOI_TO_SLUG.get(normalize_doi(text))
        if slug is not None:
            yield slug


# --------------------------------------------------------------------------
# Corpus self-checks
# --------------------------------------------------------------------------


def check_reconciled_slugs(papers: Iterable[ReconciledPaper]) -> None:
    """Fail if the reconciled set is not exactly the two papers we measured."""
    found = frozenset(p.slug for p in papers)
    if found != RECONCILED_SLUGS:
        raise AssertionError(
            f"reconciled gold set changed: expected {sorted(RECONCILED_SLUGS)}, "
            f"found {sorted(found)}. Every scoreable count in the evaluation report "
            "is derived from this set; re-measure before relying on the report."
        )


def check_paper_dois(chain_root: Path, importer_output_dir: Path) -> list[str]:
    """Cross-check :data:`PAPER_DOIS` against every independent DOI in the repo.

    Returns the list of disagreements (empty when the table is right). The table
    is hand-transcribed, and a transcription slip in it would silently break
    citation grading — a mistyped DOI turns a correct edge into a simultaneous
    false positive and false negative. Checking it against the reconciled files
    and the importer-output metadata makes that a test failure instead.
    """
    problems: list[str] = []
    for paper in load_reconciled_papers(chain_root):
        expected = PAPER_DOIS.get(paper.slug)
        if expected is None:
            problems.append(f"reconciled slug {paper.slug!r} is absent from PAPER_DOIS")
        elif normalize_doi(expected) != paper.doi:
            problems.append(
                f"{paper.slug}: PAPER_DOIS says {expected!r}, reconciled gold says {paper.doi!r}"
            )
    for filename, slug in sorted(IMPORTER_OUTPUT_SLUGS.items()):
        path = importer_output_dir / filename
        if not path.is_file():
            continue
        doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        doi = (doc.get("paper") or {}).get("doi")
        if not isinstance(doi, str):
            continue
        expected = PAPER_DOIS.get(slug)
        if expected is None or normalize_doi(expected) != normalize_doi(doi):
            problems.append(
                f"{filename}: PAPER_DOIS[{slug!r}]={expected!r} but importer output says {doi!r}"
            )
    return problems


__all__ = [
    "AD_HOC_EXTRA_BUCKETS",
    "ANY_BUCKET",
    "BUCKET_TO_ENTITY_TYPE",
    "GOLD_KEY_TO_BUCKET",
    "IMPORTER_OUTPUT_SLUGS",
    "NAMED_RESOURCE_BUCKET",
    "PAPER_DOIS",
    "RECONCILED_SLUGS",
    "AcceptableExtra",
    "ArmEntity",
    "ArmPaper",
    "CitationEdge",
    "CorpusCoverage",
    "ReconciledPaper",
    "ScoredEntity",
    "check_paper_dois",
    "check_reconciled_slugs",
    "load_importer_output",
    "load_reconciled_paper",
    "load_reconciled_papers",
    "normalize_doi",
    "normalize_surface",
    "reconciled_dir",
]
