FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    postgresql-client \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first (better Docker caching)
COPY requirements.txt .
# Retry pip on network flakes (e.g. Broken pipe in CI); --retries/--timeout help within pip
RUN for i in 1 2 3; do pip install --no-cache-dir --retries 5 --timeout 120 -r requirements.txt && break; done

# Install gunicorn for production
RUN pip install --no-cache-dir --retries 5 --timeout 120 gunicorn[gevent]

# Copy application code
COPY . .

# Expose port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Run with gunicorn (production)
CMD ["gunicorn", "app.main:app", "-w", "2", "-k", "uvicorn.workers.UvicornWorker", "-b", "0.0.0.0:8000"]

