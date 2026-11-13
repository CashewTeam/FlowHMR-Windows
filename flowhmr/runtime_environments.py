"""Windows inference runtimes are downloaded into isolated, switchable slots."""

import csv
from dataclasses import dataclass
from functools import lru_cache
from importlib.metadata import PackageNotFoundError, version
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import struct
import subprocess
import sys
from tempfile import NamedTemporaryFile, TemporaryDirectory
from threading import Event, Lock, Thread
from urllib.parse import quote
from uuid import uuid4
from zipfile import ZipFile

from .model_downloads import ACTIVE_DOWNLOAD, DownloadCancelled, DownloadError, describe_download_error, download_file
from . import runtime_rocm as rocm
from . import runtime_xpu as xpu


ROOT = Path(__file__).resolve().parents[1]
TORCH_VERSION = "2.8.0"
VISION_VERSION = "0.23.0"
CPU_TORCH_VERSION = "2.12.0"
CPU_VISION_VERSION = "0.27.0"
SUPPORTED = sys.platform == "win32" and sys.version_info[:2] == (3, 10) and struct.calcsize("P") == 8
_CHUNK = 1024 * 1024


@dataclass(frozen=True)
class Wheel:
    package: str
    release: str
    variant: str
    size: int
    sha256: str

    @property
    def relative(self):
        return f"{self.package}-{self.release}+{self.variant}-cp310-cp310-win_amd64.whl"

    @property
    def urls(self):
        path = f"/whl/{self.variant}/{quote(self.relative)}"
        return ("https://download-r2.pytorch.org" + path, "https://download.pytorch.org" + path)


# Windows CPython 3.10 wheels from the official PyTorch indices.
_SOURCES = {
    "cu126": (2915462477, "3b43acaa40ac87495d858bb1dbe574aaaff4e60e9da6221825ece694fd1d2b80",
              6227571, "089d3fbeabf273a7f138c7fa2486289313ec57b002a4856f47eba52dace35e96"),
    "cu128": (3461427674, "43938e9a174c90e5eb9e906532b2f1e21532bbfa5a61b65193b4f54714d34f9e",
              7540139, "8ec6f2281ef5d52471b01b99eb04243d0c2cccb1972ba43217085025fe5a6c3f"),
    "cu129": (3571817490, "e4adcc3d44089d4a696e6a2ca4233d1ddad7614adc1d48ec8a8cfb95ba235ea1",
              8147730, "eaec7f8dd6f50f7516d4290c865ef4c9149034983523ebed83ebbd6023b5bf72"),
}
WHEELS = {key: (Wheel("torch", TORCH_VERSION, key, ts, th), Wheel("torchvision", VISION_VERSION, key, vs, vh))
          for key, (ts, th, vs, vh) in _SOURCES.items()}
WHEELS["cu121"] = (
    Wheel("torch", "2.5.1", "cu121", 2449372784, "9b22d6d98aa56f9317902dec0e066814a6edba1aada90110ceea2bb0678df22f"),
    Wheel("torchvision", "0.20.1", "cu121", 6063665, "4cb1e44c1f7a4992f6d38a15633a1e694c093f1c52f3a036b1a719968031507a"),
)
WHEELS["cpu"] = (
    Wheel("torch", CPU_TORCH_VERSION, "cpu", 122862513, "7b9442036d46fb657c6a95f4c77c875db07e604400f29ef0ae8dc370c37619b8"),
    Wheel("torchvision", CPU_VISION_VERSION, "cpu", 3435608, "62a4318e2484871143b5f3fbca7cd245c7a8106f60aa2b7f4d8bb0d1cc87fe52"),
)
CUDA_OPTIONS = ("cu128", "cu121")
LEGACY_CUDA_OPTIONS = ("cu129", "cu126")
DRIVER_RECOMMENDED = {"cu121": "531.14", "cu126": "561.17", "cu128": "572.61", "cu129": "576.57"}


def _atomic_json(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            stream.write(json.dumps(content, ensure_ascii=False).encode("utf-8"))
            stream.close()
            temporary.replace(path)
        finally:
            stream.close()
            temporary.unlink(missing_ok=True)


def _read_json(path):
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _system_runtime():
    try:
        torch = version("torch")
        version("torchvision")
        match = re.search(r"\+(cu\d+|rocm\d+(?:\.\d+)*|cpu|xpu)", torch)
        return match[1] if match else None
    except PackageNotFoundError:
        return None


def cuda_label(variant):
    digits = variant.removeprefix("cu")
    return f"CUDA {digits[:-1]}.{digits[-1]}"


def runtime_label(variant):
    if variant == "cpu":
        return "CPU"
    if variant == xpu.PROFILE:
        return "Intel XPU"
    profile = rocm.PROFILES.get(variant)
    if profile:
        return f"{profile.series + ' · ' if profile.series else ''}ROCm {rocm.VERSION} · {profile.arch}"
    if variant.startswith("rocm"):
        return "ROCm " + variant.removeprefix("rocm")
    return cuda_label(variant)


def runtime_packages(name):
    if name == xpu.PROFILE:
        return xpu.PACKAGES
    profile = rocm.PROFILES.get(name)
    return profile.packages if profile else WHEELS.get(name, ())


@lru_cache(maxsize=1)
def nvidia_adapters():
    if not SUPPORTED:
        return ()
    executable = shutil.which("nvidia-smi")
    if not executable:
        system = Path(os.environ.get("WINDIR", r"C:\Windows")) / "System32/nvidia-smi.exe"
        executable = str(system) if system.is_file() else None
    if not executable:
        return ()
    try:
        result = subprocess.run([executable, "--query-gpu=name,driver_version,compute_cap", "--format=csv,noheader"],
                                capture_output=True, encoding="utf-8", errors="replace", timeout=4,
                                creationflags=subprocess.CREATE_NO_WINDOW, check=True)
        return tuple(dict(zip(("name", "driver", "capability"), (part.strip() for part in row)))
                     for row in csv.reader(result.stdout.splitlines()) if len(row) == 3)
    except (OSError, ValueError, subprocess.SubprocessError):
        return ()


def compute_capability(card):
    value = card.get("capability", "")
    return tuple(map(int, value.split("."))) if re.fullmatch(r"\d+\.\d+", value) else None


def cuda_compatible(variant, capability):
    if capability is None:
        return True  # The actual install probe makes the decision if detection was incomplete.
    major, _ = capability
    if variant in ("cu121", "cu126"):
        return 6 <= major <= 9
    if variant == "cu128":
        # The Windows cu128 wheels also ship sm_61. GTX 10 guidance stays
        # on the dedicated cu121 trial path, but keep existing slots usable.
        return capability >= (6, 1)
    return major >= 7


def cuda_ordinal(torch, variant, ordinal=None):
    """Find a visible compatible NVIDIA card, preferring CUDA's device order."""
    count = torch.cuda.device_count()
    for index in range(count) if ordinal is None else (ordinal,):
        if 0 <= index < count and cuda_compatible(variant, torch.cuda.get_device_capability(index)):
            return index
    raise RuntimeError(f"No visible NVIDIA GPU for {cuda_label(variant)}")


def recommended_cuda(cards):
    capabilities = tuple(compute_capability(card) for card in cards)
    # Only show a recommendation when a GPU was identified. GTX 10 is trial support.
    if any(cap and cap[0] >= 10 for cap in capabilities):
        return "cu128"
    if any(cap and 7 <= cap[0] <= 9 for cap in capabilities):
        return "cu121"
    return None


def _wheel_members(archive, wheel):
    allow = ({"torch", "torchgen", "functorch"} if wheel.package == "torch"
             else {"torchvision", "torchvision.libs"})
    allow.add(f"{wheel.package}-{wheel.release}+{wheel.variant}.dist-info")
    for info in archive.infolist():
        path = PurePosixPath(info.filename)
        if path.is_absolute() or ".." in path.parts or "\\" in info.filename or ":" in info.filename:
            raise DownloadError("运行环境包中有无效路径。", "transfer_unsafe_path")
        if info.is_dir() or not path.parts or path.parts[0] not in allow:
            continue
        if path.parts[:2] in {("torch", "include"), ("torch", "share")} or path.suffix.lower() == ".lib":
            continue  # Runtime does not compile C++ extensions.
        yield info, path


class RuntimeEnvironments:
    def __init__(self, root, logger=None):
        self.root = Path(root).resolve()
        self.directory = self.root / "runtime/environments"
        self.downloads = self.root / ".cache/runtime-downloads"
        self.preference = self.directory / "selected.json"
        self.system = _system_runtime() if SUPPORTED else None
        self.active = "system" if self.system else None
        self.logger = logger
        self.lock = Lock()
        self.cancel = Event()
        self.transfer = {"id": None, "status": "idle"}

    def installed(self, name):
        packages = runtime_packages(name)
        if not packages:
            return None
        slot = self.directory / name
        site = slot / "site-packages"
        mark = _read_json(slot / "installed.json")
        torch_library = ("torch_cpu.dll" if name == "cpu" else "torch_xpu.dll" if name == xpu.PROFILE else
                         "torch_hip.dll" if name in rocm.PROFILES else "torch_cuda.dll")
        if (mark.get("sha256") == [item.sha256 for item in packages]
                and site.resolve().is_relative_to(self.directory.resolve())
                and (site / "torch/lib" / torch_library).is_file()
                and (site / "torchvision/_C.pyd").is_file()
                and (name != xpu.PROFILE or (site / "Library/bin/sycl8.dll").is_file())):
            return site
        return None

    def selected(self):
        name = _read_json(self.preference).get("version")
        if isinstance(name, str) and self.installed(name) is not None:
            return name
        return "system" if self.system else None

    def configure_process(self, *, name=None):
        if not SUPPORTED:
            return
        if name is not None and not (name == "system" and self.system or self.installed(name) is not None):
            raise RuntimeError(f"The requested inference runtime is not installed: {name}")
        self.active = name if name is not None else self.selected()
        if self.active in rocm.PROFILES or (self.active == "system" and self.system.startswith("rocm")):
            os.environ.setdefault("MIOPEN_FIND_MODE", "FAST")
        site = self.installed(self.active)
        if site is not None:
            sys.path.insert(0, str(site))
            # Windows spawn and external Python workers need the same PyTorch build.
            os.environ["PYTHONPATH"] = str(site)
            if self.active == xpu.PROFILE:
                xpu.configure_process(site)

    def default_device(self):
        variant = self.system if self.active == "system" else self.active
        return variant if variant in ("cpu", xpu.PROFILE) else "cuda"

    def cuda_variant(self):
        variant = self.system if self.active == "system" else self.active
        return variant if variant in DRIVER_RECOMMENDED else None

    def summary(self):
        selected = self.selected() if SUPPORTED else None
        pending = SUPPORTED and selected != self.active
        return {"supported": SUPPORTED, "ready": not SUPPORTED or (self.active is not None and not pending),
                "active": self.active, "selected": selected, "pending_restart": pending,
                "label": runtime_label(self.system if self.active == "system" else self.active) if self.active else None}

    def snapshot(self):
        with self.lock:
            return dict(self.transfer)

    def details(self):
        data = self.summary()
        nvidia = nvidia_adapters() if SUPPORTED else ()
        amd = rocm.windows_adapters() if SUPPORTED else ()
        intel = xpu.windows_adapters() if SUPPORTED else ()
        cards = {card["capability"]: card for card in amd}
        no_nvidia = bool(amd or intel) and not nvidia
        preferred_amd = next((profile.arch for profile in rocm.PROFILES.values()
                              if not profile.integrated and profile.arch in cards and profile.arch not in rocm.RDNA1_ARCHES),
                             next((arch for arch in cards if arch not in rocm.RDNA1_ARCHES), None))
        suggested_cuda = recommended_cuda(nvidia)
        options = []
        if self.system:
            options.append({"id": "system", "label": runtime_label(self.system), "detail": "现有项目环境",
                            "detail_code": "runtime_existing",
                            "installed": True, "bytes": 0, "compatible": True})
        if SUPPORTED:
            for name in (*CUDA_OPTIONS, *LEGACY_CUDA_OPTIONS):
                installed = self.installed(name) is not None
                if name in LEGACY_CUDA_OPTIONS and not installed:
                    continue
                matching = [card for card in nvidia if cuda_compatible(name, compute_capability(card))]
                compatible = not no_nvidia and (bool(matching) or not nvidia)
                version = WHEELS[name][0].release
                models = {"cu121": "RTX 20 / 30 / 40", "cu128": "RTX 50"}.get(name)
                options.append({"id": name, "label": cuda_label(name), "installed": installed,
                                "bytes": sum(wheel.size for wheel in WHEELS[name]), "compatible": compatible,
                                "models": models, "note_code": "runtime_pascal_trial" if name == "cu121" else None,
                                "detail": "此运行环境需要 NVIDIA 显卡。" if no_nvidia else
                                          "此版本不适用于检测到的 NVIDIA 显卡。" if not compatible else
                                          f"PyTorch {version} · 建议驱动 {DRIVER_RECOMMENDED[name]} 或更新",
                                "detail_code": "runtime_cuda_required" if no_nvidia else
                                               "runtime_cuda_arch_mismatch" if not compatible else "runtime_driver",
                                "detail_params": {"version": version, "driver": DRIVER_RECOMMENDED[name]},
                                "recommended": compatible and name == suggested_cuda,
                                "source": f"https://download.pytorch.org/whl/{name}/torch/"})
            for profile in rocm.PROFILES.values():
                name = profile.id
                card, installed = cards.get(profile.arch), self.installed(name) is not None
                rdna1 = profile.arch in rocm.RDNA1_ARCHES
                detail_code = ("runtime_rocm_igpu" if profile.integrated else
                               "runtime_rocm_rdna1" if rdna1 else "runtime_rocm_experimental")
                detail = ("AMD 核显实验支持 · 与系统共享内存，建议先用短片测试。" if profile.integrated else
                          "实验支持 · Windows 11 · 使用该型号的 AMD 驱动，尚待实机验证。" if rdna1 else
                          f"实验支持 · Windows 11 / {profile.arch} · 建议 AMD 驱动 {rocm.DRIVER} 或更新。")
                options.append({"id": name, "label": runtime_label(name), "installed": installed,
                                "compatible": card is not None, "device": card["name"] if card else None,
                                "models": profile.models, "group": "igpu" if profile.integrated else "radeon",
                                "bytes": sum(asset.size for asset in profile.packages),
                                "detail": detail, "detail_code": detail_code,
                                "detail_params": {"arch": profile.arch, "driver": rocm.DRIVER},
                                "recommended": no_nvidia and profile.arch == preferred_amd,
                                "source": "https://rocm.docs.amd.com/en/docs-7.14.0/about/release-notes.html"})
            options.append({"id": xpu.PROFILE, "label": runtime_label(xpu.PROFILE),
                            "installed": self.installed(xpu.PROFILE) is not None, "compatible": bool(intel),
                            "group": "intel", "device": " / ".join(card["name"] for card in intel),
                            "models": "Intel Arc A / B · Core Ultra Arc",
                            "bytes": sum(asset.size for asset in xpu.PACKAGES),
                            "detail": "实验支持 · Windows 11 · 使用 Intel GPU 加速 SAM 图像编码。",
                            "detail_code": "runtime_xpu_experimental" if intel else "runtime_xpu_no_device",
                            "detail_params": {"driver": xpu.DRIVER},
                            "source": "https://www.intel.com/content/www/us/en/developer/articles/tool/pytorch-prerequisites-for-intel-gpu/2-12.html",
                            "recommended": bool(intel) and not (nvidia or amd)})
            options.append({"id": "cpu", "label": runtime_label("cpu"), "installed": self.installed("cpu") is not None,
                            "bytes": sum(wheel.size for wheel in WHEELS["cpu"]), "compatible": True,
                            "detail": "实验支持 · 无需独立显卡，运行较慢，暂不支持 HaMeR 手指精修。",
                            "detail_code": "runtime_cpu_experimental",
                            "source": "https://download.pytorch.org/whl/cpu/torch/"})
        gpus = [*nvidia, *amd, *intel]
        return {**data, "gpu": gpus[0] if gpus else None, "gpus": gpus, "options": options,
                "directory": str(self.directory), "download": self.snapshot()}

    @staticmethod
    def require_rocm_adapter(profile):
        if not any(card["capability"] == profile.arch for card in rocm.windows_adapters()):
            raise DownloadError(f"此 ROCm 配置需要 Windows 11 和 {profile.arch} 显卡。",
                                "runtime_rocm_gpu_required", arch=profile.arch)

    def select(self, name):
        if not SUPPORTED or not (name == "system" and self.system or self.installed(name) is not None):
            raise DownloadError("请先下载并安装此运行环境。", "runtime_not_installed")
        if (profile := rocm.PROFILES.get(name)) is not None:
            self.require_rocm_adapter(profile)
        elif name == xpu.PROFILE:
            self.require_xpu_adapter()
        elif name in DRIVER_RECOMMENDED and not self._compatible_cuda(name):
            raise DownloadError("此版本不适用于检测到的 NVIDIA 显卡。", "runtime_cuda_arch_mismatch")
        _atomic_json(self.preference, {"version": name})
        return self.summary()

    def start(self, name):
        if not SUPPORTED or name in LEGACY_CUDA_OPTIONS or not runtime_packages(name):
            raise DownloadError("此系统不支持所选运行环境。", "runtime_unsupported")
        if (profile := rocm.PROFILES.get(name)) is not None:
            self.require_rocm_adapter(profile)
        elif name == xpu.PROFILE:
            self.require_xpu_adapter()
        elif name != "cpu":
            if not self._compatible_cuda(name):
                raise DownloadError("此版本不适用于检测到的 NVIDIA 显卡。", "runtime_cuda_arch_mismatch")
        with self.lock:
            if self.transfer["status"] in ACTIVE_DOWNLOAD or self.installed(name) is not None:
                return dict(self.transfer)
            self.cancel.clear()
            self.transfer = {"id": uuid4().hex, "status": "running", "version": name, "phase": "download",
                             "file": "", "bytes": 0, "total": 0}
            Thread(target=self._run, args=(name,), name="flowhmr-runtime-download", daemon=True).start()
            return dict(self.transfer)

    @staticmethod
    def _compatible_cuda(name):
        cards = nvidia_adapters()
        if not cards:
            return not (rocm.windows_adapters() or xpu.windows_adapters())
        return any(cuda_compatible(name, compute_capability(card)) for card in cards)

    @staticmethod
    def require_xpu_adapter():
        if not xpu.windows_adapters():
            raise DownloadError("Intel XPU 需要 Windows 11 和受支持的 Intel Arc 显卡。",
                                "runtime_xpu_no_device")

    def stop(self, token):
        with self.lock:
            if self.transfer["id"] == token and self.transfer["status"] == "running":
                self.cancel.set()
                self.transfer["status"] = "stopping"
            return dict(self.transfer)

    def _run(self, name):
        def check():
            if self.cancel.is_set():
                raise DownloadCancelled()

        def progress(phase, filename, size, total):
            with self.lock:
                self.transfer.update(phase=phase, file=filename, bytes=size, total=total)

        try:
            self.install(name, progress, check)
            state = {"status": "done"}
        except DownloadCancelled:
            state = {"status": "cancelled"}
        except Exception as error:
            if self.logger:
                self.logger.exception("Failed to install Windows inference runtime %s", name)
            failure = describe_download_error(error, "运行环境")
            state = {"status": "failed", "error": str(failure), "error_code": failure.code,
                     "error_params": failure.params}
        with self.lock:
            self.transfer.update(state)

    def install(self, name, progress, check):
        amd = name in rocm.PROFILES
        intel = name == xpu.PROFILE
        folder = self.downloads / name
        if any(not path.resolve().is_relative_to(self.root) for path in (folder, self.directory)):
            raise DownloadError("运行环境目录必须位于 FlowHMR 文件夹内。", "transfer_unsafe_path")
        self.directory.mkdir(parents=True, exist_ok=True)
        folder.mkdir(parents=True, exist_ok=True)
        wheels = [(wheel, download_file(wheel, folder / wheel.relative, folder, progress, check,
                                       subject="运行环境", verify_existing=True)) for wheel in runtime_packages(name)]
        with TemporaryDirectory(prefix=".install-", dir=self.directory) as temporary:
            stage = Path(temporary)
            site = stage / "site-packages"
            for wheel, package in wheels:
                if amd:
                    rocm.install_package(package, site, progress, check)
                    continue
                if intel:
                    xpu.install_package(package, site, progress, check)
                    continue
                with ZipFile(package) as archive:
                    members = list(_wheel_members(archive, wheel))
                    total = sum(info.file_size for info, _ in members)
                    if shutil.disk_usage(stage).free < total + 64 * 1024 ** 2:
                        raise DownloadError("磁盘空间不足，无法解包运行环境。", "transfer_disk")
                    written = 0
                    progress("install", wheel.package, 0, total)
                    for info, relative in members:
                        check()
                        destination = site.joinpath(*relative.parts)
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        with archive.open(info) as source, destination.open("xb") as target:
                            while data := source.read(_CHUNK):
                                check()
                                target.write(data)
                                written += len(data)
                                progress("install", wheel.package, written, total)
            check()
            progress("test", "CPU" if name == "cpu" else "GPU", 0, 0)
            probe = self.root / "tools/windows" / ("probe_rocm.py" if amd else "probe_xpu.py" if intel else "probe_cuda.py")
            probe_args = [sys.executable, "-I", "-B", str(probe), str(site), name]
            if not (amd or intel):
                probe_args.extend(wheel.release for wheel, _ in wheels)
            result = subprocess.run(probe_args, cwd=self.root,
                                    capture_output=True, encoding="utf-8", errors="replace", timeout=100,
                                    creationflags=subprocess.CREATE_NO_WINDOW)
            if result.returncode:
                if self.logger:
                    self.logger.warning("Runtime probe %s: %s", name, (result.stderr or result.stdout)[-4000:])
                if name == "cpu":
                    raise DownloadError("CPU 运行环境验证失败，下载文件已保留。", "runtime_cpu_probe_failed")
                if intel:
                    raise DownloadError("Intel XPU 运算验证失败。请检查 Intel 驱动。下载文件已保留。",
                                        "runtime_xpu_probe_failed")
                raise DownloadError("GPU 验证失败。请检查显卡驱动及运行环境。下载文件已保留。", "runtime_probe_failed")
            check()
            _atomic_json(stage / "installed.json", {"sha256": [wheel.sha256 for wheel, _ in wheels]})
            destination = self.directory / name
            if destination.exists():
                raise DownloadError("目标版本目录已存在且未通过检查，请移走该目录后重试。", "runtime_directory_conflict")
            stage.rename(destination)
        for _, package in wheels:
            try:
                package.unlink()
            except OSError:
                pass  # The installed environment is valid even if a download is open elsewhere.


RUNTIME = RuntimeEnvironments(ROOT)
