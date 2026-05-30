"""Gemini Files API upload + TTL cache.

Phase 1 only supports google provider. Other providers raise NotImplementedError.
Cache lives at .cache/llm_files_index.json with shape:
  {file_path_relative_to_project_root: {"file_id": str, "uri": str,
                                         "mime_type": str, "expires_at": iso8601,
                                         "sha256": str, "uploaded_at": iso8601}}
"""
from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Iterable

import requests

from ..app_config import GOOGLE_API_KEY, LLM_PROVIDER, REQUEST_TIMEOUT_SECONDS, _request_proxies
from ..paths import CACHE_DIR, PROJECT_ROOT, ensure_directory

FILES_INDEX_PATH = CACHE_DIR / "llm_files_index.json"
GEMINI_FILES_API_BASE = "https://generativelanguage.googleapis.com/v1beta/files"
# Resumable uploads must hit the /upload/ endpoint; the plain /v1beta/files path
# ignores the X-Goog-Upload-* headers and returns no X-Goog-Upload-URL.
GEMINI_FILES_UPLOAD_URL = "https://generativelanguage.googleapis.com/upload/v1beta/files"
TTL_SAFETY_MARGIN_HOURS = 6


def _require_google_provider() -> None:
    if LLM_PROVIDER != "google":
        raise NotImplementedError(
            f"Files API support is Google-only in Phase 1; current LLM_PROVIDER={LLM_PROVIDER!r}"
        )
    if not GOOGLE_API_KEY:
        raise RuntimeError("missing env GEMINI_API_KEY/GOOGLE_API_KEY for Files API")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _parse_iso(value: str) -> datetime:
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    return datetime.fromisoformat(value)


def _project_relative(path: Path) -> str:
    p = path.resolve()
    try:
        return str(p.relative_to(PROJECT_ROOT))
    except ValueError:
        return str(p)


def _sha256_of_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _load_index() -> dict:
    if not FILES_INDEX_PATH.exists():
        return {}
    try:
        return json.loads(FILES_INDEX_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save_index(index: dict) -> None:
    ensure_directory(CACHE_DIR)
    FILES_INDEX_PATH.write_text(
        json.dumps(index, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def _entry_is_fresh(entry: dict, file_sha: str) -> bool:
    if not entry:
        return False
    if entry.get("sha256") != file_sha:
        return False
    expires_at = entry.get("expires_at")
    if not expires_at:
        return False
    try:
        deadline = _parse_iso(expires_at) - timedelta(hours=TTL_SAFETY_MARGIN_HOURS)
    except ValueError:
        return False
    return datetime.now(timezone.utc) < deadline


def _guess_mime(path: Path) -> str:
    mime, _ = mimetypes.guess_type(str(path))
    return mime or "application/octet-stream"


def _upload_one(path: Path) -> dict:
    """Upload via Gemini resumable upload protocol, return file metadata dict."""
    _require_google_provider()
    mime = _guess_mime(path)
    size = path.stat().st_size
    display_name = path.name

    start_resp = requests.post(
        f"{GEMINI_FILES_UPLOAD_URL}?key={GOOGLE_API_KEY}",
        headers={
            "X-Goog-Upload-Protocol": "resumable",
            "X-Goog-Upload-Command": "start",
            "X-Goog-Upload-Header-Content-Length": str(size),
            "X-Goog-Upload-Header-Content-Type": mime,
            "Content-Type": "application/json",
        },
        json={"file": {"display_name": display_name}},
        timeout=REQUEST_TIMEOUT_SECONDS,
        proxies=_request_proxies(),
    )
    start_resp.raise_for_status()
    upload_url = start_resp.headers.get("X-Goog-Upload-URL") or start_resp.headers.get("x-goog-upload-url")
    if not upload_url:
        raise RuntimeError(f"upload start: missing X-Goog-Upload-URL in headers {dict(start_resp.headers)}")

    with path.open("rb") as fh:
        finalize_resp = requests.post(
            upload_url,
            headers={
                "Content-Length": str(size),
                "X-Goog-Upload-Offset": "0",
                "X-Goog-Upload-Command": "upload, finalize",
            },
            data=fh.read(),
            timeout=REQUEST_TIMEOUT_SECONDS,
            proxies=_request_proxies(),
        )
    finalize_resp.raise_for_status()
    payload = finalize_resp.json()
    file_meta = payload.get("file") or payload
    if "name" not in file_meta:
        raise RuntimeError(f"upload finalize: missing 'name' in response {payload}")
    return file_meta


def upload_files(paths: Iterable[Path], *, force: bool = False, verbose: bool = True) -> dict:
    """Upload each path if missing/expired/changed. Return {path_relative: cache_entry}."""
    _require_google_provider()
    index = _load_index()
    result = {}
    for raw_path in paths:
        path = Path(raw_path).resolve()
        if not path.is_file():
            raise FileNotFoundError(f"not a file: {path}")
        rel = _project_relative(path)
        sha = _sha256_of_file(path)
        cached = index.get(rel) or {}
        if not force and _entry_is_fresh(cached, sha):
            if verbose:
                print(f"[files_cache] reuse {rel} (file_id={cached.get('file_id')})", flush=True)
            result[rel] = cached
            continue

        if verbose:
            print(f"[files_cache] uploading {rel} ({path.stat().st_size} bytes)...", flush=True)
        meta = _upload_one(path)
        # `name` is in form "files/abc-xxx"
        name = meta.get("name", "")
        file_id = name.split("/", 1)[1] if name.startswith("files/") else name
        entry = {
            "file_id": file_id,
            "name": name,
            "uri": meta.get("uri") or "",
            "mime_type": meta.get("mimeType") or _guess_mime(path),
            "expires_at": meta.get("expirationTime") or "",
            "sha256": sha,
            "uploaded_at": _now_iso(),
            "display_name": meta.get("displayName") or path.name,
        }
        # Some Gemini responses set the file in PROCESSING state; poll briefly.
        state = meta.get("state") or ""
        if state == "PROCESSING" and name:
            for _ in range(20):
                poll = requests.get(
                    f"https://generativelanguage.googleapis.com/v1beta/{name}?key={GOOGLE_API_KEY}",
                    timeout=REQUEST_TIMEOUT_SECONDS,
                    proxies=_request_proxies(),
                )
                poll.raise_for_status()
                pj = poll.json()
                if (pj.get("state") or "") != "PROCESSING":
                    entry["uri"] = pj.get("uri") or entry["uri"]
                    entry["expires_at"] = pj.get("expirationTime") or entry["expires_at"]
                    break
                time.sleep(2)
        index[rel] = entry
        result[rel] = entry
        if verbose:
            print(f"[files_cache]   -> {file_id} (expires {entry['expires_at']})", flush=True)
    _save_index(index)
    return result


def get_active_file_ids(paths: Iterable[Path] | None = None) -> list[str]:
    """Return file IDs for the given paths (default: all cached). Skips expired entries."""
    index = _load_index()
    if paths is None:
        items = index.items()
    else:
        rels = {_project_relative(Path(p).resolve()) for p in paths}
        items = [(k, v) for k, v in index.items() if k in rels]
    out = []
    now = datetime.now(timezone.utc)
    for rel, entry in items:
        expires = entry.get("expires_at")
        if expires:
            try:
                if _parse_iso(expires) <= now:
                    continue
            except ValueError:
                pass
        fid = entry.get("file_id")
        if fid:
            out.append(fid)
    return out


def get_active_file_uris(paths: Iterable[Path] | None = None) -> list[tuple[str, str]]:
    """Return [(uri, mime_type)] for active cached files (used by call_llm)."""
    index = _load_index()
    if paths is None:
        items = list(index.items())
    else:
        rels = {_project_relative(Path(p).resolve()) for p in paths}
        items = [(k, v) for k, v in index.items() if k in rels]
    out = []
    now = datetime.now(timezone.utc)
    for _, entry in items:
        expires = entry.get("expires_at")
        if expires:
            try:
                if _parse_iso(expires) <= now:
                    continue
            except ValueError:
                pass
        uri = entry.get("uri") or ""
        mime = entry.get("mime_type") or "application/pdf"
        if uri:
            out.append((uri, mime))
    return out
