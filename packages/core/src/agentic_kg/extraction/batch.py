"""
Batch processing for paper extraction.

This module provides:
- SQLite-based job queue for batch state management
- Parallel processing with rate limiting
- Resume capability for failed jobs
- Progress reporting
"""

import asyncio
import logging
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Callable, Optional

from pydantic import BaseModel, Field

from agentic_kg.extraction.kg_integration import (
    IntegrationResult,
    KnowledgeGraphIntegrator,
    get_kg_integrator,
)
from agentic_kg.extraction.pipeline import (
    PaperProcessingPipeline,
    PaperProcessingResult,
    get_pipeline,
)

logger = logging.getLogger(__name__)


class JobStatus(str, Enum):
    """Status of a batch job."""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class BatchJob(BaseModel):
    """A single job in a batch."""

    job_id: str = Field(..., description="Unique job ID")
    batch_id: str = Field(..., description="Parent batch ID")
    paper_doi: Optional[str] = Field(default=None, description="Paper DOI")
    pdf_url: Optional[str] = Field(default=None, description="PDF URL")
    pdf_path: Optional[str] = Field(default=None, description="Local PDF path")
    paper_title: Optional[str] = Field(default=None, description="Paper title")

    status: JobStatus = Field(default=JobStatus.PENDING, description="Job status")
    attempt_count: int = Field(default=0, description="Number of attempts")
    error_message: Optional[str] = Field(default=None, description="Error if failed")

    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Job creation time",
    )
    started_at: Optional[datetime] = Field(
        default=None, description="Job start time"
    )
    completed_at: Optional[datetime] = Field(
        default=None, description="Job completion time"
    )

    # Results
    problems_extracted: int = Field(default=0, description="Problems extracted")
    processing_time_ms: float = Field(default=0, description="Processing time in ms")


class BatchProgress(BaseModel):
    """Progress information for a batch."""

    batch_id: str = Field(..., description="Batch ID")
    total_jobs: int = Field(default=0, description="Total jobs in batch")
    completed_jobs: int = Field(default=0, description="Completed jobs")
    failed_jobs: int = Field(default=0, description="Failed jobs")
    pending_jobs: int = Field(default=0, description="Pending jobs")
    in_progress_jobs: int = Field(default=0, description="In-progress jobs")

    total_problems: int = Field(default=0, description="Total problems extracted")
    total_processing_time_ms: float = Field(default=0, description="Total processing time")

    @property
    def completion_percentage(self) -> float:
        """Percentage of jobs completed."""
        if self.total_jobs == 0:
            return 0.0
        return (self.completed_jobs + self.failed_jobs) / self.total_jobs * 100

    @property
    def is_complete(self) -> bool:
        """True if all jobs are done (completed or failed)."""
        return self.pending_jobs == 0 and self.in_progress_jobs == 0


class BatchResult(BaseModel):
    """Final result of a batch run."""

    batch_id: str = Field(..., description="Batch ID")
    progress: BatchProgress = Field(..., description="Final progress")
    jobs: list[BatchJob] = Field(default_factory=list, description="All jobs")

    started_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Batch start time",
    )
    completed_at: Optional[datetime] = Field(
        default=None, description="Batch completion time"
    )

    @property
    def success_rate(self) -> float:
        """Success rate as percentage."""
        total = self.progress.completed_jobs + self.progress.failed_jobs
        if total == 0:
            return 0.0
        return self.progress.completed_jobs / total * 100


@dataclass
class BatchConfig:
    """Configuration for batch processing."""

    # Concurrency
    max_concurrent: int = 3

    # Retries
    max_retries: int = 2
    retry_delay: float = 5.0  # seconds

    # Processing
    store_to_kg: bool = True

    # Database
    db_path: Optional[str] = None  # None = in-memory

    # Progress callback
    on_progress: Optional[Callable[[BatchProgress], None]] = None


class BatchJobQueue:
    """
    SQLite-based job queue for batch processing.

    Provides persistent storage for job state and enables
    resume after failures.
    """

    def __init__(self, db_path: Optional[str] = None):
        """
        Initialize the job queue.

        Args:
            db_path: Path to SQLite database. None for in-memory.
        """
        self.db_path = db_path or ":memory:"
        self._conn: Optional[sqlite3.Connection] = None
        self._init_db()

    def _init_db(self) -> None:
        """Initialize the database schema."""
        self._conn = sqlite3.connect(self.db_path)
        self._conn.row_factory = sqlite3.Row

        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS batches (
                batch_id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                completed_at TEXT
            );

            CREATE TABLE IF NOT EXISTS jobs (
                job_id TEXT PRIMARY KEY,
                batch_id TEXT NOT NULL,
                paper_doi TEXT,
                pdf_url TEXT,
                pdf_path TEXT,
                paper_title TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                attempt_count INTEGER DEFAULT 0,
                error_message TEXT,
                created_at TEXT NOT NULL,
                started_at TEXT,
                completed_at TEXT,
                problems_extracted INTEGER DEFAULT 0,
                processing_time_ms REAL DEFAULT 0,
                FOREIGN KEY (batch_id) REFERENCES batches(batch_id)
            );

            CREATE INDEX IF NOT EXISTS idx_jobs_batch ON jobs(batch_id);
            CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
        """
        )
        self._conn.commit()

    def close(self) -> None:
        """Close the database connection."""
        if self._conn:
            self._conn.close()
            self._conn = None

    def create_batch(self, batch_id: str) -> None:
        """Create a new batch."""
        self._conn.execute(
            "INSERT INTO batches (batch_id, created_at) VALUES (?, ?)",
            (batch_id, datetime.now(timezone.utc).isoformat()),
        )
        self._conn.commit()

    def add_job(self, job: BatchJob) -> None:
        """Add a job to the queue."""
        self._conn.execute(
            """
            INSERT INTO jobs (
                job_id, batch_id, paper_doi, pdf_url, pdf_path, paper_title,
                status, attempt_count, error_message, created_at, started_at,
                completed_at, problems_extracted, processing_time_ms
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job.job_id,
                job.batch_id,
                job.paper_doi,
                job.pdf_url,
                job.pdf_path,
                job.paper_title,
                job.status.value,
                job.attempt_count,
                job.error_message,
                job.created_at.isoformat(),
                job.started_at.isoformat() if job.started_at else None,
                job.completed_at.isoformat() if job.completed_at else None,
                job.problems_extracted,
                job.processing_time_ms,
            ),
        )
        self._conn.commit()

    def update_job(self, job: BatchJob) -> None:
        """Update a job in the queue."""
        self._conn.execute(
            """
            UPDATE jobs SET
                status = ?,
                attempt_count = ?,
                error_message = ?,
                started_at = ?,
                completed_at = ?,
                problems_extracted = ?,
                processing_time_ms = ?
            WHERE job_id = ?
            """,
            (
                job.status.value,
                job.attempt_count,
                job.error_message,
                job.started_at.isoformat() if job.started_at else None,
                job.completed_at.isoformat() if job.completed_at else None,
                job.problems_extracted,
                job.processing_time_ms,
                job.job_id,
            ),
        )
        self._conn.commit()

    def get_pending_jobs(self, batch_id: str, limit: int = 10) -> list[BatchJob]:
        """Get pending jobs for a batch."""
        cursor = self._conn.execute(
            """
            SELECT * FROM jobs
            WHERE batch_id = ? AND status = 'pending'
            ORDER BY created_at
            LIMIT ?
            """,
            (batch_id, limit),
        )
        return [self._row_to_job(row) for row in cursor.fetchall()]

    def get_all_jobs(self, batch_id: str) -> list[BatchJob]:
        """Get all jobs for a batch."""
        cursor = self._conn.execute(
            "SELECT * FROM jobs WHERE batch_id = ? ORDER BY created_at",
            (batch_id,),
        )
        return [self._row_to_job(row) for row in cursor.fetchall()]

    def get_progress(self, batch_id: str) -> BatchProgress:
        """Get progress for a batch."""
        cursor = self._conn.execute(
            """
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) as completed,
                SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) as failed,
                SUM(CASE WHEN status = 'pending' THEN 1 ELSE 0 END) as pending,
                SUM(CASE WHEN status = 'in_progress' THEN 1 ELSE 0 END) as in_progress,
                SUM(problems_extracted) as total_problems,
                SUM(processing_time_ms) as total_time
            FROM jobs WHERE batch_id = ?
            """,
            (batch_id,),
        )
        row = cursor.fetchone()
        return BatchProgress(
            batch_id=batch_id,
            total_jobs=row["total"] or 0,
            completed_jobs=row["completed"] or 0,
            failed_jobs=row["failed"] or 0,
            pending_jobs=row["pending"] or 0,
            in_progress_jobs=row["in_progress"] or 0,
            total_problems=row["total_problems"] or 0,
            total_processing_time_ms=row["total_time"] or 0,
        )

    def _row_to_job(self, row: sqlite3.Row) -> BatchJob:
        """Convert a database row to a BatchJob."""
        return BatchJob(
            job_id=row["job_id"],
            batch_id=row["batch_id"],
            paper_doi=row["paper_doi"],
            pdf_url=row["pdf_url"],
            pdf_path=row["pdf_path"],
            paper_title=row["paper_title"],
            status=JobStatus(row["status"]),
            attempt_count=row["attempt_count"],
            error_message=row["error_message"],
            created_at=datetime.fromisoformat(row["created_at"]),
            started_at=(
                datetime.fromisoformat(row["started_at"])
                if row["started_at"]
                else None
            ),
            completed_at=(
                datetime.fromisoformat(row["completed_at"])
                if row["completed_at"]
                else None
            ),
            problems_extracted=row["problems_extracted"],
            processing_time_ms=row["processing_time_ms"],
        )


@dataclass
class BatchProcessor:
    """
    Batch processor for paper extraction.

    Manages parallel execution of extraction jobs with
    rate limiting and progress tracking.
    """

    pipeline: Optional[PaperProcessingPipeline] = None
    integrator: Optional[KnowledgeGraphIntegrator] = None
    config: BatchConfig = field(default_factory=BatchConfig)
    queue: Optional[BatchJobQueue] = None

    def __post_init__(self):
        """Initialize components."""
        if self.pipeline is None:
            self.pipeline = get_pipeline()
        if self.integrator is None:
            self.integrator = get_kg_integrator()
        if self.queue is None:
            self.queue = BatchJobQueue(self.config.db_path)

    async def process_batch(
        self,
        papers: list[dict],
        batch_id: Optional[str] = None,
    ) -> BatchResult:
        """
        Process a batch of papers.

        Args:
            papers: List of paper specs with 'doi', 'url', 'path', 'title'
            batch_id: Optional batch ID (generated if not provided)

        Returns:
            BatchResult with processing results
        """
        import uuid

        batch_id = batch_id or f"batch-{uuid.uuid4().hex[:8]}"
        start_time = datetime.now(timezone.utc)

        # Create batch and jobs
        self.queue.create_batch(batch_id)

        for i, paper in enumerate(papers):
            job = BatchJob(
                job_id=f"{batch_id}-{i:04d}",
                batch_id=batch_id,
                paper_doi=paper.get("doi"),
                pdf_url=paper.get("url"),
                pdf_path=paper.get("path"),
                paper_title=paper.get("title"),
            )
            self.queue.add_job(job)

        # Process jobs with concurrency limit
        semaphore = asyncio.Semaphore(self.config.max_concurrent)

        async def process_with_semaphore(job: BatchJob) -> None:
            async with semaphore:
                await self._process_job(job)

        while True:
            pending_jobs = self.queue.get_pending_jobs(batch_id)
            if not pending_jobs:
                break

            tasks = [process_with_semaphore(job) for job in pending_jobs]
            await asyncio.gather(*tasks)

            # Report progress
            progress = self.queue.get_progress(batch_id)
            if self.config.on_progress:
                self.config.on_progress(progress)

        # Get final results
        final_progress = self.queue.get_progress(batch_id)
        all_jobs = self.queue.get_all_jobs(batch_id)

        return BatchResult(
            batch_id=batch_id,
            progress=final_progress,
            jobs=all_jobs,
            started_at=start_time,
            completed_at=datetime.now(timezone.utc),
        )

    async def resume_batch(self, batch_id: str) -> BatchResult:
        """
        Resume a batch that was interrupted.

        Args:
            batch_id: Batch ID to resume

        Returns:
            BatchResult with processing results
        """
        start_time = datetime.now(timezone.utc)

        # Reset any in_progress jobs to pending
        self.queue._conn.execute(
            "UPDATE jobs SET status = 'pending' WHERE batch_id = ? AND status = 'in_progress'",
            (batch_id,),
        )
        self.queue._conn.commit()

        # Process remaining jobs
        semaphore = asyncio.Semaphore(self.config.max_concurrent)

        async def process_with_semaphore(job: BatchJob) -> None:
            async with semaphore:
                await self._process_job(job)

        while True:
            pending_jobs = self.queue.get_pending_jobs(batch_id)
            if not pending_jobs:
                break

            tasks = [process_with_semaphore(job) for job in pending_jobs]
            await asyncio.gather(*tasks)

            # Report progress
            progress = self.queue.get_progress(batch_id)
            if self.config.on_progress:
                self.config.on_progress(progress)

        # Get final results
        final_progress = self.queue.get_progress(batch_id)
        all_jobs = self.queue.get_all_jobs(batch_id)

        return BatchResult(
            batch_id=batch_id,
            progress=final_progress,
            jobs=all_jobs,
            started_at=start_time,
            completed_at=datetime.now(timezone.utc),
        )

    async def _process_job(self, job: BatchJob) -> None:
        """Process a single job."""
        job.status = JobStatus.IN_PROGRESS
        job.started_at = datetime.now(timezone.utc)
        job.attempt_count += 1
        self.queue.update_job(job)

        start_time = time.time()

        try:
            # Determine input type and process
            if job.pdf_url:
                result = await self.pipeline.process_pdf_url(
                    url=job.pdf_url,
                    paper_title=job.paper_title,
                    paper_doi=job.paper_doi,
                )
            elif job.pdf_path:
                result = await self.pipeline.process_pdf_file(
                    file_path=job.pdf_path,
                    paper_title=job.paper_title,
                    paper_doi=job.paper_doi,
                )
            else:
                raise ValueError("No PDF URL or path provided")

            # Store to Knowledge Graph if enabled
            if self.config.store_to_kg and result.success:
                self.integrator.integrate_extraction_result(result)

            # Update job with success
            job.status = JobStatus.COMPLETED
            job.completed_at = datetime.now(timezone.utc)
            job.problems_extracted = result.problem_count
            job.processing_time_ms = (time.time() - start_time) * 1000

        except Exception as e:
            logger.error(f"Job {job.job_id} failed: {e}")
            job.error_message = str(e)

            # Check if we should retry
            if job.attempt_count < self.config.max_retries:
                job.status = JobStatus.PENDING
                await asyncio.sleep(self.config.retry_delay)
            else:
                job.status = JobStatus.FAILED
                job.completed_at = datetime.now(timezone.utc)
                job.processing_time_ms = (time.time() - start_time) * 1000

        self.queue.update_job(job)


# Module-level singleton
_batch_processor: Optional[BatchProcessor] = None


def get_batch_processor(
    config: Optional[BatchConfig] = None,
) -> BatchProcessor:
    """
    Get the singleton BatchProcessor instance.

    Args:
        config: Optional configuration

    Returns:
        BatchProcessor instance
    """
    global _batch_processor

    if _batch_processor is None:
        _batch_processor = BatchProcessor(config=config or BatchConfig())

    return _batch_processor


def reset_batch_processor() -> None:
    """Reset the singleton (useful for testing)."""
    global _batch_processor
    if _batch_processor and _batch_processor.queue:
        _batch_processor.queue.close()
    _batch_processor = None
