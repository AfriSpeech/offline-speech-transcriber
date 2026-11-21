"""Modal Backend for LEIN Volunteer Submission Upload Portal.

Serves a tus resumable-upload endpoint for volunteer submission packages (.zip),
stages chunks temporarily on Modal Volume, and commits completed submissions
directly to Hugging Face dataset (AfriSpeech/liberian-english-volunteer-submissions)
while logging upload metadata to submissions_log.jsonl for the admin dashboard.
"""

import base64
import hashlib
import json
import os
import re
import shutil
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict

import modal

APP_NAME = "lein-volunteer-upload-api"
VOLUME_NAME = "lein-volunteer-upload-staging"
STAGING_ROOT = Path("/staging")
UPLOADS_ROOT = STAGING_ROOT / "uploads"

CHUNK_SIZE = 16 * 1024 * 1024
MAX_FILE_SIZE = 2 * 1024**3 # 2 GB max
STALE_UPLOAD_HOURS = 24
ALLOWED_EXTENSIONS = {".zip", ".csv", ".json", ".wav", ".mp3", ".flac"}
TARGET_DATASET = "AfriSpeech/liberian-english-volunteer-submissions"

volume = modal.Volume.from_name(VOLUME_NAME, create_if_missing=True)
hub_secret = modal.Secret.from_name("huggingface")

image = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install("fastapi>=0.115,<1", "huggingface_hub>=0.25", "uvicorn>=0.30")
)

app = modal.App(APP_NAME, image=image)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _safe_filename(value: str) -> str:
    value = value.replace("\\", "/").split("/")[-1]
    value = re.sub(r"[^\w.\- ()]", "_", value, flags=re.UNICODE)
    return value.strip(". ")[:180] or "submission.zip"


def _parse_upload_metadata(header: str | None) -> dict[str, str]:
    result: dict[str, str] = {}
    if not header:
        return result
    for item in header.split(","):
        if not item.strip():
            continue
        key, _, encoded = item.strip().partition(" ")
        try:
            result[key.lower()] = base64.b64decode(encoded + "===").decode("utf-8", "replace")
        except Exception:
            continue
    return result


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _reload_volume() -> None:
    try:
        volume.reload()
    except Exception:
        pass


def _commit_volume() -> None:
    volume.commit()


@app.function(
    image=image,
    secrets=[hub_secret],
    volumes={str(STAGING_ROOT): volume},
    timeout=15 * 60,
    retries=1,
)
def finalize_volunteer_upload(upload_id: str) -> dict[str, Any]:
    """Move one fully staged submission into HF dataset, log to submissions_log.jsonl, and free staging."""
    from huggingface_hub import HfApi, hf_hub_download

    _reload_volume()
    folder = UPLOADS_ROOT / upload_id
    meta_path = folder / "meta.json"
    data_path = folder / "data.bin"
    done_path = folder / "done.json"

    if not meta_path.exists():
        return {"status": "error", "error": "upload metadata not found"}

    meta = _read_json(meta_path)
    if not data_path.exists() or data_path.stat().st_size != int(meta["length"]):
        return {"status": "error", "error": "staged file is incomplete"}

    try:
        digest = hashlib.sha256()
        with data_path.open("rb") as handle:
            for block in iter(lambda: handle.read(4 * 1024 * 1024), b""):
                digest.update(block)
        checksum = digest.hexdigest()

        stamp = datetime.now(timezone.utc)
        date_str = stamp.strftime("%Y-%m-%d")
        safe_name = meta["safe_filename"]
        task = meta.get("task_type", "transcription")
        vol_clean = re.sub(r"[^\w-]", "_", meta.get("volunteer_name", "volunteer")).lower()

        remote_path = f"submissions/{task}/{date_str}/{vol_clean}_{checksum[:8]}_{safe_name}"

        api = HfApi(token=os.environ["HF_TOKEN"])

        # Upload submission file to Hugging Face
        api.upload_file(
            path_or_fileobj=str(data_path),
            path_in_repo=remote_path,
            repo_id=TARGET_DATASET,
            repo_type="dataset",
            commit_message=f"Add volunteer submission from {meta.get('volunteer_name')} ({safe_name})"
        )

        # Log entry for admin dashboard
        log_entry = {
            "upload_id": upload_id,
            "volunteer_name": meta.get("volunteer_name", "Volunteer"),
            "phone": meta.get("phone", ""),
            "email": meta.get("email", ""),
            "task_type": task,
            "filename": safe_name,
            "size_bytes": int(meta["length"]),
            "size_mb": round(int(meta["length"]) / (1024**2), 2),
            "sha256": checksum,
            "remote_path": remote_path,
            "download_url": f"https://huggingface.co/datasets/{TARGET_DATASET}/resolve/main/{remote_path}",
            "timestamp": _utc_now()
        }

        # Append to submissions_log.jsonl on HF
        cur_log_text = ""
        try:
            local_log = hf_hub_download(repo_id=TARGET_DATASET, repo_type="dataset", filename="submissions_log.jsonl", token=os.environ["HF_TOKEN"])
            with open(local_log, "r", encoding="utf-8") as f:
                cur_log_text = f.read()
        except Exception:
            pass

        new_log_text = cur_log_text + json.dumps(log_entry, ensure_ascii=False) + "\n"
        api.upload_file(
            path_or_fileobj=new_log_text.encode("utf-8"),
            path_in_repo="submissions_log.jsonl",
            repo_id=TARGET_DATASET,
            repo_type="dataset",
            commit_message=f"Log submission from {meta.get('volunteer_name')}"
        )

        result = {
            "status": "completed",
            "file_url": log_entry["download_url"],
            "sha256": checksum,
            "completed_at": _utc_now(),
        }
        _write_json(done_path, result)
        data_path.unlink(missing_ok=True)
        _commit_volume()
        return result

    except Exception as exc:
        _write_json(done_path, {
            "status": "failed",
            "error": str(exc)[:500],
            "failed_at": _utc_now(),
        })
        _commit_volume()
        raise


@app.function(
    image=image,
    secrets=[hub_secret],
    volumes={str(STAGING_ROOT): volume},
    timeout=10 * 60,
)
@modal.concurrent(max_inputs=64)
@modal.asgi_app()
def web_app():
    from fastapi import FastAPI, Request, Response
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import JSONResponse

    api_app = FastAPI(title="LEIN Volunteer Submission Upload API")
    api_app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["Location", "Upload-Offset", "Upload-Length", "Upload-Defer-Length", "Tus-Resumable"],
    )

    def folder_for(upload_id: str) -> Path:
        if not re.fullmatch(r"[0-9a-f-]{36}", upload_id):
            raise ValueError("Invalid upload id")
        return UPLOADS_ROOT / upload_id

    def cors_headers(extra: dict[str, str] | None = None) -> dict[str, str]:
        headers = {
            "Cache-Control": "no-store",
            "Tus-Resumable": "1.0.0",
            "Access-Control-Expose-Headers": "Location, Upload-Offset, Upload-Length, Tus-Resumable",
        }
        if extra:
            headers.update(extra)
        return headers

    @api_app.get("/health")
    async def health() -> JSONResponse:
        return JSONResponse({
            "ok": True,
            "service": APP_NAME,
            "dataset": TARGET_DATASET,
            "max_file_size": MAX_FILE_SIZE,
            "chunk_size": CHUNK_SIZE,
        })

    @api_app.post("/uploads")
    async def create_upload(request: Request) -> Response:
        try:
            length = int(request.headers.get("upload-length", "0"))
        except ValueError:
            length = 0
        if length <= 0 or length > MAX_FILE_SIZE:
            return JSONResponse({"error": "Invalid Upload-Length"}, status_code=400)

        meta = _parse_upload_metadata(request.headers.get("upload-metadata"))
        name = (meta.get("volunteer_name") or meta.get("name") or "").strip()
        filename = (meta.get("filename") or "").strip()

        if not name or not filename:
            return JSONResponse({"error": "Volunteer name and filename are required in metadata"}, status_code=400)

        safe_name = _safe_filename(filename)
        extension = Path(safe_name).suffix.lower()
        if extension not in ALLOWED_EXTENSIONS:
            return JSONResponse({"error": f"Unsupported file type: {extension}"}, status_code=400)

        upload_id = str(uuid.uuid4())
        record = {
            "upload_id": upload_id,
            "length": length,
            "volunteer_name": name[:120],
            "phone": (meta.get("phone") or "")[:50],
            "email": (meta.get("email") or "")[:120],
            "task_type": (meta.get("task_type") or "transcription")[:50],
            "filename": filename[:255],
            "safe_filename": safe_name,
            "created_at": _utc_now(),
        }

        folder = folder_for(upload_id)
        folder.mkdir(parents=True, exist_ok=True)
        _write_json(folder / "meta.json", record)
        (folder / "data.bin").touch(exist_ok=True)
        _commit_volume()

        base_url = str(request.base_url).rstrip("/")
        location = f"{base_url}/uploads/{upload_id}"
        return Response(
            status_code=201,
            headers=cors_headers({
                "Location": location,
                "Upload-Offset": "0",
                "Upload-Length": str(length),
            }),
        )

    @api_app.head("/uploads/{upload_id}")
    async def get_upload_offset(upload_id: str) -> Response:
        _reload_volume()
        folder = folder_for(upload_id)
        data_path = folder / "data.bin"
        meta_path = folder / "meta.json"
        if not folder.exists() or not meta_path.exists() or not data_path.exists():
            return JSONResponse({"error": "Upload not found"}, status_code=404)

        meta = _read_json(meta_path)
        offset = data_path.stat().st_size
        return Response(
            status_code=200,
            headers=cors_headers({
                "Upload-Offset": str(offset),
                "Upload-Length": str(meta["length"]),
            }),
        )

    @api_app.patch("/uploads/{upload_id}")
    async def append_upload_chunk(upload_id: str, request: Request) -> Response:
        _reload_volume()
        folder = folder_for(upload_id)
        data_path = folder / "data.bin"
        meta_path = folder / "meta.json"
        if not folder.exists() or not meta_path.exists() or not data_path.exists():
            return JSONResponse({"error": "Upload not found"}, status_code=404)

        meta = _read_json(meta_path)
        expected_offset = data_path.stat().st_size
        try:
            req_offset = int(request.headers.get("upload-offset", "-1"))
        except ValueError:
            req_offset = -1

        if req_offset != expected_offset:
            return Response(status_code=409, headers=cors_headers({"Upload-Offset": str(expected_offset)}))

        chunk = await request.body()
        if chunk:
            with data_path.open("ab") as handle:
                handle.write(chunk)
            _commit_volume()

        current_size = data_path.stat().st_size
        target_size = int(meta["length"])

        if current_size >= target_size:
            finalize_volunteer_upload.spawn(upload_id)

        return Response(
            status_code=204,
            headers=cors_headers({
                "Upload-Offset": str(current_size),
                "Upload-Length": str(target_size),
            }),
        )

    return api_app
