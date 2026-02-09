from typing import List, Optional
from uuid import UUID

from sqlalchemy.orm import Session
from sqlalchemy import and_

from app.models.job import Job, JobChunk


class JobRepository:
    def __init__(self, db: Session):
        self.db = db

    def create(self, job: Job) -> Job:
        self.db.add(job)
        self.db.flush()
        return job

    def get_by_id(self, job_id: UUID, user_id: Optional[int] = None) -> Optional[Job]:
        q = self.db.query(Job).filter(Job.job_id == job_id)
        if user_id is not None:
            q = q.filter(Job.user_id == user_id)
        return q.first()

    def list_by_user(self, user_id: int, limit: Optional[int] = None) -> List[Job]:
        q = self.db.query(Job).filter(Job.user_id == user_id).order_by(Job.created_at.desc())
        if limit is not None:
            q = q.limit(limit)
        return q.all()

    def add_chunk(self, chunk: JobChunk) -> JobChunk:
        self.db.add(chunk)
        self.db.flush()
        return chunk

    def get_chunks(self, job_id: UUID) -> List[JobChunk]:
        return self.db.query(JobChunk).filter(JobChunk.job_id == job_id).order_by(JobChunk.chunk_index).all()

    def update_job_chunks_done(self, job_id: UUID, chunks_done_delta: int = 1) -> Optional[int]:
        """Increment chunks_done by delta and return new value. Uses atomic update."""
        job = self.get_by_id(job_id)
        if not job:
            return None
        job.chunks_done = (job.chunks_done or 0) + chunks_done_delta
        self.db.flush()
        return job.chunks_done

    def set_chunk_status(self, job_id: UUID, chunk_index: int, status: str, error: Optional[str] = None) -> None:
        chunk = (
            self.db.query(JobChunk)
            .filter(and_(JobChunk.job_id == job_id, JobChunk.chunk_index == chunk_index))
            .first()
        )
        if chunk:
            chunk.status = status
            if error is not None:
                chunk.error = error
            self.db.flush()
