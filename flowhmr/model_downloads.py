"""Install missing public models using resumable downloads and verified files."""

import errno
import hashlib
import http.client
from pathlib import Path
import re
import shutil
from threading import Event, Lock, Thread
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import uuid4

from . import model_files as files, model_sources as sources
from . import hamer_setup


_CHUNK = 1024 * 1024
_RANGE = re.compile(r"bytes (\d+)-(\d+)/(\d+)")
ACTIVE_DOWNLOAD = {"running", "stopping"}


class DownloadCancelled(Exception):
    pass


class DownloadError(RuntimeError):
    def __init__(self, message, code="transfer_failed", **params):
        super().__init__(message)
        self.code = code
        self.params = params


class InvalidDownload(DownloadError):
    pass


def _transfer(url, part, asset, progress, check, subject="模型"):
    label = Path(asset.relative).name
    while True:
        check()
        offset = part.stat().st_size if part.is_file() else 0
        if offset == asset.size:
            return
        headers = {"Accept-Encoding": "identity", "User-Agent": "FlowHMR downloader"}
        if offset:
            headers["Range"] = f"bytes={offset}-"
        progress("download", label, offset, asset.size)
        with urlopen(Request(url, headers=headers), timeout=20) as response:
            if "text/html" in response.headers.get("Content-Type", "").lower():
                raise DownloadError(f"下载源返回了网页，未提供{subject}文件。", "transfer_html")
            content_range = response.headers.get("Content-Range")
            if content_range is not None or response.status == 206:
                match = _RANGE.fullmatch(content_range or "")
                if not match:
                    raise DownloadError("下载源返回的断点信息无效。", "transfer_range")
                start, last, total = map(int, match.groups())
                if total != asset.size or start not in (0, offset) or not start <= last < total:
                    raise DownloadError("下载源返回的文件范围与预期不符。", "transfer_range")
                end = last + 1
            else:
                start, end = 0, asset.size  # A full response may ignore the Range request.
            length = response.headers.get("Content-Length")
            if length and (not length.isdigit() or int(length) != end - start):
                raise DownloadError("下载源返回的文件大小与预期不符。", "transfer_size")
            if shutil.disk_usage(part.parent).free + (offset if start == 0 else 0) < asset.size - start:
                raise DownloadError(f"磁盘空间不足，无法保存{subject}。", "transfer_disk")
            received = start
            with part.open("ab" if start else "wb") as output:
                while received < end:
                    check()
                    chunk = response.read(min(_CHUNK, end - received))
                    if not chunk:
                        raise DownloadError("连接提前断开，重试可继续下载。", "transfer_connection")
                    output.write(chunk)
                    received += len(chunk)
                    progress("download", label, received, asset.size)
            if received == asset.size:
                return
            if received <= offset:
                raise DownloadError("下载源未能继续传输此文件。", "transfer_connection")


def _verify(part, asset, progress, check):
    if part.stat().st_size != asset.size:
        raise InvalidDownload("文件大小校验失败。", "transfer_size")
    progress("verify", Path(asset.relative).name, 0, asset.size)
    with part.open("rb") as stream:
        if asset.sha256 is None:
            if stream.read(2) != b"\x1f\x8b":
                raise InvalidDownload("HaMeR 压缩包格式不正确。", "transfer_hamer_archive")
            return  # The individual official files are hashed during extraction.
        digest, read = hashlib.sha256(), 0
        while chunk := stream.read(_CHUNK):
            check()
            digest.update(chunk)
            read += len(chunk)
            progress("verify", Path(asset.relative).name, read, asset.size)
    if digest.hexdigest() != asset.sha256:
        raise InvalidDownload("SHA-256 校验失败，已丢弃临时文件。", "transfer_sha256")


def describe_download_error(error, subject="模型"):
    if isinstance(error, DownloadError):
        return error
    if isinstance(error, HTTPError):
        return DownloadError(f"下载源返回 HTTP {error.code}。", "transfer_http", status=error.code)
    if isinstance(error, (URLError, TimeoutError, ConnectionError, http.client.HTTPException)):
        return DownloadError("无法连接下载源或连接中断，可以重试。", "transfer_connection")
    if isinstance(error, OSError) and error.errno == errno.ENOSPC:
        return DownloadError(f"磁盘空间不足，无法保存{subject}。", "transfer_disk")
    if isinstance(error, PermissionError):
        return DownloadError(f"无法写入{subject}文件夹，请检查文件权限。", "transfer_permission")
    return DownloadError(f"无法安装{subject}，请重试或使用下载链接。")


def download_asset(asset, progress, check):
    """Only publish the final path after the transfer and integrity checks pass."""
    return download_file(asset, files.resolve_model(asset.relative), files.MODEL_ROOT, progress, check)


def download_file(asset, destination, directory, progress, check, *, subject="模型", verify_existing=False):
    part = destination.with_suffix(destination.suffix + ".download.part")
    root = directory.resolve()
    if any(not path.resolve().is_relative_to(root) for path in (destination, part)):
        raise DownloadError(f"下载目标必须位于{subject}文件夹内。", "transfer_unsafe_path")
    if files.file_present(destination):
        if not verify_existing:
            return destination
        try:
            _verify(destination, asset, progress, check)
            check()
            return destination
        except InvalidDownload:
            destination.unlink()
    destination.parent.mkdir(parents=True, exist_ok=True)
    if part.is_file() and part.stat().st_size > asset.size:
        part.unlink()
    last_error = None
    for url in asset.urls:
        try:
            _transfer(url, part, asset, progress, check, subject)
            _verify(part, asset, progress, check)
            check()
            if files.file_present(destination) and not verify_existing:
                part.unlink(missing_ok=True)
            else:
                part.replace(destination)
            return destination
        except InvalidDownload as error:
            part.unlink(missing_ok=True)
            last_error = error
        except (DownloadError, URLError, OSError, http.client.HTTPException) as error:
            last_error = error
    failure = describe_download_error(last_error, subject)
    raise DownloadError(str(failure), failure.code, **failure.params) from last_error


def missing_downloads(device, ckpt=None, config=None, *, include_hamer=False):
    """Plan only public files used by this installation; never download the licensed bundle."""
    active_flowhmr = {p.resolve() for p in files.flowhmr_paths(ckpt, config)}
    plan = {}
    for name, assets in sources.PUBLIC_ASSETS.items():
        pending = tuple(asset for asset in assets
                        if not files.file_present(files.resolve_model(asset.relative))
                        and (name != "flowhmr" or files.resolve_model(asset.relative).resolve() in active_flowhmr))
        if pending:
            plan[name] = pending
    if include_hamer and str(device).split(":", 1)[0] == "cuda":
        hamer = []
        if not hamer_setup.runtime_installed():
            hamer.append(sources.HAMER_CODE)
        weights = [asset for asset in sources.HAMER_DIRECT_ASSETS
                   if not files.file_present(files.resolve_model(asset.relative))]
        if weights:
            # Finish a sufficiently advanced transfer left by an earlier
            # version if it needs fewer bytes than the individual files.
            archive = files.resolve_model(sources.HAMER_ARCHIVE.relative)
            remaining = sources.HAMER_ARCHIVE.size
            for candidate in (archive, archive.with_suffix(archive.suffix + ".download.part")):
                try:
                    size = candidate.stat().st_size
                    if 0 < size <= sources.HAMER_ARCHIVE.size:
                        remaining = min(remaining, sources.HAMER_ARCHIVE.size - size)
                except OSError:
                    pass
            hamer.extend((sources.HAMER_ARCHIVE,) if remaining < sum(asset.size for asset in weights) else weights)
        if hamer:
            plan["hamer"] = tuple(hamer)
    return plan


def _install_group(assets, progress, check):
    for asset in assets:
        check()
        try:
            path = download_asset(asset, progress, check)
        except DownloadError as error:
            if (asset not in sources.HAMER_DIRECT_ASSETS
                    or error.code in ("transfer_disk", "transfer_permission", "transfer_unsafe_path")):
                raise
            _install_hamer_archive(progress, check)
            return  # The official archive supplies any remaining files.
        if asset == sources.HAMER_CODE:
            progress("install", path.name, 0, 0)
            try:
                hamer_setup.install_code(path, check=check)
            except ValueError as error:
                path.unlink(missing_ok=True)
                raise DownloadError("HaMeR 运行组件校验失败，请重试。", "transfer_hamer_archive") from error
            path.unlink(missing_ok=True)
        elif asset == sources.HAMER_ARCHIVE:
            _install_hamer_archive(progress, check, path)


def _install_hamer_archive(progress, check, path=None):
    path = path or download_asset(sources.HAMER_ARCHIVE, progress, check)
    progress("install", path.name, 0, 0)
    try:
        hamer_setup.install_weights(path, progress=progress, check=check)
    except hamer_setup.BadArchive as error:
        path.unlink(missing_ok=True)
        raise DownloadError("HaMeR 模型包校验失败，请重试。", "transfer_hamer_archive") from error
    path.unlink(missing_ok=True)


class ModelDownloads:
    """One background download per service, independent of inference jobs."""

    def __init__(self, logger):
        self._logger = logger
        self._lock = Lock()
        self._cancel = Event()
        self._state = {"status": "idle", "id": None, "errors": []}

    def _snapshot(self):
        return {**self._state, "errors": [dict(error) for error in self._state["errors"]]}

    def snapshot(self):
        with self._lock:
            return self._snapshot()

    def start(self, plan, *, scope=None):
        with self._lock:
            if self._state["status"] in ACTIVE_DOWNLOAD:
                return self._snapshot()
            if not plan:
                self._state = {"status": "idle", "id": None, "errors": []}
                return self._snapshot()
            self._cancel.clear()
            self._state = {"status": "running", "id": uuid4().hex, "group": None, "scope": scope,
                           "index": 0, "count": len(plan), "phase": "download", "file": "",
                           "bytes": 0, "total": 0, "errors": []}
            Thread(target=self._run, args=(plan,), daemon=True, name="flowhmr-model-downloads").start()
            return self._snapshot()

    def stop(self, download_id):
        with self._lock:
            if download_id == self._state["id"] and self._state["status"] == "running":
                self._cancel.set()
                self._state["status"] = "stopping"
            return self._snapshot()

    def _run(self, plan):
        def check():
            if self._cancel.is_set():
                raise DownloadCancelled()

        def progress(phase, name, size, total):
            with self._lock:
                self._state.update(phase=phase, file=name, bytes=size, total=total)

        for index, (group, assets) in enumerate(plan.items(), 1):
            if self._cancel.is_set():
                break
            with self._lock:
                self._state.update(group=group, index=index, file="", bytes=0, total=0)
            try:
                _install_group(assets, progress, check)
            except DownloadCancelled:
                break
            except Exception as error:
                failure = describe_download_error(error)
                self._logger.warning("Model download failed (%s, %s): %s", group, type(error).__name__, failure)
                with self._lock:
                    self._state["errors"].append({"group": group, "file": self._state["file"], "message": str(failure),
                                                  "error_code": failure.code, "error_params": failure.params})
        with self._lock:
            self._state["status"] = "cancelled" if self._cancel.is_set() else "done"
