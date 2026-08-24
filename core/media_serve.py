"""Authenticated media serving with HTTP Range / conditional support."""

from __future__ import annotations

import mimetypes
import os
import re
from pathlib import Path
from typing import Iterable

from flask import Request, Response, abort
from werkzeug.http import http_date, parse_date

_RANGE_RE = re.compile(r"bytes=(\d*)-(\d*)")


class MediaPathError(ValueError):
    """Path is outside approved roots or does not exist."""


def resolve_under_roots(
    path: str | Path,
    roots: Iterable[str | Path],
    *,
    must_exist: bool = True,
    allow_directory: bool = False,
) -> Path:
    """Resolve ``path`` and ensure it lies under one of ``roots`` (no traversal)."""
    target = Path(path).resolve()
    if must_exist:
        if allow_directory:
            if not target.exists():
                raise MediaPathError("Path not found")
        elif not target.is_file():
            raise MediaPathError("File not found")
    approved = [Path(r).resolve() for r in roots]
    for root in approved:
        try:
            target.relative_to(root)
            return target
        except ValueError:
            continue
    raise MediaPathError("Path outside approved media roots")


def safe_media_response(
    path: str | Path,
    *,
    roots: Iterable[str | Path],
    request: Request,
    download_name: str | None = None,
    as_attachment: bool = False,
    mimetype: str | None = None,
) -> Response:
    """Serve a file with Range and If-Modified-Since / If-None-Match support."""
    try:
        file_path = resolve_under_roots(path, roots)
    except MediaPathError:
        abort(404)

    file_size = file_path.stat().st_size
    mtime = file_path.stat().st_mtime
    etag = f'W/"{file_size}-{int(mtime)}"'
    mime = mimetype or mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"

    # Conditional GET
    if_none_match = request.headers.get("If-None-Match")
    if if_none_match and if_none_match.strip() == etag:
        return Response(status=304)
    if_modified = request.headers.get("If-Modified-Since")
    if if_modified:
        since = parse_date(if_modified)
        if since is not None and int(mtime) <= int(since.timestamp()):
            return Response(status=304)

    range_header = request.headers.get("Range")
    start = 0
    end = file_size - 1
    status = 200

    if range_header:
        match = _RANGE_RE.match(range_header.strip())
        if not match:
            return Response(status=416, headers={"Content-Range": f"bytes */{file_size}"})
        start_s, end_s = match.group(1), match.group(2)
        try:
            if start_s == "" and end_s == "":
                return Response(status=416, headers={"Content-Range": f"bytes */{file_size}"})
            if start_s == "":
                # suffix bytes: last N bytes
                length = int(end_s)
                start = max(file_size - length, 0)
                end = file_size - 1
            else:
                start = int(start_s)
                end = int(end_s) if end_s else file_size - 1
        except ValueError:
            return Response(status=416, headers={"Content-Range": f"bytes */{file_size}"})
        if start >= file_size or start < 0 or end < start:
            return Response(status=416, headers={"Content-Range": f"bytes */{file_size}"})
        end = min(end, file_size - 1)
        status = 206

    length = end - start + 1

    def generate():
        with open(file_path, "rb") as fh:
            fh.seek(start)
            remaining = length
            chunk = 1024 * 256
            while remaining > 0:
                data = fh.read(min(chunk, remaining))
                if not data:
                    break
                remaining -= len(data)
                yield data

    headers = {
        "Accept-Ranges": "bytes",
        "ETag": etag,
        "Last-Modified": http_date(mtime),
        "Cache-Control": "private, max-age=0, must-revalidate",
        "Content-Length": str(length),
    }
    if status == 206:
        headers["Content-Range"] = f"bytes {start}-{end}/{file_size}"

    disposition = "attachment" if as_attachment else "inline"
    name = download_name or file_path.name
    headers["Content-Disposition"] = f'{disposition}; filename="{name}"'

    return Response(generate(), status=status, mimetype=mime, headers=headers, direct_passthrough=True)
