"""
Unit tests for batch processing.
"""

import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from agentic_kg.extraction.batch import (
    BatchConfig,
    BatchJob,
    BatchJobQueue,
    BatchProcessor,
    BatchProgress,
    BatchResult,
    JobStatus,
    get_batch_processor,
    reset_batch_processor,
)
from agentic_kg.extraction.pipeline import PaperProcessingResult


class TestJobStatus:
    """Tests for JobStatus enum."""

    def test_all_statuses(self):
        """Test all status values exist."""
        assert JobStatus.PENDING.value == "pending"
        assert JobStatus.IN_PROGRESS.value == "in_progress"
        assert JobStatus.COMPLETED.value == "completed"
        assert JobStatus.FAILED.value == "failed"
        assert JobStatus.SKIPPED.value == "skipped"


class TestBatchConfig:
    """Tests for BatchConfig dataclass."""

    def test_default_config(self):
        """Test default configuration values."""
        config = BatchConfig()

        assert config.max_concurrent == 3
        assert config.max_retries == 2
        assert config.retry_delay == 5.0
        assert config.store_to_kg is True
        assert config.db_path is None

    def test_custom_config(self):
        """Test custom configuration."""
        config = BatchConfig(
            max_concurrent=5,
            max_retries=3,
            store_to_kg=False,
            db_path="/tmp/batch.db",
        )

        assert config.max_concurrent == 5
        assert config.max_retries == 3
        assert config.store_to_kg is False
        assert config.db_path == "/tmp/batch.db"


class TestBatchJob:
    """Tests for BatchJob model."""

    def test_create_job(self):
        """Test creating a batch job."""
        job = BatchJob(
            job_id="job-001",
            batch_id="batch-001",
            paper_doi="10.1234/test",
            pdf_url="https://example.com/paper.pdf",
            paper_title="Test Paper",
        )

        assert job.status == JobStatus.PENDING
        assert job.attempt_count == 0
        assert job.error_message is None

    def test_job_with_path(self):
        """Test job with local file path."""
        job = BatchJob(
            job_id="job-002",
            batch_id="batch-001",
            pdf_path="/path/to/paper.pdf",
        )

        assert job.pdf_path == "/path/to/paper.pdf"
        assert job.pdf_url is None


class TestBatchProgress:
    """Tests for BatchProgress model."""

    def test_completion_percentage(self):
        """Test completion percentage calculation."""
        progress = BatchProgress(
            batch_id="batch-001",
            total_jobs=10,
            completed_jobs=5,
            failed_jobs=2,
            pending_jobs=3,
        )

        assert progress.completion_percentage == 70.0  # 7/10

    def test_is_complete(self):
        """Test is_complete property."""
        # Not complete
        progress1 = BatchProgress(
            batch_id="batch-001",
            total_jobs=10,
            pending_jobs=3,
        )
        assert progress1.is_complete is False

        # Complete (all done)
        progress2 = BatchProgress(
            batch_id="batch-001",
            total_jobs=10,
            completed_jobs=8,
            failed_jobs=2,
            pending_jobs=0,
            in_progress_jobs=0,
        )
        assert progress2.is_complete is True

    def test_zero_jobs(self):
        """Test with zero jobs."""
        progress = BatchProgress(batch_id="empty")
        assert progress.completion_percentage == 0.0


class TestBatchResult:
    """Tests for BatchResult model."""

    def test_success_rate(self):
        """Test success rate calculation."""
        progress = BatchProgress(
            batch_id="batch-001",
            total_jobs=10,
            completed_jobs=8,
            failed_jobs=2,
        )

        result = BatchResult(
            batch_id="batch-001",
            progress=progress,
        )

        assert result.success_rate == 80.0

    def test_success_rate_no_jobs(self):
        """Test success rate with no completed/failed jobs."""
        progress = BatchProgress(
            batch_id="batch-001",
            total_jobs=10,
            completed_jobs=0,
            failed_jobs=0,
        )

        result = BatchResult(
            batch_id="batch-001",
            progress=progress,
        )

        assert result.success_rate == 0.0


class TestBatchJobQueue:
    """Tests for BatchJobQueue class."""

    @pytest.fixture
    def queue(self):
        """Create in-memory job queue."""
        q = BatchJobQueue(db_path=None)  # In-memory
        yield q
        q.close()

    def test_create_batch(self, queue):
        """Test creating a batch."""
        queue.create_batch("batch-001")
        # No error means success

    def test_add_and_get_job(self, queue):
        """Test adding and retrieving a job."""
        queue.create_batch("batch-001")

        job = BatchJob(
            job_id="job-001",
            batch_id="batch-001",
            paper_doi="10.1234/test",
            pdf_url="https://example.com/paper.pdf",
        )
        queue.add_job(job)

        # Get all jobs
        jobs = queue.get_all_jobs("batch-001")
        assert len(jobs) == 1
        assert jobs[0].job_id == "job-001"
        assert jobs[0].paper_doi == "10.1234/test"

    def test_get_pending_jobs(self, queue):
        """Test getting pending jobs."""
        queue.create_batch("batch-001")

        # Add some jobs
        for i in range(5):
            job = BatchJob(
                job_id=f"job-{i:03d}",
                batch_id="batch-001",
                pdf_url=f"https://example.com/paper{i}.pdf",
            )
            queue.add_job(job)

        pending = queue.get_pending_jobs("batch-001", limit=3)
        assert len(pending) == 3
        assert all(j.status == JobStatus.PENDING for j in pending)

    def test_update_job(self, queue):
        """Test updating a job."""
        queue.create_batch("batch-001")

        job = BatchJob(
            job_id="job-001",
            batch_id="batch-001",
            pdf_url="https://example.com/paper.pdf",
        )
        queue.add_job(job)

        # Update job
        job.status = JobStatus.COMPLETED
        job.problems_extracted = 5
        job.completed_at = datetime.now(timezone.utc)
        queue.update_job(job)

        # Verify update
        jobs = queue.get_all_jobs("batch-001")
        assert jobs[0].status == JobStatus.COMPLETED
        assert jobs[0].problems_extracted == 5

    def test_get_progress(self, queue):
        """Test getting batch progress."""
        queue.create_batch("batch-001")

        # Add jobs with different statuses
        for i, status in enumerate(
            [
                JobStatus.COMPLETED,
                JobStatus.COMPLETED,
                JobStatus.FAILED,
                JobStatus.PENDING,
                JobStatus.IN_PROGRESS,
            ]
        ):
            job = BatchJob(
                job_id=f"job-{i:03d}",
                batch_id="batch-001",
                pdf_url=f"https://example.com/paper{i}.pdf",
                status=status,
                problems_extracted=5 if status == JobStatus.COMPLETED else 0,
            )
            queue.add_job(job)

        progress = queue.get_progress("batch-001")

        assert progress.total_jobs == 5
        assert progress.completed_jobs == 2
        assert progress.failed_jobs == 1
        assert progress.pending_jobs == 1
        assert progress.in_progress_jobs == 1
        assert progress.total_problems == 10  # 2 * 5


class TestBatchProcessor:
    """Tests for BatchProcessor class."""

    @pytest.fixture
    def mock_pipeline(self):
        """Create mock pipeline."""
        pipeline = MagicMock()
        pipeline.process_pdf_url = AsyncMock()
        pipeline.process_pdf_file = AsyncMock()
        return pipeline

    @pytest.fixture
    def mock_integrator(self):
        """Create mock KG integrator."""
        integrator = MagicMock()
        integrator.integrate_extraction_result = MagicMock()
        return integrator

    @pytest.fixture
    def processor(self, mock_pipeline, mock_integrator):
        """Create batch processor with mocks."""
        return BatchProcessor(
            pipeline=mock_pipeline,
            integrator=mock_integrator,
            config=BatchConfig(max_concurrent=2, max_retries=1),
        )

    @pytest.mark.asyncio
    async def test_process_batch_success(
        self, processor, mock_pipeline, mock_integrator
    ):
        """Test successful batch processing."""
        mock_pipeline.process_pdf_url.return_value = PaperProcessingResult(
            paper_doi="10.1234/test",
            paper_title="Test",
            success=True,
        )

        papers = [
            {"doi": "10.1234/test1", "url": "https://example.com/1.pdf"},
            {"doi": "10.1234/test2", "url": "https://example.com/2.pdf"},
        ]

        result = await processor.process_batch(papers, batch_id="test-batch")

        assert result.batch_id == "test-batch"
        assert result.progress.total_jobs == 2
        assert result.progress.completed_jobs == 2
        assert mock_pipeline.process_pdf_url.call_count == 2

    @pytest.mark.asyncio
    async def test_process_batch_with_failures(
        self, processor, mock_pipeline, mock_integrator
    ):
        """Test batch processing with some failures."""
        # First call succeeds, second fails
        mock_pipeline.process_pdf_url.side_effect = [
            PaperProcessingResult(success=True),
            Exception("Download failed"),
        ]

        papers = [
            {"url": "https://example.com/1.pdf"},
            {"url": "https://example.com/2.pdf"},
        ]

        result = await processor.process_batch(papers)

        assert result.progress.completed_jobs == 1
        assert result.progress.failed_jobs == 1

    @pytest.mark.asyncio
    async def test_process_batch_stores_to_kg(
        self, processor, mock_pipeline, mock_integrator
    ):
        """Test that results are stored to KG when enabled."""
        mock_pipeline.process_pdf_url.return_value = PaperProcessingResult(
            paper_doi="10.1234/test",
            success=True,
        )

        papers = [{"url": "https://example.com/1.pdf"}]

        await processor.process_batch(papers)

        mock_integrator.integrate_extraction_result.assert_called_once()

    @pytest.mark.asyncio
    async def test_process_batch_local_file(self, processor, mock_pipeline):
        """Test processing local files."""
        mock_pipeline.process_pdf_file.return_value = PaperProcessingResult(
            success=True,
        )

        papers = [{"path": "/path/to/paper.pdf", "title": "Test"}]

        result = await processor.process_batch(papers)

        assert result.progress.completed_jobs == 1
        mock_pipeline.process_pdf_file.assert_called_once()

    @pytest.mark.asyncio
    async def test_resume_batch(self, processor, mock_pipeline):
        """Test resuming a batch."""
        # Create initial batch with some pending jobs
        papers = [
            {"url": "https://example.com/1.pdf"},
            {"url": "https://example.com/2.pdf"},
        ]

        # First run - simulate interruption by not running
        batch_id = "resume-test"
        processor.queue.create_batch(batch_id)
        for i, paper in enumerate(papers):
            job = BatchJob(
                job_id=f"{batch_id}-{i:04d}",
                batch_id=batch_id,
                pdf_url=paper.get("url"),
            )
            processor.queue.add_job(job)

        # Resume
        mock_pipeline.process_pdf_url.return_value = PaperProcessingResult(
            success=True,
        )

        result = await processor.resume_batch(batch_id)

        assert result.progress.completed_jobs == 2

    @pytest.mark.asyncio
    async def test_progress_callback(self, processor, mock_pipeline):
        """Test progress callback is called."""
        mock_pipeline.process_pdf_url.return_value = PaperProcessingResult(
            success=True,
        )

        progress_reports = []
        processor.config.on_progress = lambda p: progress_reports.append(p)

        papers = [{"url": "https://example.com/1.pdf"}]

        await processor.process_batch(papers)

        # At least one progress report should be made
        assert len(progress_reports) >= 1


class TestGetBatchProcessor:
    """Tests for singleton access."""

    def setup_method(self):
        """Reset singleton before each test."""
        reset_batch_processor()

    def teardown_method(self):
        """Reset singleton after each test."""
        reset_batch_processor()

    def test_returns_processor_instance(self):
        """Test that get_batch_processor returns a processor."""
        with patch("agentic_kg.extraction.batch.get_pipeline"):
            with patch("agentic_kg.extraction.batch.get_kg_integrator"):
                processor = get_batch_processor()
                assert isinstance(processor, BatchProcessor)

    def test_returns_same_instance(self):
        """Test singleton pattern."""
        with patch("agentic_kg.extraction.batch.get_pipeline"):
            with patch("agentic_kg.extraction.batch.get_kg_integrator"):
                processor1 = get_batch_processor()
                processor2 = get_batch_processor()
                assert processor1 is processor2

    def test_reset_clears_singleton(self):
        """Test reset clears singleton."""
        with patch("agentic_kg.extraction.batch.get_pipeline"):
            with patch("agentic_kg.extraction.batch.get_kg_integrator"):
                processor1 = get_batch_processor()
                reset_batch_processor()
                processor2 = get_batch_processor()
                assert processor1 is not processor2
