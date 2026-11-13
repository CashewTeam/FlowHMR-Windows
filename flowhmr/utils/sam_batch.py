"""Adapt SAM's video batches to the worker's current memory headroom."""

import sys
from contextlib import suppress
from dataclasses import dataclass

import psutil
import torch

from flowhmr.runtime_rocm import uses_shared_memory

from .runtime_capabilities import InferenceRAMError, SAMMemoryError, SharedGPUMemoryError


MIB = 1024 ** 2


def system_memory():
    if sys.platform == "win32":
        from .windows_memory import system_memory as windows_memory
        with suppress(OSError):
            return windows_memory()
    memory = psutil.virtual_memory()
    return memory.available, memory.total


def ram_reserve(total, *, shared_gpu=False):
    if shared_gpu:
        return max(2 * 1024 * MIB, min(4 * 1024 * MIB, total // 8))
    return max(512 * MIB, min(2 * 1024 * MIB, total // 20))


def ram_room(*, shared_gpu=False):
    free, total = system_memory()
    return max(0, free - ram_reserve(total, shared_gpu=shared_gpu))


def shared_ram_room():
    return ram_room(shared_gpu=True)


def require_shared_ram(needed):
    available = shared_ram_room()
    if available < needed:
        raise SharedGPUMemoryError(available, needed)


def require_inference_ram(needed):
    available = ram_room()
    if available < needed:
        raise InferenceRAMError(available, needed)


def memory_error_resource(error):
    if isinstance(error, SAMMemoryError):
        return error.resource
    message = str(error).lower()
    if (isinstance(error, MemoryError) or "defaultcpuallocator" in message or
            ("opencv" in message and "insufficient memory" in message) or
            "winerror 1455" in message):
        return "ram"
    if isinstance(error, torch.cuda.OutOfMemoryError) or any(phrase in message for phrase in (
        "cuda out of memory", "cuda error: out of memory", "cuda driver error: out of memory",
        "hip out of memory", "hip error: out of memory", "hiperroroutofmemory",
        "cublas_status_alloc_failed", "cudnn_status_alloc_failed", "hipblas_status_alloc_failed",
        "rocblas_status_memory_error", "miopenstatusallocfailed", "miopen_status_alloc_failed",
        "xpu out of memory", "ur_result_error_out_of_device_memory", "ze_result_error_out_of_device_memory",
    )):
        return "vram"
    return None


@dataclass
class _Memory:
    ram_free: int
    ram_room: int
    process_ram: int
    process_ram_peak: int
    vram_free: int = 0
    vram_total: int = 0
    vram_room: int = 0
    allocated: int = 0
    reserved: int = 0
    driver_budget: int = 0
    driver_usage: int = 0
    vram_unreported: bool = False


class SAMBatchPlanner:
    """Grow only from a successful batch and shrink before allocating when possible."""

    def __init__(self, device, frame_bytes, *, host_processing=False):
        self.device = device
        self.accelerator = getattr(torch, device.type) if device.type in ("cuda", "xpu") else None
        self.gpu = self.accelerator is not None
        self.host_processing = host_processing
        self.frame_bytes = frame_bytes
        self.process = psutil.Process()
        self.driver = None
        self.hip_on_windows = sys.platform == "win32" and bool(getattr(torch.version, "hip", None))
        if device.type == "xpu":
            from flowhmr.runtime_xpu import is_integrated

            self.shared_gpu = is_integrated(torch, device)
        else:
            self.shared_gpu = self.gpu and uses_shared_memory(torch, device)
        if device.type == "cuda" and sys.platform == "win32" and not self.shared_gpu:
            from .windows_memory import VideoMemoryBudget
            with suppress(AttributeError, OSError):
                ordinal = device.index if device.index is not None else torch.cuda.current_device()
                self.driver = VideoMemoryBudget(ordinal, self.hip_on_windows)
        self.last_good = 0
        self.seen_hands = False
        self.vram_per_crop = 0
        self.vram_per_frame = 0
        self.ram_per_frame = 0
        self.retry_limit = None
        self.retry_size = None
        self.low_memory = False
        self.info = {"requested": None, "actual": None, "reused": False, "retries": 0, "adaptive": True}
        if self.shared_gpu:
            self.info["shared_memory"] = True

    def close(self):
        if self.driver is not None:
            self.driver.close()
            self.driver = None

    def before_batch(self):
        memory = self.snapshot()
        cached = memory.reserved - memory.allocated
        if cached >= 256 * MIB and (memory.vram_room < cached or self.shared_gpu and memory.ram_room < cached):
            # The OS has already squeezed the budget. Return spare cache
            # before assuming its blocks are still resident and reusable.
            self.release_cache()
            return self.snapshot()
        return memory

    def snapshot(self):
        free, total = system_memory()
        process = self.process.memory_info()
        resident = max(process.rss, getattr(process, "private", 0))
        peak = max(resident, getattr(process, "peak_wset", 0), getattr(process, "peak_pagefile", 0))
        memory = _Memory(free, free - ram_reserve(total, shared_gpu=self.shared_gpu), resident, peak)
        if self.gpu:
            acc = self.accelerator
            memory.allocated = acc.memory_allocated(self.device)
            memory.reserved = acc.memory_reserved(self.device)
            try:
                free, total = acc.mem_get_info(self.device)
            except (AttributeError, RuntimeError):
                if self.device.type != "xpu":
                    raise
                # Some integrated drivers expose allocator usage but no free VRAM.
                # Budget shared RAM; on discrete cards keep auto to one frame.
                memory.vram_unreported = True
                if self.shared_gpu:
                    memory.vram_room = max(0, memory.ram_room) + max(0, memory.reserved - memory.allocated)
                return memory
            if self.device.type == "xpu" and total <= 0:
                memory.vram_unreported = True
                if self.shared_gpu:
                    memory.vram_room = max(0, memory.ram_room) + max(0, memory.reserved - memory.allocated)
                return memory
            memory.vram_free, memory.vram_total = free, total
            budget = self.driver.local() if self.driver is not None else None
            if budget is not None:
                memory.driver_budget, memory.driver_usage = budget
                free = min(free, memory.driver_budget - memory.driver_usage)
            reserve = max(768 * MIB, min(2 * 1024 * MIB, total // 12))
            if self.hip_on_windows and budget is None:
                reserve *= 2  # Keep more headroom without the Windows driver budget.
            memory.vram_room = free + max(0, memory.reserved - memory.allocated) - reserve
        return memory

    def choose(self, memory, remaining, pending_output, hand_crops):
        # A batch of one is also a real output frame. Never allocate separate
        # calibration tensors or jump to a large untested batch.
        maximum = min(remaining, self.last_good + max(1, self.last_good // 2))
        if self.retry_limit is not None:
            maximum = min(maximum, self.retry_limit)
        if self.retry_size is not None:
            maximum = min(maximum, self.retry_size)
        if not self.seen_hands and hand_crops(maximum):
            maximum = 1

        crop_ram = (256 if not self.gpu or self.host_processing else 8) * MIB
        ram_per_frame = max(self.ram_per_frame * 5 // 4, 3 * self.frame_bytes + crop_ram)
        ram_room = memory.ram_room - pending_output
        if not self.last_good:
            ram_room -= 1024 * MIB  # Decoder and first forward pass can initialize host workspaces.
        maximum = min(maximum, ram_room // ram_per_frame)
        if maximum < 1:
            raise SAMMemoryError("ram")
        if not self.gpu:
            return maximum
        if memory.vram_unreported and not self.shared_gpu:
            return 1

        # The merged hand pass uses two crops for each sampled frame. Account
        # for the actual samples in this upcoming span, including short tails.
        vram_per_crop = max(256 * MIB, self.vram_per_crop * 5 // 4)
        vram_per_frame = max(256 * MIB, self.vram_per_frame * 5 // 4)
        cached = max(0, memory.reserved - memory.allocated)
        fits_gpu = False
        for count in range(maximum, 0, -1):
            source_images = 0 if self.low_memory else count * self.frame_bytes
            estimate = max(hand_crops(count) * vram_per_crop, count * vram_per_frame)
            estimate += source_images
            if estimate > memory.vram_room:
                continue
            fits_gpu = True
            # APU allocations and decoded frames draw on the same RAM. Only
            # memory already held by the GPU allocator can be reused for free.
            if self.shared_gpu and count * ram_per_frame + max(0, estimate - cached) > ram_room:
                continue
            return count
        raise SAMMemoryError("ram" if fits_gpu else "vram")

    def begin(self):
        if self.gpu:
            self.accelerator.reset_peak_memory_stats(self.device)

    def observe(self, before, after, count, hand_crops):
        if self.gpu:
            peak_allocated = self.accelerator.max_memory_allocated(self.device)
            peak_reserved = self.accelerator.max_memory_reserved(self.device)
            extra = max(peak_allocated - before.allocated, peak_reserved - before.reserved,
                        after.driver_usage - before.driver_usage, before.vram_free - after.vram_free)
            if hand_crops:
                self.vram_per_crop = max(self.vram_per_crop, (extra + hand_crops - 1) // hand_crops)
            if hand_crops <= count:
                self.vram_per_frame = max(self.vram_per_frame, (extra + count - 1) // count)
        ram_growth = max(after.process_ram - before.process_ram,
                         after.process_ram_peak - before.process_ram_peak)
        # The cold batch includes persistent driver/decoder initialization.
        # Its retained memory is already accounted for in the next snapshot.
        if self.last_good:
            self.ram_per_frame = max(self.ram_per_frame, (ram_growth + count - 1) // count)
        self.last_good = count
        self.seen_hands |= bool(hand_crops)
        self.retry_size = None

    def split_hands(self):
        self.low_memory = True
        print("  [sam] using CPU crops and separate hand passes to save VRAM", flush=True)

    def release_cache(self):
        with self.accelerator.device(self.device):
            self.accelerator.empty_cache()

    def retry(self, count, resource):
        if count > 1:
            self.retry_limit = count - 1
            self.retry_size = count // 2
        else:
            self.split_hands()
            self.retry_limit = self.retry_size = 1
        if self.gpu:
            self.release_cache()
        self.info["retries"] += 1
        print(f"  [sam] {resource.upper()} allocation retry {self.info['retries']}: "
              f"batch={self.retry_size}, separate hands={self.low_memory}", flush=True)

    def record(self, count, memory, *, succeeded):
        info = self.info
        info.setdefault("initial", count)
        if succeeded:
            info.update(actual=count, min_batch=min(count, info.get("min_batch", count)),
                        max_batch=max(count, info.get("max_batch", count)))
        info["ram_free_mib"] = memory.ram_free // MIB
        if self.gpu and not memory.vram_unreported:
            info.update(free_mib=memory.vram_free // MIB, total_mib=memory.vram_total // MIB)
            info["driver_free_mib"] = (max(0, memory.driver_budget - memory.driver_usage) // MIB
                                       if memory.driver_budget else None)
