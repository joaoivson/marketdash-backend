"""
Unit tests for jobs pipeline: create job (presigned URL), commit, get status.
Run: pytest tests/unit/test_jobs.py -v
"""
import pytest
from uuid import uuid4
from unittest.mock import Mock, patch, MagicMock

from app.services.job_service import JobService
from app.repositories.job_repository import JobRepository
from app.repositories.dataset_repository import DatasetRepository
from app.models.dataset import Dataset
from app.models.job import Job


@pytest.fixture
def mock_db():
    db = Mock()
    db.flush = Mock()
    db.commit = Mock()
    db.refresh = Mock()
    return db


@pytest.fixture
def mock_job_repo(mock_db):
    repo = JobRepository(mock_db)
    return repo


@pytest.fixture
def mock_dataset_repo(mock_db):
    repo = DatasetRepository(mock_db)
    return repo


def test_create_job_returns_presigned_and_ids(mock_job_repo, mock_dataset_repo, mock_db):
    """Create job returns job_id, dataset_id, upload_url, expires_in when storage is configured."""
    with patch("app.services.job_service.is_storage_configured", return_value=True):
        with patch("app.services.job_service.create_presigned_put", return_value="https://storage.example/presigned"):
            with patch.object(mock_dataset_repo, "create", side_effect=lambda d: setattr(d, "id", 42) or d):
                with patch.object(mock_job_repo, "create", side_effect=lambda j: setattr(j, "job_id", uuid4()) or j):
                    service = JobService(mock_job_repo, mock_dataset_repo)
                    result = service.create_job(user_id=1, filename="data.csv", job_type="transaction")
                    assert "job_id" in result
                    assert "dataset_id" in result
                    assert result["dataset_id"] == 42
                    assert result["upload_url"] == "https://storage.example/presigned"
                    assert result["expires_in"] == 3600


def test_create_job_rejects_non_csv(mock_job_repo, mock_dataset_repo):
    """Create job raises 400 for non-CSV filename."""
    with patch("app.services.job_service.is_storage_configured", return_value=True):
        from fastapi import HTTPException
        service = JobService(mock_job_repo, mock_dataset_repo)
        with pytest.raises(HTTPException) as exc:
            service.create_job(user_id=1, filename="data.txt", job_type="transaction")
        assert exc.value.status_code == 400


def test_create_job_rejects_invalid_type(mock_job_repo, mock_dataset_repo):
    """Create job raises 400 for type other than transaction or click."""
    with patch("app.services.job_service.is_storage_configured", return_value=True):
        from fastapi import HTTPException
        service = JobService(mock_job_repo, mock_dataset_repo)
        with pytest.raises(HTTPException) as exc:
            service.create_job(user_id=1, filename="data.csv", job_type="invalid")
        assert exc.value.status_code == 400
