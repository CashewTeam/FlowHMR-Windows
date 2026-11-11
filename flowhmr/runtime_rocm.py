"""Verified Windows ROCm packages and startup helpers for the gfx1201 profile."""

import ctypes
from ctypes import wintypes
from functools import lru_cache
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import sys
import tarfile
from urllib.parse import quote
from zipfile import ZipFile

from .model_downloads import DownloadError
from .model_sources import Asset


PROFILE = "rocm714-gfx1201"
VERSION = "7.14.0"
ARCH = "gfx1201"
TORCH_VERSION = "2.12.0+rocm7.14.0"
VISION_VERSION = "0.27.0+rocm7.14.0"
WHEEL_INDEX = "https://repo.amd.com/rocm/whl-multi-arch/"
DRIVER = "26.6.4"
_SUPPORTED_NAME = re.compile(r"\b(?:RX\s*9070(?:\s+(?:XT|GRE))?|R9700)\b", re.I)
_CHUNK = 1 << 20

# Exact Windows x64 CPython 3.10 artifacts from AMD. The SDK core, shared
# libraries and device kernels are separate packages in AMD's official index.
PACKAGES = tuple(Asset(name, size, digest, (WHEEL_INDEX + quote(name),)) for name, size, digest in (
    ("rocm_sdk_core-7.14.0-py3-none-win_amd64.whl", 758744987,
     "e0d5650b9b6ff931496d3336eb72cb969fb3bc4e1966dc84b80b727af32a6bf0"),
    ("rocm_sdk_libraries-7.14.0-py3-none-win_amd64.whl", 115912167,
     "d03873d2879c000ae307866cc827fe76d6e9f50846fec76b7cb293202afdee56"),
    ("rocm_sdk_device_gfx1201-7.14.0-py3-none-win_amd64.whl", 312289097,
     "b89e2600cd7000154e5ee6f951daaff6b3dd69ad78137847114e1c49de8e2bd9"),
    ("amd_torch_device_gfx12_0-2.12.0+rocm7.14.0-cp310-cp310-win_amd64.whl", 113459010,
     "5961c93ce0306f4e7cff8fab31db344df6d7bda20db3888ae8afcb6c473211c7"),
    ("amd_torch_device_gfx1201-2.12.0+rocm7.14.0-cp310-cp310-win_amd64.whl", 51186860,
     "f1e069d1d4a9979aa83a91c119370363837071a1f96bf2458c42dd149b97bc83"),
    ("torch-2.12.0+rocm7.14.0-cp310-cp310-win_amd64.whl", 114047819,
     "3ed1ab7b273f123a376f1ed483ea6fe617f14a58d2365166a55d74751a417d39"),
    ("torchvision-0.27.0+rocm7.14.0-cp310-cp310-win_amd64.whl", 900770,
     "7f453f20b997f136033e60d3b82292eab5b66157a52f292dc5bd2e2a30140c65"),
    ("amd_torchvision_device_gfx1201-0.27.0+rocm7.14.0-cp310-cp310-win_amd64.whl", 133931,
     "070bf3ec7f6346f68ac57ad626914be55031b8c6ee002494331d164c7854cc95"),
    ("rocm_bootstrap-0.1.0-py3-none-any.whl", 27015,
     "33e3d4dad086b1f0e06bc049a618ca874d4fb72f758fff00676e23d2a644b652"),
    ("rocm-7.14.0.tar.gz", 24252,
     "77c622d8eef7bf7fa1af70d410a05a621abbd2baaf53e52ab268dc6d140e15b2"),
))


def windows_11():
    return sys.platform == "win32" and sys.getwindowsversion().build >= 22000


class _DisplayDevice(ctypes.Structure):
    _fields_ = [("cb", wintypes.DWORD), ("device_name", wintypes.WCHAR * 32),
                ("description", wintypes.WCHAR * 128), ("flags", wintypes.DWORD),
                ("hardware_id", wintypes.WCHAR * 128), ("key", wintypes.WCHAR * 128)]


def _amd_adapter(name, hardware_id, driver=""):
    if "VEN_1002" in (hardware_id or "").upper() and _SUPPORTED_NAME.search(name or ""):
        return {"name": name, "driver": driver or "", "capability": ARCH}
    return None


@lru_cache(maxsize=1)
def windows_adapter():
    """Report a supported AMD display adapter without importing PyTorch."""
    if not windows_11():
        return None
    try:
        enumerate_display = ctypes.WinDLL("user32").EnumDisplayDevicesW
        enumerate_display.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(_DisplayDevice), wintypes.DWORD)
        enumerate_display.restype = wintypes.BOOL
        for index in range(64):
            display = _DisplayDevice()
            display.cb = ctypes.sizeof(display)
            if not enumerate_display(None, index, ctypes.byref(display), 0):
                break
            adapter = _amd_adapter(display.description, display.hardware_id)
            if adapter:
                return adapter
    except OSError:
        pass
    # Some headless discrete adapters are only visible to the CIM inventory.
    try:
        rows = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
             "$ErrorActionPreference='Stop'; Get-CimInstance Win32_VideoController | "
             "Select-Object Name,DriverVersion,PNPDeviceID | ConvertTo-Json -Compress"],
            check=True, capture_output=True, text=True, errors="replace", timeout=6,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        devices = json.loads(rows.stdout)
        for card in devices if isinstance(devices, list) else [devices]:
            adapter = _amd_adapter(card.get("Name"), card.get("PNPDeviceID"), card.get("DriverVersion"))
            if adapter:
                return adapter
    except (OSError, AttributeError, ValueError, subprocess.SubprocessError):
        pass
    return None


def configure_windows_hip(torch, *, attention=False):
    """Use native PyTorch fallbacks for MIOpen BatchNorm and keep SDPA choices."""
    if sys.platform != "win32" or not getattr(torch.version, "hip", None):
        return
    os.environ.setdefault("MIOPEN_FIND_MODE", "FAST")
    torch.backends.cudnn.enabled = False
    if attention:
        torch.backends.cuda.enable_flash_sdp(True)
        torch.backends.cuda.enable_mem_efficient_sdp(True)
        torch.backends.cuda.enable_math_sdp(True)


def gpu_ordinal(torch, arch=ARCH):
    """Select a matching discrete GPU, respecting the user's visibility mask."""
    candidates = []
    for index in range(torch.cuda.device_count()):
        try:
            props = torch.cuda.get_device_properties(index)
        except RuntimeError:
            continue
        actual = str(getattr(props, "gcnArchName", "") or "").split(":", 1)[0]
        # New ROCm versions expose the architecture. Older Windows builds
        # may only report the adapter name.
        if (arch is None or actual == arch or
                (not actual and arch == ARCH and _SUPPORTED_NAME.search(props.name))):
            candidates.append((props.total_memory, -index))
    if not candidates:
        raise RuntimeError(f"No visible ROCm GPU for {arch or 'this runtime'}")
    return -max(candidates)[1]


def supports_bf16(torch, device):
    if getattr(torch.version, "hip", None):
        with torch.cuda.device(device):
            return torch.cuda.is_bf16_supported(including_emulation=False)
    return torch.cuda.get_device_capability(device)[0] >= 8


def _members(archive):
    for info in archive.infolist():
        path = PurePosixPath(info.filename)
        mode = info.external_attr >> 16
        if (path.is_absolute() or not path.parts or ".." in path.parts or "\\" in info.filename
                or ":" in info.filename or stat.S_ISLNK(mode)
                or any(part.endswith((".", " ")) for part in path.parts)):
            raise DownloadError("ROCm 包中有无效路径。", "transfer_unsafe_path")
        if not info.is_dir():
            yield info, path


def _install_sdk_loader(package, site, check):
    # Install the small Python loader from the verified AMD sdist without
    # executing a build script or requiring pip in the portable interpreter.
    package_root = f"rocm-{VERSION}"
    paths = {f"src/rocm_sdk/{name}.py": f"rocm_sdk/{name}.py"
             for name in ("__init__", "__main__", "_devel", "_dist_info")}
    paths["PKG-INFO"] = f"rocm-{VERSION}.dist-info/METADATA"
    with tarfile.open(package, "r:gz") as archive:
        for name, relative in paths.items():
            check()
            info = archive.getmember(f"{package_root}/{name}")
            if not info.isfile() or info.size > (1 << 20):
                raise DownloadError("ROCm SDK 加载器文件不完整。", "transfer_size")
            destination = site / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            with archive.extractfile(info) as stream, destination.open("xb") as output:
                shutil.copyfileobj(stream, output)


def install_package(package, site, progress, check):
    if package.name.endswith(".tar.gz"):
        _install_sdk_loader(package, site, check)
        return
    with ZipFile(package) as archive:
        members = list(_members(archive))
        total = sum(info.file_size for info, _ in members)
        if shutil.disk_usage(site.parent).free < total + 64 * 1024 ** 2:
            raise DownloadError("磁盘空间不足，无法解包 ROCm。", "transfer_disk")
        written = 0
        progress("install", package.name, written, total)
        for info, relative in members:
            check()
            destination = site.joinpath(*relative.parts)
            if destination.exists():
                # AMD's host and device wheels share exactly one empty namespace.
                if relative.as_posix() == "_rocm_sdk_libraries/__init__.py" and info.file_size == 0 == destination.stat().st_size:
                    continue
                raise DownloadError("ROCm 包包含冲突的文件。", "runtime_directory_conflict")
            destination.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as source, destination.open("xb") as output:
                while data := source.read(_CHUNK):
                    check()
                    output.write(data)
                    written += len(data)
                    progress("install", package.name, written, total)
