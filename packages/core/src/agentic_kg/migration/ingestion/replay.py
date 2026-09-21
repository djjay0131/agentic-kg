"""The deterministic completion client CI runs against. No provider, no network.

**KGIS already ships this, and agentic-kg does not.** The brief for this PR said
to reuse the repo's recorded-client patterns; there are none — an exhaustive
search of `packages/`, `scripts/` and `.github/` for recorded / replay /
cassette / vcr / fake-LLM infrastructure finds nothing but `MagicMock`s with
per-test `return_value`s. What does exist is upstream:
`kgis.extraction.client` ships `RecordingCompletionClient` (wraps any client,
captures `(prompt, system) -> response` keyed by a stable blake2b hash),
`ReplayCompletionClient` (serves them back, **raises `ReplayMiss` on an
unrecorded prompt** rather than inventing one) and `request_key`. This module
configures those; it reimplements none of them.

---

**What the replayed responses are, stated plainly, because it changes how the
numbers may be read.**

:func:`importer_replay_client` builds its responses from
`docs/ground-truth/importer-output/` — the committed record of what the
*existing* importer extracted from these eight papers. They are **not a
recording of a live model.** No provider call was made to produce them, and
this repo has no committed model recording to make one from.

The consequence is specific and must not be papered over: a shadow arm driven
by this client reproduces the legacy arm's findings by construction, so grading
it against the legacy arm measures nothing — it is §9.0's first shape, "compare
two results that are both derived from the artefact under test". This client
therefore exists to exercise **plumbing**: documents → sections → chunks →
prompts → parse → build → validate → evidence → ledger, deterministically, over
real committed text, with every coordinate and every identity key real. That is
a genuine end-to-end assertion about the adapter and a worthless one about
extraction quality.

Wiring the evaluation runner's `new` arm needs a *recording*, which is one
authorized provider run away: :func:`recording_client` wraps a real client,
:func:`save_recording` writes the fixture, :func:`replay_client_from_file`
loads it, and nothing else in this subpackage changes. Until that exists, the
`new` arm stays `ArmUnavailable` — see `arm_export.py`.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

import yaml

from agentic_kg.migration.ingestion._contracts import (
    CompletionClient,
    ExtractorConfig,
    RecordingCompletionClient,
    ReplayCompletionClient,
    request_key,
)
from agentic_kg.migration.ingestion.corpus import CorpusPaper
from agentic_kg.migration.ingestion.documents import SectionChunker
from agentic_kg.migration.ingestion.extractors import research_extractors

#: Confidence stamped on a replayed item. The importer output records no
#: per-item confidence for concepts, models or methods (its own `_caveats`
#: block says so: "no per-paper quoted_text/confidence retained (only
#: Problems do)"), so one declared value is used rather than a per-item
#: invention. It sits at the legacy schemas' own default, `0.8`.
REPLAY_CONFIDENCE = 0.8

#: Fallback quote for an item the importer recorded without one. `quoted_text`
#: is mandatory for the `Problem` builder (it is half of `K`) and for the
#: surface builders' evidence, and the importer kept quotes only for problems.
#: A paper-scoped placeholder keeps the span component of `K` distinct per
#: (paper, surface) instead of collapsing every unquoted item onto one span.
_NO_QUOTE = "no quoted_text recorded by the importer for: "


def _items_for(extractor_id: str, payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The JSON items one extractor would have been given for one paper."""
    if extractor_id == "research_concept":
        return [
            {
                "name": entry.get("name"),
                "description": entry.get("description"),
                "quoted_text": _NO_QUOTE + str(entry.get("name")),
                "confidence": REPLAY_CONFIDENCE,
            }
            for entry in payload.get("research_concepts") or []
            if isinstance(entry, dict) and entry.get("name")
        ]
    if extractor_id == "model":
        return [
            {
                "name": entry.get("name"),
                "description": entry.get("description"),
                "architecture": entry.get("architecture"),
                "model_type": entry.get("model_type"),
                "year_introduced": entry.get("year_introduced"),
                "quoted_text": _NO_QUOTE + str(entry.get("name")),
                "confidence": REPLAY_CONFIDENCE,
            }
            for entry in payload.get("models") or []
            if isinstance(entry, dict) and entry.get("name")
        ]
    if extractor_id == "method":
        return [
            {
                "name": entry.get("name"),
                "description": entry.get("description"),
                "method_type": entry.get("method_type"),
                "quoted_text": _NO_QUOTE + str(entry.get("name")),
                "confidence": REPLAY_CONFIDENCE,
            }
            for entry in payload.get("methods") or []
            if isinstance(entry, dict) and entry.get("name")
        ]
    if extractor_id == "topic":
        topic = payload.get("topic") or {}
        items: list[dict[str, Any]] = []
        for level in ("domain", "area", "subtopic"):
            name = topic.get(level) if isinstance(topic, dict) else None
            if isinstance(name, str) and name.strip():
                items.append(
                    {
                        "name": name.strip(),
                        "level": level,
                        "quoted_text": _NO_QUOTE + name.strip(),
                        "confidence": REPLAY_CONFIDENCE,
                    }
                )
        return items
    if extractor_id == "problem":
        return [
            {
                "statement": entry.get("statement"),
                "quoted_text": entry.get("quoted_text")
                or (_NO_QUOTE + str(entry.get("statement"))),
                "section": entry.get("section"),
                "confidence": REPLAY_CONFIDENCE,
            }
            for entry in payload.get("problems") or []
            if isinstance(entry, dict) and entry.get("statement")
        ]
    raise KeyError(
        f"no importer-output mapping for extractor {extractor_id!r}; "
        f"add one rather than replaying an empty response, which would look "
        f"like an extractor that found nothing"
    )


def importer_responses(
    papers: Sequence[CorpusPaper],
    *,
    extractors: Sequence[ExtractorConfig] | None = None,
) -> dict[str, str]:
    """`{request_key: response}` for every (paper, chunk, extractor) the run makes.

    Keys are computed with `kgis.extraction.request_key` against prompts
    rendered by `ExtractorConfig.render` — the same two functions the pipeline
    itself calls. A prompt edit therefore changes the keys and the affected
    extractor starts raising `ReplayMiss` instead of silently replaying a
    response captured for different instructions.

    A paper's items are served on its **first** chunk and `[]` on the rest. The
    importer output is per-paper and records no section, so attributing items to
    a particular section would be an invention; `[]` elsewhere is the honest
    shape and exercises the empty-response path, which is the one a real model
    hits most often.
    """
    configs = tuple(extractors) if extractors is not None else research_extractors()
    chunker = SectionChunker()
    responses: dict[str, str] = {}
    for paper in papers:
        payload = yaml.safe_load(paper.importer_path.read_text(encoding="utf-8")) or {}
        document = paper.to_document().to_kgis_document()
        chunks = chunker.chunk(document)
        for position, chunk in enumerate(chunks):
            for config in configs:
                system, prompt = config.render(chunk.text)
                items = _items_for(config.extractor_id, payload) if position == 0 else []
                responses[request_key(prompt, system)] = json.dumps(items)
    return responses


def importer_replay_client(
    papers: Sequence[CorpusPaper],
    *,
    extractors: Sequence[ExtractorConfig] | None = None,
) -> ReplayCompletionClient:
    """A deterministic client replaying the legacy importer's findings.

    Read the module docstring before using the resulting candidates as an
    evaluation arm: these responses are derived from the legacy arm, not from a
    model.
    """
    return ReplayCompletionClient(importer_responses(papers, extractors=extractors))


def replay_client_from_file(path: Path | str) -> ReplayCompletionClient:
    """Load a recording written by :func:`save_recording`.

    This is the slot a real provider recording drops into. `ReplayMiss` on a
    prompt the recording does not hold is the wanted behaviour: a replay that
    invented output for an unseen prompt would make "deterministic" a lie.
    """
    return ReplayCompletionClient.from_json(Path(path).read_text(encoding="utf-8"))


def recording_client(inner: CompletionClient) -> RecordingCompletionClient:
    """Wrap a real client to capture a replay fixture.

    `deterministic` mirrors `inner`, so recording a nondeterministic model
    captures one roll of the dice rather than making the model repeatable.
    """
    return RecordingCompletionClient(inner)


def save_recording(recording: RecordingCompletionClient, path: Path | str) -> Path:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    recording.save(destination)
    return destination


#: Environment variables whose presence means a live provider is reachable.
#: Read only by :func:`assert_no_live_provider`, never to *construct* a client.
LIVE_PROVIDER_ENV = ("OPENAI_API_KEY", "ANTHROPIC_API_KEY")


def assert_no_live_provider(environ: Mapping[str, str] | None = None) -> None:
    """Fail if a provider credential is visible to the test path.

    Belt to the braces of "the client is a required argument with no default".
    The default is the structural guarantee; this is the one that notices if
    someone later adds a convenience fallback, because a fallback would only
    ever do something when a key is present.
    """
    env = os.environ if environ is None else environ
    present = sorted(name for name in LIVE_PROVIDER_ENV if env.get(name))
    if present:
        raise AssertionError(
            f"a live provider credential is visible to the shadow-ingestion test "
            f"path: {', '.join(present)}. This path must run against a replay "
            f"client only; unset the variable or run the suite in an isolated env."
        )


def iter_request_keys(responses: Iterable[str]) -> tuple[str, ...]:
    """Stable, sorted view of a recording's keys — for diffing two fixtures."""
    return tuple(sorted(responses))


__all__ = [
    "LIVE_PROVIDER_ENV",
    "REPLAY_CONFIDENCE",
    "assert_no_live_provider",
    "importer_replay_client",
    "importer_responses",
    "iter_request_keys",
    "recording_client",
    "replay_client_from_file",
    "save_recording",
]
