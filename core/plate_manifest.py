"""Private, versioned, linked storage for experimental machine plate attempts.

Storage layout (all under the private evidence root, never served as static
files)::

    <EVIDENCE_FOLDER>/plate_ocr/
        attempts/<attempt_id>.json     immutable machine attempt manifests
        index/<review_key>.json        atomic pointer to the current attempt
        crops/<attempt_id>/*.jpg|png   server-generated crops, content-hashed

Rules enforced here
-------------------
* **Server-generated paths only.** Every path component is validated and the
  resolved path must stay under :func:`plate_root` (containment check).
* **Immutable attempts.** An attempt file is written exactly once with
  ``O_EXCL``. A retry writes a *new* attempt id; it never edits history.
* **Atomic writes.** Manifests and index pointers are written to a temporary
  file in the same directory and then ``os.replace``d, so a crash leaves either
  the old complete file or the new complete file, never a truncated one.
* **Bounded size.** A manifest larger than
  :data:`core.plate_settings.MAX_MANIFEST_BYTES` is refused.
* **No database writes.** Nothing in this module touches SQLite. TAVIDM stays
  authoritative for cases, human verification, and decisions; these files only
  hold machine attempts.

Missing, corrupt, and deleted evidence are surfaced as explicit statuses. No
source footage and no unrelated file is ever deleted by this module.
"""

from __future__ import annotations

import json
import os
import re
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from config import EVIDENCE_FOLDER
from core.plate_settings import CONTRACT_VERSION, MAX_MANIFEST_BYTES

_SAFE_COMPONENT = re.compile(r"^[A-Za-z0-9._-]{1,96}$")

#: Relative sub-root used for every plate-OCR artifact.
PLATE_SUBDIR = "plate_ocr"

#: Optional explicit override for the plate-OCR tree. The experimental demo
#: uses it to keep machine crops in a *separate* private directory from the
#: violation evidence tree, and tests use it to point at a temporary directory
#: without touching the canonical evidence root.
ROOT_OVERRIDE_ENV_VAR = "TAVIDM_PLATE_OCR_EVIDENCE_ROOT"

_WRITE_LOCK = threading.Lock()

MANIFEST_STATUS_OK = "ok"
MANIFEST_STATUS_MISSING = "missing"
MANIFEST_STATUS_CORRUPT = "corrupt"
MANIFEST_STATUS_UNSAFE_ID = "unsafe_identifier"
MANIFEST_STATUS_OVERSIZED = "oversized"


class PlateManifestError(RuntimeError):
    """Manifest storage refused an operation."""


@dataclass(frozen=True)
class LoadedManifest:
    """Result of reading one attempt manifest.

    ``status`` is one of ``ok`` / ``missing`` / ``corrupt`` / ``unsafe_identifier``
    / ``oversized``. Callers must handle a non-``ok`` status explicitly rather
    than treating it as "no machine result".
    """

    status: str
    attempt_id: str
    path: Path | None = None
    data: dict[str, Any] | None = None
    detail: str | None = None

    @property
    def ok(self) -> bool:
        return self.status == MANIFEST_STATUS_OK and self.data is not None


# ----------------------------------------------------------------------
# Paths
# ----------------------------------------------------------------------


def plate_root() -> Path:
    """Root of the private plate-OCR tree."""
    override = os.environ.get(ROOT_OVERRIDE_ENV_VAR, "").strip()
    if override:
        return Path(override)
    return Path(EVIDENCE_FOLDER) / PLATE_SUBDIR


def _safe_component(value: str, *, label: str) -> str:
    text = str(value or "").strip()
    if not _SAFE_COMPONENT.match(text) or text in (".", ".."):
        raise PlateManifestError(f"unsafe plate {label} rejected")
    return text


def _contained(root: Path, *parts: str) -> Path:
    candidate = root.joinpath(*parts)
    resolved_root = root.resolve()
    try:
        resolved = candidate.resolve()
    except OSError as exc:  # pragma: no cover - defensive
        raise PlateManifestError(f"plate path unresolvable: {label_of(parts)}") from exc
    if resolved != resolved_root and resolved_root not in resolved.parents:
        raise PlateManifestError(f"plate path escapes private root: {label_of(parts)}")
    return candidate


def label_of(parts: tuple[str, ...]) -> str:
    return "/".join(parts)


def attempts_dir() -> Path:
    return plate_root() / "attempts"


def crops_dir() -> Path:
    return plate_root() / "crops"


def index_dir() -> Path:
    return plate_root() / "index"


def review_key(review_id: int | None) -> str:
    """Server-derived, path-safe index key for one review observation."""
    if review_id is None:
        return "review_none"
    return f"review_{int(review_id)}"


def new_attempt_id() -> str:
    return f"att_{uuid.uuid4().hex}"


def attempt_manifest_path(attempt_id: str) -> Path:
    safe = _safe_component(attempt_id, label="attempt id")
    return _contained(attempts_dir(), f"{safe}.json")


def index_path(key: str) -> Path:
    safe = _safe_component(key, label="index key")
    return _contained(index_dir(), f"{safe}.json")


def relative_to_base(path: Path) -> str:
    """Project-relative stored path (mirrors ``core.evidence`` conventions)."""
    base = Path(__file__).resolve().parents[1]
    try:
        return str(path.resolve().relative_to(base)).replace("\\", "/")
    except ValueError:
        return str(path.resolve()).replace("\\", "/")


def stored_plate_path(path: Path) -> str:
    """Stored path for a plate artifact, relative to the evidence root when possible."""
    for base in (plate_root(), Path(EVIDENCE_FOLDER)):
        try:
            return str(path.resolve().relative_to(Path(base).resolve())).replace("\\", "/")
        except ValueError:
            continue
    return str(path.resolve()).replace("\\", "/")


# ----------------------------------------------------------------------
# Atomic / immutable writes
# ----------------------------------------------------------------------


def _atomic_write(path: Path, payload: bytes, *, exclusive: bool) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / f".{path.name}.{uuid.uuid4().hex}.tmp"
    try:
        if exclusive:
            # O_EXCL on the temporary file plus an explicit rename keeps the
            # "create once" guarantee even on Windows, where os.replace would
            # otherwise silently overwrite an existing attempt.
            fd = os.open(str(tmp), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            try:
                with os.fdopen(fd, "wb") as handle:
                    handle.write(payload)
            except BaseException:
                raise
        else:
            tmp.write_bytes(payload)
        if exclusive and path.exists():
            # Immutable attempts are created exactly once. The index is not an
            # attempt and may be replaced.
            raise PlateManifestError(f"immutable attempt already exists: {path.name}")
        os.replace(str(tmp), str(path))
        return path
    except FileExistsError as exc:
        raise PlateManifestError(f"immutable attempt already exists: {path.name}") from exc
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:  # pragma: no cover - best effort
                pass


def write_manifest(manifest: dict[str, Any]) -> Path:
    """Write one immutable attempt manifest and return its absolute path."""
    if not isinstance(manifest, dict):
        raise PlateManifestError("manifest must be an object")
    version = manifest.get("contract_version")
    if version != CONTRACT_VERSION:
        raise PlateManifestError(f"unsupported manifest contract_version: {version!r}")
    attempt_id = manifest.get("attempt_id")
    path = attempt_manifest_path(str(attempt_id))
    payload = json.dumps(manifest, sort_keys=True, default=str).encode("utf-8")
    if len(payload) > MAX_MANIFEST_BYTES:
        raise PlateManifestError(
            f"manifest exceeds {MAX_MANIFEST_BYTES} bytes ({len(payload)}); refusing to write"
        )
    with _WRITE_LOCK:
        return _atomic_write(path, payload, exclusive=True)


def load_manifest(attempt_id: str) -> LoadedManifest:
    """Read one attempt manifest with explicit failure statuses."""
    text = str(attempt_id or "").strip()
    try:
        path = attempt_manifest_path(text)
    except PlateManifestError:
        return LoadedManifest(MANIFEST_STATUS_UNSAFE_ID, text)
    if not path.is_file():
        return LoadedManifest(MANIFEST_STATUS_MISSING, text, path=path)
    try:
        size = path.stat().st_size
    except OSError:
        return LoadedManifest(MANIFEST_STATUS_MISSING, text, path=path)
    if size > MAX_MANIFEST_BYTES:
        return LoadedManifest(MANIFEST_STATUS_OVERSIZED, text, path=path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return LoadedManifest(MANIFEST_STATUS_CORRUPT, text, path=path, detail="unreadable_json")
    if not isinstance(data, dict) or data.get("contract_version") != CONTRACT_VERSION:
        return LoadedManifest(MANIFEST_STATUS_CORRUPT, text, path=path, detail="contract_mismatch")
    return LoadedManifest(MANIFEST_STATUS_OK, text, path=path, data=data)


# ----------------------------------------------------------------------
# Index (server-derived review -> current attempt)
# ----------------------------------------------------------------------


def write_index(key: str, entry: dict[str, Any]) -> Path:
    """Atomically point ``key`` at an attempt. The index may be replaced."""
    path = index_path(key)
    payload = json.dumps(entry, sort_keys=True, default=str).encode("utf-8")
    if len(payload) > 64 * 1024:
        raise PlateManifestError("index entry too large")
    with _WRITE_LOCK:
        return _atomic_write(path, payload, exclusive=False)


def read_index(key: str) -> dict[str, Any] | None:
    try:
        path = index_path(key)
    except PlateManifestError:
        return None
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    return data if isinstance(data, dict) else None


def clear_index(key: str) -> bool:
    """Forget the current attempt for a key (history on disk is untouched)."""
    try:
        path = index_path(key)
    except PlateManifestError:
        return False
    if not path.is_file():
        return False
    try:
        path.unlink()
        return True
    except OSError:
        return False


def list_attempt_ids() -> list[str]:
    directory = attempts_dir()
    if not directory.is_dir():
        return []
    out: list[str] = []
    for entry in sorted(directory.iterdir()):
        if entry.is_file() and entry.suffix == ".json" and not entry.name.startswith("."):
            out.append(entry.stem)
    return out


def attempts_for_review(review_id: int | None) -> list[LoadedManifest]:
    """Attempts linked to a review observation, newest index entry first.

    The link is server-derived (the index is written by the collector) and the
    scan fallback keeps orphaned attempts reachable after a crash between the
    manifest write and the index write.
    """
    found: list[LoadedManifest] = []
    seen: set[str] = set()

    entry = read_index(review_key(review_id))
    if entry and entry.get("attempt_id"):
        loaded = load_manifest(str(entry["attempt_id"]))
        if loaded.ok:
            found.append(loaded)
            seen.add(loaded.attempt_id)

    directory = attempts_dir()
    if directory.is_dir():
        for attempt_id in list_attempt_ids():
            if attempt_id in seen:
                continue
            loaded = load_manifest(attempt_id)
            if not loaded.ok or loaded.data is None:
                continue
            if loaded.data.get("review_id") == review_id:
                found.append(loaded)
                seen.add(attempt_id)
    return found


def attempts_for_review_ids(review_ids: list[int | None]) -> list[LoadedManifest]:
    out: list[LoadedManifest] = []
    seen: set[str] = set()
    for review_id in review_ids:
        for loaded in attempts_for_review(review_id):
            if loaded.attempt_id not in seen:
                seen.add(loaded.attempt_id)
                out.append(loaded)
    return out


# ----------------------------------------------------------------------
# Crops
# ----------------------------------------------------------------------


def save_crop(
    attempt_id: str,
    name: str,
    image: Any,
    *,
    params: list[int] | None = None,
) -> dict[str, Any]:
    """Write one server-named crop and return its reference + content hash."""
    import cv2

    safe_attempt = _safe_component(attempt_id, label="attempt id")
    safe_name = _safe_component(name, label="crop name")
    directory = _contained(crops_dir(), safe_attempt)
    directory.mkdir(parents=True, exist_ok=True)
    path = _contained(directory, safe_name)
    ok = cv2.imwrite(str(path), image, params or [])
    if not ok or not path.is_file():
        raise PlateManifestError(f"crop write failed: {safe_name}")
    data = path.read_bytes()
    import hashlib

    return {
        "name": safe_name,
        "stored_path": stored_plate_path(path),
        "sha256": hashlib.sha256(data).hexdigest(),
        "size_bytes": len(data),
        "shape": [int(v) for v in getattr(image, "shape", []) or []],
    }


def crop_path(attempt_id: str, name: str) -> Path | None:
    """Resolve a crop reference recorded in a manifest, with containment check.

    Returns ``None`` when the reference is missing, unsafe, or the file has
    been deleted. Callers turn ``None`` into an explicit "evidence unavailable"
    response rather than a 500.
    """
    try:
        safe_attempt = _safe_component(attempt_id, label="attempt id")
        safe_name = _safe_component(name, label="crop name")
        path = _contained(_contained(crops_dir(), safe_attempt), safe_name)
    except PlateManifestError:
        return None
    if not path.is_file():
        return None
    return path


def resolve_crop_ref(stored_ref: str | None) -> Path | None:
    """Resolve a stored crop reference from a manifest.

    ``stored_plate_path`` may be relative to the plate root or to the evidence
    root, so both are tried. Absolute references, Windows drive letters, and
    anything that escapes the plate root are refused.
    """
    if not stored_ref:
        return None
    text = str(stored_ref).replace("\\", "/").strip()
    if not text or text.startswith("/") or ":" in text:
        return None
    root = plate_root().resolve()
    for base in (plate_root(), Path(EVIDENCE_FOLDER)):
        candidate = Path(base) / text
        try:
            resolved = candidate.resolve()
        except OSError:
            continue
        if resolved == root or root not in resolved.parents:
            continue
        return resolved if resolved.is_file() else None
    return None


# ----------------------------------------------------------------------
# Recovery
# ----------------------------------------------------------------------


def sweep_partial_writes() -> dict[str, int]:
    """Remove orphaned ``*.tmp`` files from an interrupted write.

    Only this module's own temporary files are touched, and only inside the
    plate tree. No manifest, crop, footage, or unrelated file is deleted.
    """
    removed = 0
    for directory in (attempts_dir(), index_dir(), crops_dir()):
        if not directory.is_dir():
            continue
        for entry in directory.rglob(".*.tmp"):
            try:
                if entry.is_file():
                    entry.unlink()
                    removed += 1
            except OSError:  # pragma: no cover - best effort
                continue
    return {"removed_tmp_files": removed}


def orphan_report() -> dict[str, Any]:
    """Describe attempts whose evidence has been deleted or that are unreadable.

    Read-only. Used by the demo/health surface so a human can see machine
    attempts whose crops no longer exist without any automatic deletion.
    """
    total = 0
    unreadable = 0
    missing_crops = 0
    orphaned_index = 0
    for attempt_id in list_attempt_ids():
        total += 1
        loaded = load_manifest(attempt_id)
        if not loaded.ok or loaded.data is None:
            unreadable += 1
            continue
        for crop in loaded.data.get("crops") or []:
            if not isinstance(crop, dict):
                continue
            if resolve_crop_ref(crop.get("stored_path")) is None:
                missing_crops += 1
                break
    if index_dir().is_dir():
        for entry in index_dir().iterdir():
            if not entry.is_file():
                continue
            data = read_index(entry.stem)
            if data is None or not data.get("attempt_id"):
                orphaned_index += 1
                continue
            if load_manifest(str(data.get("attempt_id"))).status != MANIFEST_STATUS_OK:
                orphaned_index += 1
    return {
        "attempts": total,
        "unreadable_attempts": unreadable,
        "attempts_with_missing_crops": missing_crops,
        "orphaned_index_entries": orphaned_index,
    }