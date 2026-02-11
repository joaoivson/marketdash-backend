-- Jobs and job_chunks for CSV chunking pipeline (Object Storage + Celery chunks)
-- job_id = UUID; dataset_id = FK to datasets (created when job is created or on commit)

CREATE TABLE IF NOT EXISTS jobs (
  job_id UUID PRIMARY KEY,
  dataset_id INT NOT NULL REFERENCES datasets(id) ON DELETE CASCADE,
  user_id INT NOT NULL REFERENCES users(id),
  type VARCHAR(32) NOT NULL DEFAULT 'transaction',
  storage_key TEXT NOT NULL,
  created_at TIMESTAMPTZ DEFAULT now(),
  status VARCHAR(20) DEFAULT 'queued',
  total_chunks INT DEFAULT 0,
  chunks_done INT DEFAULT 0,
  meta JSONB
);

CREATE TABLE IF NOT EXISTS job_chunks (
  job_id UUID REFERENCES jobs(job_id) ON DELETE CASCADE,
  chunk_index INT NOT NULL,
  storage_key TEXT NOT NULL,
  status VARCHAR(20) DEFAULT 'queued',
  error TEXT,
  PRIMARY KEY (job_id, chunk_index)
);

CREATE INDEX IF NOT EXISTS idx_jobs_dataset_id ON jobs(dataset_id);
CREATE INDEX IF NOT EXISTS idx_jobs_user_created ON jobs(user_id, created_at);
