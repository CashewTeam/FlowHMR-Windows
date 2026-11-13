"""Architecture-specific Windows ROCm packages, adapter detection and startup."""

import ctypes
from ctypes import wintypes
from dataclasses import dataclass
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
from uuid import UUID
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
_AMD_PCI = re.compile(r"VEN_1002&DEV_([0-9A-F]{4})(?:[^0-9A-F]|$)", re.I)
_DGPU_PCI = {
    "7550": "gfx1201", "7551": "gfx1201",  # Navi 48 (RX 9070 / AI PRO R9700)
    "7590": "gfx1200",  # Navi 44 (RX 9060)
    "744C": "gfx1100",  # Navi 31 (RX 7900)
    "747E": "gfx1101",  # Navi 32 (RX 7800 / 7700)
    "7480": "gfx1102",  # Navi 33 (RX 7600)
    **dict.fromkeys(("7310", "7312", "7318", "7319", "731A", "731B", "731E", "731F"), "gfx1010"),  # Navi 10
    "7360": "gfx1011", "7362": "gfx1011",  # Navi 12
    **dict.fromkeys(("7340", "7341", "7347", "734F"), "gfx1012"),  # Navi 14
}
RDNA1_ARCHES = frozenset(("gfx1010", "gfx1011", "gfx1012"))
_IGPU_PCI = {
    "13C0": "gfx1036", "164E": "gfx1036",  # Granite Ridge / Raphael
    "15BF": "gfx1103", "15C8": "gfx1103", "1900": "gfx1103", "1901": "gfx1103",  # Phoenix / Hawk Point
    "150E": "gfx1150", "1586": "gfx1151",  # Strix Point / Strix Halo
    "1114": "gfx1152", "1902": "gfx1153",  # Krackan Point
}
_IGPU_ARCHES = frozenset(_IGPU_PCI.values())
_CHUNK = 1 << 20

def _asset(name, size, digest):
    return Asset(name, size, digest, (WHEEL_INDEX + quote(name),))


def _assets(*files):
    return tuple(_asset(*file) for file in files)


# Exact Windows x64 CPython 3.10 artifacts from AMD. SDK core, shared
# libraries and device kernels are separate packages in the official index.
_SDK = _assets(
    ("rocm_sdk_core-7.14.0-py3-none-win_amd64.whl", 758744987,
     "e0d5650b9b6ff931496d3336eb72cb969fb3bc4e1966dc84b80b727af32a6bf0"),
    ("rocm_sdk_libraries-7.14.0-py3-none-win_amd64.whl", 115912167,
     "d03873d2879c000ae307866cc827fe76d6e9f50846fec76b7cb293202afdee56"),
)
_TORCH, _VISION = _assets(
    ("torch-2.12.0+rocm7.14.0-cp310-cp310-win_amd64.whl", 114047819,
     "3ed1ab7b273f123a376f1ed483ea6fe617f14a58d2365166a55d74751a417d39"),
    ("torchvision-0.27.0+rocm7.14.0-cp310-cp310-win_amd64.whl", 900770,
     "7f453f20b997f136033e60d3b82292eab5b66157a52f292dc5bd2e2a30140c65"),
)
_BOOTSTRAP = _assets(
    ("rocm_bootstrap-0.1.0-py3-none-any.whl", 27015,
     "33e3d4dad086b1f0e06bc049a618ca874d4fb72f758fff00676e23d2a644b652"),
    ("rocm-7.14.0.tar.gz", 24252,
     "77c622d8eef7bf7fa1af70d410a05a621abbd2baaf53e52ab268dc6d140e15b2"),
)
_GFX11 = _asset(f"amd_torch_device_gfx11-{TORCH_VERSION}-cp310-cp310-win_amd64.whl", 475620008,
                "144edc8500db57fb2b5eea88a5147962e8e1c4b9b749aca528542d21e5d0f1e4")
_GFX12 = _asset(f"amd_torch_device_gfx12_0-{TORCH_VERSION}-cp310-cp310-win_amd64.whl", 113459010,
                "5961c93ce0306f4e7cff8fab31db344df6d7bda20db3888ae8afcb6c473211c7")


@dataclass(frozen=True)
class Profile:
    arch: str
    integrated: bool
    packages: tuple[Asset, ...]
    models: str
    series: str | None

    @property
    def id(self):
        return f"rocm714-{self.arch}"


def _profile(arch, sdk_file, torch_file, vision_file, *, models, series=None, family=()):
    sdk = _asset(f"rocm_sdk_device_{arch}-{VERSION}-py3-none-win_amd64.whl", *sdk_file)
    torch = _asset(f"amd_torch_device_{arch}-{TORCH_VERSION}-cp310-cp310-win_amd64.whl", *torch_file)
    vision = _asset(f"amd_torchvision_device_{arch}-{VISION_VERSION}-cp310-cp310-win_amd64.whl", *vision_file)
    return Profile(arch, arch in _IGPU_ARCHES, (*_SDK, sdk, *family, torch, _TORCH, _VISION, vision, *_BOOTSTRAP),
                   models, series)


PROFILES = {profile.id: profile for profile in (
    _profile("gfx1201",
             (312289097, "b89e2600cd7000154e5ee6f951daaff6b3dd69ad78137847114e1c49de8e2bd9"),
             (51186860, "f1e069d1d4a9979aa83a91c119370363837071a1f96bf2458c42dd149b97bc83"),
             (133931, "070bf3ec7f6346f68ac57ad626914be55031b8c6ee002494331d164c7854cc95"),
             models="RX 9070 XT / 9070 / 9070 GRE", series="RX 9000", family=(_GFX12,)),
    _profile("gfx1200",
             (377173993, "e3a1f3d9cb8cc3a53cd1ecea52d826d81c23cbf8ba7dae559a91629c85d9d55c"),
             (51167527, "429b78cc3c02eeecfe58a91f2f7d23b25454f469049abc03bc811636ff76749b"),
             (133929, "6bfd5ef7f22eb2c5633b0b41c4eacbadc7c0297bed12ec0cb67000668990f1ad"),
             models="RX 9060 XT / 9060", series="RX 9000", family=(_GFX12,)),
    _profile("gfx1100",
             (179340237, "336775de86b441470735e7947db326be7f0c34a6a165ba0388de9505917fc233"),
             (50535709, "bf8e4144e4b4918253534a27754ef39bf04c02c7bcd5cac56689075c26bcfab0"),
             (140742, "2651fe1d1119a3981906bbe18e000b6697bfaedf1384a4a604baa4b295b37f23"),
             models="RX 7900 XTX / 7900 XT / 7900 GRE", series="RX 7000", family=(_GFX11,)),
    _profile("gfx1101",
             (176230178, "e6c3db7c575f8c3c7acc8046313dae1774a654043e7d705c20e9746ba65e9a12"),
             (50498703, "21d8d07cdf12113672627a08f65f83c267e58403039a0414cffbdbe894d2f3d5"),
             (140936, "3eec1cd5236e1480514d31e35c19a86c9974d9bbf33107fe3dc6bfa296e7adfb"),
             models="RX 7800 XT / 7700 XT / 7700", series="RX 7000", family=(_GFX11,)),
    _profile("gfx1102",
             (174225001, "c56183dcdc3ca90c551623f66a0418d15b7988c9c5eab2a3c3836620f6e7865f"),
             (50756966, "377d6eebe6911780ecdbc0f11da1963d1b2137afff3864c3315334a2f86a99a7"),
             (140317, "5fabe94d01c5272a2e7c67a44c392eb0dcaed33807d620a19007fa05c44174b2"),
             models="RX 7600 XT / 7600", series="RX 7000", family=(_GFX11,)),
    # TheRock publishes these RDNA 1 packages; they are outside the 7.14 product matrix.
    _profile("gfx1010",
             (47210847, "002117b2186c6116e0c5561334d4da62103a1460169ecc73f50dbcc630a95aa7"),
             (44832281, "5429b02a4fd9b772d8d6cfdb3e61e033464517b266edcccd81a80ed74dd8ec8b"),
             (116995, "8541e640b94462217b8f956c40d06f2e395a853dd7969290f2a0ccc030316de5"),
             models="RX 5700 XT / 5700 / 5600 XT", series="RX 5000"),
    _profile("gfx1012",
             (46860789, "af2865798b572996e3b79c8b8082193be5a32b33a6138bb95ee6a8ccc96d45b0"),
             (44832069, "4cec5ce501ffa0a5f689edb9369ee10a55a204c0434bca6d44c77d9590067550"),
             (116990, "581421c9ff6cea1f67d2bdc673f5e422018a66ae9cf227eddae77da08b900c31"),
             models="RX 5500 XT / 5500 / 5300", series="RX 5000"),
    _profile("gfx1011",
             (46862888, "e3670cc9b9bd806e3659471cd10549c21db0e5729b861104de51d9138862cb0b"),
             (44832161, "264ffc0804f9f04fbc00a9930092bc7b85c606ca294c3ca80c8ef4d69738b689"),
             (116993, "1570c73e9b38d8dbd4005d62808fadd114b0cbac1f98fbe5bb1061b7a52c8ddc"),
             models="Radeon Pro V520", series="Radeon Pro"),
    _profile("gfx1036",
             (51651289, "a82267fa67c7e0a7dde303d4feac3d3605576827d6c819c8693fa3a559135e8d"),
             (45016408, "c9ab97ade194a4a14f5e589bc2765b1c0d9f7ed92ca127f6a5e69a196c9544b5"),
             (118186, "a3005bba4e300f6d3f5590a9179fbe054abd61f1f3c4fe1dd93923bb7f479280"),
             models="Radeon Graphics"),
    _profile("gfx1103",
             (55135656, "c969bababa0b11cb6c0c88843e788a06c3895314f435910826d49d6ee4dec924"),
             (50707844, "d909c5a3f8a5a755c2c723991507fe624e73103c75c0f7e83a4761251213353a"),
             (140513, "43df31bdb4171a05799da564f5372898c70c4f82ea5984dc5c443e144fe509dc"),
             models="Radeon 740M / 760M / 780M", family=(_GFX11,)),
    _profile("gfx1150",
             (133885056, "9041380077c7d0a95f2445ea7653948a3c58f89833d48894db113053401cd790"),
             (50265847, "23c289482b369a8a2e8f0f579e576835edaa00ca3715779fe937f3ff01ea4abc"),
             (136675, "4751b184c233661fcec5bc9ae52a65b948e68196c5c5f7ca4408483fa46344fc"),
             models="Radeon 880M / 890M", family=(_GFX11,)),
    _profile("gfx1151",
             (138404766, "7e409fbec9d02a96e2fa454b88a3c664aaf2d2150d60b128d76a9e6007d917fc"),
             (50068739, "13d3618d467781eeabed647f12e9a29525b0ac133a96c82368a122a5ddf7ccf4"),
             (137145, "333ae3f5b4a10477ce5e4f9172cb0547658732ba371e873aadf1a267df54131d"),
             models="Radeon 8040S / 8050S / 8060S / 8065S", family=(_GFX11,)),
    _profile("gfx1152",
             (133784968, "a616f28e8feb387f6a8d0b4adc36cf3d156576b1e63546b0f1ddb039952d54f2"),
             (50265787, "4d8298803cebc75e79477dbeb240f557f5a2c8379045d98eb872128e7ffc3dbd"),
             (136656, "8605fec5bb4e8d75abde7d44d401468db179ffe81fce80632771af5e5bdc2c09"),
             models="Radeon 820M / 840M / 860M", family=(_GFX11,)),
    _profile("gfx1153",
             (133943011, "a1cd06119768144fa5664ac813d074b78961b54c2e1a49ea8f8f84e36ca02445"),
             (50265913, "379f85154f22b4da190d4ea55999266e2e9ea0d3a6ee356c88c0fc58bdab375e"),
             (136676, "becc8171a1afc2a2a27a2ff097adaa3728a717f27c7935cdb7f8304206b0d4e3"),
             models="Radeon 820M / 840M", family=(_GFX11,)),
)}
PACKAGES = PROFILES[PROFILE].packages  # Retain the legacy discrete slot's manifest order.


def windows_11():
    return sys.platform == "win32" and sys.getwindowsversion().build >= 22000


class _DisplayDevice(ctypes.Structure):
    _fields_ = [("cb", wintypes.DWORD), ("device_name", wintypes.WCHAR * 32),
                ("description", wintypes.WCHAR * 128), ("flags", wintypes.DWORD),
                ("hardware_id", wintypes.WCHAR * 128), ("key", wintypes.WCHAR * 128)]


class _DeviceInfo(ctypes.Structure):
    _fields_ = [("size", wintypes.DWORD), ("class_guid", ctypes.c_ubyte * 16),
                ("devinst", wintypes.DWORD), ("reserved", ctypes.c_size_t)]


def _amd_adapter(name, hardware_id, driver=""):
    match = _AMD_PCI.search(hardware_id or "")
    if not match:
        return None
    pci = match[1].upper()
    arch = _IGPU_PCI.get(pci) or _DGPU_PCI.get(pci)
    if arch is None and _SUPPORTED_NAME.search(name or ""):
        arch = ARCH
    if arch:
        return {"name": name or "AMD Radeon", "driver": driver or "", "capability": arch,
                "integrated": arch in _IGPU_ARCHES}
    return None


def _present_display_adapters():
    """SetupAPI includes enabled secondary adapters without an attached monitor."""
    import winreg

    api = ctypes.WinDLL("setupapi", use_last_error=True)
    w = wintypes
    info_ptr = ctypes.POINTER(_DeviceInfo)

    def bind(name, result, *args):
        call = getattr(api, name)
        call.restype, call.argtypes = result, args
        return call

    open_devices = bind("SetupDiGetClassDevsW", w.HANDLE, ctypes.c_void_p, w.LPCWSTR, w.HWND, w.DWORD)
    enumerate_devices = bind("SetupDiEnumDeviceInfo", w.BOOL, w.HANDLE, w.DWORD, info_ptr)
    instance_id = bind("SetupDiGetDeviceInstanceIdW", w.BOOL, w.HANDLE, info_ptr, w.LPWSTR, w.DWORD, ctypes.c_void_p)
    property_value = bind("SetupDiGetDeviceRegistryPropertyW", w.BOOL, w.HANDLE, info_ptr, w.DWORD,
                          ctypes.c_void_p, ctypes.c_void_p, w.DWORD, ctypes.c_void_p)
    destroy = bind("SetupDiDestroyDeviceInfoList", w.BOOL, w.HANDLE)
    devnode_status = ctypes.WinDLL("cfgmgr32").CM_Get_DevNode_Status
    devnode_status.argtypes = (ctypes.POINTER(w.ULONG), ctypes.POINTER(w.ULONG), w.DWORD, w.ULONG)
    devnode_status.restype = w.ULONG
    display_class = (ctypes.c_ubyte * 16).from_buffer_copy(UUID("4d36e968-e325-11ce-bfc1-08002be10318").bytes_le)
    devices = open_devices(display_class, None, None, 2)  # DIGCF_PRESENT
    if devices in (None, ctypes.c_void_p(-1).value):
        raise ctypes.WinError(ctypes.get_last_error())

    def get_property(info, prop):
        buf = ctypes.create_unicode_buffer(1024)
        if property_value(devices, info, prop, None, buf, ctypes.sizeof(buf), None):
            return buf.value
        return ""

    cards = []
    try:
        for index in range(64):
            info = _DeviceInfo(size=ctypes.sizeof(_DeviceInfo))
            if not enumerate_devices(devices, index, ctypes.byref(info)):
                error = ctypes.get_last_error()
                if error != 259:  # ERROR_NO_MORE_ITEMS
                    raise ctypes.WinError(error)
                break
            identifier = ctypes.create_unicode_buffer(1024)
            if not instance_id(devices, ctypes.byref(info), identifier, len(identifier), None):
                continue
            if not _AMD_PCI.search(identifier.value):
                continue
            status, problem = w.ULONG(), w.ULONG()
            if devnode_status(ctypes.byref(status), ctypes.byref(problem), info.devinst, 0) == 0:
                if not status.value & 8:  # DN_STARTED
                    continue
            name, driver = get_property(info, 12) or get_property(info, 0), ""  # Friendly name / description
            driver_key = get_property(info, 9)  # SPDRP_DRIVER
            if driver_key:
                try:
                    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                                       rf"SYSTEM\CurrentControlSet\Control\Class\{driver_key}") as key:
                        name = winreg.QueryValueEx(key, "DriverDesc")[0] or name
                        driver = winreg.QueryValueEx(key, "DriverVersion")[0]
                except OSError:
                    pass
            card = _amd_adapter(name, identifier.value, driver)
            if card:
                cards.append(card)
    finally:
        destroy(devices)
    return tuple(cards)


def windows_adapters():
    """List supported AMD adapters without importing PyTorch."""
    if not windows_11():
        return ()
    try:
        return _present_display_adapters()
    except (OSError, AttributeError):
        return _fallback_windows_adapters()


@lru_cache(maxsize=1)
def _fallback_windows_adapters():
    cards = []
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
                cards.append(adapter)
    except OSError:
        pass
    if cards:
        return tuple(cards)
    # Fall back to CIM if neither SetupAPI nor User32 returned the inventory.
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
                cards.append(adapter)
    except (OSError, AttributeError, ValueError, subprocess.SubprocessError):
        pass
    return tuple(cards)


def configure_windows_hip(torch, *, device, attention=False):
    """Use native PyTorch fallbacks for MIOpen BatchNorm and keep SDPA choices."""
    if sys.platform != "win32" or not getattr(torch.version, "hip", None):
        return
    os.environ.setdefault("MIOPEN_FIND_MODE", "FAST")
    torch.backends.cudnn.enabled = False
    if attention:
        # AMD's RDNA 1 Windows wheels lack fused attention for these targets.
        fused = gpu_arch(torch.cuda.get_device_properties(device)) not in RDNA1_ARCHES
        torch.backends.cuda.enable_flash_sdp(fused)
        torch.backends.cuda.enable_mem_efficient_sdp(fused)
        torch.backends.cuda.enable_math_sdp(True)


def gpu_arch(props):
    return str(getattr(props, "gcnArchName", "") or "").split(":", 1)[0]


def _integrated(props):
    return gpu_arch(props) in _IGPU_ARCHES or bool(getattr(props, "is_integrated", False))


def uses_shared_memory(torch, device):
    return (sys.platform == "win32" and bool(getattr(torch.version, "hip", None))
            and _integrated(torch.cuda.get_device_properties(device)))


def sam_hybrid_candidate(profile_id):
    """Check hardware eligibility for UI without importing the model runtime."""
    profile = PROFILES.get(profile_id)
    if profile is None or not profile.integrated or not windows_11():
        return False
    try:
        # Win32 exposes AVX512F; the worker verifies BF16 and oneDNN before loading weights.
        has_avx512 = ctypes.WinDLL("kernel32").IsProcessorFeaturePresent(41)
    except (AttributeError, OSError):
        return False
    return bool(has_avx512 and any(card["capability"] == profile.arch for card in windows_adapters()))


def gpu_ordinal(torch, arch=ARCH, *, ordinal=None):
    """Select a matching HIP GPU and respect the user's visibility mask."""
    candidates = []
    for index in range(torch.cuda.device_count()):
        if ordinal is not None and ordinal != index:
            continue
        try:
            props = torch.cuda.get_device_properties(index)
        except RuntimeError:
            continue
        actual = gpu_arch(props)
        # New ROCm versions expose the architecture. Older Windows builds
        # may only report the adapter name.
        if (arch is None or actual == arch or
                (not actual and arch == ARCH and _SUPPORTED_NAME.search(props.name))):
            candidates.append((not _integrated(props), props.total_memory, -index))
    if not candidates:
        raise RuntimeError(f"No visible ROCm GPU for {arch or 'this runtime'}")
    return -max(candidates)[2]


def supports_bf16(torch, device):
    if getattr(torch.version, "hip", None):
        # Windows HIP can report BF16 support for devices that should use FP16.
        if sys.platform == "win32":
            arch = gpu_arch(torch.cuda.get_device_properties(device))
            if arch == "gfx1036" or arch in RDNA1_ARCHES:
                return False
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
