"""Jobs pipeline API: create job (presigned URL), commit, status. Only registered when USE_JOBS_PIPELINE=true."""
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status

from app.api.v1.dependencies import require_active_subscription
from app.core.config import settings
from app.db.session import get_db
from app.models.dataset import Dataset
from app.models.job import Job
from app.models.user import User
from app.repositories.dataset_repository import DatasetRepository
from app.repositories.job_repository import JobRepository
from app.services.job_service import JobService
from app.services.storage import upload_file_obj, is_storage_configured
from app.tasks.job_tasks import split_and_enqueue_chunks
from pydantic import BaseModel, Field

router = APIRouter(tags=["jobs"])


class JobCreateBody(BaseModel):
    filename: str = Field(..., min_length=1, description="CSV filename (e.g. data.csv)")
    type: str = Field("transaction", description="transaction or click")


class JobCreateResponse(BaseModel):
    job_id: str
    dataset_id: int
    upload_url: str
    expires_in: int


class JobCommitResponse(BaseModel):
    job_id: str
    status: str
    message: str


class JobStatusResponse(BaseModel):
    job_id: str
    dataset_id: int
    status: str
    total_chunks: int
    chunks_done: int
    created_at: str | None
    errors: list


class JobListItem(BaseModel):
    job_id: str
    dataset_id: int
    status: str
    total_chunks: int
    chunks_done: int
    created_at: str | None


@router.post("", response_model=JobCreateResponse, status_code=status.HTTP_201_CREATED)
def create_job(
    body: JobCreateBody,
    current_user: User = Depends(require_active_subscription),
    db=Depends(get_db),
):
    """
    Create a job and dataset; returns a presigned PUT URL.
    Client must upload the CSV file to upload_url (PUT with body = file content), then call POST /jobs/{job_id}/commit.
    """
    service = JobService(JobRepository(db), DatasetRepository(db))
    result = service.create_job(current_user.id, body.filename, body.type)
    return result


class JobUploadResponse(BaseModel):
    job_id: str
    dataset_id: int
    status: str = "processing"


@router.post("/upload", response_model=JobUploadResponse, status_code=status.HTTP_202_ACCEPTED)
async def upload_job_file(
    file: UploadFile = File(...),
    type: str = "transaction",
    current_user: User = Depends(require_active_subscription),
    db=Depends(get_db),
):
    """
    Fallback: upload CSV in request body (streaming). File is written to storage in chunks (1–4 MB),
    then job + dataset are created and split_and_enqueue_chunks is enqueued. Use when client cannot use presigned PUT.
    """
    if not is_storage_configured():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Object Storage not configured. Use presigned flow or configure S3_*.",
        )
    if not file.filename or not file.filename.endswith(".csv"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="A CSV file is required.")
    if type not in ("transaction", "click"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="type must be 'transaction' or 'click'.")

    from io import BytesIO
    from uuid import uuid4

    job_id = uuid4()
    storage_key = f"uploads/{job_id}/{file.filename}"
    bucket = settings.S3_BUCKET
    buf = BytesIO()
    chunk_size = 4 * 1024 * 1024  # 4 MB
    while chunk := await file.read(chunk_size):
        buf.write(chunk)
    buf.seek(0)
    if not upload_file_obj(bucket, storage_key, buf, content_type="text/csv"):
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Failed to upload file to storage.")

    dataset = Dataset(
        user_id=current_user.id,
        filename=file.filename,
        type=type,
        status="pending",
    )
    dataset_repo = DatasetRepository(db)
    job_repo = JobRepository(db)
    dataset_repo.create(dataset)
    job_repo.db.flush()
    job = Job(
        job_id=job_id,
        dataset_id=dataset.id,
        user_id=current_user.id,
        type=type,
        storage_key=storage_key,
        status="processing",
    )
    job_repo.create(job)
    job_repo.db.commit()

    split_and_enqueue_chunks.delay(str(job_id))
    return {"job_id": str(job_id), "dataset_id": dataset.id, "status": "processing"}


@router.post("/{job_id}/commit", response_model=JobCommitResponse, status_code=status.HTTP_202_ACCEPTED)
def commit_job(
    job_id: UUID,
    current_user: User = Depends(require_active_subscription),
    db=Depends(get_db),
):
    """
    Confirm upload and start processing. File must already be uploaded to the presigned URL.
    Returns 202 Accepted; poll GET /jobs/{job_id} for progress.
    """
    service = JobService(JobRepository(db), DatasetRepository(db))
    result = service.commit_job(job_id, current_user.id)
    return result


@router.get("/{job_id}", response_model=JobStatusResponse)
def get_job(
    job_id: UUID,
    current_user: User = Depends(require_active_subscription),
    db=Depends(get_db),
):
    """Get job status and progress (total_chunks, chunks_done, errors)."""
    service = JobService(JobRepository(db), DatasetRepository(db))
    return service.get_job(job_id, current_user.id)


@router.get("", response_model=list[JobListItem])
def list_jobs(
    current_user: User = Depends(require_active_subscription),
    db=Depends(get_db),
    limit: int = 50,
):
    """List jobs for the current user, most recent first."""
    service = JobService(JobRepository(db), DatasetRepository(db))
    return service.list_jobs(current_user.id, limit=limit)
