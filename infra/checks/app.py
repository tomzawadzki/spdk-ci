"""FastAPI app for the SPDK Gerrit Checks backend."""

import logging
from contextlib import asynccontextmanager

import requests as http_requests
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from config import config
from common.gerrit_helpers import validate_change_for_ci
import database
import github_client
import webhook_handler

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=getattr(logging, config.log_level, logging.INFO),
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    database.init_db()
    database.cleanup_old_runs()
    logger.info("SPDK Checks backend started (repo=%s)", config.github_repo)
    yield

app = FastAPI(title="SPDK Checks Backend", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=config.cors_origins,
    allow_methods=["GET", "POST"],
    allow_headers=["X-API-Key", "Content-Type"],
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _require_api_key(x_api_key: str | None):
    """Validate API key if one is configured."""
    if config.api_key and x_api_key != config.api_key:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")


def _validate_change_patchset(change: int, patchset: int):
    if change <= 0 or patchset <= 0:
        raise HTTPException(status_code=400, detail="Invalid change or patchset number")


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------
@app.get("/checks-api/v1/health")
def health():
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# GET runs
# ---------------------------------------------------------------------------
@app.get("/checks-api/v1/changes/{change_number}/patchsets/{patchset_number}/runs")
def get_runs(change_number: int, patchset_number: int):
    _validate_change_patchset(change_number, patchset_number)
    runs = database.get_runs_for_change(change_number, patchset_number)
    return {"runs": runs}


# ---------------------------------------------------------------------------
# POST trigger
# ---------------------------------------------------------------------------
class TriggerRequest(BaseModel):
    event_type: str = Field(default="per-patch-event")
    ref: str | None = Field(default=None, description="Gerrit patchset ref, e.g. refs/changes/45/12345/5")


@app.post("/checks-api/v1/changes/{change_number}/patchsets/{patchset_number}/trigger",
           status_code=202)
def trigger(change_number: int, patchset_number: int,
            body: TriggerRequest | None = None,
            x_api_key: str | None = Header(default=None)):
    _require_api_key(x_api_key)
    _validate_change_patchset(change_number, patchset_number)

    _validate_gerrit_change(change_number, patchset_number)

    ref = (body.ref if body and body.ref
           else f"refs/changes/{change_number % 100:02d}/{change_number}/{patchset_number}")
    event_type = body.event_type if body else "per-patch-event"

    client_payload = {
        "change": {"number": change_number},
        "patchSet": {"number": patchset_number, "ref": ref},
    }

    try:
        github_client.trigger_workflow(event_type, client_payload)
    except http_requests.HTTPError as exc:
        logger.error("GitHub trigger failed: %s", exc)
        raise HTTPException(status_code=502, detail="Failed to trigger GitHub workflow")

    logger.info("Triggered workflow for change %d/%d", change_number, patchset_number)
    return {"message": "Workflow triggered", "change": change_number, "patchset": patchset_number}


def _validate_gerrit_change(change_number: int, patchset_number: int):
    """Check change state via the Gerrit REST API using common helpers."""
    result = validate_change_for_ci(config.gerrit_url, change_number, patchset_number)
    if not result["valid"]:
        error = result["error"]
        status = 502 if error.startswith("Failed to validate") else 409
        raise HTTPException(status_code=status, detail=error)

    # Check for existing Verified vote from CI (app-specific)
    data = result["data"]
    labels = data.get("labels", {})
    verified = labels.get("Verified", {})
    for vote in verified.get("all", []):
        if vote.get("value") and "ci" in (vote.get("name", "") + vote.get("username", "")).lower():
            logger.info("CI already voted on change %d patchset %d", change_number, patchset_number)


# ---------------------------------------------------------------------------
# POST rerun
# ---------------------------------------------------------------------------
@app.post("/checks-api/v1/changes/{change_number}/patchsets/{patchset_number}/rerun",
           status_code=202)
def rerun(change_number: int, patchset_number: int,
          x_api_key: str | None = Header(default=None)):
    _require_api_key(x_api_key)
    _validate_change_patchset(change_number, patchset_number)

    latest_run = database.get_latest_run_for_change(change_number, patchset_number)
    if not latest_run:
        raise HTTPException(status_code=404, detail="No workflow runs found for this change/patchset")

    github_run_id = latest_run["github_run_id"]
    try:
        github_client.rerun_failed_jobs(github_run_id)
    except http_requests.HTTPError as exc:
        logger.error("GitHub rerun failed: %s", exc)
        raise HTTPException(status_code=502, detail="Failed to rerun failed jobs")

    logger.info("Rerun failed jobs for run %d (change %d/%d)",
                github_run_id, change_number, patchset_number)
    return {"message": "Rerun triggered", "github_run_id": github_run_id}


# ---------------------------------------------------------------------------
# POST webhook
# ---------------------------------------------------------------------------
@app.post("/checks-api/v1/webhook/github")
async def github_webhook(request: Request):
    body = await request.body()
    signature = request.headers.get("X-Hub-Signature-256")

    if not webhook_handler.verify_signature(body, signature):
        raise HTTPException(status_code=401, detail="Invalid webhook signature")

    event = request.headers.get("X-GitHub-Event", "")
    payload = await request.json()

    if event == "workflow_run":
        webhook_handler.handle_workflow_run(payload)
    elif event == "workflow_job":
        webhook_handler.handle_workflow_job(payload)
    else:
        logger.debug("Ignoring GitHub event: %s", event)

    return {"status": "ok"}


# ---------------------------------------------------------------------------
# POST register (called by forwarder to map Gerrit → GitHub run)
# ---------------------------------------------------------------------------
class RegisterRequest(BaseModel):
    gerrit_change: int
    gerrit_patchset: int
    gerrit_project: str = ""
    github_run_id: int


@app.post("/checks-api/v1/runs/register", status_code=201)
def register_run(body: RegisterRequest,
                 x_api_key: str | None = Header(default=None)):
    _require_api_key(x_api_key)
    if body.gerrit_change <= 0 or body.gerrit_patchset <= 0 or body.github_run_id <= 0:
        raise HTTPException(status_code=400, detail="Invalid input values")

    database.upsert_workflow_run(
        gerrit_change_number=body.gerrit_change,
        gerrit_patchset_number=body.gerrit_patchset,
        gerrit_project=body.gerrit_project,
        github_run_id=body.github_run_id,
    )
    logger.info("Registered run mapping: change %d/%d → run %d",
                body.gerrit_change, body.gerrit_patchset, body.github_run_id)
    return {"message": "Run registered", "github_run_id": body.github_run_id}
