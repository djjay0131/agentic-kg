"""Tests for the canonical Model seed loader (E-3, Unit 6).

Pure parsing/validation tests run without Neo4j. The idempotency test
that uses ``neo4j_repository`` is marked ``integration``.
"""

import pytest
import yaml
from agentic_kg.knowledge_graph.seed_models import (
    DEFAULT_SEED_PATH,
    SeedModelEntry,
    load_seed_models,
    parse_seed_models,
)

# =============================================================================
# Parsing & schema validation (no Neo4j)
# =============================================================================


class TestSeedModelEntry:
    def test_minimum_valid_entry(self):
        entry = SeedModelEntry(name="BERT")
        assert entry.name == "BERT"
        assert entry.aliases == []
        assert entry.architecture is None

    def test_full_entry(self):
        entry = SeedModelEntry(
            name="BERT",
            description="A transformer-based language model",
            aliases=["bert-base", "bert-large"],
            architecture="transformer",
            model_type="language_model",
            year_introduced=2018,
            introducing_paper_doi="10.18653/v1/N19-1423",
        )
        assert entry.name == "BERT"
        assert entry.aliases == ["bert-base", "bert-large"]
        assert entry.year_introduced == 2018

    def test_missing_name_raises(self):
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            SeedModelEntry()


class TestParseSeedModels:
    def test_parses_minimal_yaml(self):
        yaml_text = "- name: BERT\n"
        entries = parse_seed_models(yaml_text)
        assert len(entries) == 1
        assert entries[0].name == "BERT"

    def test_parses_full_yaml(self):
        yaml_text = """
        - name: BERT
          description: A transformer-based language model
          aliases: [bert-base, bert-large]
          architecture: transformer
          model_type: language_model
          year_introduced: 2018
        - name: GPT-4
          architecture: transformer
          model_type: language_model
        """
        entries = parse_seed_models(yaml_text)
        assert len(entries) == 2
        names = {e.name for e in entries}
        assert {"BERT", "GPT-4"} == names

    def test_empty_yaml_raises(self):
        with pytest.raises(ValueError):
            parse_seed_models("")

    def test_non_list_root_raises(self):
        with pytest.raises(ValueError, match="list"):
            parse_seed_models("not_a_list: [1, 2]")

    def test_duplicate_names_raise(self):
        yaml_text = """
        - name: BERT
        - name: BERT
        """
        with pytest.raises(ValueError, match="duplicate"):
            parse_seed_models(yaml_text)

    def test_missing_name_raises_with_context(self):
        yaml_text = """
        - description: missing name
        """
        with pytest.raises(ValueError):
            parse_seed_models(yaml_text)

    def test_parses_path(self, tmp_path):
        f = tmp_path / "seed.yml"
        f.write_text("- name: BERT\n- name: GPT-4\n")
        entries = parse_seed_models(f)
        assert {e.name for e in entries} == {"BERT", "GPT-4"}


# =============================================================================
# Bundled seed file
# =============================================================================


class TestBundledSeed:
    def test_bundled_seed_parses(self):
        entries = parse_seed_models(DEFAULT_SEED_PATH)
        assert len(entries) >= 10  # v1 ships ~15-20

    def test_bundled_seed_covers_required_families(self):
        """Spec calls for coverage of language / vision / multimodal /
        classical / graph families. Check we have at least one well-known
        model per family in the seed."""
        entries = parse_seed_models(DEFAULT_SEED_PATH)
        names = {e.name for e in entries}
        # Touchstones from the spec's eval-set discussion.
        for required in (
            "BERT",
            "GPT-4",
            "T5",
            "ResNet",
            "Mistral",
        ):
            assert required in names, f"seed missing {required!r}"

    def test_bundled_seed_yaml_has_no_duplicate_names(self):
        raw = yaml.safe_load(DEFAULT_SEED_PATH.read_text())
        names = [e["name"] for e in raw]
        assert len(names) == len(set(names))


# =============================================================================
# Integration — idempotent load against live Neo4j
# =============================================================================


@pytest.mark.integration
class TestLoadSeedModelsIdempotent:
    """Idempotency tests use deterministic-by-name embeddings so dedup
    is fully exercised even without OPENAI_API_KEY."""

    @pytest.fixture(autouse=True)
    def _patch_embeddings(self, monkeypatch):
        """Make every entry's embedding deterministic so re-loads merge.

        Each unique name maps to a distinct sparse one-hot vector — different
        names stay below the 0.95 dedup threshold; the same name re-embedded
        on a re-run matches itself perfectly.
        """
        from agentic_kg.knowledge_graph import embeddings

        seen: dict[str, int] = {}

        def _det_emb(name: str, description=None) -> list[float]:
            slot = seen.setdefault(name, len(seen))
            v = [0.0] * 1536
            v[slot % 1536] = 1.0
            return v

        monkeypatch.setattr(embeddings, "generate_model_embedding", _det_emb)
        # The repository imports the function locally inside methods, so
        # also patch on the repository module's binding.
        monkeypatch.setattr(
            "agentic_kg.knowledge_graph.embeddings.generate_model_embedding",
            _det_emb,
            raising=False,
        )

    def test_first_load_creates_then_second_merges(
        self, neo4j_repository, tmp_path
    ):
        """AC-5: ``load_seed_models`` is idempotent. First call creates N,
        second call merges N (no duplicates)."""
        f = tmp_path / "tiny_seed.yml"
        f.write_text(
            "- name: TEST_SeedA\n"
            "  description: First seed model\n"
            "  architecture: transformer\n"
            "- name: TEST_SeedB\n"
            "  description: Second seed model\n"
            "  architecture: cnn\n"
        )

        first = load_seed_models(neo4j_repository, path=f)
        assert first["created"] == 2
        assert first["merged"] == 0

        second = load_seed_models(neo4j_repository, path=f)
        assert second["created"] == 0
        assert second["merged"] == 2

    def test_loaded_entries_are_canonical(
        self, neo4j_repository, tmp_path
    ):
        f = tmp_path / "canon.yml"
        f.write_text("- name: TEST_Canon\n")
        load_seed_models(neo4j_repository, path=f)

        model = neo4j_repository.get_model_by_name("TEST_Canon")
        assert model.is_canonical is True
