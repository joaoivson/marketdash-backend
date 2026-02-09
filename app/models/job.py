from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Index, Text
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from sqlalchemy.dialects.postgresql import UUID, JSONB

from app.db.base import Base


class Job(Base):
    __tablename__ = "jobs"

    job_id = Column(UUID(as_uuid=True), primary_key=True)
    dataset_id = Column(Integer, ForeignKey("datasets.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    type = Column(String(32), nullable=False, default="transaction")
    storage_key = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    status = Column(String(20), default="queued")
    total_chunks = Column(Integer, default=0)
    chunks_done = Column(Integer, default=0)
    meta = Column(JSONB, nullable=True)

    # Relationships
    chunks = relationship("JobChunk", back_populates="job", cascade="all, delete-orphan")

    __table_args__ = (
        Index("idx_jobs_dataset_id", "dataset_id"),
        Index("idx_jobs_user_created", "user_id", "created_at"),
    )


class JobChunk(Base):
    __tablename__ = "job_chunks"

    job_id = Column(UUID(as_uuid=True), ForeignKey("jobs.job_id", ondelete="CASCADE"), primary_key=True)
    chunk_index = Column(Integer, primary_key=True)
    storage_key = Column(Text, nullable=False)
    status = Column(String(20), default="queued")
    error = Column(Text, nullable=True)

    # Relationships
    job = relationship("Job", back_populates="chunks")
