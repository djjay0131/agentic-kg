"""`NORM`, the join key `K`, and the §3.4 fragment grammar.

Every case below is one the mapping spec names with evidence, so a failure here
points at a specific claim the spec makes rather than at a taste preference.
`test_shadow_is_falsifiable.py` mutates `norm` and `join_key` and asserts the
named tests in this module go red.
"""

from __future__ import annotations

import pytest
from agentic_kg.migration.ingestion.identity import (
    Fragment,
    join_key,
    norm,
    normalize_doi,
    parse_fragment,
    span_digest,
)

#: The four names §3.1 measures the punctuation disagreement on. Recorded here
#: because they are the evidence for the rule, not decoration: an earlier draft
#: of the spec stripped punctuation and these four are among the 26 of 194
#: corpus names (13%) where that produced a different key.
PUNCTUATED_NAMES = (
    "fine-tuning",
    "part-of-speech tagging",
    "DeLong's test",
    "paraphrase-distilroberta-base-v2",
)


def test_norm_preserves_punctuation() -> None:
    """Hyphens and apostrophes survive `NORM`.

    The defect this catches: a `NORM` that strips punctuation. It merges
    distinct entities — `part-of-speech tagging` and `part of speech tagging`
    become one key — and it disagrees with §6.4.1's join on 13% of the corpus,
    which silently files 13% of legacy ids as `no_canonical_counterpart`.
    """
    assert PUNCTUATED_NAMES, "the punctuation cases must be non-empty"
    for name in PUNCTUATED_NAMES:
        normalized = norm(name)
        for mark in "-'":
            if mark in name:
                assert mark in normalized, (
                    f"NORM({name!r}) = {normalized!r} dropped {mark!r}; "
                    f"punctuation is semantic here and stripping it merges "
                    f"distinct entities"
                )


def test_norm_rejoins_a_hyphen_broken_across_a_line() -> None:
    """The one corruption the gold files document.

    §6.4.1's headline example: a PDF breaks `paraphrase-distilroberta-base-v2`
    across a line and the hyphen is dropped by the typesetter's own rule. A raw
    hash over the quote would treat the two forms as different spans.
    """
    broken = "paraphrase-\ndistilroberta-base-v2"
    assert norm(broken) == norm("paraphrasedistilroberta-base-v2")
    assert "\n" not in norm(broken)


def test_norm_folds_the_ligature_nfkc_handles() -> None:
    """`Classiﬁer` (U+FB01) folds to `classifier`, per the NFKC step."""
    assert norm("Classiﬁer") == "classifier"


def test_norm_collapses_whitespace_and_casefolds() -> None:
    assert norm("  Knowledge   Graph\t\n Embedding ") == "knowledge graph embedding"


def test_norm_does_not_rejoin_a_hyphen_inside_a_line() -> None:
    """The rejoin rule is narrow, and it has to be.

    A rule broad enough to delete any hyphen would take `fine-tuning` to
    `finetuning` and collapse it with a genuinely different surface form. This
    is the case that separates "rejoin a line break" from "strip hyphens".
    """
    assert norm("fine-tuning") == "fine-tuning"


def test_span_digest_is_stable_and_normalized() -> None:
    """Equal after `NORM` implies equal span.

    The defect: hashing the raw quote. Two renderings of one passage — one with
    a line-broken hyphen, one without — would get different spans, so the same
    mention would fail to join to itself.
    """
    a = span_digest("paraphrase-\ndistilroberta-base-v2 was used")
    b = span_digest("paraphrasedistilroberta-base-v2   was used")
    assert a == b
    assert len(a) == 16


def test_span_digest_separates_different_quotes() -> None:
    """...and a digest that collapsed everything would satisfy the test above.

    So the companion assertion: genuinely different quotes get different spans.
    Without this, `span_digest` could `return "0" * 16` and stay green.
    """
    assert span_digest("one passage") != span_digest("a different passage")


def test_join_key_is_scoped_by_doi() -> None:
    """The same surface in two papers is two keys.

    §6.4.1: `doi` scopes the key to a paper, because the same concept recurs
    across the citation chain *by design*. Dropping it would merge every
    paper's mention of "knowledge graph" into one identity at ingestion time —
    exactly the decision the adoption moves to KGCS entity resolution.
    """
    a = join_key(
        entity_type="Problem", doi="10.1/a", surface="same", quoted_text="same quote"
    )
    b = join_key(
        entity_type="Problem", doi="10.2/b", surface="same", quoted_text="same quote"
    )
    assert a.paper_span != b.paper_span
    assert a.doi != b.doi


def test_join_key_is_scoped_by_entity_type() -> None:
    """One span in the corpus is cited by both a Model and a Method (§6.4.1)."""
    model = join_key(
        entity_type="Model", doi="10.1/a", surface="BERT", quoted_text="we use BERT"
    )
    method = join_key(
        entity_type="Method", doi="10.1/a", surface="BERT", quoted_text="we use BERT"
    )
    assert model.entity_type != method.entity_type
    assert model != method


def test_join_key_surface_discriminates_a_shared_span() -> None:
    """The fix for the 16% collision rate.

    §6.4.1 measures it: 32 of 194 ResearchConcept/Model/Method entries share a
    span, because one enumeration sentence evidences many entities — four
    distinct Models in `reconciled/paper_cskg2.gold.yml`. `(doi, quoted_text)`
    alone conflates them; the surface form splits them.
    """
    sentence = "We compare BERT, RoBERTa, DeBERTa and ELECTRA."
    keys = {
        join_key(
            entity_type="Model", doi="10.1/a", surface=name, quoted_text=sentence
        ).paper_span
        for name in ("BERT", "RoBERTa", "DeBERTa", "ELECTRA")
    }
    assert len(keys) == 4, f"one enumeration sentence collapsed to {len(keys)} key(s)"


def test_join_key_normalizes_its_surface() -> None:
    """Callers pass a raw surface; `NORM` is applied inside.

    The defect: a `join_key` that trusted callers to pre-normalize. One caller
    who forgets is the 13% disagreement, and there is no way to notice from the
    outside — both keys look perfectly well-formed.
    """
    assert (
        join_key(
            entity_type="Model", doi="10.1/A", surface="  BERT ", quoted_text="q"
        ).paper_span
        == join_key(
            entity_type="Model", doi="10.1/a", surface="bert", quoted_text="q"
        ).paper_span
    )


@pytest.mark.parametrize(
    "raw",
    ["10.1/A", "https://doi.org/10.1/A", "doi:10.1/A", "  10.1/a  "],
)
def test_normalize_doi_folds_case_and_resolver_prefixes(raw: str) -> None:
    assert normalize_doi(raw) == "10.1/a"


def test_fragment_round_trips() -> None:
    for fragment in (
        Fragment(section_slug="introduction", start=1024, end=1180),
        Fragment(section_slug="related_work", start=0, end=7, page=2),
    ):
        assert parse_fragment(fragment.render()) == fragment


def test_fragment_example_from_the_spec_parses() -> None:
    parsed = parse_fragment("sec:introduction#chars:1024-1180#page:2")
    assert parsed == Fragment(
        section_slug="introduction", start=1024, end=1180, page=2
    )


@pytest.mark.parametrize(
    "bad",
    [
        "chunk:0@chars:0-10",  # the KGIS grammar, not ours
        "sec:introduction",
        "sec:introduction#chars:10",
        "sec:Introduction#chars:1-2",  # slugs are lowercase
        "",
    ],
)
def test_parse_fragment_rejects_a_non_fragment(bad: str) -> None:
    """Raising, not returning `None`.

    A malformed coordinate read as "no coordinate" lets a projector silently
    drop the span it was supposed to carry, and the first listed case is the
    realistic one: KGIS's own `chunk:N@chars:` grammar arriving because someone
    used a plain `Chunk`.
    """
    with pytest.raises(ValueError):
        parse_fragment(bad)
