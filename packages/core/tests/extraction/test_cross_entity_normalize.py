"""E-7 Unit 7 — normalize_cross_entity + integrator wiring.

Covers AC-8 (accept drops loser), AC-9 (reject keeps both), AC-10 (triple
collision = 1 LLM call), AC-11 (clean paper short-circuits), AC-13
(embedder failure absorbed), AC-14 (audit on Paper node), AC-15
(integrator wiring order), AC-16 (client + embedder injection), AC-17
(existing extractor untouched), AC-19 (cost ceiling).
"""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from agentic_kg.extraction.cross_entity_normalizer import (
    DisambiguationDecision,
    NormalizationResult,
    audit_to_json,
    normalize_cross_entity,
)
from agentic_kg.extraction.kg_integration_v2 import integrate_paper_entities
from agentic_kg.extraction.llm_client import LLMError, LLMResponse, TokenUsage
from agentic_kg.extraction.pipeline import PaperExtractionResult
from agentic_kg.extraction.schemas import (
    ExtractedMethod,
    ExtractedModel,
    ExtractedResearchConcept,
)


def _concept(name: str = "attention"):
    return ExtractedResearchConcept(
        name=name, quoted_text="grounding text for concept here",
        confidence=0.9,
    )


def _model(name: str = "attention"):
    return ExtractedModel(
        name=name, quoted_text="grounding text for model here",
        confidence=0.9,
    )


def _method(name: str = "attention"):
    return ExtractedMethod(
        name=name, quoted_text="grounding text for method here",
        confidence=0.9,
    )


def _decision_response(
    *,
    picked: str = "concept",
    confidence: float = 0.9,
    grounded: bool = True,
    specific: bool = True,
    reason: str = None,
) -> LLMResponse:
    return LLMResponse(
        content=DisambiguationDecision(
            picked_kind=picked,
            confidence=confidence,
            is_grounded_in_paper_context=grounded,
            is_specific_to_one_kind=specific,
            rejection_reason=reason,
        ),
        usage=TokenUsage(total_tokens=200),
    )


@pytest.fixture
def llm_client() -> MagicMock:
    c = MagicMock()
    c.extract = AsyncMock()
    return c


@pytest.fixture
def embedder() -> MagicMock:
    e = MagicMock()
    e.generate_embedding = MagicMock()
    return e


# =============================================================================
# AC-11 — clean paper: zero pairs, zero LLM calls, is_clean=True
# =============================================================================


class TestCleanPaper:
    @pytest.mark.asyncio
    async def test_no_collisions_short_circuits(self, llm_client, embedder):
        # Embedder returns dissimilar (orthogonal) vectors so the fuzzy
        # scan emits nothing. The embedder IS called during the scan
        # (one per distinct name); only the LLM is bypassed.
        vec_map = {
            "attention": [1.0, 0.0, 0.0],
            "BERT": [0.0, 1.0, 0.0],
            "fine-tuning": [0.0, 0.0, 1.0],
        }
        embedder.generate_embedding.side_effect = lambda name: vec_map[name]
        result = PaperExtractionResult(
            concepts=[_concept("attention")],
            models=[_model("BERT")],
            methods=[_method("fine-tuning")],
        )
        out = await normalize_cross_entity(
            result,
            paper_title="A paper",
            embedder=embedder,
            llm_client=llm_client,
        )
        assert out.is_clean is True
        assert out.pairs_detected == 0
        assert out.pairs_resolved == 0
        assert out.pairs_rejected == 0
        # AC-11/AC-19 contract: zero pairs ⇒ zero LLM calls.
        llm_client.extract.assert_not_called()

    @pytest.mark.asyncio
    async def test_extraction_result_untouched_on_clean(
        self, llm_client, embedder,
    ):
        """AC-17 / AC-11: when no collisions, extractions pass through
        untouched."""
        result = PaperExtractionResult(
            concepts=[_concept("attention")],
            models=[_model("BERT")],
            methods=[_method("fine-tuning")],
        )
        original_ids = (
            id(result.concepts[0]), id(result.models[0]), id(result.methods[0]),
        )
        await normalize_cross_entity(
            result,
            paper_title="A",
            embedder=embedder,
            llm_client=llm_client,
        )
        assert len(result.concepts) == 1
        assert len(result.models) == 1
        assert len(result.methods) == 1
        assert (
            id(result.concepts[0]),
            id(result.models[0]),
            id(result.methods[0]),
        ) == original_ids


# =============================================================================
# AC-8 — accept path: drop loser, keep picked
# =============================================================================


class TestAcceptPath:
    @pytest.mark.asyncio
    async def test_concept_picked_method_dropped(self, llm_client, embedder):
        c = _concept("attention")
        m = _method("attention")
        result = PaperExtractionResult(
            concepts=[c], models=[], methods=[m],
        )
        llm_client.extract.return_value = _decision_response(
            picked="concept", confidence=0.95,
        )

        out = await normalize_cross_entity(
            result,
            paper_title="A",
            embedder=embedder,
            llm_client=llm_client,
        )

        assert out.pairs_detected == 1
        assert out.pairs_resolved == 1
        assert out.pairs_rejected == 0
        # Drop semantics — concept survives, method dropped (in place).
        assert result.concepts == [c]
        assert result.methods == []
        assert out.audit[0].picked == "concept"
        assert out.audit[0].dropped_kinds == ["method"]

    @pytest.mark.asyncio
    async def test_method_picked_concept_dropped(self, llm_client, embedder):
        c = _concept("attention")
        m = _method("attention")
        result = PaperExtractionResult(
            concepts=[c], models=[], methods=[m],
        )
        llm_client.extract.return_value = _decision_response(
            picked="method", confidence=0.95,
        )
        await normalize_cross_entity(
            result, paper_title="A", embedder=embedder,
            llm_client=llm_client,
        )
        assert result.concepts == []
        assert result.methods == [m]


# =============================================================================
# AC-9 — reject path: keep both (TL Q1 review)
# =============================================================================


class TestRejectPath:
    @pytest.mark.asyncio
    async def test_gates_fail_keeps_both(self, llm_client, embedder):
        c = _concept("attention")
        m = _method("attention")
        result = PaperExtractionResult(
            concepts=[c], models=[], methods=[m],
        )
        llm_client.extract.return_value = _decision_response(
            grounded=False, reason="insufficient context",
        )

        out = await normalize_cross_entity(
            result, paper_title="A", embedder=embedder,
            llm_client=llm_client,
        )

        # AC-9: both extractions REMAIN. The integrator will write both
        # nodes as it would have pre-E-7. Recall preserved.
        assert result.concepts == [c]
        assert result.methods == [m]
        assert out.pairs_rejected == 1
        assert out.pairs_resolved == 0
        assert out.audit[0].picked is None
        assert out.audit[0].dropped_kinds == []
        assert "insufficient context" in out.audit[0].rejection_reason

    @pytest.mark.asyncio
    async def test_llm_exception_keeps_both(self, llm_client, embedder):
        c = _concept("attention")
        m = _method("attention")
        result = PaperExtractionResult(
            concepts=[c], models=[], methods=[m],
        )
        llm_client.extract.side_effect = LLMError("openai down")

        out = await normalize_cross_entity(
            result, paper_title="A", embedder=embedder,
            llm_client=llm_client,
        )

        # AC-7 + AC-9: never raises; both extractions kept.
        assert result.concepts == [c]
        assert result.methods == [m]
        assert out.audit[0].picked is None

    @pytest.mark.asyncio
    async def test_low_confidence_keeps_both(self, llm_client, embedder):
        c = _concept("attention")
        m = _method("attention")
        result = PaperExtractionResult(
            concepts=[c], models=[], methods=[m],
        )
        llm_client.extract.return_value = _decision_response(confidence=0.4)

        await normalize_cross_entity(
            result, paper_title="A", embedder=embedder,
            llm_client=llm_client,
        )
        assert result.concepts == [c]
        assert result.methods == [m]


# =============================================================================
# AC-10 — triple collision is ONE LLM call
# =============================================================================


class TestTripleCollision:
    @pytest.mark.asyncio
    async def test_three_kinds_one_llm_call_two_drops(
        self, llm_client, embedder,
    ):
        c = _concept("attention")
        m = _model("attention")
        meth = _method("attention")
        result = PaperExtractionResult(
            concepts=[c], models=[m], methods=[meth],
        )
        llm_client.extract.return_value = _decision_response(
            picked="concept", confidence=0.95,
        )
        out = await normalize_cross_entity(
            result, paper_title="A", embedder=embedder,
            llm_client=llm_client,
        )
        # AC-19: exactly one LLM call regardless of triple-pair size.
        assert llm_client.extract.call_count == 1
        # AC-8/AC-10: model and method dropped; concept survives.
        assert result.concepts == [c]
        assert result.models == []
        assert result.methods == []
        assert out.audit[0].dropped_kinds == ["method", "model"]


# =============================================================================
# AC-19 — cost ceiling + failure isolation across pairs
# =============================================================================


class TestCostCeiling:
    @pytest.mark.asyncio
    async def test_n_pairs_make_exactly_n_calls(
        self, llm_client, embedder,
    ):
        # 2 independent cheap collisions: "attention" and "ResNet".
        c1, m1 = _concept("attention"), _method("attention")
        c2, mod2 = _concept("ResNet"), _model("ResNet")
        result = PaperExtractionResult(
            concepts=[c1, c2], models=[mod2], methods=[m1],
        )
        llm_client.extract.side_effect = [
            _decision_response(picked="concept", confidence=0.9),
            _decision_response(picked="model", confidence=0.9),
        ]
        out = await normalize_cross_entity(
            result, paper_title="A", embedder=embedder,
            llm_client=llm_client,
        )
        assert out.pairs_detected == 2
        assert llm_client.extract.call_count == 2

    @pytest.mark.asyncio
    async def test_fuzzy_pair_makes_one_llm_call(
        self, llm_client, embedder,
    ):
        """An embedding-only pair (no cheap-trigger) still costs
        exactly one LLM call per pair."""
        # Two extractions with completely different names but the
        # embedder reports them as similar.
        c = _concept("self-attention")
        m = _method("scaled dot product attention")
        result = PaperExtractionResult(
            concepts=[c], models=[], methods=[m],
        )
        embedder.generate_embedding.side_effect = [[1.0, 0.0], [0.99, 0.14]]
        llm_client.extract.return_value = _decision_response(
            picked="concept", confidence=0.9,
        )
        await normalize_cross_entity(
            result, paper_title="A", embedder=embedder,
            llm_client=llm_client,
        )
        assert llm_client.extract.call_count == 1
        # Sanity: the surviving extraction is the concept; method dropped.
        assert result.concepts == [c]
        assert result.methods == []

    @pytest.mark.asyncio
    async def test_first_pair_fails_second_still_runs(
        self, llm_client, embedder,
    ):
        c1, m1 = _concept("attention"), _method("attention")
        c2, mod2 = _concept("ResNet"), _model("ResNet")
        result = PaperExtractionResult(
            concepts=[c1, c2], models=[mod2], methods=[m1],
        )
        llm_client.extract.side_effect = [
            LLMError("first pair fails"),
            _decision_response(picked="model", confidence=0.95),
        ]
        out = await normalize_cross_entity(
            result, paper_title="A", embedder=embedder,
            llm_client=llm_client,
        )
        assert out.pairs_rejected == 1
        assert out.pairs_resolved == 1


# =============================================================================
# AC-16 — client + embedder injection
# =============================================================================


class TestInjection:
    @pytest.mark.asyncio
    async def test_injected_clients_used_exclusively(
        self, embedder,
    ):
        """The normalizer must not fall back to global singletons."""
        my_client = MagicMock()
        my_client.extract = AsyncMock(return_value=_decision_response())
        c = _concept("attention")
        m = _method("attention")
        result = PaperExtractionResult(
            concepts=[c], models=[], methods=[m],
        )
        await normalize_cross_entity(
            result, paper_title="A",
            embedder=embedder, llm_client=my_client,
        )
        my_client.extract.assert_awaited_once()


# =============================================================================
# audit_to_json — serialization contract
# =============================================================================


class TestAuditToJson:
    def test_clean_returns_empty_string(self):
        assert audit_to_json(NormalizationResult()) == ""

    def test_serializes_resolved_pair(self):
        from agentic_kg.extraction.cross_entity_normalizer import (
            NormalizationAuditEntry,
        )

        r = NormalizationResult(
            pairs_detected=1,
            pairs_resolved=1,
            audit=[
                NormalizationAuditEntry(
                    surface="attention",
                    trigger="exact",
                    picked="concept",
                    dropped_kinds=["method"],
                )
            ],
        )
        payload = json.loads(audit_to_json(r))
        assert payload == [
            {
                "surface": "attention",
                "trigger": "exact",
                "picked": "concept",
                "dropped_kinds": ["method"],
                "rejection_reason": None,
            }
        ]

    def test_serializes_rejected_pair(self):
        from agentic_kg.extraction.cross_entity_normalizer import (
            NormalizationAuditEntry,
        )

        r = NormalizationResult(
            pairs_detected=1,
            pairs_rejected=1,
            audit=[
                NormalizationAuditEntry(
                    surface="attention",
                    trigger="exact",
                    picked=None,
                    dropped_kinds=[],
                    rejection_reason="insufficient context",
                )
            ],
        )
        payload = json.loads(audit_to_json(r))
        assert payload[0]["picked"] is None
        assert payload[0]["dropped_kinds"] == []
        assert payload[0]["rejection_reason"] == "insufficient context"


# =============================================================================
# AC-14 + AC-15 — integrator wiring: audit on Paper, order is normalize→write
# =============================================================================


def _mock_repo_for_integration():
    repo = MagicMock()

    def _merge_concept(name, **_):
        c = MagicMock()
        c.id = f"rc-{name}"
        c.name = name
        return c, True
    repo.create_or_merge_research_concept.side_effect = _merge_concept

    def _merge_method(name, **_):
        m = MagicMock()
        m.id = f"meth-{name}"
        m.name = name
        return m, True
    repo.create_or_merge_method.side_effect = _merge_method

    session = MagicMock()
    session.__enter__ = lambda self: session
    session.__exit__ = lambda self, *a: None
    session.run.return_value = MagicMock()
    repo.session.return_value = session
    return repo


class TestIntegratorWiringAudit:
    def test_audit_written_when_normalization_has_pairs(self):
        """AC-14: a non-empty NormalizationResult lands on the Paper
        node as JSON via SET p.normalization_audit."""
        from agentic_kg.extraction.cross_entity_normalizer import (
            NormalizationAuditEntry,
        )

        repo = _mock_repo_for_integration()
        norm = NormalizationResult(
            pairs_detected=1, pairs_resolved=1,
            audit=[
                NormalizationAuditEntry(
                    surface="attention", trigger="exact",
                    picked="concept", dropped_kinds=["method"],
                )
            ],
        )

        integrate_paper_entities(
            paper_doi="10.1/abc",
            extraction_result=PaperExtractionResult(),
            mentions=[],
            taxonomy_hash="hash-1",
            repo=repo,
            normalization_result=norm,
        )

        # Look for the audit SET call in the session.run sequence.
        cypher_calls = [
            call.args[0] for call in repo.session().__enter__().run.call_args_list
        ]
        assert any(
            "normalization_audit" in c for c in cypher_calls
        ), "expected SET p.normalization_audit Cypher to fire"

    def test_audit_written_when_normalization_has_only_rejections(self):
        """AC-14 + AC-9: a rejected pair still produces an audit row.
        The Paper node must record it so operators can debug."""
        from agentic_kg.extraction.cross_entity_normalizer import (
            NormalizationAuditEntry,
        )

        repo = _mock_repo_for_integration()
        norm = NormalizationResult(
            pairs_detected=1, pairs_rejected=1,
            audit=[
                NormalizationAuditEntry(
                    surface="attention", trigger="exact",
                    picked=None, dropped_kinds=[],
                    rejection_reason="insufficient context",
                )
            ],
        )
        integrate_paper_entities(
            paper_doi="10.1/abc",
            extraction_result=PaperExtractionResult(),
            mentions=[],
            taxonomy_hash="hash-1",
            repo=repo,
            normalization_result=norm,
        )
        cypher_calls = [
            call.args[0] for call in repo.session().__enter__().run.call_args_list
        ]
        assert any("normalization_audit" in c for c in cypher_calls)

    def test_audit_not_written_when_clean(self):
        """AC-14 contract: clean papers leave the property NULL so the
        audit query (WHERE p.normalization_audit IS NOT NULL) picks up
        exactly the papers that had collisions."""
        repo = _mock_repo_for_integration()
        norm = NormalizationResult()  # clean
        integrate_paper_entities(
            paper_doi="10.1/abc",
            extraction_result=PaperExtractionResult(),
            mentions=[],
            taxonomy_hash="hash-1",
            repo=repo,
            normalization_result=norm,
        )
        cypher_calls = [
            call.args[0] for call in repo.session().__enter__().run.call_args_list
        ]
        assert not any(
            "normalization_audit" in c for c in cypher_calls
        )

    def test_audit_not_written_when_normalization_result_omitted(self):
        """AC-17 backwards-compat: existing callers that don't pass the
        kwarg get unchanged behavior."""
        repo = _mock_repo_for_integration()
        integrate_paper_entities(
            paper_doi="10.1/abc",
            extraction_result=PaperExtractionResult(),
            mentions=[],
            taxonomy_hash="hash-1",
            repo=repo,
        )
        cypher_calls = [
            call.args[0] for call in repo.session().__enter__().run.call_args_list
        ]
        assert not any("normalization_audit" in c for c in cypher_calls)


class TestIntegratorRespectsPrunedExtractionResult:
    """AC-15: when normalize_cross_entity dropped a Method extraction
    in place, the integrator's Method writer block must NOT call
    create_or_merge_method for that dropped extraction."""

    def test_dropped_method_not_written(self):
        repo = _mock_repo_for_integration()
        # The orchestrator calls normalize first which mutates the
        # extraction_result by dropping the method. By the time the
        # integrator gets the result, the method list is empty.
        c = _concept("attention")
        # Simulating the post-normalization state: method dropped already.
        result = PaperExtractionResult(
            concepts=[c], models=[], methods=[],  # method removed by normalizer
        )
        integrate_paper_entities(
            paper_doi="10.1/abc",
            extraction_result=result,
            mentions=[],
            taxonomy_hash="hash-1",
            repo=repo,
        )
        repo.create_or_merge_method.assert_not_called()
        repo.create_or_merge_research_concept.assert_called_once()
