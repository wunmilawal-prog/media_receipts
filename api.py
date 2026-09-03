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
from fastapi import (
    BackgroundTasks,
    Depends,
    FastAPI,
    File,
    Header,
    HTTPException,
    UploadFile,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
import jwt
import requests

from process_media_receipts import (
    DROPBOX_CONTENT_URL,
    DROPBOX_TIMEOUT_SECONDS,
    DropboxIntegrationError,
    _dropbox_error,
    _dropbox_remote_path,
    dropbox_list_folder,
    get_dropbox_access_token,
    validate_filename,
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


class InvoiceUploadResult(BaseModel):
    name: str
    status: Literal["uploaded", "rejected", "failed"]
    size: Optional[int] = None
    message: str
    warnings: List[str] = Field(default_factory=list)


class InvoiceUploadResponse(BaseModel):
    uploaded: int
    rejected: int
    failed: int
    results: List[InvoiceUploadResult]


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


class RunFile(BaseModel):
    id: str
    name: str
    kind: Literal["fp_import", "summary", "manual_review", "multi_job", "other"]
    size: Optional[int] = None


RUNS: Dict[str, RunRecord] = {}
RUN_FILES: Dict[str, List[dict]] = {}
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
    root = os.getenv("DROPBOX_MEDIA_ROOT", "/Automation Testing").strip().strip('"')
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


def _safe_upload_filename(filename: Optional[str]) -> Tuple[Optional[str], str]:
    name = (filename or "").strip()
    if not name:
        return None, "The uploaded file has no filename."
    if (
        name in {".", ".."}
        or "/" in name
        or "\\" in name
        or "\x00" in name
        or any(ord(character) < 32 for character in name)
    ):
        return None, "The filename contains an unsafe path or control character."
    if len(name) > 255:
        return None, "The filename is longer than 255 characters."
    if not name.lower().endswith(".pdf"):
        return None, "Only PDF invoices are accepted."
    return name, ""


async def _read_upload_limited(upload: UploadFile, maximum_bytes: int) -> bytes:
    chunks = []
    total = 0
    while True:
        chunk = await upload.read(1024 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > maximum_bytes:
            raise ValueError(
                f"The file exceeds the {maximum_bytes // (1024 * 1024)} MB limit."
            )
        chunks.append(chunk)
    return b"".join(chunks)


def _upload_invoice_bytes(
    access_token: str, remote_path: str, content: bytes
) -> dict:
    response = requests.post(
        f"{DROPBOX_CONTENT_URL}/files/upload",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/octet-stream",
            "Dropbox-API-Arg": json.dumps({
                "path": remote_path,
                "mode": "add",
                "autorename": False,
                "mute": False,
            }),
        },
        data=content,
        timeout=DROPBOX_TIMEOUT_SECONDS,
    )
    _dropbox_error(response, f"upload {remote_path}")
    return response.json()


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
        is_processed_route = bool(
            route == "Processed" or str(route or "").startswith("Processed/")
        )
        results.append(PreviewItem(
            filename=filename,
            status="ready" if row and is_processed_route else "review",
            route=route,
            row=row,
            message=None if row else "No import-ready row was generated.",
        ))
    return results


def _output_kind(filename: str) -> str:
    if filename.startswith("FP_Import_"):
        return "fp_import"
    if filename.startswith("Processing_Summary_"):
        return "summary"
    if filename.startswith("ManualReview_"):
        return "manual_review"
    if filename.startswith("MultiJob_Summary_"):
        return "multi_job"
    return "other"


def _parse_uploaded_files(output: str) -> List[dict]:
    marker = "ZGM_OUTPUTS_JSON="
    for line in reversed(output.splitlines()):
        if not line.startswith(marker):
            continue
        try:
            metadata_items = json.loads(line[len(marker):])
        except json.JSONDecodeError:
            return []
        files = []
        for metadata in metadata_items:
            remote_path = metadata.get("path_display") or metadata.get("path_lower")
            name = metadata.get("name")
            if not remote_path or not name:
                continue
            files.append({
                "id": str(uuid.uuid4()),
                "name": name,
                "kind": _output_kind(name),
                "size": metadata.get("size"),
                "remote_path": remote_path,
            })
        return files
    return []


def _public_run_file(item: dict) -> RunFile:
    return RunFile(
        id=item["id"],
        name=item["name"],
        kind=item["kind"],
        size=item.get("size"),
    )


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
    "/api/invoices/upload",
    response_model=InvoiceUploadResponse,
    dependencies=[Depends(require_api_token)],
)
async def upload_invoices(
    files: List[UploadFile] = File(...),
) -> InvoiceUploadResponse:
    maximum_files = int(os.getenv("MAX_UPLOAD_FILES", "20"))
    maximum_bytes = int(os.getenv("MAX_UPLOAD_BYTES", str(25 * 1024 * 1024)))
    if not files:
        raise HTTPException(status_code=400, detail="Select at least one PDF.")
    if len(files) > maximum_files:
        raise HTTPException(
            status_code=413,
            detail=f"A maximum of {maximum_files} invoices can be uploaded at once.",
        )
    if RUN_LOCK.locked():
        raise HTTPException(
            status_code=409,
            detail="Wait for the active preview or processing run to finish.",
        )

    try:
        existing = await asyncio.to_thread(_dropbox_incoming_files)
        access_token = await asyncio.to_thread(get_dropbox_access_token)
    except DropboxIntegrationError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    root = os.getenv("DROPBOX_MEDIA_ROOT", "/Automation Testing").strip().strip('"')
    incoming_root = _dropbox_remote_path(root, "Incoming")
    reserved_names = {
        entry.get("name", "").casefold()
        for entry in existing
        if entry.get("name")
    }
    results = []

    async with RUN_LOCK:
        for upload in files:
            safe_name, filename_error = _safe_upload_filename(upload.filename)
            display_name = safe_name or upload.filename or "unnamed file"
            if filename_error:
                results.append(InvoiceUploadResult(
                    name=display_name,
                    status="rejected",
                    message=filename_error,
                ))
                await upload.close()
                continue
            if safe_name.casefold() in reserved_names:
                results.append(InvoiceUploadResult(
                    name=safe_name,
                    status="rejected",
                    message="A file with this name already exists in Dropbox Incoming.",
                ))
                await upload.close()
                continue

            try:
                content = await _read_upload_limited(upload, maximum_bytes)
                if not content.startswith(b"%PDF-"):
                    results.append(InvoiceUploadResult(
                        name=safe_name,
                        status="rejected",
                        size=len(content),
                        message="The file does not contain a valid PDF header.",
                    ))
                    continue
                is_valid_name, naming_issues = validate_filename(safe_name)
                warnings = [] if is_valid_name else naming_issues
                remote_path = _dropbox_remote_path(incoming_root, safe_name)
                metadata = await asyncio.to_thread(
                    _upload_invoice_bytes,
                    access_token,
                    remote_path,
                    content,
                )
                reserved_names.add(safe_name.casefold())
                results.append(InvoiceUploadResult(
                    name=metadata.get("name", safe_name),
                    status="uploaded",
                    size=metadata.get("size", len(content)),
                    message="Uploaded to Dropbox Incoming.",
                    warnings=warnings,
                ))
            except ValueError as exc:
                results.append(InvoiceUploadResult(
                    name=safe_name,
                    status="rejected",
                    message=str(exc),
                ))
            except (DropboxIntegrationError, requests.RequestException) as exc:
                results.append(InvoiceUploadResult(
                    name=safe_name,
                    status="failed",
                    message=str(exc),
                ))
            finally:
                await upload.close()

    return InvoiceUploadResponse(
        uploaded=sum(result.status == "uploaded" for result in results),
        rejected=sum(result.status == "rejected" for result in results),
        failed=sum(result.status == "failed" for result in results),
        results=results,
    )


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
        RUN_FILES[run_id] = _parse_uploaded_files(output)
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
    RUN_FILES[run_id] = []
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


@app.get(
    "/api/runs/{run_id}/files",
    response_model=List[RunFile],
    dependencies=[Depends(require_api_token)],
)
def list_run_files(run_id: str) -> List[RunFile]:
    record = RUNS.get(run_id)
    if not record:
        raise HTTPException(status_code=404, detail="Run not found.")
    if record.status in {"queued", "running"}:
        raise HTTPException(status_code=409, detail="The run is not finished yet.")
    return [_public_run_file(item) for item in RUN_FILES.get(run_id, [])]


@app.get(
    "/api/runs/{run_id}/files/{file_id}/download",
    dependencies=[Depends(require_api_token)],
)
def download_run_file(run_id: str, file_id: str) -> StreamingResponse:
    record = RUNS.get(run_id)
    if not record:
        raise HTTPException(status_code=404, detail="Run not found.")
    item = next(
        (
            candidate for candidate in RUN_FILES.get(run_id, [])
            if candidate["id"] == file_id
        ),
        None,
    )
    if not item:
        raise HTTPException(status_code=404, detail="Run output file not found.")

    try:
        access_token = get_dropbox_access_token()
        response = requests.post(
            f"{DROPBOX_CONTENT_URL}/files/download",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Dropbox-API-Arg": json.dumps({"path": item["remote_path"]}),
            },
            timeout=DROPBOX_TIMEOUT_SECONDS,
            stream=True,
        )
        _dropbox_error(response, f"download {item['remote_path']}")
    except (DropboxIntegrationError, requests.RequestException) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    media_type = (
        "text/csv; charset=utf-8"
        if item["name"].lower().endswith(".csv")
        else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )

    def content():
        try:
            for chunk in response.iter_content(chunk_size=64 * 1024):
                if chunk:
                    yield chunk
        finally:
            response.close()

    safe_name = item["name"].replace('"', "")
    return StreamingResponse(
        content(),
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{safe_name}"'},
    )
