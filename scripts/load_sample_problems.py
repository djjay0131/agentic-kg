#!/usr/bin/env python3
"""
Load sample research problems into the knowledge graph.

This script populates the Neo4j database with realistic research problems
extracted from actual papers across NLP, Computer Vision, and Machine Learning.

Usage:
    python scripts/load_sample_problems.py [--clear]

Options:
    --clear     Clear existing data before loading
"""

import argparse
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "packages/core/src"))

from agentic_kg.knowledge_graph.embeddings import generate_problem_embedding
from agentic_kg.knowledge_graph.models import (
    Assumption,
    Author,
    Baseline,
    Constraint,
    ConstraintType,
    Dataset,
    Evidence,
    ExtractionMetadata,
    Metric,
    Paper,
    Problem,
    ProblemStatus,
)
from agentic_kg.knowledge_graph.relations import RelationService
from agentic_kg.knowledge_graph.repository import Neo4jRepository
from agentic_kg.knowledge_graph.schema import SchemaManager

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


# =============================================================================
# Sample Papers
# =============================================================================

SAMPLE_PAPERS = [
    Paper(
        doi="10.48550/arXiv.1706.03762",
        title="Attention Is All You Need",
        authors=["Ashish Vaswani", "Noam Shazeer", "Niki Parmar"],
        venue="NeurIPS 2017",
        year=2017,
        abstract="We propose a new simple network architecture, the Transformer, "
        "based solely on attention mechanisms.",
        arxiv_id="1706.03762",
    ),
    Paper(
        doi="10.48550/arXiv.1810.04805",
        title="BERT: Pre-training of Deep Bidirectional Transformers",
        authors=["Jacob Devlin", "Ming-Wei Chang", "Kenton Lee", "Kristina Toutanova"],
        venue="NAACL 2019",
        year=2019,
        abstract="We introduce BERT, which obtains state-of-the-art results on "
        "eleven NLP tasks.",
        arxiv_id="1810.04805",
    ),
    Paper(
        doi="10.48550/arXiv.2005.14165",
        title="Language Models are Few-Shot Learners",
        authors=["Tom Brown", "Benjamin Mann", "Nick Ryder"],
        venue="NeurIPS 2020",
        year=2020,
        abstract="We demonstrate that scaling up language models greatly improves "
        "task-agnostic, few-shot performance.",
        arxiv_id="2005.14165",
    ),
    Paper(
        doi="10.48550/arXiv.1512.03385",
        title="Deep Residual Learning for Image Recognition",
        authors=["Kaiming He", "Xiangyu Zhang", "Shaoqing Ren", "Jian Sun"],
        venue="CVPR 2016",
        year=2016,
        abstract="We present a residual learning framework to ease the training "
        "of networks that are substantially deeper than those used previously.",
        arxiv_id="1512.03385",
    ),
    Paper(
        doi="10.48550/arXiv.2010.11929",
        title="An Image is Worth 16x16 Words: Transformers for Image Recognition",
        authors=["Alexey Dosovitskiy", "Lucas Beyer", "Alexander Kolesnikov"],
        venue="ICLR 2021",
        year=2021,
        abstract="We show that transformers applied directly to sequences of "
        "image patches can perform very well on image classification tasks.",
        arxiv_id="2010.11929",
    ),
    Paper(
        doi="10.48550/arXiv.2203.02155",
        title="Training language models to follow instructions with human feedback",
        authors=["Long Ouyang", "Jeff Wu", "Xu Jiang"],
        venue="NeurIPS 2022",
        year=2022,
        abstract="We show an avenue for aligning language models with user intent "
        "on a wide range of tasks by fine-tuning with human feedback.",
        arxiv_id="2203.02155",
    ),
]

# =============================================================================
# Sample Authors
# =============================================================================

SAMPLE_AUTHORS = [
    Author(
        name="Ashish Vaswani",
        affiliations=["Google Brain"],
        orcid="0000-0001-1234-5678",
    ),
    Author(
        name="Jacob Devlin",
        affiliations=["Google AI Language"],
        orcid="0000-0002-2345-6789",
    ),
    Author(
        name="Kaiming He",
        affiliations=["Meta AI Research"],
        orcid="0000-0003-3456-7890",
    ),
]

# =============================================================================
# Sample Research Problems
# =============================================================================


def create_sample_problems() -> list[Problem]:
    """Create sample research problems from real papers."""
    problems = []

    # Problem 1: Attention mechanism scalability (from Transformer paper)
    problems.append(
        Problem(
            statement=(
                "Self-attention mechanisms have quadratic complexity O(n²) with "
                "respect to sequence length, limiting their applicability to long "
                "documents and requiring significant computational resources for "
                "sequences beyond a few thousand tokens."
            ),
            domain="NLP",
            status=ProblemStatus.IN_PROGRESS,
            assumptions=[
                Assumption(
                    text="Full pairwise attention is necessary for capturing "
                    "long-range dependencies",
                    implicit=True,
                    confidence=0.7,
                ),
                Assumption(
                    text="Memory constraints will continue to limit attention span",
                    implicit=False,
                    confidence=0.8,
                ),
            ],
            constraints=[
                Constraint(
                    text="GPU memory limits practical sequence length to ~4096 tokens",
                    type=ConstraintType.COMPUTATIONAL,
                    confidence=0.9,
                ),
            ],
            datasets=[
                Dataset(
                    name="Long Range Arena",
                    url="https://github.com/google-research/long-range-arena",
                    available=True,
                ),
            ],
            metrics=[
                Metric(name="FLOPS", description="Floating point operations"),
                Metric(name="Memory usage", description="Peak GPU memory in GB"),
            ],
            baselines=[
                Baseline(
                    name="Standard Transformer",
                    paper_doi="10.48550/arXiv.1706.03762",
                    performance={"complexity": "O(n²)"},
                ),
            ],
            evidence=Evidence(
                source_doi="10.48550/arXiv.1706.03762",
                source_title="Attention Is All You Need",
                section="Introduction",
                quoted_text="The Transformer uses self-attention to compute "
                "representations of its input and output without using sequence-aligned "
                "RNNs or convolution.",
            ),
            extraction_metadata=ExtractionMetadata(
                extraction_model="gpt-4",
                confidence_score=0.92,
                human_reviewed=True,
                reviewed_by="researcher@example.com",
                reviewed_at=datetime.now(timezone.utc),
            ),
        )
    )

    # Problem 2: Pre-training data requirements (from BERT paper)
    problems.append(
        Problem(
            statement=(
                "Large language models require massive amounts of high-quality "
                "pre-training data, but the optimal data composition, quality "
                "filters, and data-to-model scaling laws remain poorly understood, "
                "making it difficult to predict model capabilities."
            ),
            domain="NLP",
            status=ProblemStatus.OPEN,
            assumptions=[
                Assumption(
                    text="More diverse data leads to better generalization",
                    implicit=True,
                    confidence=0.85,
                ),
                Assumption(
                    text="Data quality is more important than quantity beyond "
                    "certain scale",
                    implicit=False,
                    confidence=0.75,
                ),
            ],
            constraints=[
                Constraint(
                    text="Web-scraped data contains noise, duplicates, and "
                    "potentially harmful content",
                    type=ConstraintType.DATA,
                    confidence=0.95,
                ),
                Constraint(
                    text="High-quality curated datasets are expensive to create",
                    type=ConstraintType.DATA,
                    confidence=0.9,
                ),
            ],
            datasets=[
                Dataset(name="C4", url="https://www.tensorflow.org/datasets/catalog/c4"),
                Dataset(name="The Pile", url="https://pile.eleuther.ai/"),
            ],
            metrics=[
                Metric(name="Perplexity", description="Language modeling perplexity"),
                Metric(name="Downstream accuracy", description="Average on benchmarks"),
            ],
            baselines=[],
            evidence=Evidence(
                source_doi="10.48550/arXiv.1810.04805",
                source_title="BERT: Pre-training of Deep Bidirectional Transformers",
                section="Pre-training Data",
                quoted_text="For pre-training corpus we use the BooksCorpus "
                "(800M words) and English Wikipedia (2,500M words).",
            ),
            extraction_metadata=ExtractionMetadata(
                extraction_model="gpt-4",
                confidence_score=0.88,
                human_reviewed=False,
            ),
        )
    )

    # Problem 3: Few-shot learning reliability (from GPT-3 paper)
    problems.append(
        Problem(
            statement=(
                "Few-shot and zero-shot prompting of large language models produces "
                "inconsistent results across different prompt formulations, with "
                "small changes in wording leading to large performance variations, "
                "making it difficult to reliably deploy these models."
            ),
            domain="NLP",
            status=ProblemStatus.IN_PROGRESS,
            assumptions=[
                Assumption(
                    text="Models learn robust task representations during pre-training",
                    implicit=True,
                    confidence=0.6,
                ),
            ],
            constraints=[
                Constraint(
                    text="Cannot fine-tune on target task data in few-shot setting",
                    type=ConstraintType.METHODOLOGICAL,
                    confidence=0.95,
                ),
            ],
            datasets=[
                Dataset(name="SuperGLUE", url="https://super.gluebenchmark.com/"),
                Dataset(name="BIG-bench", url="https://github.com/google/BIG-bench"),
            ],
            metrics=[
                Metric(name="Accuracy variance", description="Variance across prompts"),
                Metric(name="Prompt sensitivity", description="Max-min accuracy gap"),
            ],
            baselines=[
                Baseline(
                    name="GPT-3 175B",
                    paper_doi="10.48550/arXiv.2005.14165",
                    performance={"few_shot_accuracy": 0.71},
                ),
            ],
            evidence=Evidence(
                source_doi="10.48550/arXiv.2005.14165",
                source_title="Language Models are Few-Shot Learners",
                section="Results",
                quoted_text="We find that GPT-3 achieves promising results in "
                "the zero-shot and one-shot settings.",
            ),
            extraction_metadata=ExtractionMetadata(
                extraction_model="claude-3",
                confidence_score=0.85,
                human_reviewed=True,
                reviewed_by="ml_researcher@example.com",
                reviewed_at=datetime.now(timezone.utc),
            ),
        )
    )

    # Problem 4: Deep network training (from ResNet paper)
    problems.append(
        Problem(
            statement=(
                "Training very deep neural networks suffers from degradation "
                "problems where adding more layers leads to higher training error, "
                "not due to overfitting but due to optimization difficulties in "
                "learning identity mappings."
            ),
            domain="Computer Vision",
            status=ProblemStatus.RESOLVED,
            assumptions=[
                Assumption(
                    text="Deeper networks should be able to represent more complex "
                    "functions",
                    implicit=False,
                    confidence=0.95,
                ),
            ],
            constraints=[
                Constraint(
                    text="Gradient flow becomes difficult in very deep networks",
                    type=ConstraintType.METHODOLOGICAL,
                    confidence=0.9,
                ),
            ],
            datasets=[
                Dataset(name="ImageNet", url="https://image-net.org/", available=True),
                Dataset(name="CIFAR-10", url="https://www.cs.toronto.edu/~kriz/cifar.html"),
            ],
            metrics=[
                Metric(
                    name="Top-1 accuracy",
                    description="Classification accuracy",
                    baseline_value=0.76,
                ),
                Metric(name="Training loss", description="Convergence speed"),
            ],
            baselines=[
                Baseline(
                    name="VGG-19",
                    performance={"top1_accuracy": 0.744},
                ),
            ],
            evidence=Evidence(
                source_doi="10.48550/arXiv.1512.03385",
                source_title="Deep Residual Learning for Image Recognition",
                section="Introduction",
                quoted_text="When deeper networks are able to start converging, "
                "a degradation problem has been exposed.",
            ),
            extraction_metadata=ExtractionMetadata(
                extraction_model="gpt-4",
                confidence_score=0.95,
                human_reviewed=True,
                reviewed_by="cv_expert@example.com",
                reviewed_at=datetime.now(timezone.utc),
            ),
        )
    )

    # Problem 5: Vision Transformer data efficiency (from ViT paper)
    problems.append(
        Problem(
            statement=(
                "Vision Transformers require significantly more pre-training data "
                "than convolutional neural networks to achieve competitive "
                "performance, with pure ViT models underperforming CNNs when "
                "trained on datasets smaller than 100M images."
            ),
            domain="Computer Vision",
            status=ProblemStatus.IN_PROGRESS,
            assumptions=[
                Assumption(
                    text="Transformers lack the inductive biases that make CNNs "
                    "data-efficient",
                    implicit=False,
                    confidence=0.85,
                ),
            ],
            constraints=[
                Constraint(
                    text="Large-scale labeled image datasets are expensive to create",
                    type=ConstraintType.DATA,
                    confidence=0.9,
                ),
            ],
            datasets=[
                Dataset(name="JFT-300M", available=False),
                Dataset(name="ImageNet-21k", url="https://image-net.org/"),
            ],
            metrics=[
                Metric(name="Top-1 accuracy", description="ImageNet classification"),
                Metric(name="Data efficiency", description="Accuracy per training sample"),
            ],
            baselines=[
                Baseline(
                    name="ResNet-152",
                    performance={"imagenet_top1": 0.785},
                ),
                Baseline(
                    name="ViT-L/16",
                    paper_doi="10.48550/arXiv.2010.11929",
                    performance={"imagenet_top1": 0.876, "pretrain_data": "JFT-300M"},
                ),
            ],
            evidence=Evidence(
                source_doi="10.48550/arXiv.2010.11929",
                source_title="An Image is Worth 16x16 Words",
                section="Experiments",
                quoted_text="When trained on mid-sized datasets such as ImageNet "
                "without strong regularization, these models yield modest accuracies.",
            ),
            extraction_metadata=ExtractionMetadata(
                extraction_model="claude-3",
                confidence_score=0.9,
                human_reviewed=False,
            ),
        )
    )

    # Problem 6: RLHF alignment tax (from InstructGPT paper)
    problems.append(
        Problem(
            statement=(
                "Reinforcement Learning from Human Feedback (RLHF) improves model "
                "alignment with human preferences but may degrade performance on "
                "certain benchmarks, creating a trade-off between helpfulness "
                "and raw capability ('alignment tax')."
            ),
            domain="Machine Learning",
            status=ProblemStatus.OPEN,
            assumptions=[
                Assumption(
                    text="Human preferences can be accurately captured by reward models",
                    implicit=True,
                    confidence=0.7,
                ),
                Assumption(
                    text="Optimization against reward model preserves capabilities",
                    implicit=True,
                    confidence=0.65,
                ),
            ],
            constraints=[
                Constraint(
                    text="Human feedback is expensive and slow to collect",
                    type=ConstraintType.DATA,
                    confidence=0.95,
                ),
                Constraint(
                    text="Reward model may be hackable or misspecified",
                    type=ConstraintType.METHODOLOGICAL,
                    confidence=0.8,
                ),
            ],
            datasets=[
                Dataset(name="HH-RLHF", url="https://github.com/anthropics/hh-rlhf"),
            ],
            metrics=[
                Metric(name="Human preference rate", description="% preferred by humans"),
                Metric(name="TruthfulQA", description="Truthfulness benchmark"),
            ],
            baselines=[
                Baseline(
                    name="GPT-3",
                    performance={"human_preference": 0.5},
                ),
                Baseline(
                    name="InstructGPT",
                    paper_doi="10.48550/arXiv.2203.02155",
                    performance={"human_preference": 0.85},
                ),
            ],
            evidence=Evidence(
                source_doi="10.48550/arXiv.2203.02155",
                source_title="Training language models to follow instructions",
                section="Results",
                quoted_text="We find that InstructGPT models show improvements in "
                "truthfulness and reductions in toxic output generation.",
            ),
            extraction_metadata=ExtractionMetadata(
                extraction_model="gpt-4",
                confidence_score=0.87,
                human_reviewed=True,
                reviewed_by="safety_researcher@example.com",
                reviewed_at=datetime.now(timezone.utc),
            ),
        )
    )

    return problems


def load_sample_data(clear: bool = False) -> None:
    """Load all sample data into Neo4j."""
    repo = Neo4jRepository()
    relation_service = RelationService(repository=repo)
    schema_manager = SchemaManager(repository=repo)

    try:
        # Verify connection
        repo.verify_connectivity()
        logger.info("Connected to Neo4j")

        # Initialize schema
        schema_manager.initialize()
        logger.info("Schema initialized")

        # Optionally clear existing data
        if clear:
            schema_manager.drop_all(confirm=True)
            schema_manager.initialize(force=True)
            logger.info("Cleared existing data")

        # Load papers
        logger.info("Loading papers...")
        paper_map = {}
        for paper in SAMPLE_PAPERS:
            try:
                repo.create_paper(paper)
                paper_map[paper.doi] = paper
                logger.info(f"  Created paper: {paper.title[:50]}...")
            except Exception as e:
                logger.warning(f"  Paper exists or error: {e}")

        # Load authors
        logger.info("Loading authors...")
        author_map = {}
        for author in SAMPLE_AUTHORS:
            try:
                repo.create_author(author)
                author_map[author.name] = author
                logger.info(f"  Created author: {author.name}")
            except Exception as e:
                logger.warning(f"  Author exists or error: {e}")

        # Load problems
        logger.info("Loading problems...")
        problems = create_sample_problems()
        problem_map = {}
        for problem in problems:
            try:
                # Generate embedding if API key is configured
                try:
                    problem.embedding = generate_problem_embedding(problem)
                    logger.info("  Generated embedding for problem")
                except Exception:
                    logger.warning("  Skipping embedding (API not configured)")

                repo.create_problem(problem)
                problem_map[problem.id] = problem
                logger.info(f"  Created problem: {problem.statement[:50]}...")

                # Link to source paper
                relation_service.link_problem_to_paper(
                    problem.id,
                    problem.evidence.source_doi,
                    problem.evidence.section,
                )
            except Exception as e:
                logger.warning(f"  Problem exists or error: {e}")

        # Create some problem-to-problem relations
        logger.info("Creating relations...")
        problem_list = list(problem_map.values())
        if len(problem_list) >= 2:
            # Problem about attention scalability EXTENDS deep network training
            try:
                from agentic_kg.knowledge_graph.models import RelationType

                relation_service.create_relation(
                    problem_list[0].id,  # Attention scalability
                    problem_list[3].id,  # Deep network training
                    RelationType.EXTENDS,
                    confidence=0.75,
                )
                logger.info("  Created EXTENDS relation")
            except Exception as e:
                logger.warning(f"  Relation error: {e}")

        logger.info("Sample data loading complete!")
        logger.info(f"  Papers: {len(SAMPLE_PAPERS)}")
        logger.info(f"  Authors: {len(SAMPLE_AUTHORS)}")
        logger.info(f"  Problems: {len(problems)}")

    finally:
        repo.close()


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(description="Load sample research problems")
    parser.add_argument(
        "--clear",
        action="store_true",
        help="Clear existing data before loading",
    )
    args = parser.parse_args()

    try:
        load_sample_data(clear=args.clear)
    except Exception as e:
        logger.error(f"Failed to load sample data: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
