"""Import known model files from extracted local folders without loading models."""

from contextlib import contextmanager
from dataclasses import dataclass
import errno
import hashlib
import logging
from math import prod
import os
from pathlib import Path
import shutil
import stat
from threading import Lock, Thread
from zipfile import BadZipFile, ZipFile

from . import model_files as files, model_integrity, model_sources as sources


_CHUNK = 4 << 20
_MAX_DEPTH = 6
_MAX_ENTRIES = 4096
_MANO_MODELS = (*files.MANO_FILES, files.SMPLH_FILES["female"], files.SMPLH_FILES["male"])
_NEUTRAL = files.SMPLH_FILES["neutral"]
_GROUPS = {
    **{key: tuple(asset.relative for asset in assets) for key, assets in sources.PUBLIC_ASSETS.items()},
    "smplh": (_NEUTRAL,),
    "mano": _MANO_MODELS,
    "hamer": files.HAMER_FILES,
}
_ASSETS = {name: asset for name, asset in model_integrity.published_assets().items()
           if name != files.FLOWHMR_CONFIG}
_HAMER_LAYOUTS = {asset.relative: name.casefold() for name, asset in sources.HAMER_MEMBERS.items()}
_MODEL_NAMES = {Path(relative).name.casefold() for group in _GROUPS.values() for relative in group}
_SMPLH_SHAPES = {
    "f.npy": (13776, 3), "J_regressor.npy": (52, 6890), "kintree_table.npy": (2, 52),
    "posedirs.npy": (6890, 3, 459), "shapedirs.npy": (6890, 3, 16),
    "v_template.npy": (6890, 3), "weights.npy": (6890, 52),
}


class ModelImportError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class _Candidate:
    path: Path
    info: os.stat_result
    priority: int


def _reparse(info):
    return (stat.S_ISLNK(info.st_mode)
            or bool(getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT))


def _check_directories(path, *, destination=False):
    """Check the whole chain without resolving links, including Windows junctions."""
    for parent in (*reversed(path.parents), path):
        try:
            info = parent.lstat()
        except FileNotFoundError:
            if not destination or parent == parent.parent:
                raise
            # Avoid Path.mkdir(exist_ok=True), which may follow a junction.
            try:
                parent.mkdir()
            except FileExistsError:
                pass
            info = parent.lstat()
        if _reparse(info) or (destination and not stat.S_ISDIR(info.st_mode)):
            raise ModelImportError("unsafe_destination" if destination else "unsafe_source")
        if not stat.S_ISDIR(info.st_mode):
            raise ModelImportError("invalid_source")


def _source_path(value):
    if not isinstance(value, str) or not value or "\0" in value:
        raise ModelImportError("invalid_source")
    path = Path(value)
    if not path.is_absolute() or path == Path(path.anchor) or ".." in path.parts:
        raise ModelImportError("invalid_source")
    if os.name == "nt" and (len(path.drive) != 2 or path.drive[1] != ":"):
        # Never connect to an SMB share or a Win32 device on behalf of the local UI.
        raise ModelImportError("unsafe_source")
    return path


def _scan(source):
    """Index only known basenames under a bounded subtree."""
    _check_directories(source)
    stack = [(source, 0)]
    found = {}
    visited = 0
    while stack:
        directory, depth = stack.pop()
        try:
            if _reparse(directory.lstat()):
                continue
            with os.scandir(directory) as entries:
                for entry in entries:
                    visited += 1
                    if visited > _MAX_ENTRIES:
                        raise ModelImportError("scan_limit")
                    name = entry.name.casefold()
                    if name.startswith(".") or name in {"__macosx", "__pycache__"}:
                        continue
                    path = directory / entry.name
                    try:
                        info = entry.stat(follow_symlinks=False)
                    except FileNotFoundError:
                        continue
                    if stat.S_ISDIR(info.st_mode) and not _reparse(info) and depth < _MAX_DEPTH:
                        stack.append((path, depth + 1))
                    elif name in _MODEL_NAMES and (stat.S_ISREG(info.st_mode) or _reparse(info)):
                        try:
                            # Windows DirEntry.stat() does not include an inode for fstat comparison.
                            info = path.lstat()
                        except FileNotFoundError:
                            continue
                        found.setdefault(name, []).append((path, info))
        except PermissionError:
            if directory == source:
                raise ModelImportError("source_unreadable") from None
        except FileNotFoundError:
            if directory == source:
                raise
    return found


def _matches_suffix(path, relative):
    tail = tuple(part.casefold() for part in Path(relative).parts)
    return tuple(part.casefold() for part in path.parts[-len(tail):]) == tail


def _priority(relative, path, info, known_paths):
    if _matches_suffix(path, relative):
        return 3
    if relative in _HAMER_LAYOUTS and _matches_suffix(path, _HAMER_LAYOUTS[relative]):
        return 3
    if relative == _NEUTRAL:
        return 2 if path.parent.name.casefold() == "neutral" else 0
    if relative == files.FLOWHMR_CONFIG:
        return 2 if path.parent / Path(files.FLOWHMR_CKPT).name in known_paths else 0
    if relative == files.SAM3D_FILES[0]:
        if path.parent / "assets" / Path(files.SAM3D_FILES[2]).name in known_paths:
            return 2
    if relative == files.SAM3D_FILES[1]:
        if (path.parent / "assets" / Path(files.SAM3D_FILES[2]).name in known_paths
                or path.parent / Path(files.SAM3D_FILES[0]).name in known_paths
                and known_paths[path.parent / Path(files.SAM3D_FILES[0]).name].st_size
                == _ASSETS[files.SAM3D_FILES[0]].size):
            return 2
    if relative == files.HAMER_FILES[1] and (
            path.parent / "checkpoints/hamer.ckpt" in known_paths or path.parent / "hamer.ckpt" in known_paths):
        return 2
    if path.name.casefold() not in {"model.ckpt", "model_config.yaml"}:
        return 1  # Unambiguous filename, checked before installation.
    asset = _ASSETS.get(relative)
    return 1 if asset and info.st_size == asset.size else 0


def _discover(source):
    found = _scan(source)
    known_paths = {path: info for matches in found.values() for path, info in matches}
    matches = {}
    for relatives in _GROUPS.values():
        for relative in relatives:
            name = Path(relative).name.casefold()
            candidates = []
            for path, info in found.get(name, ()):
                priority = _priority(relative, path, info, known_paths)
                if priority:
                    candidates.append(_Candidate(path, info, priority))
            if candidates:
                best = max(candidate.priority for candidate in candidates)
                matches[relative] = [candidate for candidate in candidates if candidate.priority == best]
    return matches


def _validate_neutral(path, *, deep):
    # Inspect NPY headers without allocating arrays or deserializing object data.
    from numpy.lib import format as npy

    try:
        with ZipFile(path) as archive:
            members = archive.infolist()
            if (len(members) > 32 or len({member.filename for member in members}) != len(members)
                    or sum(member.file_size for member in members) > (256 << 20)
                    or not _SMPLH_SHAPES.keys() <= set(archive.namelist())):
                raise ModelImportError("invalid_format")
            for member in members:
                with archive.open(member) as stream:
                    if npy.read_magic(stream) != (1, 0):
                        raise ModelImportError("invalid_format")
                    shape, _, dtype = npy.read_array_header_1_0(stream)
                    if (dtype.hasobject or stream.tell() + prod(shape) * dtype.itemsize != member.file_size
                            or member.filename in _SMPLH_SHAPES and (
                                shape != _SMPLH_SHAPES[member.filename] or dtype.kind not in "iuf")):
                        raise ModelImportError("invalid_format")
            if deep and archive.testzip() is not None:
                raise ModelImportError("invalid_format")
    except (BadZipFile, EOFError, ValueError, SyntaxError, RuntimeError, NotImplementedError):
        raise ModelImportError("invalid_format") from None


def _validate_config(path):
    import yaml

    # This YAML is editable. Identify a FlowHMR config by its structure instead of requiring
    # the exact publisher bytes, and never import the modules it names during installation.
    try:
        with path.open("rb") as stream:
            value = yaml.safe_load(stream.read(64 << 10))
    except (yaml.YAMLError, UnicodeError, RecursionError):
        raise ModelImportError("invalid_format") from None
    if (not isinstance(value, dict)
            or value.get("network_module") != "flowhmr/network/hymotion_mmdit_for_v2m.HunyuanMotionMMDiT"
            or value.get("train_pipeline", "flowhmr/pipeline/pipeline_v2m_simple.SimpleV2MPipeline") not in (
                "flowhmr/pipeline/pipeline_v2m.V2MPipeline", "flowhmr/pipeline/pipeline_v2m_simple.SimpleV2MPipeline")
            or not all(isinstance(value.get(key), dict) for key in ("train_pipeline_args", "network_module_args"))):
        raise ModelImportError("invalid_format")


def _validate_size(relative, info):
    asset = _ASSETS.get(relative)
    if asset:
        if info.st_size != asset.size:
            raise ModelImportError("invalid_size")
    elif relative in _MANO_MODELS or relative == _NEUTRAL:
        if not (1 << 20) <= info.st_size <= (256 << 20):
            raise ModelImportError("invalid_size")
    elif relative == files.FLOWHMR_CONFIG and not 0 < info.st_size <= (64 << 10):
        raise ModelImportError("invalid_size")


def _validate_format(relative, path, *, deep=False):
    if relative == _NEUTRAL:
        _validate_neutral(path, deep=deep)
    elif relative == files.FLOWHMR_CONFIG:
        _validate_config(path)
    elif relative in _MANO_MODELS:
        # The licensed v1.2 files are protocol-2 array dictionaries. Check the container
        # markers without unpickling; inference uses the existing restricted array reader.
        with path.open("rb") as stream:
            prefix = stream.read(64)
            stream.seek(-1, os.SEEK_END)
            stop = stream.read(1)
        if not prefix.startswith(b"\x80\x02}") or b"numpy" not in prefix or stop != b".":
            raise ModelImportError("invalid_format")


def _snapshot_matches(before, after):
    return (stat.S_ISREG(after.st_mode) and not _reparse(after)
            and os.path.samestat(before, after)
            and (before.st_size, before.st_mtime_ns) == (after.st_size, after.st_mtime_ns))


def _error_code(error, *, reading=False):
    if isinstance(error, ModelImportError):
        return error.code
    if isinstance(error, PermissionError):
        return "source_unreadable" if reading else "permission_denied"
    if isinstance(error, FileNotFoundError) and reading:
        return "invalid_source"
    if isinstance(error, OSError) and error.errno == errno.ENOSPC:
        return "no_space"
    return "import_failed"


def _publish(part, destination):
    # Windows rename is exclusive. POSIX rename replaces its target, so use an exclusive
    # hard link on the same volume instead. Neither path can overwrite an existing file.
    if os.name == "nt":
        part.rename(destination)
    else:
        os.link(part, destination, follow_symlinks=False)


@contextmanager
def _target_lock(root):
    """Let a new import reclaim its own incomplete files after a process exit."""
    _check_directories(root, destination=True)
    marker = root / ".flowhmr-import.lock"
    try:
        info = marker.lstat()
        if _reparse(info) or not stat.S_ISREG(info.st_mode):
            raise ModelImportError("unsafe_destination")
    except FileNotFoundError:
        pass
    with marker.open("a+b") as lock:
        try:
            if os.name == "nt":
                from msvcrt import LK_NBLCK, locking

                lock.seek(0)
                locking(lock.fileno(), LK_NBLCK, 1)
            else:
                from fcntl import LOCK_EX, LOCK_NB, flock

                flock(lock, LOCK_EX | LOCK_NB)
        except OSError as error:
            if error.errno in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                raise ModelImportError("busy") from None
            raise
        yield  # Closing the handle releases the lock, including after a forced exit.


def _part_path(destination):
    return destination.with_name("." + destination.name + ".flowhmr-import.part")


def _remove_incomplete(destination):
    part = _part_path(destination)
    try:
        info = part.lstat()
    except FileNotFoundError:
        return
    if _reparse(info) or not stat.S_ISREG(info.st_mode):
        raise ModelImportError("unsafe_destination")
    part.unlink()


class ModelImporter:
    def __init__(self, root=files.MODEL_ROOT, logger=None):
        self.root = Path(root).absolute()
        self._logger = logger or logging.getLogger(__name__)
        self._lock = Lock()
        self._state = self._initial("idle")

    @staticmethod
    def _initial(state):
        return {"state": state, "phase": "scan" if state == "running" else None, "results": [], "groups": [],
                "bytes_done": 0, "bytes_total": 0, "current_file": None, "error_code": None}

    def _snapshot(self):
        return {**self._state, "groups": list(self._state["groups"]),
                "results": [dict(item) for item in self._state["results"]]}

    @property
    def active(self):
        with self._lock:
            return self._state["state"] == "running"

    def status(self):
        with self._lock:
            return self._snapshot()

    def start(self, source):
        source = _source_path(source)
        with self._lock:
            if self._state["state"] == "running":
                raise ModelImportError("busy")
            self._state = self._initial("running")
            Thread(target=self._run, args=(source,), daemon=True, name="flowhmr-model-import").start()
            return self._snapshot()

    def _update(self, **fields):
        with self._lock:
            self._state.update(fields)

    def _result(self, relative, status, code=None):
        result = {"file": relative, "status": status}
        if code is not None:
            result["code"] = code
        with self._lock:
            self._state["results"].append(result)

    def _run(self, source):
        try:
            matches = _discover(source)
        except Exception as error:
            self._fail(_error_code(error, reading=True))
            return
        if not matches:
            try:
                pending_tar = (source / "smplh.tar").is_file()
            except OSError:
                pending_tar = False
            self._fail("extract_smplh_tar" if pending_tar else "no_models_found")
            return
        self._update(groups=[group for group, relatives in _GROUPS.items() if matches.keys() & set(relatives)])
        try:
            with _target_lock(self.root):
                self._discard_interrupted_copies()
                self._install(matches)
        except Exception as error:
            self._fail(_error_code(error))
            return
        self._update(state="completed", phase=None, current_file=None)

    def _fail(self, code):
        self._logger.warning("Model folder import did not complete (%s)", code)
        self._update(state="failed", phase=None, current_file=None, error_code=code)

    def _discard_interrupted_copies(self):
        for relative in files.MODEL_DESTINATIONS:
            destination = self.root / relative
            try:
                _check_directories(destination.parent)
                _remove_incomplete(destination)
            except (ModelImportError, OSError):
                # Other groups may still be imported. An unsafe part file at the target
                # of this import is reported per file when the copy is attempted.
                continue

    def _install(self, matches):
        pending = []
        for relatives in _GROUPS.values():
            if not matches.keys() & set(relatives):
                continue
            for relative in relatives:
                candidates = matches.get(relative, [])
                destination = self.root / relative
                try:
                    _check_directories(destination.parent, destination=True)
                    try:
                        existing = destination.lstat()
                    except FileNotFoundError:
                        existing = None
                    if existing is not None:
                        if _reparse(existing):
                            raise ModelImportError("unsafe_destination")
                        try:
                            if not stat.S_ISREG(existing.st_mode):
                                raise ModelImportError("invalid_format")
                            _validate_size(relative, existing)
                            _validate_format(relative, destination)
                            if self.root == files.MODEL_ROOT and not model_integrity.check(destination).ok:
                                raise ModelImportError("invalid_format")
                        except ModelImportError:
                            self._result(relative, "conflict", "destination_exists")
                        else:
                            already_there = any(candidate.path == destination for candidate in candidates)
                            self._result(relative, "skipped", "already_in_place" if already_there else "already_installed")
                        continue
                except (ModelImportError, OSError) as error:
                    self._result(relative, "failed", _error_code(error))
                    continue

                if not candidates:
                    self._result(relative, "missing", "missing_file")
                elif len(candidates) > 1:
                    self._result(relative, "conflict", "multiple_candidates")
                else:
                    candidate, = candidates
                    try:
                        if _reparse(candidate.info):
                            raise ModelImportError("unsafe_source")
                        _validate_size(relative, candidate.info)
                    except ModelImportError as error:
                        self._result(relative, "invalid", error.code)
                    else:
                        pending.append((relative, candidate, destination))

        self._update(phase="copy", bytes_total=sum(candidate.info.st_size for _, candidate, _ in pending))
        offset = 0
        for relative, candidate, destination in pending:
            self._update(current_file=relative, bytes_done=offset)
            try:
                self._copy(relative, candidate, destination, offset)
            except ModelImportError as error:
                status = "failed" if error.code in {"source_unreadable", "unsafe_destination", "no_space"} else "invalid"
                self._result(relative, status, error.code)
            except FileExistsError:
                self._result(relative, "conflict", "destination_exists")
            except OSError as error:
                self._result(relative, "failed", _error_code(error))
            else:
                self._result(relative, "installed")
            offset += candidate.info.st_size
            self._update(bytes_done=offset)

    def _copy(self, relative, candidate, destination, offset):
        path = candidate.path
        size = candidate.info.st_size
        asset = _ASSETS.get(relative)
        digest = hashlib.sha256() if asset and asset.sha256 else None
        _check_directories(destination.parent, destination=True)
        if shutil.disk_usage(destination.parent).free < size:
            raise ModelImportError("no_space")

        try:
            _check_directories(path.parent)
            if not _snapshot_matches(candidate.info, path.lstat()):
                raise ModelImportError("source_changed")
            source = path.open("rb")
        except OSError:
            raise ModelImportError("source_unreadable") from None
        with source:
            if not _snapshot_matches(candidate.info, os.fstat(source.fileno())):
                raise ModelImportError("source_changed")
            part = _part_path(destination)
            _remove_incomplete(destination)
            temporary = part.open("xb")
            try:
                with temporary:
                    copied = 0
                    while copied < size:
                        try:
                            chunk = source.read(min(_CHUNK, size - copied))
                        except OSError:
                            raise ModelImportError("source_unreadable") from None
                        if not chunk:
                            raise ModelImportError("source_changed")
                        temporary.write(chunk)
                        if digest is not None:
                            digest.update(chunk)
                        copied += len(chunk)
                        self._update(bytes_done=offset + copied)
                    if not _snapshot_matches(candidate.info, os.fstat(source.fileno())):
                        raise ModelImportError("source_changed")
                    if digest is not None and digest.hexdigest() != asset.sha256:
                        raise ModelImportError("checksum_mismatch")
                    temporary.flush()
                    os.fsync(temporary.fileno())
                _validate_format(relative, part, deep=True)
                _check_directories(destination.parent, destination=True)
                _publish(part, destination)
            finally:
                part.unlink(missing_ok=True)
