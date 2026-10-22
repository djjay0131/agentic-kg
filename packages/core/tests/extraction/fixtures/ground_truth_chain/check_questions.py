#!/usr/bin/env python3
"""Validate questions.v1.yml against the ground-truth-chain paper texts.

Checks: every gold quote is a substring of its paper after whitespace
normalisation; ids unique; kinds valid; per-kind counts; each paper touched
by >= 2 questions; quote length 8-40 words; unanswerable has empty gold;
cross_paper cites >= 2 distinct papers.

Usage: python check_questions.py [path/to/questions.yml]
Stdlib + PyYAML only. Exit code 0 on success, 1 on any failure.
"""
from __future__ import annotations

import re
import sys
from collections import Counter
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
SLUGS = [
    "cskg", "cskg2", "kg_construction_survey", "llm_ontology_gen",
    "fact_completion", "kg_validation_hitl", "hypothesis_generation", "empire",
]
KINDS = {"single_fact", "multi_fact", "cross_paper", "comparison", "unanswerable"}
TOTAL = 30
# Exact count for unanswerable; approximate (+/-2) targets for the rest.
EXACT_COUNTS = {"unanswerable": 3}
APPROX_COUNTS = {"single_fact": 12, "multi_fact": 6, "cross_paper": 6, "comparison": 3}
APPROX_TOL = 2
MIN_WORDS, MAX_WORDS = 8, 40
MIN_TOUCHES_PER_PAPER = 2

_WS = re.compile(r"\s+")


def norm(text: str) -> str:
    return _WS.sub(" ", text).strip()


def main(argv: list[str]) -> int:
    qpath = Path(argv[1]) if len(argv) > 1 else HERE / "questions.v1.yml"
    data = yaml.safe_load(qpath.read_text(encoding="utf-8"))
    texts = {s: norm((HERE / f"paper_{s}.txt").read_text(encoding="utf-8")) for s in SLUGS}

    errors: list[str] = []
    questions = data.get("questions") or []
    n_quotes = 0

    if len(questions) != TOTAL:
        errors.append(f"expected {TOTAL} questions, found {len(questions)}")

    ids = [q.get("id") for q in questions]
    for qid, c in Counter(ids).items():
        if c > 1:
            errors.append(f"duplicate id {qid!r} ({c}x)")

    kinds = Counter()
    touches = Counter()
    for q in questions:
        qid = q.get("id")
        kind = q.get("kind")
        if kind not in KINDS:
            errors.append(f"{qid}: invalid kind {kind!r}")
        kinds[kind] += 1
        for field in ("question", "reference_answer"):
            if not str(q.get(field) or "").strip():
                errors.append(f"{qid}: missing {field}")

        gold = q.get("gold")
        if gold is None:
            gold = []
            errors.append(f"{qid}: gold key missing")
        papers_listed = list(q.get("papers") or [])
        for p in papers_listed:
            if p not in texts:
                errors.append(f"{qid}: unknown paper {p!r} in papers")

        if kind == "unanswerable":
            if gold:
                errors.append(f"{qid}: unanswerable question must have empty gold")
            continue
        if not gold:
            errors.append(f"{qid}: answerable question has empty gold")

        gold_papers = set()
        for i, g in enumerate(gold):
            n_quotes += 1
            paper, quote = g.get("paper"), g.get("quote") or ""
            tag = f"{qid}.gold[{i}]"
            if paper not in texts:
                errors.append(f"{tag}: unknown paper {paper!r}")
                continue
            gold_papers.add(paper)
            nq = norm(quote)
            words = len(nq.split(" ")) if nq else 0
            if not MIN_WORDS <= words <= MAX_WORDS:
                errors.append(f"{tag}: quote has {words} words (want {MIN_WORDS}-{MAX_WORDS})")
            if nq not in texts[paper]:
                errors.append(f"{tag}: quote not found in paper_{paper}.txt: {nq[:80]!r}...")

        if set(papers_listed) != gold_papers:
            errors.append(
                f"{qid}: papers {sorted(papers_listed)} != gold papers {sorted(gold_papers)}"
            )
        if kind == "cross_paper" and len(gold_papers) < 2:
            errors.append(
                f"{qid}: cross_paper needs >=2 distinct papers, has {sorted(gold_papers)}"
            )
        if kind in ("single_fact", "multi_fact") and len(gold_papers) != 1:
            errors.append(f"{qid}: {kind} should cite exactly one paper, has {sorted(gold_papers)}")
        if kind == "single_fact" and len(gold) != 1:
            errors.append(f"{qid}: single_fact should have 1 quote, has {len(gold)}")
        if kind == "multi_fact" and not 2 <= len(gold) <= 3:
            errors.append(f"{qid}: multi_fact should have 2-3 quotes, has {len(gold)}")
        for p in gold_papers:
            touches[p] += 1

    for k, want in EXACT_COUNTS.items():
        if kinds[k] != want:
            errors.append(f"kind {k}: expected exactly {want}, found {kinds[k]}")
    for k, want in APPROX_COUNTS.items():
        if abs(kinds[k] - want) > APPROX_TOL:
            errors.append(f"kind {k}: expected ~{want} (+/-{APPROX_TOL}), found {kinds[k]}")
    for s in SLUGS:
        if touches[s] < MIN_TOUCHES_PER_PAPER:
            errors.append(
                f"paper {s}: touched by {touches[s]} questions"
                f" (want >= {MIN_TOUCHES_PER_PAPER})"
            )

    print(f"questions: {len(questions)}  quotes checked: {n_quotes}")
    print("kinds:  " + ", ".join(f"{k}={kinds[k]}" for k in sorted(KINDS)))
    print("papers: " + ", ".join(f"{s}={touches[s]}" for s in SLUGS))
    if errors:
        print(f"\nFAIL ({len(errors)} problem(s)):")
        for e in errors:
            print(f"  - {e}")
        return 1
    print("\nOK: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
