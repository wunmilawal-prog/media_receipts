"""HTTPS API for the ZGM Media Receipts processor.

The API deliberately runs the existing CLI in a child process. This keeps the
tested extraction/routing flow as the single source of truth and isolates its
temporary global folder configuration from the web server.
"""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Literal, Optional, Tuple

from dotenv import load_dotenv
from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
import jwt

from process_media_receipts import (
    DropboxIntegrationError,
    _dropbox_remote_path,
    dropbox_list_folder,
    get_dropbox_access_token,
)


PROJECT_DIR = Path(__file__).resolve().parent
PROCESSOR = PROJECT_DIR / "process_media_receipts.py"
load_dotenv(PROJECT_DIR / ".env")

app = FastAPI(
    title="ZGM Media Receipts API",
    version="1.0.0",
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
)

allowed_origins = [
    origin.strip()
    for origin in os.getenv("CORS_ORIGINS", "http://localhost:5173").split(",")
    if origin.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Authorization", "Content-Type"],
)


class Invoice(BaseModel):
    name: str
    size: Optional[int] = None
    modified_at: Optional[str] = None


class Selection(BaseModel):
    filenames: List[str] = Field(min_length=1, max_length=100)


class PreviewItem(BaseModel):
    filename: str
    status: Literal["ready", "review", "error"]
    route: Optional[str] = None
    row: Optional[Dict[str, str]] = None
    message: Optional[str] = None


class RunRecord(BaseModel):
    id: str
    status: Literal["queued", "running", "succeeded", "failed"]
    filenames: List[str]
    created_at: str
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    message: Optional[str] = None
    log: str = ""


RUNS: Dict[str, RunRecord] = {}
RUN_LOCK = asyncio.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def require_api_token(authorization: Optional[str] = Header(default=None)) -> None:
    """Accept a Supabase user JWT in production or a shared token for local use."""
    shared_token = os.getenv("MEDIA_API_TOKEN", "").strip()
    jwt_secret = os.getenv("SUPABASE_JWT_SECRET", "").strip()
    if not shared_token and not jwt_secret:
        raise HTTPException(
            status_code=503,
            detail="Backend authentication is not configured.",
        )
    scheme, _, supplied = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not supplied:
        raise HTTPException(status_code=401, detail="Invalid or missing bearer token.")

    if shared_token and secrets.compare_digest(supplied, shared_token):
        return

    if jwt_secret:
        try:
            jwt.decode(
                supplied,
                jwt_secret,
                algorithms=["HS256"],
                audience=os.getenv("SUPABASE_JWT_AUDIENCE", "authenticated"),
            )
            return
        except jwt.PyJWTError:
            pass

    raise HTTPException(status_code=401, detail="Invalid or missing bearer token.")


def _dropbox_incoming_files() -> List[dict]:
    root = os.getenv("DROPBOX_MEDIA_ROOT", "/Media Receipts").strip().strip('"')
    incoming = _dropbox_remote_path(root, "Incoming")
    token = get_dropbox_access_token()
    return [
        entry
        for entry in dropbox_list_folder(token, incoming)
        if entry.get(".tag") == "file"
    ]


def _validate_selection(filenames: List[str], available: List[dict]) -> None:
    available_names = {entry.get("name", "").casefold() for entry in available}
    missing = [name for name in filenames if name.casefold() not in available_names]
    if missing:
        raise HTTPException(
            status_code=404,
            detail={"message": "Invoice is no longer in Incoming.", "files": missing},
        )


async def _processor_command(filenames: List[str], dry_run: bool) -> Tuple[int, str]:
    command = [sys.executable, str(PROCESSOR)]
    for filename in filenames:
        command.extend(["--file", filename])
    if dry_run:
        command.append("--dry-run")
    process = await asyncio.create_subprocess_exec(
        *command,
        cwd=str(PROJECT_DIR),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    output, _ = await process.communicate()
    return process.returncode or 0, output.decode("utf-8", errors="replace")


def _parse_preview(
    filenames: List[str], returncode: int, output: str
) -> List[PreviewItem]:
    marker = "ZGM_PREVIEW_JSON="
    payload = None
    for line in reversed(output.splitlines()):
        if line.startswith(marker):
            try:
                payload = json.loads(line[len(marker):])
            except json.JSONDecodeError:
                payload = None
            break

    if returncode != 0 or payload is None:
        return [
            PreviewItem(
                filename=filename,
                status="error",
                message="The batch preview failed or returned an invalid result.",
            )
            for filename in filenames
        ]

    results_by_name = {
        item.get("filename", "").casefold(): item for item in payload
    }
    results = []
    for filename in filenames:
        item = results_by_name.get(filename.casefold())
        if not item:
            results.append(PreviewItem(
                filename=filename,
                status="error",
                message="No preview result was returned for this invoice.",
            ))
            continue
        row = item.get("row")
        route = item.get("route")
        results.append(PreviewItem(
            filename=filename,
            status="ready" if row and route == "Processed" else "review",
            route=route,
            row=row,
            message=None if row else "No import-ready row was generated.",
        ))
    return results


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "zgm-media-receipts"}


@app.get(
    "/api/invoices",
    response_model=list[Invoice],
    dependencies=[Depends(require_api_token)],
)
def invoices() -> List[Invoice]:
    try:
        entries = _dropbox_incoming_files()
    except DropboxIntegrationError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return [
        Invoice(
            name=entry["name"],
            size=entry.get("size"),
            modified_at=entry.get("client_modified") or entry.get("server_modified"),
        )
        for entry in sorted(entries, key=lambda item: item["name"].casefold())
    ]


@app.post(
    "/api/runs/preview",
    response_model=list[PreviewItem],
    dependencies=[Depends(require_api_token)],
)
async def preview(selection: Selection) -> List[PreviewItem]:
    if RUN_LOCK.locked():
        raise HTTPException(status_code=409, detail="A processing run is in progress.")
    try:
        available = await asyncio.to_thread(_dropbox_incoming_files)
    except DropboxIntegrationError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    _validate_selection(selection.filenames, available)

    async with RUN_LOCK:
        returncode, output = await _processor_command(
            selection.filenames, dry_run=True
        )
        return _parse_preview(selection.filenames, returncode, output)


async def _execute_run(run_id: str) -> None:
    record = RUNS[run_id]
    record.status = "running"
    record.started_at = _now()
    try:
        async with RUN_LOCK:
            returncode, output = await _processor_command(
                record.filenames, dry_run=False
            )
            if returncode != 0 or "Dropbox synchronization stopped" in output:
                raise RuntimeError("The selected invoice batch failed.")
        record.status = "succeeded"
        record.message = f"Processed {len(record.filenames)} invoice(s)."
    except Exception as exc:
        record.status = "failed"
        record.message = str(exc)
    finally:
        record.log = output[-50000:] if "output" in locals() else ""
        record.finished_at = _now()


@app.post(
    "/api/runs",
    response_model=RunRecord,
    status_code=202,
    dependencies=[Depends(require_api_token)],
)
async def start_run(selection: Selection, background_tasks: BackgroundTasks) -> RunRecord:
    if RUN_LOCK.locked() or any(
        run.status in {"queued", "running"} for run in RUNS.values()
    ):
        raise HTTPException(status_code=409, detail="A processing run is in progress.")
    try:
        available = await asyncio.to_thread(_dropbox_incoming_files)
    except DropboxIntegrationError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    _validate_selection(selection.filenames, available)

    run_id = str(uuid.uuid4())
    record = RunRecord(
        id=run_id,
        status="queued",
        filenames=selection.filenames,
        created_at=_now(),
    )
    RUNS[run_id] = record
    background_tasks.add_task(_execute_run, run_id)
    return record


@app.get(
    "/api/runs/{run_id}",
    response_model=RunRecord,
    dependencies=[Depends(require_api_token)],
)
def get_run(run_id: str) -> RunRecord:
    record = RUNS.get(run_id)
    if not record:
        raise HTTPException(status_code=404, detail="Run not found.")
    return record
