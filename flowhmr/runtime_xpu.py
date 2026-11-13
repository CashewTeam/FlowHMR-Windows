"""Official PyTorch XPU wheels for an isolated Windows 11 runtime slot."""

import ctypes
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
import os
import re
import shutil
import stat
import sys
from uuid import UUID
from zipfile import ZipFile

from .model_downloads import DownloadError


PROFILE = "xpu"
TORCH_VERSION = "2.12.0+xpu"
VISION_VERSION = "0.27.0+xpu"
DRIVER = "32.0.101.8724"
DRIVER_URL = "https://www.intel.com/content/www/us/en/download/785597/intel-arc-graphics-windows.html"
_CHUNK = 1024 * 1024


@dataclass(frozen=True)
class Wheel:
    relative: str
    size: int
    sha256: str
    urls: tuple[str, ...]

    @property
    def package(self):
        return self.relative.partition("-")[0]


def _torch(filename, size, digest, path):
    return Wheel(filename, size, digest, tuple(f"https://{host}{path}" for host in
                                              ("download-r2.pytorch.org", "download.pytorch.org")))


def _pypi(filename, size, digest, path):
    return Wheel(filename, size, digest, (f"https://files.pythonhosted.org/packages/{path}/{filename}",))


# CPython 3.10 / win_amd64 from the official PyTorch XPU index and PyPI.
# The portable base already provides Torch's platform-independent dependencies.
PACKAGES = (
    _torch("torch-2.12.0+xpu-cp310-cp310-win_amd64.whl", 746548922,
           "b9779b71457b5a916ae052ed2467c10273cae4862d469b191359173b2038c53e", "/whl/xpu/torch-2.12.0%2Bxpu-cp310-cp310-win_amd64.whl"),
    _torch("torchvision-0.27.0+xpu-cp310-cp310-win_amd64.whl", 3435606,
           "65dbb041057dddfe369f29cfaab63f75563621779a23a7b1e2c0ff8a84d4376a", "/whl/xpu/torchvision-0.27.0%2Bxpu-cp310-cp310-win_amd64.whl"),
    _pypi("dpcpp_cpp_rt-2025.3.2-py2.py3-none-win_amd64.whl", 58378,
          "3a8645e0ae570f54e2514c92fbfb57c0759158afe576b8c966ba06dc90729ca5", "6e/8b/9c9bf12d09033de5721070701b859917c0d3a2da214467caf1378a38df98"),
    _pypi("intel_cmplr_lib_rt-2025.3.2-py2.py3-none-win_amd64.whl", 17741766,
          "89bba5899d0e057f0ecbc55768558e55bd7a3740950d0e4d0b01d25d8b62b020", "de/02/46ec0e8aad41a7d95ba62dc65238c88fc302120c9a143c211990eef9c0b2"),
    _pypi("intel_cmplr_lib_ur-2025.3.2-py2.py3-none-win_amd64.whl", 1253478,
          "28dcf7e7401e54f16c4ece55c07029d9ef2b8fe8602b6530f0bcfec906c63823", "b5/ef/a6de04eb38a359004e9b6d8fd8bbbaedbc5fce1379e16bd26374aade67db"),
    _pypi("intel_cmplr_lic_rt-2025.3.2-py2.py3-none-win_amd64.whl", 49549,
          "b4d79398df82325dbd121ad7239735117e541b768c45ae4ad29fb40b07531d7e", "fd/9f/4d9b6e8d93cc0685323b617023716132c6b65796f4103e167a296973db1f"),
    _pypi("intel_opencl_rt-2025.3.2-py2.py3-none-win_amd64.whl", 109882206,
          "fa0df93b218eb1b690729ac2b842aba491648b52556d9d520ac88e0c73a9f8e6", "fd/f4/3c859afc3965fd4ccd5d32e979fae446b1f9c7388f95f23eef6bd8d57dc5"),
    _pypi("intel_openmp-2025.3.2-py2.py3-none-win_amd64.whl", 32087876,
          "8950bb59ffd146232aaf5c0d4272acdfcbe251f5982b83b329dab2e67069dc11", "52/e7/31d84b2c547a9319c2ad5dc744ca0b45e23ddf5a6959e96718d3c93d963a"),
    _pypi("intel_pti-0.16.0-py2.py3-none-win_amd64.whl", 662771,
          "5f50056cea4ef1dbfb0a52c96feab2137e9fc2fbd742e8d4081adfb467d221be", "dd/16/a100ed54b8d2f91a62bfac325b09276bf38602ae9676c07cc657e05a0328"),
    _pypi("intel_sycl_rt-2025.3.2-py2.py3-none-win_amd64.whl", 49654161,
          "c3a38e2ec1f15417c77f67ece1e8ebb0aef81ffab946e0bf8b1aaa2052e700bb", "a4/ac/82eccd64c17cfac23e265af44400897f1cb30bd5c0830099a39c8425a293"),
    _pypi("mkl-2025.3.1-py2.py3-none-win_amd64.whl", 155530417,
          "74084f58784c4cde1dc3284bdf3a19df30fbfe11416567e4ba6419d3bd9ce33e", "99/c2/97c8df6e8c0663903ab7b0fe4d9529e6a75ffeeeef7b1727bc14996d389b"),
    _pypi("onemkl_license-2025.3.1-py2.py3-none-win_amd64.whl", 58959,
          "9fa45b274f05a61797f9ec3166b136124250ec7c9c91dedb87f3a926bfed9a54", "1b/7f/732bd62f4ce4ddcbf97003efa856fcafc99c29e6b05deb0c1bb9cb85bdc4"),
    _pypi("onemkl_sycl_blas-2025.3.1-py2.py3-none-win_amd64.whl", 17500055,
          "8a094fb2d6f393e6beb030e507475cdb704178ac7afec80b634a5d1eb848b557", "e1/4b/1c60d831fb54c13fead3bb8e98d44721059b9bbad44184a507c42589234f"),
    _pypi("onemkl_sycl_dft-2025.3.1-py2.py3-none-win_amd64.whl", 6075958,
          "720d31ef23d5fe15355e6b95434244b27e254f817c45c89057f576a8a3e087a1", "17/e9/2a20a5130f4ec203ee62fdd7adbaacd413f22729522988d112a48a1f7f11"),
    _pypi("onemkl_sycl_lapack-2025.3.1-py2.py3-none-win_amd64.whl", 7588219,
          "b4df2a705422a7c27d5ad35ea853a26103a39dbfc1fe4c5180c4d7b39ba34e7e", "80/33/3aef7852a99a08ec6a6535268f63e600e998b011bffafa5cd6205050b46f"),
    _pypi("onemkl_sycl_rng-2025.3.1-py2.py3-none-win_amd64.whl", 16292810,
          "fe9bf5b9db1bb72578df997beb7e8ff7e4c0345b692fe947ee4a0af284709006", "81/57/d9e2941e40cdcbfc6a6dbd6c1184d4be5138965e63f978c8a595dcb2ce65"),
    _pypi("onemkl_sycl_sparse-2025.3.1-py2.py3-none-win_amd64.whl", 17905264,
          "994969efc1a4ee0483466ce0b75c510df4c5ffc89a6a12a0e878d5536ce29c69", "58/88/970baf908e8be5d5d7d0ed7e86863b350f1419cf9b6a9bd015a020e03bf2"),
    _pypi("tbb-2022.3.1-py3-none-win_amd64.whl", 422729,
          "8187e4c50a77c75c51ca9395febddd3a67d88dcb4cc537947d68d244925710f7", "68/ef/d01ec56a854949efc727c8cac8941325a2020f345a786e64ca6ad3a05611"),
    _pypi("tcmlib-1.4.1-py2.py3-none-win_amd64.whl", 370347,
          "5202a1fd8541182fbd4ef72848784e4bf94340152d7ddff33d401d30930fa53c", "eb/4d/2b1d55dba475f602197e284b9a5e5460139a9c5ef2bfb63c8d2a1fafc163"),
    _torch("triton_xpu-3.7.1-cp310-cp310-win_amd64.whl", 376799883,
           "0d7730651c3e52fbf3a430cc201455f0c6600dc72e681aec495f131ea44f341a", "/whl/triton_xpu-3.7.1-cp310-cp310-win_amd64.whl"),
    _pypi("umf-1.0.3-py2.py3-none-win_amd64.whl", 249098,
          "2182a623433329bd53863ac3b15a4af253d0eda62c603dc7c4e2bdce78658270", "97/49/23b714e4ca9655f04d5b958ca8b4ef8361e10743581a637f141755b9f4fb"),
    _pypi("pyelftools-0.33-py3-none-any.whl", 201178,
          "f215ad5f47d3f1373a21496a6c9e0707c622840d0622f23ff7ce08678b020036", "46/2a/f9697576603dae937727827505a6126a066affb227034e77e6f9068910da"),
)

# Intel's validated Windows 11 product families for PyTorch 2.12:
# https://www.intel.com/content/www/us/en/developer/articles/tool/pytorch-prerequisites-for-intel-gpu/2-12.html
# Device IDs: https://github.com/intel/compute-runtime/blob/master/shared/source/dll/devices/devices_base.inl
_DISCRETE = {
    **dict.fromkeys((0x5690, 0x5691, 0x5692, 0x5693, 0x5694, 0x5696, 0x5697,
                     0x56A0, 0x56A1, 0x56A2, 0x56A5, 0x56A6), "Arc A"),
    **dict.fromkeys((0xE209, 0xE20B, 0xE20C), "Arc B"),
}
_INTEGRATED = {0x7D55: "Meteor Lake-H", 0x7D51: "Arrow Lake-H", 0x64A0: "Lunar Lake",
               **dict.fromkeys((0xB080, 0xB081, 0xB082, 0xB083), "Panther Lake")}
_INTEL_PCI = re.compile(r"^PCI\\VEN_8086&DEV_([0-9A-F]{4})(?=&|\\|$)", re.IGNORECASE)
_DLL_HANDLES = {}


def windows_11():
    return sys.platform == "win32" and sys.getwindowsversion().build >= 22000


def _adapter(name, hardware_id, driver=""):
    match = _INTEL_PCI.match(hardware_id or "")
    if match:
        pci = int(match[1], 16)
        family = _DISCRETE.get(pci) or _INTEGRATED.get(pci)
        if family:
            return {"name": name or f"Intel {family}", "driver": driver or "", "capability": family,
                    "integrated": pci in _INTEGRATED, "pci_id": f"{pci:04X}"}
    return None


class _DeviceInfo(ctypes.Structure):
    _fields_ = [("size", wintypes.DWORD), ("class_guid", ctypes.c_ubyte * 16),
                ("devinst", wintypes.DWORD), ("reserved", ctypes.c_size_t)]


def _present_display_adapters():
    """Enumerate started display adapters, including cards with no monitor attached."""
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
    prop = bind("SetupDiGetDeviceRegistryPropertyW", w.BOOL, w.HANDLE, info_ptr, w.DWORD,
                ctypes.c_void_p, ctypes.c_void_p, w.DWORD, ctypes.c_void_p)
    destroy = bind("SetupDiDestroyDeviceInfoList", w.BOOL, w.HANDLE)
    status_of = ctypes.WinDLL("cfgmgr32").CM_Get_DevNode_Status
    status_of.argtypes = (ctypes.POINTER(w.ULONG), ctypes.POINTER(w.ULONG), w.DWORD, w.ULONG)
    status_of.restype = w.ULONG
    display_class = (ctypes.c_ubyte * 16).from_buffer_copy(UUID("4d36e968-e325-11ce-bfc1-08002be10318").bytes_le)
    devices = open_devices(display_class, None, None, 2)  # DIGCF_PRESENT
    if devices in (None, ctypes.c_void_p(-1).value):
        raise ctypes.WinError(ctypes.get_last_error())

    def get_property(info, key):
        buf = ctypes.create_unicode_buffer(1024)
        return buf.value if prop(devices, info, key, None, buf, ctypes.sizeof(buf), None) else ""

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
            if not instance_id(devices, info, identifier, len(identifier), None) or not _adapter("", identifier.value):
                continue
            status, problem = w.ULONG(), w.ULONG()
            if status_of(ctypes.byref(status), ctypes.byref(problem), info.devinst, 0) == 0 and not status.value & 8:
                continue  # DN_STARTED
            name = get_property(info, 12) or get_property(info, 0)  # Friendly name or description
            driver, driver_key = "", get_property(info, 9)
            if driver_key:
                try:
                    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                                       rf"SYSTEM\CurrentControlSet\Control\Class\{driver_key}") as key:
                        name = winreg.QueryValueEx(key, "DriverDesc")[0] or name
                        driver = winreg.QueryValueEx(key, "DriverVersion")[0]
                except OSError:
                    pass
            cards.append(_adapter(name, identifier.value, driver))
    finally:
        destroy(devices)
    return tuple(cards)


def windows_adapters():
    """List eligible Intel cards without importing or initializing PyTorch."""
    if not windows_11():
        return ()
    try:
        return _present_display_adapters()
    except (OSError, AttributeError):
        return ()


def is_integrated(torch, device):
    properties = torch.xpu.get_device_properties(device)
    # PyTorch 2.12 provides the PCI device ID. The dedicated property appeared later.
    return bool(getattr(properties, "is_integrated_gpu", False) or properties.device_id in _INTEGRATED)


def xpu_ordinal(torch, ordinal=None):
    """Choose a supported visible XPU, preferring a discrete card with more memory."""
    count = torch.xpu.device_count() if getattr(torch.version, "xpu", None) else 0
    eligible = []
    for index in range(count) if ordinal is None else (ordinal,):
        if 0 <= index < count:
            prop = torch.xpu.get_device_properties(index)
            if prop.device_id in _DISCRETE or prop.device_id in _INTEGRATED:
                eligible.append((prop.device_id in _DISCRETE, prop.total_memory, -index))
    if not eligible:
        raise RuntimeError("No supported Intel XPU device is visible to PyTorch. Check the Intel graphics driver.")
    return -max(eligible)[2]


def configure_process(site):
    """Register oneAPI libraries for this process without touching system settings."""
    site = Path(site).resolve()
    dll_path = (site / "Library/bin").resolve(strict=True)
    if not dll_path.is_relative_to(site):
        raise RuntimeError("Intel XPU runtime library directory is outside its slot")
    path = str(dll_path)
    if path not in _DLL_HANDLES:
        _DLL_HANDLES[path] = os.add_dll_directory(path)
    paths = os.environ.get("PATH", "").split(os.pathsep)
    os.environ["PATH"] = os.pathsep.join([path, *(p for p in paths if p and os.path.normcase(p) != os.path.normcase(path))])


def _members(archive, distribution):
    allowed = {"torch": {"torch", "torchgen", "functorch"}, "torchvision": {"torchvision", "torchvision.libs"},
               "triton_xpu": {"triton"}, "pyelftools": {"elftools"}}.get(distribution.partition("-")[0], set())
    for info in archive.infolist():
        path = PurePosixPath(info.filename)
        parts = path.parts
        if (not parts or path.is_absolute() or ".." in parts or "\\" in info.orig_filename or ":" in info.orig_filename
                or "\0" in info.orig_filename or stat.S_ISLNK(info.external_attr >> 16)
                or any(p.endswith((" ", ".")) or PureWindowsPath(p).is_reserved() for p in parts)):
            raise DownloadError("Intel XPU 包中有无效路径。", "transfer_unsafe_path")
        if (info.is_dir() or path.suffix.lower() in {".lib", ".pdb", ".ilk"}
                or parts[:2] in {("torch", "include"), ("torch", "share")}):
            continue
        if parts[0] in allowed or parts[0] == distribution + ".dist-info":
            yield info, path
        elif parts[:2] == (distribution + ".data", "data"):
            target = parts[2:]
            if target[:2] == ("Library", "bin"):
                # Torch supplies these itself. Keep one copy in the DLL search path.
                if path.name.lower() not in {"libiomp5md.dll", "libiompstubs5md.dll"}:
                    yield info, PurePosixPath(*target)
            elif (target[:2] in {("share", "doc"), ("etc", "OpenCL")}
                  or target[:3] == ("Library", "share", "doc") or target[:1] == ("licensing",)):
                # Keep each dependency's notices, without running its installation scripts.
                yield info, PurePosixPath("wheel-data", distribution, *target)


def install_package(package, site, progress, check):
    """Extract a checksum-verified wheel into a new runtime slot without running pip."""
    package, site = Path(package), Path(site)
    distribution = package.name.rsplit("-", 3)[0]
    site.mkdir(parents=True, exist_ok=True)
    with ZipFile(package) as archive:
        members = list(_members(archive, distribution))
        total = sum(info.file_size for info, _ in members)
        if not total:
            raise DownloadError("Intel XPU 包中没有运行文件。", "transfer_size")
        if shutil.disk_usage(site).free < total + 64 * 1024 ** 2:
            raise DownloadError("磁盘空间不足，无法解包 Intel XPU。", "transfer_disk")
        written = 0
        progress("install", package.name, written, total)
        for info, relative in members:
            check()
            destination = site.joinpath(*relative.parts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            try:
                output = destination.open("xb")
            except FileExistsError as error:
                raise DownloadError("Intel XPU 包包含冲突的文件。", "runtime_directory_conflict") from error
            with output, archive.open(info) as source:
                while data := source.read(_CHUNK):
                    check()
                    output.write(data)
                    written += len(data)
                    progress("install", package.name, written, total)
