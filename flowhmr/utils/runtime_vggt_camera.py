from __future__ import annotations

"""VGGT-Omega camera extraction utilities.

Extract per-frame camera extrinsics from a video and save them as NPZ.
The saved ``camera_RT`` / ``RT`` keeps the raw VGGT/OpenCV convention. It is
converted to the V2M Y-up convention only when feeding the model.
"""

import os
import os.path as osp
import math
import multiprocessing
import shutil
import sys
import tempfile
from contextlib import nullcontext
from dataclasses import dataclass
from typing import Any, Optional, Tuple
from zipfile import BadZipFile

import numpy as np
import torch

from flowhmr.model_files import VGGT, file_present, resolve_model
from flowhmr.runtime_rocm import configure_windows_hip, supports_bf16, uses_shared_memory
from flowhmr.utils.runtime_image_io import write_image
from flowhmr.utils.runtime_capabilities import InferenceRAMError, SharedCameraSamplingError, SharedGPUMemoryError
from flowhmr.utils.sam_batch import require_inference_ram, require_shared_ram, shared_ram_room
from flowhmr.utils.runtime_video_processing import open_video_at_frame

_repo_root = osp.normpath(osp.join(osp.dirname(__file__), "..", ".."))
_vggt_repo_dir = osp.join(_repo_root, "third_party", "vggt-omega")
if _vggt_repo_dir not in sys.path:
    sys.path.insert(0, _vggt_repo_dir)

@dataclass
class VGGTCameraResult:
    camera_RT: np.ndarray[Any, Any] # (T,4,4), raw VGGT/OpenCV cam <- world
    RT_vggt: np.ndarray[Any, Any] # (T,4,4), same as camera_RT, kept for clarity
    K: np.ndarray[Any, Any] # (T,3,3), intrinsics decoded in original-frame resolution
    image_size_hw: tuple[int, int]


_VGGT_MODEL_CACHE: dict[tuple[str, str, bool], Any] = {}
_MIB = 1024 ** 2
_GIB = 1024 ** 3
_CAMERA_QUALITY_REVISION = 2


def camera_intervals(frames: int, requested: int) -> tuple[int, ...]:
    """An explicit stride is exact. Auto starts with at most 64 camera samples."""
    if requested < 0 or frames < 1:
        raise ValueError("Invalid camera sampling interval or frame count")
    if requested:
        return (requested,)
    return tuple(dict.fromkeys(max(1, math.ceil((frames - 1) / (count - 1)))
                               for count in (64, 32, 16)))


def _read_camera_cache(path, frame_range, expected_len, intervals, *, upgrade_compact=False):
    if not path or not osp.isfile(path):
        return None
    try:
        with np.load(path, allow_pickle=False) as data:
            if (upgrade_compact and str(data.get("camera_profile", "full")) in ("low_vram", "cpu")
                    and int(data.get("camera_quality_revision", 0)) != _CAMERA_QUALITY_REVISION):
                return None
            rt = data["RT_vggt"] if "RT_vggt" in data.files else data["camera_RT"]
            intrinsics = data["K"]
            interval = int(data["camera_sampling_interval"]) if "camera_sampling_interval" in data.files else 1
            size = tuple(int(x) for x in data["image_size_hw"])
            if (str(data["k_space"]) != "original_frame" or interval not in intervals
                    or (expected_len is not None and rt.shape[0] != expected_len)
                    or (frame_range is not None and not np.array_equal(data["frame_range"], frame_range))
                    or rt.ndim != 3 or rt.shape[1:] != (4, 4) or intrinsics.shape != (len(rt), 3, 3)
                    or len(size) != 2 or min(size) < 1 or not np.isfinite(rt).all()
                    or not np.isfinite(intrinsics).all()):
                return None
            raw = rt.astype(np.float32)
            return VGGTCameraResult(raw, raw, intrinsics.astype(np.float32), size)
    except (KeyError, ValueError, OSError, TypeError, IndexError, BadZipFile):
        return None


_CAMERA_MEMORY_EXIT = 75
_CAMERA_PRECISION_EXIT = 76


class _CameraPrecisionError(RuntimeError):
    pass


def camera_cuda_budget(device):
    free, total = torch.cuda.mem_get_info(device)
    room = free - max(768 * _MIB, int(total * 0.06))
    if uses_shared_memory(torch, device):
        room = min(room, shared_ram_room())
    return max(0, room)


def _camera_attempts(frames, requested, device, budget):
    device = torch.device(device)
    full, medium, low = (requested or max(1, math.ceil((frames - 1) / (count - 1)))
                         for count in (64, 32, 16))
    if device.type != "cuda":
        yield device, medium, 320, False
        yield device, low, 256, False
        return
    if budget >= 8 * _GIB:
        yield device, full, 512, False
    # First keep the regular resolution and sample positions. Estimate the
    # room needed for activations, then let the worker's CUDA limit decide.
    if budget >= max(3584, 3000 + 18 * _sample_count(frames, full)) * _MIB:
        yield device, full, 512, True
    if medium != full and budget >= max(3584, 3000 + 18 * _sample_count(frames, medium)) * _MIB:
        yield device, medium, 512, True
    if budget >= 4608 * _MIB:
        yield device, full, 384, True
    if budget >= 3 * _GIB:
        yield device, medium, 320, True
        yield device, low, 256, True
    # A CPU retry keeps the moving camera and the requested manual interval.
    # Bound it so a very dense manual run cannot exhaust system RAM instead.
    if _sample_count(frames, low) <= 32:
        yield torch.device("cpu"), low, 256, False


_SHARED_CAMERA_MAX_SAMPLES = 16


def _shared_camera_attempts(frames, requested):
    """Use the CPU on integrated GPUs without allocating the GPU's shared budget."""
    if requested and _sample_count(frames, requested) > _SHARED_CAMERA_MAX_SAMPLES:
        return
    previous = None
    for samples, resolution in ((_SHARED_CAMERA_MAX_SAMPLES, 320), (12, 256), (8, 256)):
        interval = requested or max(1, math.ceil((frames - 1) / (samples - 1)))
        if (interval, resolution) != previous:
            yield torch.device("cpu"), interval, resolution, False
            previous = (interval, resolution)


def _sample_count(frames, interval):
    return (frames - 2) // interval + 2 if frames > 1 else 1


def _cpu_camera_workspace(samples, resolution):
    # Budget FP32 global attention in case a fused CPU kernel is unavailable.
    # At most resolution² pixels, ViT patch size 16 and 17 register/camera tokens.
    tokens = samples * ((resolution // 16) ** 2 + 17)
    return (1024 + 32 * samples) * _MIB + 2 * 16 * 4 * tokens ** 2


def _camera_worker(video_path, output_npz, frame_range, frames, device, interval, resolution, compact, budget):
    device = torch.device(device)
    try:
        if device.type == "cuda":
            device = torch.device("cuda", device.index if device.index is not None else 0)
            torch.cuda.set_device(device)
            configure_windows_hip(torch, device=device, attention=True)
            _, total = torch.cuda.mem_get_info(device)
            limit = min(budget, camera_cuda_budget(device))
            if limit < 3 * _GIB:
                raise torch.cuda.OutOfMemoryError("No CUDA memory remains for camera estimation")
            torch.cuda.set_per_process_memory_fraction(limit / total, device)
            torch.cuda.reset_peak_memory_stats(device)
            if _vggt_dtype(device) == torch.float16:
                torch.backends.cuda.matmul.allow_fp16_reduced_precision_reduction = False
            print(f"  [vggt] CUDA allocator budget: {limit / _MIB:.0f} MiB", flush=True)
        else:
            torch.set_num_threads(min(8, torch.get_num_threads()))
        extract_vggt_camera(video_path, output_npz=output_npz, frame_range=frame_range,
                            expected_len=frames, device=device, frame_interval=interval,
                            image_resolution=resolution, compact=compact)
    except Exception as error:
        if device.type == "cuda" and isinstance(error, _CameraPrecisionError):
            print(f"  [vggt] CUDA precision: {error}", file=sys.stderr, flush=True)
            raise SystemExit(_CAMERA_PRECISION_EXIT) from None
        if device.type == "cuda" and (isinstance(error, torch.cuda.OutOfMemoryError) or any(
                name in str(error).lower() for name in ("cuda out of memory", "cuda error: out of memory",
                                                       "hip out of memory", "hip error: out of memory",
                                                       "cublas_status_alloc_failed", "cudnn_status_alloc_failed"))):
            print(f"  [vggt] CUDA memory limit: {error}", file=sys.stderr, flush=True)
            raise SystemExit(_CAMERA_MEMORY_EXIT) from None
        # Pass a short diagnosis back to the job panel; the traceback still goes to stderr.
        try:
            with open(output_npz + ".error.txt", "w", encoding="utf-8") as report:
                report.write(f"{type(error).__name__}: {error}")
        except OSError:
            pass
        raise


def extract_vggt_camera_isolated(video_path, *, output_npz, frame_range, expected_len,
                                 device, frame_interval, cancel_check, on_progress, release_cuda=nullcontext):
    """Run the expensive camera forward in a cancellable process with a VRAM budget."""
    device = torch.device(device)
    shared_gpu = device.type == "cuda" and uses_shared_memory(torch, device)
    if shared_gpu:
        shared_attempts = tuple(_shared_camera_attempts(expected_len, frame_interval))
        # Reusing a compatible estimate incurs no VGGT allocation.
        intervals = tuple(dict.fromkeys((*camera_intervals(expected_len, frame_interval),
                                         *(attempt[1] for attempt in shared_attempts))))
    else:
        intervals = camera_intervals(expected_len, frame_interval)
    cached = _read_camera_cache(output_npz, frame_range, expected_len, intervals, upgrade_compact=True)
    if cached is not None:
        print(f"  [vggt] skip cached camera: {output_npz}", flush=True)
        on_progress("已复用相机估计")
        return cached

    cancel_check()
    if shared_gpu and not shared_attempts:
        raise SharedCameraSamplingError(
            "AMD 核显的运动相机由 CPU 估计。请改用自动采样、增大手动间隔，或改用固定机位。"
        )
    invalid_pose = False
    ram_failure = None
    with release_cuda(), tempfile.TemporaryDirectory(prefix=".camera-", dir=osp.dirname(osp.abspath(output_npz))) as scratch:
        budget = camera_cuda_budget(device) if device.type == "cuda" and not shared_gpu else None
        attempts = shared_attempts if shared_gpu else _camera_attempts(expected_len, frame_interval, device, budget)
        temporary = osp.join(scratch, "camera.npz")
        for target, interval, resolution, compact in attempts:
            cancel_check()
            samples = _sample_count(expected_len, interval)
            if target.type == "cpu":
                # Include the checkpoint plus worst-case attention workspace.
                needed = osp.getsize(resolve_model(VGGT)) + _cpu_camera_workspace(samples, resolution)
                try:
                    (require_shared_ram if shared_gpu else require_inference_ram)(needed)
                except (SharedGPUMemoryError, InferenceRAMError) as error:
                    ram_failure = error
                    continue
            mode = "CPU 相机估计" if target.type == "cpu" else "省显存相机估计" if compact else "相机估计"
            on_progress(f"{mode} · 采样 {samples}/{expected_len} 帧 · 间隔 {interval}")
            process = multiprocessing.get_context("spawn").Process(
                target=_camera_worker,
                args=(video_path, temporary, frame_range, expected_len, str(target), interval, resolution, compact, budget),
                daemon=True,
            )
            process.start()
            try:
                while process.is_alive():
                    cancel_check()
                    process.join(0.25)
                cancel_check()
                code = process.exitcode
            finally:
                if process.is_alive():
                    process.terminate()
                    process.join()
                process.close()
            if code == _CAMERA_MEMORY_EXIT:
                continue
            if code == _CAMERA_PRECISION_EXIT:
                invalid_pose = True
                continue
            if code != 0:
                try:
                    with open(temporary + ".error.txt", encoding="utf-8") as report:
                        detail = report.read(600).strip()
                except OSError:
                    detail = ""
                if detail:
                    raise RuntimeError(f"相机估计失败：{detail}")
                raise RuntimeError(f"相机计算进程退出（{code}），请查看后台日志。")
            result = _read_camera_cache(temporary, frame_range, expected_len, (interval,))
            if result is None:
                raise RuntimeError("相机计算没有产出有效结果。")
            os.replace(temporary, output_npz)
            return result
    if ram_failure is not None:
        raise ram_failure
    if shared_gpu:
        raise SharedCameraSamplingError(
            "AMD 核显的 CPU 相机估计未能完成。请缩短处理区间、增大采样间隔，或改用固定机位。"
        )
    if invalid_pose:
        raise RuntimeError("当前 GPU 的相机估计未得到有效结果。请改用自动采样或增大手动间隔，以便使用 CPU 重试。")
    raise RuntimeError("移动相机估计可用显存不足。请释放其他程序占用的显存，或使用自动采样、增大手动采样间隔。")


def make_static_camera(image_size_hw, *, frame_range, output_npz=None) -> VGGTCameraResult:
    """Use a fixed camera and SAM's default intrinsics without loading VGGT."""
    from flowhmr.utils.runtime_video_processing import VideoProcessor

    height, width = map(int, image_size_hw)
    start, end = map(int, frame_range)
    if min(height, width) <= 0 or start < 0 or end < start:
        raise ValueError(f"Invalid static camera dimensions/range: {image_size_hw}, {frame_range}")
    length = end - start + 1
    camera_rt = np.repeat(np.eye(4, dtype=np.float32)[None], length, axis=0)
    K = np.repeat(VideoProcessor.default_K_from_hw(height, width)[None], length, axis=0)
    result = VGGTCameraResult(camera_rt, camera_rt, K, (height, width))
    if output_npz is not None:
        valid_cache = False
        if osp.isfile(output_npz):
            try:
                with open(output_npz, "rb") as cache_file, np.load(cache_file, allow_pickle=False) as data:
                    valid_cache = (
                        str(data["camera_mode"]) == "static"
                        and str(data["k_space"]) == "original_frame"
                        and np.array_equal(data["frame_range"], [start, end])
                        and np.array_equal(data["image_size_hw"], [height, width])
                        and np.array_equal(data["camera_RT"], camera_rt)
                        and np.array_equal(data["K"], K)
                    )
            except (KeyError, ValueError, OSError, BadZipFile):
                pass
        if not valid_cache:
            os.makedirs(osp.dirname(osp.abspath(output_npz)), exist_ok=True)
            np.savez(output_npz, camera_mode="static", camera_world="opencv_static",
                     camera_RT=camera_rt, RT=camera_rt, K=K,
                     image_size_hw=np.array([height, width], dtype=np.int64),
                     frame_range=np.array([start, end], dtype=np.int64),
                     k_space="original_frame", k_source="image_diagonal")
    return result


def _interpolate_camera_sequence(
    extrinsic: torch.Tensor,
    intrinsic: torch.Tensor,
    sample_indices: list[int],
    target_len: int,
) -> tuple[torch.Tensor, torch.Tensor]:
    """Interpolate sampled camera predictions to every frame."""
    if target_len <= 0:
        raise ValueError(f"target_len must be positive, got {target_len}")
    if len(sample_indices) != int(extrinsic.shape[0]):
        raise ValueError(
            f"sample_indices length {len(sample_indices)} != extrinsic length {int(extrinsic.shape[0])}"
        )
    if len(sample_indices) == target_len and np.array_equal(
        np.asarray(sample_indices, dtype=np.int64), np.arange(target_len, dtype=np.int64)
    ):
        return extrinsic, intrinsic

    sample_x = np.asarray(sample_indices, dtype=np.float64)
    target_x = np.arange(target_len, dtype=np.float64)
    if sample_x[0] != 0 or sample_x[-1] != target_len - 1:
        raise ValueError(
            f"sample_indices must include first/last frame, got {sample_indices[:3]}...{sample_indices[-3:]}, "
            f"target_len={target_len}"
        )

    from scipy.spatial.transform import Rotation as R, Slerp

    device = extrinsic.device
    dtype = extrinsic.dtype
    ext_np = extrinsic.detach().float().cpu().numpy()
    int_np = intrinsic.detach().float().cpu().numpy()

    if len(sample_indices) == 1:
        full_ext = np.repeat(ext_np, target_len, axis=0)
        full_int = np.repeat(int_np, target_len, axis=0)
    else:
        full_ext = np.zeros((target_len, 3, 4), dtype=np.float32)
        rots = R.from_matrix(ext_np[:, :3, :3])
        full_ext[:, :3, :3] = Slerp(sample_x, rots)(target_x).as_matrix().astype(np.float32)
        for r in range(3):
            full_ext[:, r, 3] = np.interp(target_x, sample_x, ext_np[:, r, 3]).astype(np.float32)

        full_int = np.zeros((target_len, 3, 3), dtype=np.float32)
        for r in range(3):
            for c in range(3):
                full_int[:, r, c] = np.interp(target_x, sample_x, int_np[:, r, c]).astype(np.float32)

    return torch.from_numpy(full_ext).to(device=device, dtype=dtype), torch.from_numpy(full_int).to(device=device, dtype=dtype)


def _vggt_opencv_to_yup_rt(camera_rt: torch.Tensor) -> torch.Tensor:
    """Convert raw VGGT/OpenCV ``cam <- world`` RT to V2M ``cam <- y_up_world`` RT."""
    vggt_from_yup = torch.eye(4, dtype=camera_rt.dtype, device=camera_rt.device)
    vggt_from_yup[1, 1] = -1
    vggt_from_yup[2, 2] = -1
    return camera_rt @ vggt_from_yup


def _vggt_dtype(device):
    return torch.bfloat16 if supports_bf16(torch, device) else torch.float16


def _load_vggt_model(checkpoint_path: str, device: torch.device, compact: bool) -> Any:
    key = (osp.abspath(checkpoint_path), str(device), compact)
    if key in _VGGT_MODEL_CACHE:
        return _VGGT_MODEL_CACHE[key]
    if not osp.isfile(checkpoint_path):
        raise FileNotFoundError(f"VGGT-Omega checkpoint not found: {checkpoint_path}")

    from vggt_omega.models import VGGTOmega

    # FlowHMR consumes only camera poses. Map the original checkpoint and
    # initialize only the modules used here, without loading a second RAM copy.
    with torch.device("meta"):
        model = VGGTOmega(enable_depth=False, autocast=False).eval()
    state_dict = torch.load(checkpoint_path, map_location="cpu", weights_only=True, mmap=True)
    model.load_state_dict({k: v for k, v in state_dict.items() if not k.startswith("dense_head.")}, assign=True)
    encoder = model.aggregator
    encoder.cached_layer_indices = {encoder.depth - 1}
    if compact and device.type == "cuda":
        dtype = _vggt_dtype(device)
        # Only store the layers that autocast already runs in the lower dtype
        # that way. Keep normalization, residual scales and tokens in FP32.
        for module in encoder.modules():
            if isinstance(module, (torch.nn.Linear, torch.nn.Conv2d)):
                module.to(device=device, dtype=dtype)
        encoder.to(device)
    else:
        model.to(device)
    _VGGT_MODEL_CACHE[key] = model
    return model


def _predict_camera(model, images, device, *, compact=False):
    dtype = _vggt_dtype(device) if device.type == "cuda" else None
    with torch.autocast("cuda", dtype=dtype, enabled=device.type == "cuda"):
        tokens, patch_start = model.aggregator(images.unsqueeze(0))
        # CameraHead consumes only camera/register tokens from the last layer.
        # Copy that small slice before releasing the image tokens.
        camera_tokens = tokens[-1][:, :, :patch_start].contiguous()
    del tokens
    head = model.camera_head
    staged = compact and device.type == "cuda"
    if staged:
        torch.cuda.empty_cache()
    try:
        if staged:
            head.to(device)
        with torch.autocast(device.type, enabled=False):
            return head([camera_tokens], patch_token_start=patch_start).float().cpu()
    finally:
        if staged:
            head.to("cpu")


def _save_video_frames(
    video_path: str,
    frame_dir: str,
    *,
    frame_range: Optional[Tuple[int, int]] = None,
    max_frames: Optional[int] = None,
    frame_interval: int = 1,
) -> tuple[list[str], tuple[int, int], list[int]]:
    start, end = frame_range if frame_range is not None else (0, None)
    start = max(int(start), 0)
    if end is not None:
        end = int(end)

    frame_interval = max(1, int(frame_interval))
    os.makedirs(frame_dir, exist_ok=True)
    paths: list[str] = []
    sample_indices: list[int] = []
    image_size_hw: tuple[int, int] | None = None
    frame_idx = start
    cap = open_video_at_frame(video_path, start)
    try:
        while True:
            if end is not None and frame_idx > end:
                break
            if max_frames is not None and max_frames > 0 and len(paths) >= max_frames:
                break
            ok, frame = cap.read()
            if not ok:
                break
            if image_size_hw is None:
                h, w = frame.shape[:2]
                image_size_hw = (int(h), int(w))
            rel_idx = frame_idx - start
            should_save = (rel_idx % frame_interval == 0) or (end is not None and frame_idx == end)
            if should_save:
                out_path = osp.join(frame_dir, f"{len(paths):06d}.jpg")
                write_image(out_path, frame)
                paths.append(out_path)
                sample_indices.append(rel_idx)
            frame_idx += 1
    finally:
        cap.release()

    if not paths or image_size_hw is None:
        raise ValueError(f"VGGT-Omega read no frames from video: {video_path}, frame_range={frame_range}")
    return paths, image_size_hw, sample_indices


def extract_vggt_camera(
    video_path: str,
    *,
    output_npz: Optional[str] = None,
    frame_range: Optional[Tuple[int, int]] = None,
    expected_len: Optional[int] = None,
    checkpoint_path: Optional[str] = None,
    device: torch.device | str = "cuda",
    image_resolution: int = 512,
    preprocess_mode: str = "max_size",
    keep_frames: bool = False,
    frame_interval: int = 1,
    compact: bool = False,
) -> VGGTCameraResult:
    frame_interval = max(1, int(frame_interval))
    cached = _read_camera_cache(output_npz, frame_range, expected_len, (frame_interval,))
    if cached is not None:
        print(f"  [vggt] skip cached camera: {output_npz}")
        return cached

    checkpoint_path = checkpoint_path or str(resolve_model(VGGT))
    if not file_present(checkpoint_path):
        raise FileNotFoundError(f"VGGT-Omega 缺少相机模型：{checkpoint_path}。固定机位模式不需要此模型。")

    device_obj = torch.device(device)
    if device_obj.type == "cuda" and not torch.cuda.is_available():
        raise RuntimeError("VGGT-Omega camera extraction requires CUDA, but CUDA is not available")

    from vggt_omega.utils.load_fn import load_and_preprocess_images
    from vggt_omega.utils.pose_enc import encoding_to_camera

    if output_npz:
        frame_dir = output_npz + ".frames"
        if osp.isdir(frame_dir):
            shutil.rmtree(frame_dir)
        tmp_ctx = None
        os.makedirs(frame_dir, exist_ok=True)
    else:
        tmp_ctx = tempfile.TemporaryDirectory(prefix="vggt_omega_frames_")
        frame_dir = tmp_ctx.name

    try:
        frame_paths, original_image_size_hw, sample_indices = _save_video_frames(
            video_path,
            frame_dir,
            frame_range=frame_range,
            max_frames=expected_len,
            frame_interval=frame_interval,
        )
        if expected_len is not None:
            target_len = int(expected_len)
            if sample_indices[-1] != target_len - 1:
                raise ValueError(
                    f"VGGT-Omega sampled frames do not cover the final frame: last_sample={sample_indices[-1]}, "
                    f"expected_last={target_len - 1}, video={video_path}, frame_range={frame_range}"
                )
        else:
            target_len = sample_indices[-1] + 1
        profile = "cpu" if device_obj.type == "cpu" else "low_vram" if compact else "full"
        print(f"  [vggt] sampled {len(frame_paths)}/{target_len} frames "
              f"(interval={frame_interval}, resolution={image_resolution}, profile={profile})", flush=True)

        model = _load_vggt_model(checkpoint_path, device_obj, compact)
        images = load_and_preprocess_images(
            frame_paths,
            mode=preprocess_mode,
            image_resolution=image_resolution,
        ).to(device_obj)

        with torch.inference_mode():
            pose = _predict_camera(model, images, device_obj, compact=compact)
            if not torch.isfinite(pose).all():
                raise _CameraPrecisionError("相机姿态包含非有限数值。")
            extrinsic, intrinsic = encoding_to_camera(
                pose,
                original_image_size_hw,
            )
            if not torch.isfinite(extrinsic).all() or not torch.isfinite(intrinsic).all():
                raise _CameraPrecisionError("相机参数包含非有限数值。")

        # Remove batch dimension from README-style outputs.
        extrinsic = extrinsic[0] if extrinsic.ndim == 4 else extrinsic
        intrinsic = intrinsic[0] if intrinsic.ndim == 4 else intrinsic
        if int(extrinsic.shape[0]) != len(sample_indices):
            raise ValueError(
                f"VGGT output length {int(extrinsic.shape[0])} != sampled frame count {len(sample_indices)}"
            )
        extrinsic, intrinsic = _interpolate_camera_sequence(
            extrinsic,
            intrinsic,
            sample_indices,
            target_len,
        )

        T = extrinsic.shape[0]
        RT_vggt = torch.eye(4, dtype=extrinsic.dtype, device=extrinsic.device).unsqueeze(0).repeat(T, 1, 1)
        RT_vggt[:, :3, :4] = extrinsic

        camera_RT_yup = _vggt_opencv_to_yup_rt(RT_vggt)

        vggt_hw = images.shape[-2:]
        vggt_image_size_hw = (int(vggt_hw[0]), int(vggt_hw[1]))
        raw_rt_np = RT_vggt.detach().float().cpu().numpy().astype(np.float32)
        result = VGGTCameraResult(
            camera_RT=raw_rt_np,
            RT_vggt=raw_rt_np,
            K=intrinsic.detach().float().cpu().numpy().astype(np.float32),
            image_size_hw=original_image_size_hw,
        )

        if output_npz:
            os.makedirs(osp.dirname(output_npz), exist_ok=True)
            peak_reserved = torch.cuda.max_memory_reserved(device_obj) // _MIB if device_obj.type == "cuda" else 0
            peak_allocated = torch.cuda.max_memory_allocated(device_obj) // _MIB if device_obj.type == "cuda" else 0
            np.savez(
                output_npz,
                camera_RT=result.camera_RT,
                RT=result.camera_RT,
                RT_vggt=result.RT_vggt,
                camera_RT_yup=camera_RT_yup.detach().float().cpu().numpy().astype(np.float32),
                K=result.K,
                image_size_hw=np.array(result.image_size_hw, dtype=np.int64),
                vggt_image_size_hw=np.array(vggt_image_size_hw, dtype=np.int64),
                frame_range=np.array(frame_range if frame_range is not None else (0, T - 1), dtype=np.int64),
                checkpoint_path=checkpoint_path,
                image_resolution=image_resolution,
                camera_profile=profile,
                camera_quality_revision=_CAMERA_QUALITY_REVISION,
                camera_peak_reserved_mib=peak_reserved,
                camera_peak_allocated_mib=peak_allocated,
                preprocess_mode=preprocess_mode,
                camera_world="vggt_opencv_raw",
                k_space="original_frame",
                camera_sampling_interval=frame_interval,
                camera_sample_indices=np.array(sample_indices, dtype=np.int64),
            )
            print(
                f"  [vggt] camera saved -> {output_npz}  "
                f"(T={T}, sampled={len(sample_indices)}, interval={frame_interval}, image_size={result.image_size_hw}, "
                f"CUDA peak reserved={peak_reserved} MiB)", flush=True,
            )

        return result
    finally:
        if tmp_ctx is not None:
            tmp_ctx.cleanup()
        elif not keep_frames and output_npz:
            shutil.rmtree(frame_dir, ignore_errors=True)


def load_or_extract_vggt_camera_rt(
    video_path: str,
    output_npz: str,
    *,
    frame_range: Optional[Tuple[int, int]],
    expected_len: int,
    device: torch.device | str,
    checkpoint_path: Optional[str] = None,
    image_resolution: int = 512,
    frame_interval: int = 1,
) -> torch.Tensor:
    result = extract_vggt_camera(
        video_path,
        output_npz=output_npz,
        frame_range=frame_range,
        expected_len=expected_len,
        checkpoint_path=checkpoint_path,
        device=device,
        image_resolution=image_resolution,
        frame_interval=frame_interval,
    )
    raw_rt = torch.from_numpy(result.camera_RT).to(torch.float32)
    return _vggt_opencv_to_yup_rt(raw_rt)
