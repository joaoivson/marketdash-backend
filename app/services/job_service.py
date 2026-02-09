"""
Job service: create job + dataset, presigned URL, commit (enqueue split), get status.
"""
import logging
from uuid import UUID, uuid4

from fastapi import HTTPException, status

from app.core.config import settings
from app.models.dataset import Dataset
from app.models.job import Job
from app.repositories.dataset_repository import DatasetRepository
from app.repositories.job_repository import JobRepository
from app.services.storage import create_presigned_put, object_exists, is_storage_configured

logger = logging.getLogger(__name__)


def _bucket() -> str:
    return settings.S3_BUCKET or ""


class JobService:
    def __init__(self, job_repo: JobRepository, dataset_repo: DatasetRepository):
        self.job_repo = job_repo
        self.dataset_repo = dataset_repo

    def create_job(self, user_id: int, filename: str, job_type: str = "transaction") -> dict:
        """
        Create Dataset (pending) and Job. Generate presigned PUT for uploads/{job_id}/{filename}.
        Returns dict with job_id, dataset_id, upload_url, expires_in.
        """
        if not is_storage_configured():
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Object Storage not configured (S3_*). Jobs pipeline unavailable.",
            )
        if not filename or not filename.endswith(".csv"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="filename must be a non-empty CSV filename.",
            )
        if job_type not in ("transaction", "click"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="type must be 'transaction' or 'click'.",
            )

        job_id = uuid4()
        storage_key = f"uploads/{job_id}/{filename}"

        dataset = Dataset(
            user_id=user_id,
            filename=filename,
            type=job_type,
            status="pending",
        )
        self.dataset_repo.create(dataset)
        self.job_repo.db.flush()

        job = Job(
            job_id=job_id,
            dataset_id=dataset.id,
            user_id=user_id,
            type=job_type,
            storage_key=storage_key,
            status="queued",
        )
        self.job_repo.create(job)
        self.job_repo.db.commit()
        self.job_repo.db.refresh(job)
        self.job_repo.db.refresh(dataset)

        upload_url = create_presigned_put(_bucket(), storage_key, expires_in=3600, content_type="text/csv")
        if not upload_url:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Failed to generate upload URL.",
            )

        return {
            "job_id": str(job_id),
            "dataset_id": dataset.id,
            "upload_url": upload_url,
            "expires_in": 3600,
        }

    def commit_job(self, job_id: UUID, user_id: int) -> dict:
        """
        Verify job exists and belongs to user; verify object exists in storage.
        Set job status to processing and enqueue split_and_enqueue_chunks.
        Returns 202 payload.
        """
        job = self.job_repo.get_by_id(job_id, user_id=user_id)
        if not job:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")

        if not object_exists(_bucket(), job.storage_key):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="File not found in storage. Upload the file to the presigned URL before committing.",
            )

        job.status = "processing"
        self.job_repo.db.commit()

        # Enqueue split task (import here to avoid circular import and ensure task is registered)
        from app.tasks.job_tasks import split_and_enqueue_chunks
        split_and_enqueue_chunks.delay(str(job_id))

        return {
            "job_id": str(job_id),
            "status": job.status,
            "message": "File uploaded, processing scheduled.",
        }

    def get_job(self, job_id: UUID, user_id: int) -> dict:
        """Return job status, total_chunks, chunks_done, created_at, errors from failed chunks."""
        job = self.job_repo.get_by_id(job_id, user_id=user_id)
        if not job:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")

        chunks = self.job_repo.get_chunks(job_id)
        errors = [{"chunk_index": c.chunk_index, "error": c.error} for c in chunks if c.status == "failed" and c.error]

        return {
            "job_id": str(job.job_id),
            "dataset_id": job.dataset_id,
            "status": job.status,
            "total_chunks": job.total_chunks or 0,
            "chunks_done": job.chunks_done or 0,
            "created_at": job.created_at.isoformat() if job.created_at else None,
            "errors": errors,
        }

    def list_jobs(self, user_id: int, limit: int = 50) -> list:
        """List jobs for user, most recent first."""
        jobs = self.job_repo.list_by_user(user_id, limit=limit)
        return [
            {
                "job_id": str(j.job_id),
                "dataset_id": j.dataset_id,
                "status": j.status,
                "total_chunks": j.total_chunks or 0,
                "chunks_done": j.chunks_done or 0,
                "created_at": j.created_at.isoformat() if j.created_at else None,
            }
            for j in jobs
        ]
