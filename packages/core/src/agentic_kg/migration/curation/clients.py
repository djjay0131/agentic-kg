"""Completion clients for the bounded-adviser adjudication stage.

Three clients, one seam. KGCS's ``CompletionPort`` is the only LLM surface the
advisers touch, and it is deliberately provider-free; this module supplies the
adopter-side adapters:

- :class:`SyntheticAdmissionOracle` — a deterministic, **SYNTHETIC** client that
  admits every eligible candidate with the evidence it was given. It calls no
  model and makes no network request. It exists to validate *wiring*: that the
  question is built, the request keyed, the assessment parsed, the gate folded
  and the plan built. Any number produced through it measures the plumbing and
  **nothing about model quality**.
- :func:`record_admission_fixtures` / :func:`load_recorded_adviser` — the
  record/replay pair CI runs against. Recordings are keyed by KGCS's
  ``CompletionRequest.request_hash``; an unrecorded question raises
  ``CompletionMiss`` loudly rather than inventing an answer.
- :class:`OpenAICompletionClient` — the live adapter, built on this repo's
  existing ``extraction.llm_client`` and ``OPENAI_API_KEY``. It is used **only**
  when explicitly enabled for staging; no test imports it at call time.

The synthetic recordings are labelled SYNTHETIC in their filename, their
envelope, and the model id on every response, because the difference between
"the wiring works" and "the model admits these entities well" is the only thing
that matters about them.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from agentic_kg.migration.curation._contracts import (
    Candidate,
    CompletionError,
    CompletionPort,
    CompletionRequest,
    CompletionResponse,
    EngineResult,
    RecordedCompletionClient,
)
from agentic_kg.migration.curation.adjudication import (
    AdmissionRecommendation,
    CandidateAdmissionAdviser,
    admission_question,
    eligible_llm_assess_entities,
)

#: Stamped on every synthetic response so a recorded fixture can never be read
#: as a model output by accident.
SYNTHETIC_MODEL_ID = "SYNTHETIC-oracle"
SYNTHETIC_MODEL_VERSION = "1"

#: The schema version of a recordings file, so a future format change is a loud
#: mismatch rather than a silent misparse.
RECORDINGS_FORMAT = "agentic-kg.admission-recordings/1"


class _AdmissionPayload(BaseModel):
    """The JSON shape the live model is asked to return."""

    recommendation: str
    evidence_ids: list[str] = []
    confidence: float | None = None
    rationale: str = ""


class SyntheticAdmissionOracle:
    """A deterministic ``CompletionPort`` that admits with the supplied evidence.

    **SYNTHETIC.** It does not read the prompt and does not weigh the evidence;
    it echoes the evidence ids it was handed and recommends ``admit`` with a
    high confidence. That is deliberately the simplest thing that exercises the
    whole path, and it is why every number produced through it validates wiring
    rather than model quality.
    """

    def __init__(
        self,
        *,
        confidence: float = 0.99,
        model_id: str = SYNTHETIC_MODEL_ID,
        model_version: str = SYNTHETIC_MODEL_VERSION,
    ) -> None:
        self._confidence = confidence
        self._model_id = model_id
        self._model_version = model_version

    def complete(self, request: CompletionRequest) -> CompletionResponse:
        text = json.dumps(
            {
                "recommendation": AdmissionRecommendation.ADMIT.value,
                "evidence_ids": list(request.evidence_ids),
                "confidence": self._confidence,
                "rationale": (
                    "SYNTHETIC oracle: admits every candidate to validate wiring; "
                    "this is not a model judgement and no quality metric may be "
                    "read from it."
                ),
            },
            sort_keys=True,
        )
        return CompletionResponse(
            text=text,
            model_id=self._model_id,
            model_version=self._model_version,
        )


def record_admission_fixtures(
    candidates: Sequence[Candidate],
    engine_result: EngineResult,
    *,
    out_path: Path,
    oracle: CompletionPort | None = None,
) -> Path:
    """Write a recordings file for every eligible admission question.

    The deterministic record half of the record/replay pair. It builds each
    question with the *same* function the run uses, asks ``oracle`` for a
    response and keys it by ``request_hash`` — so a recording can never miss a
    question the run will ask. ``out_path`` is written as UTF-8 JSON, sorted.
    """
    port = oracle or SyntheticAdmissionOracle()
    # One adviser per recording is what keys the request: the template id/version
    # are part of the hash, so recordings and replay must use the same adviser.
    adviser = CandidateAdmissionAdviser(port=port)
    fixtures: dict[str, dict[str, Any]] = {}
    for candidate in eligible_llm_assess_entities(candidates, engine_result):
        request = adviser.build_request(admission_question(candidate))
        response = port.complete(request)
        fixtures[request.request_hash] = response.model_dump(mode="json")

    payload = {
        "format": RECORDINGS_FORMAT,
        "synthetic": True,
        "label": "SYNTHETIC",
        "note": (
            "Generated by SyntheticAdmissionOracle (deterministic, no model, no "
            "network). These recordings validate the adjudication wiring; any "
            "metric produced through them is not a measurement of model quality."
        ),
        "adviser_type": CandidateAdmissionAdviser.ADVISER_TYPE,
        "adviser_version": CandidateAdmissionAdviser.ADVISER_VERSION,
        "prompt_version": CandidateAdmissionAdviser.PROMPT_VERSION,
        "fixtures": dict(sorted(fixtures.items())),
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return out_path


def _load_fixtures(path: Path) -> dict[str, CompletionResponse]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    fmt = payload.get("format")
    if fmt != RECORDINGS_FORMAT:
        raise ValueError(
            f"{path} is not an admission-recordings file (format={fmt!r}, "
            f"expected {RECORDINGS_FORMAT!r})"
        )
    return {
        request_hash: CompletionResponse.model_validate(body)
        for request_hash, body in payload["fixtures"].items()
    }


def load_recorded_adviser(path: Path) -> CandidateAdmissionAdviser:
    """The replay adviser: a recorded client wired into the domain adviser.

    A missing fixture raises ``CompletionMiss`` from the adviser machinery — a
    loud wiring error, never a silent empty answer.
    """
    return CandidateAdmissionAdviser(port=RecordedCompletionClient(_load_fixtures(path)))


class OpenAICompletionClient:
    """The live adapter: this repo's OpenAI client behind KGCS's port.

    Used **only** when explicitly enabled for staging. It resolves the model and
    key through ``extraction.llm_client`` (``OPENAI_API_KEY``,
    ``OPENAI_EXTRACTION_MODEL``) rather than reading the environment here, so
    there is one place those names live. The heavy import is deferred to the
    first call, so importing the curation package never pulls in the LLM stack.

    ``complete`` is synchronous because KGCS's ``CompletionPort`` is; the repo's
    client is async, so the coroutine is driven to completion with
    ``asyncio.run``. A failure is raised as ``CompletionError`` — the typed
    recoverable failure the adviser machinery catches and turns into an abstain
    (law 1), never an exception that reaches the orchestrator.
    """

    def __init__(self, *, model: str | None = None, temperature: float = 0.0) -> None:
        self._model = model
        self._temperature = temperature

    def complete(self, request: CompletionRequest) -> CompletionResponse:  # pragma: no cover
        import asyncio

        try:
            from agentic_kg.extraction.llm_client import (
                LLMConfig,
                LLMProvider,
                OpenAIClient,
            )
        except Exception as exc:  # noqa: BLE001 - surface as a recoverable port error
            raise CompletionError(f"the OpenAI LLM stack is unavailable: {exc}") from exc

        config = LLMConfig(provider=LLMProvider.OPENAI, temperature=self._temperature)
        if self._model is not None:
            config.model = self._model
        client = OpenAIClient(config)

        async def _extract() -> str:
            response = await client.extract(request.prompt, _AdmissionPayload)
            return response.content.model_dump_json()

        try:
            text = asyncio.run(_extract())
        except Exception as exc:  # noqa: BLE001 - any provider failure must abstain
            raise CompletionError(f"OpenAI admission call failed: {exc}") from exc
        return CompletionResponse(
            text=text,
            model_id=self._model or config.model,
            model_version="live",
        )


__all__ = [
    "RECORDINGS_FORMAT",
    "SYNTHETIC_MODEL_ID",
    "SYNTHETIC_MODEL_VERSION",
    "OpenAICompletionClient",
    "SyntheticAdmissionOracle",
    "load_recorded_adviser",
    "record_admission_fixtures",
]
