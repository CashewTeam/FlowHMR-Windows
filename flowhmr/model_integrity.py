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
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from stat import S_ISREG

from . import model_files as files
from . import model_sources as sources

_CHUNK_SIZE = 1 << 22
_CACHE_PATH = files.PROJECT_ROOT / ".cache" / "model-integrity.json"
_CACHE_VERSION = 2

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
    detail_code: str | None = None

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
    """Key for an installed model path. Windows paths are case insensitive."""
    try:
        relative = Path(os.path.abspath(path)).relative_to(files.MODEL_ROOT.absolute()).as_posix()
        return relative.casefold() if os.name == "nt" else relative
    except (OSError, ValueError):
        return None


def pinned_asset(relative: str | None) -> sources.Asset | None:
    # The FlowHMR YAML is also the default user-editable config. Its downloaded
    # copy is still verified before installation, but edits are not corruption.
    if relative is None or relative == files.FLOWHMR_CONFIG:
        return None
    assets = published_assets()
    return assets.get(relative) or (next((asset for name, asset in assets.items()
                                         if name.casefold() == relative), None) if os.name == "nt" else None)


def check(path) -> Verdict:
    """Stat-level check: existence plus the size the source declares."""
    path = Path(path)
    relative = relative_name(path)
    asset = pinned_asset(relative)
    expected = asset.size if asset else None
    label = asset.relative if asset else (relative or str(path))
    try:
        info = path.stat()
    except (FileNotFoundError, NotADirectoryError):
        return Verdict("missing", label, 0, expected, "文件不存在", "file_missing")
    except OSError:
        return Verdict("corrupt", label, 0, expected, "无法读取文件", "file_unreadable")
    if not S_ISREG(info.st_mode) or info.st_size == 0:
        return Verdict("missing", label, 0, expected, "文件不存在或为空", "file_missing")
    if expected is not None and info.st_size != expected:
        if info.st_size < expected:
            detail = f"只有 {info.st_size} / {expected} 字节，下载未完成"
            return Verdict("incomplete", label, info.st_size, expected, detail, "file_incomplete")
        return Verdict("corrupt", label, info.st_size, expected,
                       f"文件为 {info.st_size} 字节，应为 {expected} 字节", "file_wrong_size")
    entry = _cached_entry(relative) if asset and asset.sha256 else None
    if entry and asset and asset.sha256 and _entry_matches(entry, info) and entry.get("sha256") != asset.sha256:
        return Verdict("corrupt", label, info.st_size, expected, "SHA-256 与发布值不一致", "file_digest_mismatch")
    return Verdict("ok", label, info.st_size, expected)


def digest(path) -> str:
    """SHA-256 of the file, reusing the cached value while size and mtime match."""
    path = Path(path)
    info = path.stat()
    relative = relative_name(path)
    entry = _cached_entry(relative) if pinned_asset(relative) else None
    if entry and _entry_matches(entry, info) and entry.get("sha256"):
        return entry["sha256"]
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(_CHUNK_SIZE), b""):
            hasher.update(chunk)
    if _fingerprint(info) != _fingerprint(path.stat()):
        raise OSError("Model file changed during verification")
    result = hasher.hexdigest()
    if pinned_asset(relative):
        _store_entry(relative, info, result)
    return result


def verify(deep: bool = False, *, required_paths=None) -> list[Verdict]:
    """Check active required models and any installed optional model files."""
    required = tuple(required_paths if required_paths is not None else files.core_model_paths())
    optional = (*files.HAMER_FILES, *files.MANO_FILES, files.SMPLH_FILES["male"], files.SMPLH_FILES["female"])
    paths = dict.fromkeys(Path(path) for path in required)
    for relative in optional:
        path = files.resolve_model(relative)
        # Absent optional packs must not make verification fail.
        try:
            present = path.exists()
        except OSError:
            present = True  # Show the read error.
        if present:
            paths[path] = None
    with _verify_lock:
        verdicts = []
        for path in paths:
            verdict = check(path)
            asset = pinned_asset(relative_name(path))
            if verdict.ok and deep and asset and asset.sha256:
                try:
                    if digest(path) != asset.sha256:
                        verdict = Verdict("corrupt", verdict.relative, verdict.size, asset.size,
                                          "SHA-256 与发布值不一致", "file_digest_mismatch")
                except OSError:
                    verdict = Verdict("corrupt", verdict.relative, verdict.size, asset.size,
                                      "无法读取文件或文件在校验时发生变化", "file_unreadable")
            verdicts.append(verdict)
        return verdicts


def issue(verdict: Verdict) -> dict:
    return {"file": verdict.relative, "state": verdict.state, "detail": verdict.detail,
            "detail_code": verdict.detail_code, "size": verdict.size, "expected": verdict.expected}


def summary(deep: bool = False, *, required_paths=None) -> dict:
    """Small JSON-friendly report for the UI."""
    verdicts = verify(deep, required_paths=required_paths)
    return {
        "deep": deep,
        "checked": len(verdicts),
        "ok": sum(1 for verdict in verdicts if verdict.ok),
        "problems": [issue(verdict) for verdict in verdicts if not verdict.ok],
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
            if isinstance(raw, dict) and raw.get("version") == _CACHE_VERSION:
                entries = raw.get("files")
                if isinstance(entries, dict):
                    loaded = {name: value for name, value in entries.items()
                              if isinstance(value, dict) and isinstance(value.get("sha256"), str)}
        _cache, _cache_stamp = loaded, stamp
        return _cache


def _cached_entry(relative) -> dict | None:
    return _load_cache().get(relative) if relative else None


def _fingerprint(info) -> list[int]:
    return [info.st_size, info.st_mtime_ns, info.st_ctime_ns, info.st_dev, info.st_ino]


def _entry_matches(entry, info) -> bool:
    return entry.get("fingerprint") == _fingerprint(info)


def _store_entry(relative, info, sha256) -> None:
    """Record a digest, writing through a temporary file so a crash cannot truncate it."""
    global _cache, _cache_stamp
    with _lock:
        entries = dict(_cache or {})
        entries[relative] = {"fingerprint": _fingerprint(info), "sha256": sha256}
        payload = {"version": _CACHE_VERSION, "files": entries}
        try:
            _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
            temporary = _CACHE_PATH.with_suffix(f".{os.getpid()}.tmp")
            temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=1), "utf-8")
            temporary.replace(_CACHE_PATH)
        except OSError:
            return  # a read-only cache directory only costs us the reuse
        _cache = entries
        _cache_stamp = _cache_stamp_now()
