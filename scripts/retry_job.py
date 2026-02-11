"""
Script to retry a stuck job.
Usage: python retry_job.py <job_id>
"""
import sys
import requests
from uuid import UUID

# Configuration
API_BASE_URL = "https://api.marketdash.com.br"  # Change to your API URL
# You need to provide a valid JWT token for authentication
# Get this from your browser's localStorage or by logging in via API
AUTH_TOKEN = "YOUR_JWT_TOKEN_HERE"

def retry_job(job_id: str):
    """Retry a stuck job via API."""
    try:
        # Validate UUID
        UUID(job_id)
    except ValueError:
        print(f"Error: Invalid job_id format: {job_id}")
        return False

    url = f"{API_BASE_URL}/api/v1/jobs/{job_id}/retry"
    headers = {
        "Authorization": f"Bearer {AUTH_TOKEN}",
        "Content-Type": "application/json",
    }

    print(f"Retrying job {job_id}...")
    response = requests.post(url, headers=headers)

    if response.status_code == 202:
        data = response.json()
        print(f"✓ Job retry scheduled successfully!")
        print(f"  Job ID: {data['job_id']}")
        print(f"  Status: {data['status']}")
        print(f"  Message: {data['message']}")
        return True
    else:
        print(f"✗ Failed to retry job. Status: {response.status_code}")
        print(f"  Response: {response.text}")
        return False


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python retry_job.py <job_id>")
        print("Example: python retry_job.py b9016fe0-6926-4aca-a918-e03ff49e6955")
        sys.exit(1)

    job_id = sys.argv[1]
    
    if AUTH_TOKEN == "YOUR_JWT_TOKEN_HERE":
        print("Error: Please set AUTH_TOKEN in the script before running.")
        print("You can get your token from:")
        print("  1. Browser DevTools → Application → Local Storage → token")
        print("  2. Or login via API: POST /api/v1/auth/login")
        sys.exit(1)

    success = retry_job(job_id)
    sys.exit(0 if success else 1)
