"""Check model files against the size and digest their download sources declare.

``model_files.file_present`` answers "is there a non-empty file here", which is
all it used to do. That let an interrupted download pass every check and fail
much later inside ``torch.load`` with "PytorchStreamReader failed reading zip
archive: failed finding central directory".

This module answers the question the model dialog and the download planner
actually need, at two costs:

* ``check`` only stats the file, so it is cheap enough to run on every poll and
  catches anything still being downloaded or truncated along the way;
* ``digest`` reads the file once and verifies its SHA-256, caching the result by
  size and mtime so a later check does not re-read gigabytes.

Both are driven by ``model_sources``: anything without published metadata (the
licensed SMPL-H and MANO bundles) can only be checked for existence.
"""

from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import dataclass
from pathlib import Path
from stat import S_ISREG

from . import model_files as files
from . import model_sources as sources

_CHUNK_SIZE = 1 << 22
_CACHE_PATH = files.PROJECT_ROOT / ".cache" / "model-integrity.json"
_CACHE_VERSION = 1

_lock = threading.Lock()
# Verifying reads whole files; keep two dialogs from hashing the same 10 GB twice.
_verify_lock = threading.Lock()
_assets: dict[str, sources.Asset] | None = None
_cache: dict[str, dict] | None = None
_cache_stamp: tuple[int, int] | None = None


@dataclass(frozen=True)
class Verdict:
    """Outcome of checking one file. ``state`` is "ok" when the file is usable."""

    state: str  # ok | missing | incomplete | corrupt
    relative: str
    size: int = 0
    expected: int | None = None
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.state == "ok"


def published_assets() -> dict[str, sources.Asset]:
    """Every model file with published metadata, keyed by models/-relative path."""
    global _assets
    with _lock:
        if _assets is None:
            found = {asset.relative: asset for group in sources.PUBLIC_ASSETS.values() for asset in group}
            found.update({asset.relative: asset for asset in sources.HAMER_MEMBERS.values()})
            _assets = found
        return _assets


def relative_name(path) -> str | None:
    """Path relative to the model root, or None when the file lives elsewhere."""
    try:
        return Path(path).resolve().relative_to(files.MODEL_ROOT.resolve()).as_posix()
    except (OSError, ValueError):
        return None


def check(path) -> Verdict:
    """Stat-level check: existence plus the size the source declares."""
    path = Path(path)
    relative = relative_name(path)
    asset = published_assets().get(relative) if relative else None
    expected = asset.size if asset else None
    label = relative or str(path)
    try:
        info = path.stat()
    except OSError:
        return Verdict("missing", label, 0, expected, "文件不存在")
    if not S_ISREG(info.st_mode) or info.st_size == 0:
        return Verdict("missing", label, 0, expected, "文件不存在或为空")
    if expected is not None and info.st_size != expected:
        if info.st_size < expected:
            detail = f"只有 {info.st_size} / {expected} 字节，下载未完成"
            return Verdict("incomplete", label, info.st_size, expected, detail)
        return Verdict("corrupt", label, info.st_size, expected,
                       f"文件为 {info.st_size} 字节，应为 {expected} 字节")
    entry = _cached_entry(relative) if relative else None
    if entry and asset and asset.sha256 and _entry_matches(entry, info) and entry.get("sha256") != asset.sha256:
        return Verdict("corrupt", label, info.st_size, expected, "SHA-256 与发布值不一致")
    return Verdict("ok", label, info.st_size, expected)


def digest(path) -> str:
    """SHA-256 of the file, reusing the cached value while size and mtime match."""
    path = Path(path)
    info = path.stat()
    relative = relative_name(path)
    entry = _cached_entry(relative) if relative else None
    if entry and _entry_matches(entry, info) and entry.get("sha256"):
        return entry["sha256"]
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(_CHUNK_SIZE), b""):
            hasher.update(chunk)
    result = hasher.hexdigest()
    if relative:
        _store_entry(relative, info, result)
    return result


def verify(deep: bool = False) -> list[Verdict]:
    """Check every published file; with ``deep`` also compare SHA-256 digests."""
    with _verify_lock:
        verdicts = []
        for relative, asset in sorted(published_assets().items()):
            path = files.resolve_model(relative)
            verdict = check(path)
            if verdict.ok and deep and asset.sha256:
                if digest(path) != asset.sha256:
                    verdict = Verdict("corrupt", relative, verdict.size, asset.size,
                                      "SHA-256 与发布值不一致")
            verdicts.append(verdict)
        return verdicts


def summary(deep: bool = False) -> dict:
    """Small JSON-friendly report for the UI."""
    verdicts = verify(deep)
    return {
        "deep": deep,
        "checked": len(verdicts),
        "ok": sum(1 for verdict in verdicts if verdict.ok),
        "problems": [{"file": verdict.relative, "state": verdict.state, "detail": verdict.detail}
                     for verdict in verdicts if not verdict.ok],
    }


# ----------------------------------------------------------------- cache


def _cache_stamp_now() -> tuple[int, int] | None:
    try:
        info = _CACHE_PATH.stat()
        return info.st_mtime_ns, info.st_size
    except OSError:
        return None


def _load_cache() -> dict[str, dict]:
    global _cache, _cache_stamp
    stamp = _cache_stamp_now()
    with _lock:
        if _cache is not None and stamp == _cache_stamp:
            return _cache
        loaded: dict[str, dict] = {}
        if stamp is not None:
            try:
                raw = json.loads(_CACHE_PATH.read_text("utf-8"))
            except (OSError, ValueError):
                raw = {}
            if raw.get("version") == _CACHE_VERSION:
                loaded = {name: value for name, value in (raw.get("files") or {}).items()
                          if isinstance(value, dict)}
        _cache, _cache_stamp = loaded, stamp
        return _cache


def _cached_entry(relative) -> dict | None:
    return _load_cache().get(relative) if relative else None


def _entry_matches(entry, info) -> bool:
    return entry.get("size") == info.st_size and entry.get("mtime_ns") == info.st_mtime_ns


def _store_entry(relative, info, sha256) -> None:
    """Record a digest, writing through a temporary file so a crash cannot truncate it."""
    global _cache, _cache_stamp
    with _lock:
        entries = dict(_cache or {})
        entries[relative] = {"size": info.st_size, "mtime_ns": info.st_mtime_ns, "sha256": sha256}
        payload = {"version": _CACHE_VERSION, "files": entries}
        try:
            _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
            temporary = _CACHE_PATH.with_suffix(".json.tmp")
            temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=1), "utf-8")
            temporary.replace(_CACHE_PATH)
        except OSError:
            return  # a read-only cache directory only costs us the reuse
        _cache = entries
        _cache_stamp = _cache_stamp_now()
