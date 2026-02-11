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
from app.tasks.job_tasks import process_job_from_storage
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

    process_job_from_storage.delay(str(job_id))
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


class MultipartUploadInitResponse(BaseModel):
    job_id: str
    dataset_id: int
    upload_id: str
    storage_key: str


class MultipartUploadPartRequest(BaseModel):
    part_number: int = Field(..., ge=1, le=10000, description="Part number (1-10000)")


class MultipartUploadPartResponse(BaseModel):
    part_number: int
    upload_url: str
    expires_in: int


class MultipartUploadCompleteRequest(BaseModel):
    parts: list[dict] = Field(..., description='List of {"PartNumber": int, "ETag": str}')


@router.post("/multipart/init", response_model=MultipartUploadInitResponse, status_code=status.HTTP_201_CREATED)
def init_multipart_upload(
    body: JobCreateBody,
    current_user: User = Depends(require_active_subscription),
    db=Depends(get_db),
):
    """
    Initiate multipart upload for large files (>20MB recommended).
    Returns upload_id and storage_key. Client should:
    1. Call POST /jobs/multipart/{job_id}/part for each chunk (5MB-5GB per part)
    2. Upload each part to the presigned URL (PUT with body = chunk)
    3. Collect ETags from response headers
    4. Call POST /jobs/multipart/{job_id}/complete with parts list
    """
    from app.services.storage import create_multipart_upload
    from uuid import uuid4

    if not is_storage_configured():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Object Storage not configured.",
        )

    job_id = uuid4()
    storage_key = f"uploads/{job_id}/{body.filename}"

    upload_id = create_multipart_upload(settings.S3_BUCKET, storage_key, content_type="text/csv")
    if not upload_id:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Failed to initiate multipart upload.",
        )

    dataset = Dataset(
        user_id=current_user.id,
        filename=body.filename,
        type=body.type,
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
        type=body.type,
        storage_key=storage_key,
        status="queued",
    )
    job_repo.create(job)
    job_repo.db.commit()

    return {
        "job_id": str(job_id),
        "dataset_id": dataset.id,
        "upload_id": upload_id,
        "storage_key": storage_key,
    }


@router.post("/multipart/{job_id}/part", response_model=MultipartUploadPartResponse)
def get_multipart_part_url(
    job_id: UUID,
    body: MultipartUploadPartRequest,
    upload_id: str,
    current_user: User = Depends(require_active_subscription),
    db=Depends(get_db),
):
    """
    Generate presigned URL for uploading a single part.
    Client should PUT the chunk to the returned upload_url and save the ETag from response headers.
    """
    from app.services.storage import create_presigned_upload_part

    job_repo = JobRepository(db)
    job = job_repo.get_by_id(job_id, user_id=current_user.id)
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")

    url = create_presigned_upload_part(
        settings.S3_BUCKET,
        job.storage_key,
        upload_id,
        body.part_number,
        expires_in=3600,
    )
    if not url:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Failed to generate presigned URL for part.",
        )

    return {
        "part_number": body.part_number,
        "upload_url": url,
        "expires_in": 3600,
    }


@router.post("/multipart/{job_id}/complete", response_model=JobCommitResponse, status_code=status.HTTP_202_ACCEPTED)
def complete_multipart_upload_endpoint(
    job_id: UUID,
    upload_id: str,
    body: MultipartUploadCompleteRequest,
    current_user: User = Depends(require_active_subscription),
    db=Depends(get_db),
):
    """
    Complete multipart upload and start processing.
    parts = [{"PartNumber": 1, "ETag": "abc123"}, {"PartNumber": 2, "ETag": "def456"}, ...]
    """
    from app.services.storage import complete_multipart_upload

    job_repo = JobRepository(db)
    job = job_repo.get_by_id(job_id, user_id=current_user.id)
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")

    if not complete_multipart_upload(settings.S3_BUCKET, job.storage_key, upload_id, body.parts):
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to complete multipart upload.",
        )

    job.status = "processing"
    job_repo.db.commit()

    process_job_from_storage.delay(str(job_id))

    return {
        "job_id": str(job_id),
        "status": "processing",
        "message": "Multipart upload completed, processing scheduled.",
    }


@router.post("/multipart/{job_id}/abort", status_code=status.HTTP_204_NO_CONTENT)
def abort_multipart_upload_endpoint(
    job_id: UUID,
    upload_id: str,
    current_user: User = Depends(require_active_subscription),
    db=Depends(get_db),
):
    """Abort multipart upload (cleanup). Call this if upload fails or is cancelled."""
    from app.services.storage import abort_multipart_upload

    job_repo = JobRepository(db)
    job = job_repo.get_by_id(job_id, user_id=current_user.id)
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")

    abort_multipart_upload(settings.S3_BUCKET, job.storage_key, upload_id)

    job.status = "cancelled"
    job_repo.db.commit()


@router.post("/{job_id}/retry", response_model=JobCommitResponse, status_code=status.HTTP_202_ACCEPTED)
def retry_job(
    job_id: UUID,
    current_user: User = Depends(require_active_subscription),
    db=Depends(get_db),
):
    """
    Retry a stuck or failed job. Resets chunks_done and re-enqueues the task.
    Only works for jobs in 'pending', 'error', or 'cancelled' status.
    """
    job_repo = JobRepository(db)
    job = job_repo.get_by_id(job_id, user_id=current_user.id)
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")

    if job.status not in ("pending", "error", "cancelled", "queued"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Job is in '{job.status}' status and cannot be retried. Only 'pending', 'error', 'cancelled', or 'queued' jobs can be retried.",
        )

    from app.services.storage import object_exists

    if not object_exists(settings.S3_BUCKET, job.storage_key):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="File not found in storage. Cannot retry job without uploaded file.",
        )

    job.status = "processing"
    job.chunks_done = 0
    job_repo.db.commit()

    process_job_from_storage.delay(str(job_id))

    return {
        "job_id": str(job_id),
        "status": "processing",
        "message": "Job retry scheduled.",
    }

