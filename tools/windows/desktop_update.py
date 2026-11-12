"""Check official releases and stage verified portable updates off the UI thread."""

from dataclasses import dataclass
from hashlib import sha256
from http.client import HTTPException
import json
import logging
from pathlib import Path, PureWindowsPath
import re
import shutil
import stat
from tempfile import NamedTemporaryFile, TemporaryDirectory
from threading import Event, Lock, Thread, Timer
from time import monotonic
from urllib.request import Request, urlopen
from zipfile import BadZipFile, ZipFile


REPO = "xiaoerofficial021-collab/FlowHMR-Windows"
RELEASES_API = f"https://api.github.com/repos/{REPO}/releases?per_page=100"
RELEASES = f"https://github.com/{REPO}/releases"
PACKAGE = "FlowHMR-Windows.zip"
VERSION_FILE = "FlowHMR-Version.txt"
UPDATE_INFO = "FlowHMR-Windows-Update.json"
CHECK_INTERVAL = 12 * 60 * 60
CHECK_TIMEOUT = 5
MAX_RELEASE_NOTES = 12_000
_VERSION = re.compile(r"(?:0|[1-9][0-9]{0,8})(?:\.(?:0|[1-9][0-9]{0,8})){2}")
_CODE_DIRECTORIES = {"flowhmr", "tools", "third_party"}
_SHARED_DIRECTORIES = {"assets", "configs", "data", "docs"}
_ROOT_FILES = {
    VERSION_FILE, ".gitignore", ".gitmodules", "app.py", "check-install.cmd", "download-models.cmd",
    "LICENSE", "README.md", "requirements-phc.txt", "requirements-windows.lock.txt",
    "requirements-windows.txt", "requirements.txt", "run_v2m_demo_generation.py", "start-web.cmd",
    "stop-web.cmd", "THIRD_PARTY_NOTICES.md", "train_v2m.py",
}
_REQUIRED = {VERSION_FILE, "FlowHMR.exe", "app.py", "runtime/python.exe", "runtime/pythonw.exe",
             "tools/web/index.html", "tools/windows/desktop_launcher.pyw", "tools/windows/desktop_update.py",
             "tools/web/static/js/update.js"}
_TRANSFER = {"download", "verify", "stage", "stopping"}


@dataclass(frozen=True)
class Release:
    version: str
    url: str
    size: int
    sha256: str
    installable: bool = False
    notes: str = ""


class LocalUpdateError(ValueError):
    pass


def release_version(value):
    if not isinstance(value, str) or not _VERSION.fullmatch(value):
        raise ValueError("Expected a Windows release version in X.Y.Z format")
    return tuple(map(int, value.split(".")))


def read_version(root, name=VERSION_FILE):
    try:
        with (root / name).open("rb") as stream:
            value = stream.read(40).decode("ascii").strip()
        release_version(value)
        return value
    except (OSError, ValueError):
        return None


def _portable_release(data):
    tag = data["tag_name"]
    if not tag.startswith("windows-v") or data.get("draft") or data.get("prerelease"):
        return None
    version = tag.removeprefix("windows-v")
    release_version(version)
    packages = [asset for asset in data["assets"] if asset.get("name") == PACKAGE]
    if len(packages) != 1:
        raise ValueError("The release must contain one portable ZIP")
    asset = packages[0]
    expected = f"{RELEASES}/download/{tag}/{PACKAGE}"
    size, digest = asset["size"], asset.get("digest")
    if (asset.get("state") != "uploaded" or asset.get("browser_download_url") != expected
            or type(size) is not int or not 0 < size < 2 * (1 << 30)
            or not isinstance(digest, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", digest)):
        raise ValueError("The portable ZIP does not have trusted download metadata")
    installable = any(item.get("name") == UPDATE_INFO and item.get("state") == "uploaded" for item in data["assets"])
    body = data.get("body")
    notes = body.strip()[:MAX_RELEASE_NOTES] if isinstance(body, str) else ""
    return Release(version, expected, size, digest[7:], installable, notes)


def published_releases():
    request = Request(RELEASES_API, headers={"Accept": "application/vnd.github+json", "User-Agent": "FlowHMR desktop updater"})
    with urlopen(request, timeout=CHECK_TIMEOUT - 1) as response:
        if response.geturl() != RELEASES_API:
            raise ValueError("Unexpected release metadata origin")
        payload = response.read((4 << 20) + 1)
    if len(payload) > 4 << 20:
        raise ValueError("Release metadata is too large")
    entries = json.loads(payload)
    if not isinstance(entries, list):
        raise ValueError("Expected a list of published releases")
    releases = {}
    for entry in entries:
        try:
            if release := _portable_release(entry):
                releases[release.version] = release
        except (ValueError, TypeError, KeyError, AttributeError):
            continue  # Releases for other assets and incomplete uploads are skipped.
    return dict(sorted(releases.items(), key=lambda item: release_version(item[0]), reverse=True))


def _archive_version(path):
    try:
        with ZipFile(path) as archive, archive.open(f"FlowHMR/{VERSION_FILE}") as stream:
            version = stream.read(40).decode("ascii").strip()
        release_version(version)
        return version
    except KeyError as error:
        raise LocalUpdateError("legacy_zip") from error
    except (BadZipFile, UnicodeError, ValueError) as error:
        raise LocalUpdateError("invalid_zip") from error


def _update_target(parts):
    """Keep runtime packages, models and user data outside the program transaction."""
    if len(parts) == 1 and parts[0] in _ROOT_FILES:
        return parts[0]
    if len(parts) > 1:
        if parts[0] in _CODE_DIRECTORIES:
            return parts[0]
        if parts[0] in _SHARED_DIRECTORIES:
            return "/".join(parts)
        if parts[0] == "runtime" and parts[1].casefold() not in {"environments", "webview2"}:
            return "/".join(parts[:2])
    raise ValueError("Unexpected program update path: " + "/".join(parts))


def stage_package(archive_path, destination, version, check):
    """Extract the portable package on the same drive without touching the installation."""
    files = destination / "files"
    seen = set()
    targets = set()
    required = set()
    members = []
    with ZipFile(archive_path) as archive:
        for item in archive.infolist():
            check()
            name = item.filename.removesuffix("/") if item.is_dir() else item.filename
            parts = name.split("/")
            if (parts[0] != "FlowHMR" or any(not part or part in {".", ".."} or part.endswith((" ", "."))
                    or PureWindowsPath(part).is_reserved() for part in parts)
                    or any(ord(char) < 32 or char in '\\<>:"|?*' for char in name)
                    or stat.S_IFMT(item.external_attr >> 16) not in (0, stat.S_IFREG, stat.S_IFDIR)):
                raise ValueError("Unsafe file in portable ZIP")
            key = name.casefold()
            if key in seen:
                raise ValueError("Duplicate Windows path in portable ZIP")
            seen.add(key)
            if item.is_dir():
                continue
            relative = "/".join(parts[1:])
            if relative in _REQUIRED:
                required.add(relative)
            if relative in ("FlowHMR.exe", "models/README.md"):
                continue  # The launcher is running, and the models folder belongs to the user.
            targets.add(_update_target(parts[1:]))
            members.append((item, files.joinpath(*parts[1:])))
        if required != _REQUIRED:
            raise ValueError("Incomplete portable ZIP or missing update version")
        if shutil.disk_usage(destination).free < sum(item.file_size for item, _ in members):
            raise OSError("Insufficient disk space to stage the portable update")
        for item, target in members:
            check()
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(item) as source, target.open("xb") as output:
                while chunk := source.read(1 << 20):
                    check()
                    output.write(chunk)
    if read_version(files) != version:
        raise ValueError("The packaged version does not match the release tag")
    (destination / "targets.txt").write_text("\n".join(sorted(targets - {VERSION_FILE}) + [VERSION_FILE]) + "\n",
                                             encoding="utf-8", newline="\n")


class DesktopUpdate:
    def __init__(self, root):
        self.root = root
        self.work = root / ".cache/program-update"
        self.current = read_version(root) if (root / "runtime/python.exe").is_file() else None
        self.lock = Lock()
        self.stop = Event()
        self.release = None
        self.releases = {}
        self.checking = False
        self.next_check = 0
        queued = (read_version(self.work / "ready/files") or read_version(self.work, "rollback-request.txt")) if self.current else None
        self.state = {"version": self.current, "status": "ready" if queued else "idle", "target": queued,
                      "received": 0, "total": 0, "message": None}

    def _saved_version(self):
        applying = self.work / "applying"
        previous = applying if (applying / "healthy").is_file() else self.work / "previous"
        backup = read_version(previous / "backup") if read_version(previous, "installed-version.txt") == self.current else None
        version = backup or read_version(self.work / "forward/files")
        return version if version != self.current else None

    def _snapshot(self):
        saved = self._saved_version() if self.current else None
        target = self.releases.get(self.state["target"])
        return {**self.state, "downloadable": self.release is not None, "saved": saved,
                "notes": target.notes if target else None,
                "releases": [{"version": release.version, "url": release.url, "installable": release.installable}
                             for release in self.releases.values() if release.version != self.current and release.version != saved]}

    def snapshot(self):
        with self.lock:
            return self._snapshot()

    def check(self, manual=False):
        with self.lock:
            if (self.current and not self.checking and self.state["status"] not in _TRANSFER | {"ready"}
                    and (manual or monotonic() >= self.next_check)):
                self.checking = True
                self.next_check = monotonic() + CHECK_INTERVAL
                self.state.update(status="checking", message=None)
                Thread(target=self._check, name="flowhmr-release-check", daemon=True).start()
            return self._snapshot()

    def _check(self):
        def timed_out():
            with self.lock:
                if self.state["status"] == "checking":
                    self.state["status"] = "offline"

        timeout = Timer(CHECK_TIMEOUT, timed_out)
        timeout.daemon = True
        timeout.start()
        try:
            releases = published_releases()
            release = next(iter(releases.values()), None)
            available = release and release_version(release.version) > release_version(self.current)
            with self.lock:
                self.releases = releases
                if self.state["status"] == "checking":
                    self.release = release if available and release.installable else None
                    status = ("available" if self.release else "legacy_available") if available else "current"
                    self.state.update(status=status,
                                      target=release.version if available else None,
                                      total=release.size if available else 0, received=0)
        except (OSError, ValueError, TypeError, KeyError, AttributeError, HTTPException):
            logging.debug("Could not check for a FlowHMR update", exc_info=True)
            timed_out()
        finally:
            timeout.cancel()
            with self.lock:
                self.checking = False
            self._clean_retired()

    def _clean_retired(self):
        if not self.work.is_dir() or self.work.resolve() != self.work:
            return
        for path in self.work.iterdir():
            if (re.fullmatch(r"retired-[0-9a-f]{32}", path.name)
                    and path.resolve() == path and path.is_dir()):
                shutil.rmtree(path, ignore_errors=True)

    def download(self):
        with self.lock:
            if self.release and self.state["status"] in {"available", "failed", "cancelled", "offline"}:
                self._start_transfer(release=self.release)
            return self._snapshot()

    def _start_transfer(self, *, release=None, local=None):
        self.stop.clear()
        self.state.update(status="download" if release else "stage", message=None, received=0,
                          total=release.size if release else 0)
        Thread(target=self._prepare_update, kwargs={"release": release, "local": local},
               name="flowhmr-prepare-update", daemon=True).start()

    def stage_local(self, path):
        with self.lock:
            if self.current and self.state["status"] not in _TRANSFER | {"ready"}:
                self._start_transfer(local=path)
            return self._snapshot()

    def select_version(self, version):
        with self.lock:
            if (not self.current or not isinstance(version, str) or version == self.current
                    or self.state["status"] in _TRANSFER | {"ready"}):
                return self._snapshot()
            if version == self._saved_version():
                try:
                    if self.work.resolve() != self.work:
                        raise ValueError("Update cache must be inside the installation")
                    forward = self.work / "forward"
                    if read_version(forward / "files") == version:
                        if forward.resolve() != forward:
                            raise ValueError("Invalid saved update path")
                        (forward / "allow-downgrade").write_text(version, encoding="ascii")
                        forward.rename(self.work / "ready")
                    else:
                        with NamedTemporaryFile(dir=self.work, delete=False) as output:
                            request = Path(output.name)
                            try:
                                output.write(version.encode("ascii"))
                                output.close()
                                request.replace(self.work / "rollback-request.txt")
                            finally:
                                output.close()
                                request.unlink(missing_ok=True)
                    self.state.update(status="ready", target=version, message=None, total=0, received=0)
                except (OSError, ValueError):
                    logging.exception("Could not schedule a saved FlowHMR version")
                    self.state.update(status="failed", message="manual_failed")
            elif (release := self.releases.get(version)) and release.installable:
                self.release = release
                self.state["target"] = version
                self._start_transfer(release=release)
            return self._snapshot()

    def cancel(self):
        with self.lock:
            if self.state["status"] in {"download", "verify", "stage"}:
                self.stop.set()
                self.state["status"] = "stopping"
            return self._snapshot()

    def _prepare_update(self, *, release, local):
        try:
            from flowhmr.model_downloads import DownloadCancelled, download_file
            from flowhmr.model_sources import Asset
        except ImportError:
            logging.exception("Could not load the portable update downloader")
            with self.lock:
                self.state.update(status="failed", message="manual_failed" if local is not None else None)
            return

        def check():
            if self.stop.is_set():
                raise DownloadCancelled()

        def progress(phase, _name, received, total):
            with self.lock:
                if not self.stop.is_set():
                    self.state.update(status=phase, received=received, total=total)

        try:
            if local is not None:
                archive = Path(local)
                if archive.suffix.casefold() != ".zip" or not archive.is_file():
                    raise LocalUpdateError("invalid_zip")
                version = _archive_version(archive)
                if version == self.current:
                    raise LocalUpdateError("same_version")
                with self.lock:
                    known = self.releases.get(version)
                if known is not None:
                    if archive.stat().st_size != known.size:
                        raise LocalUpdateError("hash_mismatch")
                    digest, received = sha256(), 0
                    with archive.open("rb") as stream:
                        while block := stream.read(1 << 20):
                            check()
                            digest.update(block)
                            received += len(block)
                            progress("verify", archive.name, received, known.size)
                    if digest.hexdigest() != known.sha256:
                        raise LocalUpdateError("hash_mismatch")
            else:
                version = release.version
            if self.work.resolve() != self.work:
                raise ValueError("Update cache must be inside the installation")
            self.work.mkdir(parents=True, exist_ok=True)
            for path in self.work.glob("stage-*"):
                if re.fullmatch(r"stage-[a-z0-9_]{8}", path.name) and path.resolve() == path and path.is_dir():
                    shutil.rmtree(path, ignore_errors=True)
            if release:
                name = f"{release.version}-{release.sha256}.zip"
                asset = Asset(name, release.size, release.sha256, (release.url,))
                archive = download_file(asset, self.work / name, self.work, progress, check,
                                        subject="程序更新", verify_existing=True)
            progress("stage", archive.name, 0, 0)
            with TemporaryDirectory(prefix="stage-", dir=self.work) as temporary:
                pending = Path(temporary) / "ready"
                pending.mkdir()
                stage_package(archive, pending, version, check)
                if release_version(version) < release_version(self.current):
                    (pending / "allow-downgrade").write_text(version, encoding="ascii")
                with self.lock:
                    check()
                    pending.rename(self.work / "ready")
                    self.state.update(status="ready", target=version)
            if release:
                archive.unlink(missing_ok=True)
        except DownloadCancelled:
            with self.lock:
                self.state.update(status="cancelled", message="manual_cancelled" if local is not None else None)
        except Exception as error:
            logging.exception("Could not stage a FlowHMR update")
            message = None
            if local is not None:
                message = str(error) if isinstance(error, LocalUpdateError) else (
                    "invalid_zip" if isinstance(error, (BadZipFile, ValueError)) else "manual_failed")
            with self.lock:
                if self.state["status"] != "ready":
                    self.state.update(status="failed", message=message)
