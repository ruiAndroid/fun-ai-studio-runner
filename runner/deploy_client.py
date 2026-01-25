import requests
from typing import Optional, Dict, Any

from runner import settings


def claim_job() -> Optional[Dict[str, Any]]:
    url = settings.DEPLOY_BASE_URL.rstrip("/") + "/deploy/jobs/claim"
    body = {"runnerId": settings.RUNNER_ID, "leaseSeconds": settings.JOB_LEASE_SECONDS}
    r = requests.post(url, json=body, timeout=10)
    r.raise_for_status()
    data = r.json()
    if data.get("code") != 200:
        raise RuntimeError(f"deploy claim failed: {data}")
    return data.get("data")

def heartbeat_job(job_id: str, extend_seconds: int) -> None:
    url = settings.DEPLOY_BASE_URL.rstrip("/") + f"/deploy/jobs/{job_id}/heartbeat"
    body = {"runnerId": settings.RUNNER_ID, "extendSeconds": int(extend_seconds)}
    r = requests.post(url, json=body, timeout=10)
    r.raise_for_status()
    data = r.json()
    if data.get("code") != 200:
        raise RuntimeError(f"deploy heartbeat failed: {data}")


def report_job(job_id: str, status: str, error_message: Optional[str] = None) -> None:
    url = settings.DEPLOY_BASE_URL.rstrip("/") + f"/deploy/jobs/{job_id}/report"
    body = {"runnerId": settings.RUNNER_ID, "status": status}
    if error_message:
        body["errorMessage"] = error_message
    r = requests.post(url, json=body, timeout=10)
    r.raise_for_status()
    data = r.json()
    if data.get("code") != 200:
        raise RuntimeError(f"deploy report failed: {data}")


