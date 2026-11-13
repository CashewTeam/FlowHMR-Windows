"""Memory headroom reported by Windows for the current process."""

import ctypes as ct
from ctypes import wintypes as wt
from uuid import UUID


class _MemoryStatus(ct.Structure):
    _fields_ = [("length", wt.DWORD), ("load", wt.DWORD)] + [
        (name, ct.c_uint64) for name in (
            "total_physical", "available_physical", "commit_limit", "available_commit",
            "total_virtual", "available_virtual", "available_extended_virtual",
        )
    ]


def system_memory():
    status = _MemoryStatus()
    status.length = ct.sizeof(status)
    if not ct.windll.kernel32.GlobalMemoryStatusEx(ct.byref(status)):
        raise OSError("GlobalMemoryStatusEx failed")
    return min(status.available_physical, status.available_commit), status.total_physical


class _Luid(ct.Structure):
    _fields_ = [("low", wt.DWORD), ("high", wt.LONG)]


class _VideoMemory(ct.Structure):
    _fields_ = [
        ("budget", ct.c_uint64), ("usage", ct.c_uint64),
        ("available_reservation", ct.c_uint64), ("reservation", ct.c_uint64),
    ]


def _method(interface, slot, result, *arguments):
    vtable = ct.cast(interface, ct.POINTER(ct.POINTER(ct.c_void_p))).contents
    return ct.WINFUNCTYPE(result, ct.c_void_p, *arguments)(vtable[slot])


def _release(interface):
    _method(interface, 2, wt.ULONG)(interface)  # IUnknown::Release


def _gpu_luid(ordinal, hip):
    luid = _Luid()
    nodes = wt.UINT()
    if hip:
        # Use the HIP library PyTorch has already initialized in this process.
        get_module = ct.windll.kernel32.GetModuleHandleW
        get_module.argtypes = [wt.LPCWSTR]
        get_module.restype = wt.HMODULE
        for name in ("amdhip64_7.dll", "amdhip64_6.dll", "amdhip64.dll"):
            if handle := get_module(name):
                get_luid = ct.CDLL(name, handle=handle).hipDeviceGetLuid
                break
        else:
            raise OSError("HIP runtime library is unavailable")
        device = ordinal
    else:
        driver = ct.WinDLL("nvcuda.dll", winmode=0x800)  # LOAD_LIBRARY_SEARCH_SYSTEM32
        device = ct.c_int()
        if driver.cuDeviceGet(ct.byref(device), ordinal):
            raise OSError("CUDA device is unavailable")
        get_luid = driver.cuDeviceGetLuid
    get_luid.argtypes = [ct.POINTER(_Luid), ct.POINTER(wt.UINT), ct.c_int]
    get_luid.restype = ct.c_int
    if get_luid(ct.byref(luid), ct.byref(nodes), device) or not nodes.value:
        raise OSError("GPU has no DXGI adapter identifier")
    return luid, (nodes.value & -nodes.value).bit_length() - 1


class VideoMemoryBudget:
    """Query the WDDM budget for the exact CUDA/HIP adapter in use."""

    def __init__(self, ordinal, hip):
        luid, self.node = _gpu_luid(ordinal, hip)
        dxgi = ct.WinDLL("dxgi.dll", winmode=0x800)
        factory_iid = ct.create_string_buffer(UUID("1bc6ea02-ef36-464f-bf0c-21ca39e5168a").bytes_le)
        adapter_iid = ct.create_string_buffer(UUID("645967a4-1392-4310-a798-8053ce3e93fd").bytes_le)
        factory = ct.c_void_p()
        create = dxgi.CreateDXGIFactory1
        create.argtypes = [ct.c_void_p, ct.POINTER(ct.c_void_p)]
        create.restype = ct.HRESULT
        if create(factory_iid, ct.byref(factory)) < 0:
            raise OSError("Cannot create a DXGI factory")
        self.adapter = ct.c_void_p()
        try:
            lookup = _method(factory, 26, ct.HRESULT, _Luid, ct.c_void_p, ct.POINTER(ct.c_void_p))
            if lookup(factory, luid, adapter_iid, ct.byref(self.adapter)) < 0:
                raise OSError("Cannot match the compute device to a DXGI adapter")
        finally:
            _release(factory)
        self.query = _method(self.adapter, 14, ct.HRESULT, wt.UINT, ct.c_int, ct.POINTER(_VideoMemory))

    def local(self):
        info = _VideoMemory()
        if self.query(self.adapter, self.node, 0, ct.byref(info)) < 0 or not info.budget:
            return None
        return info.budget, info.usage

    def close(self):
        _release(self.adapter)
