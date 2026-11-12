import os
import json
import shutil
import subprocess
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, List, Optional

import cv2
import numpy as np
import torch
import yaml
from torch import Tensor

from flowhmr.utils.runtime_capabilities import LONG_MOTION_EXPERIMENTAL_THRESHOLD
from flowhmr.utils.runtime_image_io import write_image


# Enums & Dataclasses

class StageStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    WAITING_USER = "waiting_user"
    COMPLETED = "completed"
    FAILED = "failed"


class StageName(str, Enum):
    PREPROCESS = "preprocess"
    DETECTION = "detection"
    CAMERA = "camera"
    SAM_EXTRACT = "sam_extract"
    FEATURE_EXTRACT = "feature_extract"
    GENERATION = "generation"
    HAMER = "hamer"


@dataclass
class StageProgress:
    name: StageName
    status: StageStatus = StageStatus.PENDING
    progress: int = 0
    description: str = ""
    result: dict | None = None


@dataclass
class VideoTask:
    video_name: str
    stages: list[StageProgress] = field(default_factory=list)
    current_stage_index: int = 0

    def __post_init__(self):
        if not self.stages:
            self.stages = [
                StageProgress(name=StageName.PREPROCESS),
                StageProgress(name=StageName.DETECTION),
                StageProgress(name=StageName.CAMERA),
                StageProgress(name=StageName.FEATURE_EXTRACT),
                StageProgress(name=StageName.GENERATION),
            ]


# Utility functions (extracted from test_batch_v2m.py)

def load_yaml(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _set_buffer(module, dotted_key: str, tensor: Tensor):
    parts = dotted_key.split(".")
    obj = module
    for p in parts[:-1]:
        obj = getattr(obj, p)
    delattr(obj, parts[-1])
    obj.register_buffer(parts[-1], tensor)


def build_pipeline(model_cfg: dict, device: str = "cpu", ckpt: str = None,
                   validation_steps: int = None,
                   smpl_model_path: str = None,
                   j_regressor_path: str = None):
    from flowhmr.core.framework.loaders import load_object

    pipeline_args = dict(model_cfg["train_pipeline_args"])
    network_module = model_cfg["network_module"]
    network_module_args = dict(model_cfg["network_module_args"])

    if validation_steps is not None:
        pipeline_args.setdefault("infer_noise_scheduler_cfg", {})
        pipeline_args["infer_noise_scheduler_cfg"]["validation_steps"] = validation_steps

    if smpl_model_path is not None:
        pipeline_args["smpl_model_path"] = smpl_model_path
    if j_regressor_path is not None:
        pipeline_args["j_regressor_path"] = j_regressor_path

    train_pipeline = model_cfg.get(
        "train_pipeline",
        "flowhmr/pipeline/pipeline_v2m_simple.SimpleV2MPipeline",
    )
    print(f"  pipeline: {train_pipeline}")

    pipeline = load_object(
        train_pipeline,
        pipeline_args,
        network_module=network_module,
        network_module_args=network_module_args,
    )

    if ckpt:
        print(f"  loading checkpoint: {ckpt}")
        checkpoint = torch.load(ckpt, map_location="cpu")
        if "model_state_dict" in checkpoint:
            state_dict = checkpoint["model_state_dict"]
            epoch = checkpoint.get("epoch", "?")
            step = checkpoint.get("global_step", "?")
            print(f"  checkpoint epoch={epoch}, global_step={step}")
        elif "state_dict" in checkpoint:
            state_dict = checkpoint["state_dict"]
        else:
            state_dict = checkpoint

        model_sd = pipeline.state_dict()
        filtered_sd = {}
        skipped = []
        resized = []
        for k, v in state_dict.items():
            if k not in model_sd:
                skipped.append(k)
                continue
            if v.shape != model_sd[k].shape:
                _set_buffer(pipeline, k, torch.zeros_like(v))
                resized.append(f"{k}: {list(model_sd[k].shape)} -> {list(v.shape)}")
            filtered_sd[k] = v

        missing, unexpected = pipeline.load_state_dict(filtered_sd, strict=False)
        print(f"  loaded {len(filtered_sd)} params/buffers")
        if missing:
            print(f"  Missing keys ({len(missing)}): {missing[:5]}{'...' if len(missing) > 5 else ''}")
        if skipped:
            print(f"  Skipped keys ({len(skipped)}): {skipped[:5]}{'...' if len(skipped) > 5 else ''}")
        if resized:
            print(f"  Resized buffers ({len(resized)}): {resized}")

    pipeline.eval()
    pipeline = pipeline.to(device)
    return pipeline


def build_feature_dict(sam_tokens: Tensor, camera_RT: Tensor, device: torch.device) -> dict:
    T = sam_tokens.shape[0]
    feature = sam_tokens.unsqueeze(0).to(device, dtype=torch.float32)
    camera_R = camera_RT[:, :3, :3].to(device=device, dtype=torch.float32)
    # Match training's compute_camera_features: world-to-camera rotations
    # relative to the first frame, independent of the chosen world axes.
    camera_R = camera_R @ camera_R[0].transpose(-1, -2)
    camera_R = camera_R.reshape(T, 9).unsqueeze(0)
    return {"feature": feature, "camera_R": camera_R}


def _limit_track_frames(bbox_path: str, max_frames: int | None,
                        dependent_paths: tuple[str, ...] = (), *, start_frame=0, end_frame=None) -> None:
    """Intersect the requested source range with the full detected track."""
    with np.load(bbox_path) as data:
        payload = {key: data[key] for key in data.files}
    full_bbox = payload.get("unclipped_bbox", payload["bbox"])
    full_range = payload.get("unclipped_start_end", payload["start_end"])
    full_start, full_end = (int(value) for value in full_range)
    if full_start < 0 or full_end < full_start or len(full_bbox) != full_end - full_start + 1:
        raise ValueError(f"Invalid bbox track range: {full_range}, boxes={len(full_bbox)}")
    end = min(full_end, int(max_frames) - 1) if max_frames is not None and max_frames > 0 else full_end
    start = max(full_start, start_frame)
    if end_frame is not None:
        end = min(end, end_frame)
    if start > end:
        raise ValueError(
            f"处理区间与人物轨迹（第 {full_start + 1}–{full_end + 1} 帧）不重叠。请调整区间或更换人物。"
        )
    if np.array_equal(payload["start_end"], [start, end]):
        return

    # Retain the complete selected track so a later run can increase/remove the limit.
    payload["unclipped_bbox"] = full_bbox
    payload["unclipped_start_end"] = np.asarray(full_range, dtype=np.int64)
    payload["bbox"] = full_bbox[start - full_start:end - full_start + 1]
    payload["start_end"] = np.array([start, end], dtype=np.int64)
    # SAM features depend on both the selected range and the camera intrinsics.
    for path in dependent_paths:
        if os.path.exists(path):
            os.remove(path)
    np.savez(bbox_path, **payload)
    print(f"  [bbox] processing frames [{start},{end}] ({end - start + 1} frames)")


def plan_sam_batch(device, frames, requested=None):
    """Estimate the initial SAM batch from memory available to this worker now."""
    device = torch.device(device)
    info = {"requested": requested, "actual": min(frames, requested or 16), "reused": False, "retries": 0}
    if device.type == "cuda":
        mib = 1024 ** 2
        free, total = torch.cuda.mem_get_info(device)
        info.update(free_mib=free // mib, total_mib=total // mib)
        if requested is None:
            # Free memory is measured with SAM resident and unused allocator blocks released.
            # Small or busy GPUs need extra room for the first body and two-hand passes.
            constrained = total <= 9 * 1024 * mib or free < 5 * 1024 * mib
            reserve = max((2048 if constrained else 1536) * mib, total * 22 // 100)
            per_frame = (512 if constrained else 384) * mib
            info["actual"] = min(frames, max(1, (free - reserve) // per_frame))
    return info


def forward_smpl_batch(body_model, rot6d, shapes, trans):
    B, L = trans.shape[:2]
    J = rot6d.shape[2]
    rot6d_flat = rot6d.reshape(B * L, J, 6)
    shapes_expanded = shapes.expand(B, L, -1).reshape(B * L, shapes.shape[-1])
    trans_flat = trans.reshape(B * L, 3)
    out = body_model({"rot6d": rot6d_flat, "shapes": shapes_expanded, "trans": trans_flat})
    k3d = out["keypoints3d"]
    return k3d.reshape(B, L, k3d.shape[1], 3)


def fit_floor_height(k3d: Tensor, axis: str = "y") -> Tensor:
    axis_idx = {"x": 0, "y": 1, "z": 2}[axis]
    zs = k3d[..., axis_idx].amin(dim=-1).reshape(-1)
    zs, _ = torch.sort(zs)

    min_z = zs.min()
    max_z = zs.max()
    zs = zs[zs <= min_z + (max_z - min_z) * 1.0]

    inlier_thresh = 0.05
    best_inliers = -1
    best_z = zs[0]
    n = zs.numel()
    for _ in range(10_000):
        z = zs[torch.randint(0, n, (1,), device=zs.device)]
        inliers = (zs - z).abs() < inlier_thresh
        cnt = int(inliers.sum().item())
        if cnt > best_inliers:
            best_inliers = cnt
            best_z = z

    offset_val = zs[(zs - best_z).abs() < inlier_thresh].median()
    height_offset = torch.zeros(3, device=k3d.device, dtype=k3d.dtype)
    height_offset[axis_idx] = offset_val
    return height_offset


def _rotate_smplh_params_y180(poses_aa: np.ndarray, trans: np.ndarray):
    from scipy.spatial.transform import Rotation as R

    # 180° rotation around Y axis
    R_y180 = R.from_euler("y", 180, degrees=True)

    # Rotate root orientation: R_new = R_y180 @ R_old
    root_aa = poses_aa[:, 0, :]
    root_rot = R.from_rotvec(root_aa)
    new_root_rot = R_y180 * root_rot
    poses_aa[:, 0, :] = new_root_rot.as_rotvec().astype(poses_aa.dtype)

    # Rotate root translation
    trans[:] = (R_y180.apply(trans)).astype(trans.dtype)

    return poses_aa, trans


def save_fbx(output: dict, length: int, output_path: str, save_npz: bool = True, export_mesh: bool = True,
             *, smpl_model_path=None, body_reference=None):
    """Write the SMPL-H npz and, if the Autodesk FBX SDK is installed, the .fbx."""
    from flowhmr.core.math.geometry import (
        rot6d_to_rotation_matrix,
        rotation_matrix_to_angle_axis,
    )
    from flowhmr.model_files import smplh_reference, smplh_reference_path

    body_reference = body_reference or smplh_reference() or "neutral"

    rot6d = output["rot6d"][0, :length]
    shapes = output["shapes"][0, :1]
    trans = output["trans"][0, :length]

    rotations = rot6d_to_rotation_matrix(rot6d)
    poses = rotation_matrix_to_angle_axis(rotations)

    poses_np = poses.cpu().numpy()
    trans_np = trans.cpu().numpy().copy()  # The export coordinate conversion is in place.

    # Rotate 180° around Y axis before saving
    _rotate_smplh_params_y180(poses_np, trans_np)

    params = {
        "poses": poses_np,
        "betas": shapes.cpu().numpy(),
        "trans": trans_np,
        "Rh": poses_np[:, 0, :],
        "mocap_framerate": 30,
        "num_frames": length,
        "gender": body_reference,
        "body_reference": body_reference,
    }

    if "foot_contacts" in output:
        fc = output["foot_contacts"][0, :length]  # (T, 4)
        params["foot_contacts"] = fc.cpu().numpy()

    if "local_joints_positions" in output:
        params["local_joints_positions"] = output["local_joints_positions"][0, :length].cpu().numpy()
    if "smooth_root_pos" in output:
        params["smooth_root_pos"] = output["smooth_root_pos"][0, :length].cpu().numpy()
    if "pelvis_world" in output:
        params["pelvis_world"] = output["pelvis_world"][0, :length].cpu().numpy()

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    if save_npz:
        npz_path = output_path.replace(".fbx", ".npz")
        np.savez(npz_path, **params)

    try:
        from flowhmr.core.export.smplh2fbx import SMPLH2FBX
    except ImportError:  # fbxsdkpy not installed: npz only
        return
    with SMPLH2FBX(smplh_model_path=smpl_model_path or smplh_reference_path(body_reference)) as smplh2fbx:
        smplh2fbx.convert_npz_to_fbx(params, output_path, export_mesh=export_mesh)


# Bbox visualization helpers

def draw_bbox_on_frame(frame_bgr: np.ndarray, bbox_xyxy: np.ndarray) -> np.ndarray:
    vis = frame_bgr.copy()
    x1, y1, x2, y2 = bbox_xyxy.astype(int)
    cv2.rectangle(vis, (x1, y1), (x2, y2), (0, 255, 0), 2)
    return vis


def save_bbox_vis_video(
    video_path: str,
    bbox_xyxy: Tensor,
    output_path: str,
    fps: float = 30.0,
):
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise ValueError(f"cannot open video: {video_path}")

    bbox_np = bbox_xyxy.cpu().numpy()
    total = bbox_np.shape[0]

    writer = None
    i = 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        if i >= total:
            break
        vis = draw_bbox_on_frame(frame, bbox_np[i])
        if writer is None:
            h, w = vis.shape[:2]
            fourcc = cv2.VideoWriter_fourcc(*"mp4v")
            writer = cv2.VideoWriter(output_path, fourcc, fps, (w, h))
        writer.write(vis)
        i += 1

    cap.release()
    if writer is not None:
        writer.release()
    print(f"    Bbox vis video saved: {output_path} ({i} frames)")


# SAM sparse visualization

def save_sam_sparse_vis(
    sam_extractor,
    video_path: str,
    bbox_xyxy: Tensor,
    K_all: Tensor,
    output_dir: str,
    vis_frame_indices: list,
):
    from flowhmr.utils.runtime_sam_features import get_visualize_fn

    visualize_sample_together = get_visualize_fn()
    faces = sam_extractor.faces

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise ValueError(f"cannot open video: {video_path}")

    bbox_np = bbox_xyxy.cpu()
    K_cpu = K_all.cpu()

    vis_set = set(vis_frame_indices)
    i = 0
    saved = 0
    while True:
        ok, frame_bgr = cap.read()
        if not ok:
            break
        if i in vis_set:
            frame_rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
            b = bbox_np[min(i, bbox_np.shape[0] - 1)].reshape(1, 4).numpy()
            cam_int = K_cpu[min(i, K_cpu.shape[0] - 1)].reshape(1, 3, 3)
            _token, outputs = sam_extractor.extract_frame(
                frame_rgb,
                bboxes=b,
                cam_int=cam_int,
                inference_type="full",
                is_vis=True,
            )
            if outputs:
                rend = visualize_sample_together(frame_bgr, outputs, faces)
                rend = np.clip(rend, 0, 255).astype(np.uint8)
                out_path = os.path.join(output_dir, f"sam_vis_frame{i:06d}.jpg")
                write_image(out_path, rend)
                saved += 1
        i += 1
    cap.release()
    print(f"    SAM sparse vis saved: {saved} frames to {output_dir}")


def pick_vis_frame_indices(total_frames: int, max_vis: int = 5) -> list:
    if total_frames <= 0:
        return []
    indices = [0]
    if total_frames > 1 and max_vis > 1:
        step = max(1, (total_frames - 1) // max_vis)
        for j in range(step, total_frames, step):
            indices.append(j)
            if len(indices) >= max_vis:
                break
    return sorted(set(indices))


class V2MInferenceEngine:

    def __init__(self, pipeline, proc, sam_extractor, body_model,
                 smpl_mesh, postprocess, device, staged_models=None, *, smpl_model_path=None, body_reference=None):
        self.pipeline = pipeline
        self.proc = proc
        self.sam_extractor = sam_extractor
        self.body_model = body_model
        self.smpl_mesh = smpl_mesh
        self.postprocess = postprocess
        self.device = device
        self.smpl_model_path = smpl_model_path
        self.body_reference = body_reference
        self._staged_models = staged_models or {}
        self._active_stage = None

    @staticmethod
    def _body_reference(smpl_model_path, body_reference):
        from pathlib import Path

        from flowhmr.core.bodymodels.smplh_data import validated_smplh_reference
        from flowhmr.model_files import SMPLH_FILES, smplh_reference_path

        if smpl_model_path is None:
            return validated_smplh_reference(body_reference)
        path = Path(smpl_model_path).absolute()
        name = body_reference or next(
            (name for name in SMPLH_FILES if path.name.casefold() == smplh_reference_path(name).name.casefold()),
            "neutral",
        )
        return name, path

    def _activate_stage(self, stage=None):
        if not self._staged_models:
            return
        stage = stage if stage in self._staged_models else None
        if stage == self._active_stage:
            return
        if self._active_stage is not None:
            model, owner = self._staged_models[self._active_stage]
            model.to("cpu")
            if owner is not None:
                owner.device = torch.device("cpu")
            self._active_stage = None
            torch.cuda.empty_cache()
        if stage is not None:
            model, owner = self._staged_models[stage]
            try:
                model.to(self.device)
            except Exception:
                model.to("cpu")
                torch.cuda.empty_cache()
                raise
            if owner is not None:
                owner.device = self.device
            self._active_stage = stage

    @contextmanager
    def _release_camera_cuda(self):
        """Give the camera worker room on small or already occupied GPUs."""
        if self.device.type != "cuda":
            yield
            return
        if self._staged_models:
            self._activate_stage()
            torch.cuda.empty_cache()
            yield
            return
        torch.cuda.empty_cache()
        free, total = torch.cuda.mem_get_info(self.device)
        if total > 9 * 1024 ** 3 and free >= 8 * 1024 ** 3:
            yield
            return

        camera_device = torch.device("cuda", self.device.index if self.device.index is not None else torch.cuda.current_device())
        detector = getattr(getattr(self.proc, "detector", None), "model", None)
        sam_model = getattr(getattr(self.sam_extractor, "estimator", None), "model", None)
        parked = []
        try:
            for model in (self.pipeline, detector, sam_model):
                if model is not None and next(model.parameters()).device == camera_device:
                    parked.append(model)
                    model.to("cpu")
            torch.cuda.empty_cache()
            print(f"  [vggt] temporarily moved {len(parked)} inactive models to RAM", flush=True)
            yield
        finally:
            for model in reversed(parked):
                model.to(camera_device)

    @classmethod
    def build_lite(cls, ckpt: str, model_cfg_path, device: str = "cuda",
                   validation_steps: int = None,
                   postprocess: bool = False,
                   smpl_model_path: str = None,
                   j_regressor_path: str = None, *, body_reference=None) -> "V2MInferenceEngine":
        device_obj = torch.device(device)

        print("[V2MInferenceEngine] Loading models (lite mode, skip YOLOX & SAM3D)...")
        t0 = time.time()

        from flowhmr.core.bodymodels.smpl_skeleton import SMPLSkeleton, SMPLMesh

        if isinstance(model_cfg_path, dict):
            model_cfg = model_cfg_path
        else:
            model_cfg = load_yaml(model_cfg_path)

        body_reference, smpl_model_path = cls._body_reference(smpl_model_path, body_reference)
        bp_kwargs = {"smpl_model_path": str(smpl_model_path)}
        if j_regressor_path is not None:
            bp_kwargs["j_regressor_path"] = j_regressor_path

        pipe = build_pipeline(
            model_cfg, device=device, ckpt=ckpt,
            validation_steps=validation_steps,
            **bp_kwargs,
        )
        print(f"  Pipeline loaded ({sum(p.numel() for p in pipe.parameters()):,} params)")

        smpl_kw = {"model_path": smpl_model_path}
        body_model = SMPLSkeleton(**smpl_kw).to(device_obj)
        print(f"  SMPL body model loaded")

        smpl_mesh = SMPLMesh(**smpl_kw).to(device_obj)
        print(f"  SMPL mesh model loaded")

        if postprocess:
            from flowhmr.utils.runtime_v2m_postprocess_simple import PostprocessPipeline
            postprocess = PostprocessPipeline.default(body_model=body_model, smpl_mesh=smpl_mesh)
            print("  Postprocess: on")
        else:
            postprocess = None
            print("  Postprocess: off")

        print(f"  All models loaded in lite mode ({time.time() - t0:.1f}s)")

        return cls(
            pipeline=pipe,
            proc=None,
            sam_extractor=None,
            body_model=body_model,
            smpl_mesh=smpl_mesh,
            postprocess=postprocess,
            device=device_obj,
            smpl_model_path=smpl_model_path,
            body_reference=body_reference,
        )

    @classmethod
    def build(cls, ckpt: str, model_cfg_path: str, device: str = "cuda",
              validation_steps: int = None,
              postprocess: bool = False,
              sam_device_ids: list[int] | None = None,
              sam_base_port: int = 29500, *, smpl_model_path=None, body_reference=None) -> "V2MInferenceEngine":
        device_obj = torch.device(device)

        print("[V2MInferenceEngine] Loading models...")
        t0 = time.time()

        from flowhmr.utils.runtime_video_processing import (
            VideoProcessor,
            build_human_detector,
        )
        from flowhmr.core.bodymodels.smpl_skeleton import SMPLSkeleton, SMPLMesh
        body_reference, smpl_model_path = cls._body_reference(smpl_model_path, body_reference)

        stage_models = False
        if device_obj.type == "cuda":
            if device_obj.index is None:
                device_obj = torch.device("cuda", torch.cuda.current_device())
            free, total = torch.cuda.mem_get_info(device_obj)
            stage_models = total <= 9 * 1024 ** 3 or free < 7 * 1024 ** 3
        resident = torch.device("cpu") if stage_models else device_obj
        staged = {}
        if stage_models:
            print("  [cuda] Low VRAM: load inactive models on CPU, move each to CUDA for its stage", flush=True)

        detector = build_human_detector(resident)
        proc = VideoProcessor(detector=detector)
        if stage_models:
            staged[StageName.DETECTION] = (detector.model, detector)
        print(f"  YOLOX detector loaded")

        if sam_device_ids and len(sam_device_ids) > 1:
            from flowhmr.utils.runtime_sam_worker import SamWorkerPool
            sam_extractor = SamWorkerPool(
                device_ids=sam_device_ids, base_port=sam_base_port,
            )
            sam_extractor.start()
            print(f"  SAM3D worker pool loaded ({len(sam_device_ids)} GPUs: {sam_device_ids})")
        else:
            from flowhmr.utils.runtime_sam_features import build_sam3d_extractor
            sam_dev = device_obj
            if sam_device_ids and len(sam_device_ids) == 1:
                sam_dev = torch.device(f"cuda:{sam_device_ids[0]}")
            stage_sam = stage_models and sam_dev == device_obj
            sam_extractor = build_sam3d_extractor(sam_dev, load_device=resident if stage_sam else None,
                                                  use_compile=False if stage_sam else None)
            if stage_sam:
                staged[StageName.FEATURE_EXTRACT] = (sam_extractor.estimator.model, sam_extractor.estimator)
            print(f"  SAM3D extractor loaded (single GPU: {sam_dev})")

        if isinstance(model_cfg_path, dict):
            model_cfg = model_cfg_path
        else:
            model_cfg = load_yaml(model_cfg_path)
        pipe = build_pipeline(
            model_cfg, device=str(resident), ckpt=ckpt,
            validation_steps=validation_steps,
            smpl_model_path=str(smpl_model_path),
        )
        if stage_models:
            staged[StageName.GENERATION] = (pipe, None)
        print(f"  Pipeline loaded ({sum(p.numel() for p in pipe.parameters()):,} params)")

        body_model = SMPLSkeleton(model_path=smpl_model_path).to(device_obj)
        print(f"  SMPL body model loaded")

        smpl_mesh = SMPLMesh(model_path=smpl_model_path).to(device_obj)
        print(f"  SMPL mesh model loaded")

        if postprocess:
            from flowhmr.utils.runtime_v2m_postprocess_simple import PostprocessPipeline
            postprocess = PostprocessPipeline.default(body_model=body_model, smpl_mesh=smpl_mesh)
            print("  Postprocess: on")
        else:
            postprocess = None
            print("  Postprocess: off")

        print(f"  All models loaded ({time.time() - t0:.1f}s)")

        return cls(
            pipeline=pipe,
            proc=proc,
            sam_extractor=sam_extractor,
            body_model=body_model,
            smpl_mesh=smpl_mesh,
            postprocess=postprocess,
            device=device_obj,
            staged_models=staged,
            smpl_model_path=smpl_model_path,
            body_reference=body_reference,
        )

    # Stage 1: Preprocess
    def run_preprocess(self, video_path: str, output_dir: str,
                       progress_cb: Callable[[int, str], None] = None) -> dict:
        video_name = os.path.splitext(os.path.basename(video_path))[0]
        sub_dir = os.path.join(output_dir, video_name)
        os.makedirs(sub_dir, exist_ok=True)

        _cb(progress_cb, 0, "Copying original video")
        orig_copy = os.path.join(sub_dir, f"{video_name}_original.mp4")
        if not os.path.exists(orig_copy):
            shutil.copy2(video_path, orig_copy)
        print(f"  [preprocess] Original copied: {orig_copy}")

        _cb(progress_cb, 30, "Transcoding to 30fps")
        transcoded_path = self.proc.transcode(
            video_path,
            backup_dir=sub_dir,
            force_fps=30,
        )
        print(f"  [preprocess] Transcoded: {transcoded_path}")

        _cb(progress_cb, 100, "Preprocess done")
        return {
            "working_video": transcoded_path,
            "sub_dir": sub_dir,
            "video_name": video_name,
        }

    # Stage 2: Detection
    def run_detection(self, working_video: str, sub_dir: str, *,
                      manual_select: bool = False,
                      progress_cb: Callable[[int, str], None] = None) -> dict:
        video_name = os.path.basename(sub_dir)
        bbox_npz_path = os.path.join(sub_dir, f"{video_name}_bbox.npz")
        camera_npz_path = os.path.join(sub_dir, f"{video_name}_camera.npz")

        if os.path.exists(bbox_npz_path) and os.path.exists(camera_npz_path):
            print(f"  [detection] CACHED, loading from npz")
            bbox_data = np.load(bbox_npz_path)
            bbox_xyxy = torch.from_numpy(bbox_data["bbox"])
            K_all = torch.from_numpy(np.load(camera_npz_path)["K"])
            frame_range = None
            if "frame_range" in bbox_data:
                fr = bbox_data["frame_range"]
                frame_range = (int(fr[0]), int(fr[1]))
            _cb(progress_cb, 100, "Detection cached")
            return {"bbox_xyxy": bbox_xyxy, "K_all": K_all, "frame_range": frame_range}

        _cb(progress_cb, 10, "Running human detection + tracking")
        t0 = time.time()

        from flowhmr.utils.runtime_video_processing import VideoProcessor
        self._activate_stage(StageName.DETECTION)
        tracking_result = self.proc.detect_and_track(working_video)

        if not tracking_result.tracks:
            print(f"  [detection] No tracks found, falling back to full-image bbox")
            bbox_xyxy = self.proc.compute_bboxes(working_video)
            K_all, _ = self.proc.compute_camera_K(working_video)
            self.proc.save_bbox_npz(bbox_npz_path, bbox_xyxy)
            self.proc.save_camera_npz(camera_npz_path, K_all)
            _cb(progress_cb, 100, "Detection done (no tracks, full-image fallback)")
            return {"bbox_xyxy": bbox_xyxy, "K_all": K_all}

        _cb(progress_cb, 50, f"Found {len(tracking_result.tracks)} person(s)")

        if manual_select and len(tracking_result.tracks) > 1:
            detections = []
            for i, track in enumerate(tracking_result.tracks):
                thumbnail_path = self._save_track_thumbnail(
                    working_video, track, sub_dir, person_id=i,
                )
                detections.append({
                    "person_id": i,
                    "track_id": track.track_id,
                    "num_frames": track.length,
                    "avg_area": track.avg_area,
                    "thumbnail_path": thumbnail_path,
                })
            _cb(progress_cb, 60, "Waiting for user selection")
            return {
                "detections": detections,
                "needs_user_select": True,
                "_tracking_result": tracking_result,
                "_sub_dir": sub_dir,
                "_working_video": working_video,
                "_bbox_npz_path": bbox_npz_path,
                "_camera_npz_path": camera_npz_path,
            }

        best_idx = VideoProcessor.select_best_track(tracking_result)
        bbox_xyxy, K_all, frame_range = self._finalize_detection(
            tracking_result, best_idx, working_video, sub_dir,
            bbox_npz_path, camera_npz_path,
        )
        print(f"  [detection] Auto-selected track {tracking_result.tracks[best_idx].track_id} "
              f"({time.time() - t0:.1f}s)")
        _cb(progress_cb, 100, "Detection done")
        return {"bbox_xyxy": bbox_xyxy, "K_all": K_all, "frame_range": frame_range}

    def confirm_detection(self, person_id: int, detection_result: dict,
                          progress_cb: Callable[[int, str], None] = None) -> dict:
        tracking_result = detection_result["_tracking_result"]
        sub_dir = detection_result["_sub_dir"]
        working_video = detection_result["_working_video"]
        bbox_npz_path = detection_result["_bbox_npz_path"]
        camera_npz_path = detection_result["_camera_npz_path"]

        _cb(progress_cb, 70, f"Computing bbox for person {person_id}")
        bbox_xyxy, K_all, frame_range = self._finalize_detection(
            tracking_result, person_id, working_video, sub_dir,
            bbox_npz_path, camera_npz_path,
        )
        print(f"  [detection] User selected person {person_id}")
        _cb(progress_cb, 100, "Detection confirmed")
        return {"bbox_xyxy": bbox_xyxy, "K_all": K_all, "frame_range": frame_range}

    def _finalize_detection(self, tracking_result, track_idx: int,
                            working_video: str, sub_dir: str,
                            bbox_npz_path: str, camera_npz_path: str):
        from flowhmr.utils.runtime_video_processing import VideoProcessor

        track = tracking_result.tracks[track_idx]

        frame_start = int(track.frames.min())
        frame_end = int(track.frames.max()) + 1 # exclusive

        bbox_arr = VideoProcessor.interpolate_track(track, tracking_result.total_frames)
        bbox_arr = VideoProcessor.smooth_bboxes(bbox_arr, tracking_result.width, tracking_result.height)

        bbox_arr = bbox_arr[frame_start:frame_end]
        bbox_xyxy = torch.from_numpy(bbox_arr).to(torch.float32)

        K_all_full, _ = self.proc.compute_camera_K(working_video)
        K_all = K_all_full[frame_start:frame_end]

        frame_range = None
        if frame_start > 0 or frame_end < tracking_result.total_frames:
            frame_range = (frame_start, frame_end)
            print(f"  [detection] Track frame range: {frame_start}-{frame_end} "
                  f"({frame_end - frame_start} frames) / total {tracking_result.total_frames}")

        bbox_np = bbox_xyxy.detach().cpu().numpy().astype(np.float32)
        save_kw = {"bbox": bbox_np, "start_end": np.array([0, bbox_np.shape[0]], dtype=np.int64)}
        if frame_range is not None:
            save_kw["frame_range"] = np.array(frame_range, dtype=np.int64)
        np.savez(bbox_npz_path, **save_kw)

        self.proc.save_camera_npz(camera_npz_path, K_all)

        return bbox_xyxy, K_all, frame_range

    def _save_track_thumbnail(self, video_path: str, track, sub_dir: str,
                              person_id: int) -> str:
        mid_idx = track.length // 2
        frame_idx = int(track.frames[mid_idx])
        bbox = track.bboxes[mid_idx].astype(int)

        cap = cv2.VideoCapture(video_path)
        cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
        ok, frame = cap.read()
        cap.release()

        if not ok:
            return ""

        x1, y1, x2, y2 = bbox
        h, w = frame.shape[:2]
        x1, y1 = max(0, x1), max(0, y1)
        x2, y2 = min(w, x2), min(h, y2)
        crop = frame[y1:y2, x1:x2]

        thumb_path = os.path.join(sub_dir, f"person_{person_id}_thumb.jpg")
        write_image(thumb_path, crop)
        return thumb_path

    def _trim_video(self, video_path: str, frame_start: int, frame_end: int,
                    sub_dir: str) -> str:
        meta = self.proc.get_video_meta(video_path)
        fps = meta.fps
        start_time = frame_start / fps
        duration = (frame_end - frame_start) / fps

        video_name = os.path.basename(sub_dir)
        trimmed_path = os.path.join(sub_dir, f"{video_name}_trimmed.mp4")

        if os.path.exists(trimmed_path):
            print(f"  [trim] CACHED: {trimmed_path}")
            return trimmed_path

        def build_cmd(encoder_name: str) -> List[str]:
            cmd = [
                "ffmpeg", "-y",
                "-i", video_path,
                "-ss", str(start_time),
                "-t", str(duration),
                "-c:v", encoder_name, "-vsync", "1", "-pix_fmt", "yuv420p", "-an",
            ]
            if "libx264" in encoder_name or "libx265" in encoder_name:
                cmd += ["-preset", "fast", "-crf", "22"]
            cmd.append(trimmed_path)
            return cmd

        candidate_encoders: List[str] = ["libx264", "h264_nvenc", "mpeg4", "libx265"]
        last_error = ""
        for enc in candidate_encoders:
            cmd = build_cmd(enc)
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode == 0:
                break
            last_error = result.stderr.strip() if result.stderr else str(result.returncode)
        else:
            raise RuntimeError(
                f"FFmpeg trim FAILED (tried encoders: {candidate_encoders}): {last_error}"
            )

        trimmed_meta = self.proc.get_video_meta(trimmed_path)
        expected = frame_end - frame_start
        actual = trimmed_meta.total_frames
        print(f"  [trim] frames {frame_start}-{frame_end} ({expected} expected), "
              f"actual trimmed: {actual} frames")

        return trimmed_path

    # Stage 3: Feature Extract
    def run_feature_extract(self, working_video: str, bbox_xyxy: Tensor, K_all: Tensor,
                        sub_dir: str, max_frames: int = None,
                        sam_vis_frames: int = 5,
                        progress_cb: Callable[[int, str], None] = None) -> dict:
        video_name = os.path.basename(sub_dir)
        sam_npz_path = os.path.join(sub_dir, f"{video_name}_sam_tokens.npz")

        if os.path.exists(sam_npz_path):
            print(f"  [feature_extract] CACHED, loading from npz")
            sam_tokens = torch.from_numpy(np.load(sam_npz_path)["tokens"])
            _cb(progress_cb, 100, "Feature tokens cached")
            return {"sam_tokens": sam_tokens}

        _cb(progress_cb, 0, "Starting feature extraction")
        t0 = time.time()

        def _sam_progress(stage, current, total, message):
            if total and total > 0:
                pct = min(95, int(current / total * 95))
                _cb(progress_cb, pct, f"SAM: {current}/{total} frames")

        self._activate_stage(StageName.FEATURE_EXTRACT)
        sam_tokens = self.sam_extractor.extract_video_tokens(
            working_video,
            bbox_xyxy=bbox_xyxy,
            K_all=K_all,
            max_frames=max_frames,
            progress_cb=_sam_progress,
        )
        print(f"  [feature_extract] tokens shape: {tuple(sam_tokens.shape)}")

        np.savez(sam_npz_path, tokens=sam_tokens.cpu().numpy())

        from flowhmr.utils.runtime_sam_features import Sam3DTokenExtractor
        if isinstance(self.sam_extractor, Sam3DTokenExtractor):
            T_vis = sam_tokens.shape[0]
            vis_indices = pick_vis_frame_indices(T_vis, max_vis=sam_vis_frames)
            print(f"  [feature_extract] vis frames: {vis_indices}")
            sam_vis_dir = os.path.join(sub_dir, "sam_vis")
            os.makedirs(sam_vis_dir, exist_ok=True)
            save_sam_sparse_vis(
                self.sam_extractor, working_video, bbox_xyxy, K_all,
                output_dir=sam_vis_dir,
                vis_frame_indices=vis_indices,
            )
        else:
            print(f"  [feature_extract] vis skipped (pool mode)")

        print(f"  [feature_extract] Done ({time.time() - t0:.1f}s)")
        _cb(progress_cb, 100, "Feature extraction done")
        return {"sam_tokens": sam_tokens}

    # Stage 4: Generation
    def run_generation(self, sam_tokens: Tensor, sub_dir: str, video_name: str, *,
                       seed: int = 0, cfg_scale: float = 1.0,
                       export_mesh: bool = True,
                       progress_cb: Callable[[int, str], None] = None) -> dict:
        T = sam_tokens.shape[0]

        _cb(progress_cb, 0, "ODE sampling")
        t0 = time.time()
        self._activate_stage(StageName.GENERATION)

        camera_RT = torch.eye(4, dtype=torch.float32).unsqueeze(0).repeat(T, 1, 1)
        feature_dict = build_feature_dict(sam_tokens, camera_RT, self.device)
        print(f"  [generation] Feature dict: { {k: tuple(v.shape) for k, v in feature_dict.items()} }")

        with torch.no_grad():
            output = self.pipeline.generate(
                feature=feature_dict,
                seeds=[seed],
                length=T,
                cfg_scale=cfg_scale,
            )

        print(f"  [generation] Output: { {k: tuple(v.shape) for k, v in output.items() if isinstance(v, Tensor)} }")
        _cb(progress_cb, 50, "ODE sampling done, exporting")

        origin_output = {k: v.clone() if isinstance(v, Tensor) else v for k, v in output.items()}
        with torch.no_grad():
            first_rot6d = origin_output["rot6d"][:, :1]
            first_shapes = origin_output["shapes"]
            first_trans = origin_output["trans"][:, :1]
            B_o = first_rot6d.shape[0]
            first_mesh_out = self.smpl_mesh(
                {"rot6d": first_rot6d.reshape(B_o, 52, 6),
                 "shapes": first_shapes.reshape(B_o, -1),
                 "trans": first_trans.reshape(B_o, 3)},
            )
            first_verts = first_mesh_out["vertices"]
            floor_y = first_verts[:, :, 1].min(dim=-1)[0]
            origin_output["trans"][:, :, 1] -= floor_y[:, None]
            print(f"  [generation] Origin floor offset: {floor_y[0].item()*1000:.1f}mm")

        origin_fbx_path = os.path.join(sub_dir, f"{video_name}_seed{seed}_origin.fbx")
        save_fbx(origin_output, length=T, output_path=origin_fbx_path, save_npz=True, export_mesh=export_mesh,
                 smpl_model_path=self.smpl_model_path, body_reference=self.body_reference)
        _cb(progress_cb, 70, "Origin FBX saved")

        fbx_path = origin_fbx_path
        if self.postprocess is not None:
            output = self.postprocess.run(output)
            fbx_path = os.path.join(sub_dir, f"{video_name}_seed{seed}.fbx")
            save_fbx(output, length=T, output_path=fbx_path, save_npz=True, export_mesh=export_mesh,
                     smpl_model_path=self.smpl_model_path, body_reference=self.body_reference)
        _cb(progress_cb, 95, "Post-processed FBX saved")

        print(f"  [generation] Done ({time.time() - t0:.1f}s)")
        _cb(progress_cb, 100, "Generation complete")
        return {
            "output": output,
            "fbx_path": fbx_path,
            "origin_fbx_path": origin_fbx_path,
        }

    def process_video(self, video_path: str, output_dir: str, *,
                      seed: int = 0, cfg_scale: float = 1.0,
                      max_frames: int = None,
                      start_frame: int = 0, end_frame: int | None = None,
                      smoothing_frames: float = 0,
                      hamer: bool = False,
                      hamer_stride: int = 1,
                      vggt_frame_interval: int = 1,
                      static_camera: bool = False,
                      person_selector: Callable = None,
                      progress_callback: Callable[[StageName, int, str], None] = None,
                      cancel_check: Callable[[], None] = None,
                      sam_chunk_size: int | None = 16,
                      details_callback: Callable[[dict], None] = None) -> str:
        """Video -> SMPL-H motion.

        static_camera: skip VGGT and use a fixed pose with default intrinsics.
        sam_chunk_size: frames per SAM body pass; both hands are batched together.
        hamer: refine the selected person's fingers before exporting the motion.
        hamer_stride: interval in video frames between hand predictions; fill gaps with rotation interpolation.

        person_selector: optional ``f(result, candidates, default) -> candidate index``,
        called after tracking when more than one person track (>= 15 frames) is found, e.g.
        to let a user pick. ``candidates`` lists those tracks (id, frame range, boxes);
        returning None / an out-of-range index keeps ``default``. Without it the longest /
        largest track is used.
        """
        if sam_chunk_size is not None and (type(sam_chunk_size) is not int or sam_chunk_size < 1):
            raise ValueError("sam_chunk_size must be a positive integer or automatic")
        from flowhmr.utils.runtime_hamer import hamer_available, load_hand_boxes, refine_fingers, MISSING_HAMER
        from flowhmr.utils.runtime_video_processing import VideoProcessor

        extract_hand_boxes = getattr(self.sam_extractor, "extract_video_hand_boxes", None)
        if hamer and not hamer_available(self.device):
            raise RuntimeError(MISSING_HAMER)
        if hamer and extract_hand_boxes is None:
            raise RuntimeError("HaMeR 需要单 GPU 的 SAM 特征提取模式。")

        def _pcb(stage_name, progress, desc):
            if cancel_check is not None:
                cancel_check()
            if progress_callback is not None:
                progress_callback(stage_name, progress, desc)

        def _details(**details):
            if details_callback is not None:
                details_callback(details)

        vggt_frame_interval = max(0, int(vggt_frame_interval))
        video_name = os.path.splitext(os.path.basename(video_path))[0]
        sub_dir = os.path.join(output_dir, video_name)
        os.makedirs(sub_dir, exist_ok=True)

        bbox_npz_path = os.path.join(sub_dir, f"{video_name}_bbox.npz")
        feature_suffix = "_static" if static_camera else ""
        camera_mode = "static" if static_camera else "vggt"
        feat_pt_path = os.path.join(sub_dir, f"{video_name}_sam3d_feat{feature_suffix}.pt")
        batch_path = feat_pt_path + ".batch.json"
        hand_boxes_path = feat_pt_path + ".hands.npy"
        camera_npz_path = os.path.join(sub_dir, f"{video_name}_{camera_mode}_camera.npz")

        print(f"\n  === Step 1: Bbox ===")
        _pcb(StageName.DETECTION, 0, "Starting bbox extraction")

        working_video = self.proc.transcode(video_path, backup_dir=sub_dir, force_fps=30)
        video_meta = VideoProcessor.get_video_meta(working_video)
        _details(source_frames=video_meta.total_frames or None)
        if video_meta.total_frames > 0 and start_frame >= video_meta.total_frames:
            raise ValueError(f"起始帧超出素材范围（共 {video_meta.total_frames} 帧）。")
        last_requested = end_frame
        if max_frames is not None and max_frames > 0:
            last_requested = min(last_requested, max_frames - 1) if last_requested is not None else max_frames - 1

        if os.path.exists(bbox_npz_path):
            print(f"  [bbox] skip (exists): {bbox_npz_path}")
            _pcb(StageName.DETECTION, 100, "Bbox cached")
        else:
            _pcb(StageName.DETECTION, 20, "Transcoded, detecting")

            self._activate_stage(StageName.DETECTION)
            result = self.proc.detect_and_track(working_video)
            if result.total_frames != video_meta.total_frames:
                _details(source_frames=result.total_frames)
            _pcb(StageName.DETECTION, 60, f"Found {len(result.tracks)} person(s)")

            if not result.tracks:
                track_idx = -1
                print(f"  [bbox] WARN no_tracks -> full-image bbox x {result.total_frames} frames")
            else:
                track_idx = VideoProcessor.select_best_track(result)
                eligible = [i for i, t in enumerate(result.tracks)
                            if t.frames.max() >= start_frame and (last_requested is None or t.frames.min() <= last_requested)]
                if not eligible:
                    raise ValueError("处理区间里没有检测到人物，请调整时间轴的起止帧。")
                if track_idx not in eligible:
                    track_idx = max(eligible, key=lambda i: (result.tracks[i].length, result.tracks[i].avg_area))
                # Fragments shorter than this (ByteTrack id switches, passers-by) are not offered.
                min_len = 15
                offered = [i for i in eligible if result.tracks[i].length >= min_len or i == track_idx]
                if person_selector is not None and len(offered) > 1:
                    candidates = []
                    for i in offered:
                        t = result.tracks[i]
                        order = np.argsort(t.frames)
                        candidates.append({
                            "index": len(candidates), "track_index": i, "track_id": int(t.track_id),
                            "start": int(t.frames.min()), "end": int(t.frames.max()),
                            "preview_start": max(start_frame, int(t.frames.min())),
                            "preview_end": min(last_requested, int(t.frames.max())) if last_requested is not None else int(t.frames.max()),
                            "num_frames": int(t.length), "avg_area": float(t.avg_area),
                            "frames": t.frames[order].astype(int).tolist(),
                            "boxes": np.round(t.bboxes[order].astype(np.float64), 1).tolist(),
                        })
                    _pcb(StageName.DETECTION, 70, "Waiting for person selection")
                    default_pos = offered.index(track_idx)
                    chosen = person_selector(result, candidates, default_pos)
                    if chosen is not None and 0 <= int(chosen) < len(offered):
                        track_idx = offered[int(chosen)]
                track = result.tracks[track_idx]
                print(f"  [bbox] track {track.track_id}: length={track.length} "
                      f"frame_range=[{int(track.frames.min())},{int(track.frames.max())}] "
                      f"/ total={result.total_frames}")
            bbox_np, start_end = VideoProcessor.track_bboxes(result, track_idx)

            np.savez(bbox_npz_path, bbox=bbox_np, start_end=start_end)
            print(f"  [bbox] saved -> {bbox_npz_path}  (shape={bbox_np.shape})")
            _pcb(StageName.DETECTION, 100, "Bbox done")

        _limit_track_frames(
            bbox_npz_path, max_frames, start_frame=start_frame, end_frame=end_frame,
            dependent_paths=tuple(os.path.join(sub_dir, f"{video_name}_{suffix}") for suffix in (
                "vggt_camera.npz", "static_camera.npz", "sam3d_feat.pt", "sam3d_feat_static.pt",
                "sam3d_feat.pt.batch.json", "sam3d_feat_static.pt.batch.json",
                "sam3d_feat.pt.hands.npy", "sam3d_feat_static.pt.hands.npy",
            )),
        )

        # ==== Step 2: Camera ====
        print(f"\n  === Step 2: {'Static' if static_camera else 'VGGT'} Camera ===")
        _pcb(StageName.CAMERA, 0, "Using a static camera" if static_camera else "Starting VGGT camera extraction")

        with np.load(bbox_npz_path) as data:
            start_end_arr = data["start_end"].astype(np.int64)
        s, e = int(start_end_arr[0]), int(start_end_arr[1])
        t_track = e - s + 1
        long_motion_frames = t_track if t_track > LONG_MOTION_EXPERIMENTAL_THRESHOLD else None
        _details(track_range=[s, e], long_motion_frames=long_motion_frames)
        if long_motion_frames:
            print(f"  [motion] experimental long track: {t_track} frames in [{s},{e}]", flush=True)

        from flowhmr.utils.runtime_vggt_camera import (
            camera_intervals, extract_vggt_camera, extract_vggt_camera_isolated,
            make_static_camera, _vggt_opencv_to_yup_rt,
        )

        camera_stamp = os.stat(camera_npz_path).st_mtime_ns if os.path.exists(camera_npz_path) else None
        if static_camera:
            camera_result = make_static_camera(
                (video_meta.height, video_meta.width), frame_range=(s, e), output_npz=camera_npz_path,
            )
        elif cancel_check is not None or self._staged_models:
            camera_result = extract_vggt_camera_isolated(
                working_video, output_npz=camera_npz_path, frame_range=(s, e),
                expected_len=t_track, device=self.device, frame_interval=vggt_frame_interval,
                cancel_check=cancel_check or (lambda: None), on_progress=lambda message: _pcb(StageName.CAMERA, 10, message),
                release_cuda=self._release_camera_cuda,
            )
        else:
            camera_result = extract_vggt_camera(
                working_video,
                output_npz=camera_npz_path,
                frame_range=(s, e),
                expected_len=t_track,
                device=self.device,
                frame_interval=camera_intervals(t_track, vggt_frame_interval)[0],
            )
        if camera_stamp != os.stat(camera_npz_path).st_mtime_ns:
            if os.path.exists(feat_pt_path):
                os.remove(feat_pt_path)
                print("  [sam] camera intrinsics changed, recomputing features")
            if os.path.exists(hand_boxes_path):
                os.remove(hand_boxes_path)
        _pcb(StageName.CAMERA, 100, "Static camera: estimation skipped" if static_camera else "VGGT camera ready")

        # ==== Step 3: SAM feature extraction ====
        print(f"\n  === Step 3: SAM Feature ===")
        _pcb(StageName.FEATURE_EXTRACT, 0, "Starting SAM extraction")

        feature_cached = False
        if os.path.exists(feat_pt_path):
            try:
                cached_tokens = torch.load(feat_pt_path, map_location="cpu", weights_only=True)
                feature_cached = (
                    torch.is_tensor(cached_tokens)
                    and cached_tokens.shape == (t_track, 3072)
                )
                del cached_tokens
            except Exception as exc:
                print(f"  [sam] cached features could not be loaded ({exc}), recomputing")
        need_hand_boxes = hamer and load_hand_boxes(hand_boxes_path, t_track) is None
        if feature_cached:
            batch = {"requested": sam_chunk_size, "actual": min(t_track, sam_chunk_size) if sam_chunk_size else None}
            try:
                with open(batch_path, encoding="utf-8") as f:
                    cached_batch = json.load(f)
                if isinstance(cached_batch, dict) and cached_batch.get("requested") == sam_chunk_size:
                    batch.update(cached_batch)
            except (OSError, ValueError):
                pass
            _details(sam_batch={**batch, "reused": True})
            print(f"  [sam] skip (exists): {feat_pt_path}")
        if feature_cached and not need_hand_boxes:
            _pcb(StageName.FEATURE_EXTRACT, 100, "SAM cached")
        else:
            if feature_cached:
                print("  [sam] cached tokens have no hand boxes; recovering boxes only")
            elif os.path.exists(feat_pt_path):
                print(f"  [sam] invalid cached features, expected ({t_track}, 3072); recomputing")
            with np.load(bbox_npz_path) as data:
                bbox_track = data["bbox"].astype(np.float32)

            total_frames = video_meta.total_frames
            if total_frames <= 0:
                cap = cv2.VideoCapture(working_video)
                total_frames = 0
                while cap.read()[0]:
                    total_frames += 1
                cap.release()

            bbox_full = np.empty((total_frames, 4), dtype=np.float32)
            if s > 0:
                bbox_full[:s] = bbox_track[0]
            bbox_full[s:e + 1] = bbox_track
            if e + 1 < total_frames:
                bbox_full[e + 1:] = bbox_track[-1]

            max_frames_needed = e + 1

            print(
                f"  [sam] bbox.shape={bbox_track.shape}, start_end=[{s},{e}], "
                f"total_frames={total_frames}, max_frames={max_frames_needed}"
            )

            bbox_full_t = torch.from_numpy(bbox_full).float()
            K_all, _ = self.proc.compute_camera_K(working_video)

            K_track = torch.from_numpy(camera_result.K).to(torch.float32)
            assert K_track.shape[0] == t_track, (
                f"Camera K length {K_track.shape[0]} != track length {t_track}"
            )
            K_all = K_all.to(torch.float32)
            K_all[s:e + 1] = K_track
            print(f"  [sam] using Stage-2 {camera_mode} original-frame K for track frames: {tuple(K_track.shape)}")

            def _sam_progress(stage, current, total, message):
                if total and total > 0:
                    pct = min(95, int(current / total * 95))
                    desc = (f"正在补提取手框 · {current} / {total} 帧" if feature_cached
                            else f"SAM: {current}/{total} frames")
                    _pcb(StageName.FEATURE_EXTRACT, pct, desc)

            self._activate_stage(StageName.FEATURE_EXTRACT)
            estimator = getattr(self.sam_extractor, "estimator", None)
            sam_device = torch.device(getattr(estimator, "device", self.device))
            if sam_chunk_size is None and sam_device.type == "cuda":
                with torch.cuda.device(sam_device):
                    torch.cuda.empty_cache()
            batch = plan_sam_batch(sam_device, t_track, sam_chunk_size)
            if not feature_cached:
                _details(sam_batch=batch.copy())
            print(f"  [sam] {'auto' if sam_chunk_size is None else 'manual'} batch={batch['actual']}, "
                  f"free={batch.get('free_mib', '?')} MiB of {batch.get('total_mib', '?')} MiB")

            def _reduce_batch(size):
                batch.update(actual=size, retries=batch["retries"] + 1)
                if not feature_cached:
                    _details(sam_batch=batch.copy())
                print(f"  [sam] GPU memory retry {batch['retries']}: batch={size}", flush=True)

            sam_args = dict(
                bbox_xyxy=bbox_full_t,
                K_all=K_all,
                max_frames=max_frames_needed,
                start_frame=s,
                progress_cb=_sam_progress,
                frame_batch_size=batch["actual"],
                on_oom_reduce=_reduce_batch if sam_chunk_size is None else None,
            )
            if feature_cached:
                hand_boxes = extract_hand_boxes(working_video, **sam_args)
            else:
                box_chunks = []
                if extract_hand_boxes is not None:
                    sam_args["hand_boxes_out"] = box_chunks
                tokens = self.sam_extractor.extract_video_tokens(working_video, token_dim=3072, **sam_args)
                tokens = tokens.contiguous().cpu().to(torch.float32)
                if tokens.shape != (t_track, 3072):
                    raise ValueError(
                        f"SAM returned {tuple(tokens.shape)} for track [{s},{e}], "
                        f"expected ({t_track}, 3072); incomplete features were not cached."
                    )
                hand_boxes = np.concatenate(box_chunks) if box_chunks else None
            if hand_boxes is not None:
                if hand_boxes.shape != (t_track, 2, 4):
                    raise ValueError("SAM hand boxes do not match the selected track")
                np.save(hand_boxes_path, hand_boxes)
            if not feature_cached:
                torch.save(tokens, feat_pt_path)
                with open(batch_path, "w", encoding="utf-8") as f:
                    json.dump(batch, f)
                print(f"  [sam] saved -> {feat_pt_path}  (shape={tuple(tokens.shape)})")
            _pcb(StageName.FEATURE_EXTRACT, 100, "SAM done")

        # ==== Step 4: Generation ====
        print(f"\n  === Step 4: Generation ===")
        _pcb(StageName.GENERATION, 0, "Starting generation")
        self._activate_stage(StageName.GENERATION)

        origin_fbx_path = os.path.join(sub_dir, f"{video_name}_seed{seed}_origin.fbx")
        final_fbx_path = os.path.join(sub_dir, f"{video_name}_seed{seed}.fbx")

        data = np.load(bbox_npz_path)
        start_end_arr = data["start_end"].astype(np.int64)
        s, e = int(start_end_arr[0]), int(start_end_arr[1])
        expected_len = e - s + 1

        sam_tokens = torch.load(feat_pt_path, map_location="cpu", weights_only=True)
        sam_tokens = sam_tokens.to(torch.float32).contiguous()
        T = sam_tokens.shape[0]
        assert T == expected_len, f"sam_tokens.shape[0]={T} != expected {expected_len}"
        print(f"  [gen] T={T}, range=[{s},{e}], seed={seed}, cfg={cfg_scale}")

        camera_RT = _vggt_opencv_to_yup_rt(
            torch.from_numpy(camera_result.camera_RT).to(torch.float32),
        )
        feature_dict = build_feature_dict(sam_tokens, camera_RT, self.device)

        with torch.no_grad():
            output = self.pipeline.generate(
                feature=feature_dict,
                seeds=[seed],
                length=T,
                cfg_scale=cfg_scale,
                step_callback=cancel_check,
            )
        del feature_dict
        _pcb(StageName.GENERATION, 50, "ODE sampling done, exporting")

        if smoothing_frames:
            from flowhmr.utils.runtime_motion_filter import smooth_motion
            _pcb(StageName.GENERATION, 55, "正在平滑动作")
            output = smooth_motion(output, smoothing_frames)
            print(f"  [gen] smoothing sigma={smoothing_frames:g} frames")

        origin_output = {k: v.clone() if isinstance(v, Tensor) else v
                        for k, v in output.items()}
        with torch.no_grad():
            first_rot6d = origin_output["rot6d"][:, :1]
            first_shapes = origin_output["shapes"]
            first_trans = origin_output["trans"][:, :1]
            B_o = first_rot6d.shape[0]
            first_mesh_out = self.smpl_mesh(
                {"rot6d": first_rot6d.reshape(B_o, 52, 6),
                 "shapes": first_shapes.reshape(B_o, -1),
                 "trans": first_trans.reshape(B_o, 3)},
            )
            first_verts = first_mesh_out["vertices"]
            floor_y = first_verts[:, :, 1].min(dim=-1)[0]
            origin_output["trans"][:, :, 1] -= floor_y[:, None]

        save_fbx(origin_output, length=T, output_path=origin_fbx_path,
                 save_npz=True, export_mesh=True, smpl_model_path=self.smpl_model_path,
                 body_reference=self.body_reference)
        _pcb(StageName.GENERATION, 70, "Origin FBX saved")

        final_output = origin_output
        if self.postprocess is not None:
            _pcb(StageName.GENERATION, 75, "正在修正脚底接触")
            final_output = self.postprocess.run(output, cancel_check=cancel_check)
        if hamer:
            self._activate_stage()
            _pcb(StageName.GENERATION, 100, "动作生成完成")
            _pcb(StageName.HAMER, 0, "正在启动 HaMeR 手指精修")

            def _hamer_progress(current, total, message):
                if current == total:
                    message = f"正在整理手指精修结果 · {total} / {total} 帧"
                _pcb(StageName.HAMER, min(99, 100 * current // total), message)

            final_output, report = refine_fingers(
                working_video, hand_boxes_path, s, final_output, device=self.device,
                stride=hamer_stride, cancel_check=cancel_check, on_progress=_hamer_progress,
            )
            _details(hamer_report=report)
            _pcb(StageName.HAMER, 99, "正在保存手指精修结果")
        if self.postprocess is not None or hamer:
            save_fbx(final_output, length=T, output_path=final_fbx_path,
                     save_npz=True, export_mesh=True, smpl_model_path=self.smpl_model_path,
                     body_reference=self.body_reference)
        if hamer:
            _pcb(StageName.HAMER, 100, "手指精修完成")
        else:
            _pcb(StageName.GENERATION, 100, "Generation complete")

        self._activate_stage()
        return sub_dir

    def process_videos(self, video_paths: list, output_dir: str, **kwargs) -> list:
        results = []
        for vi, video_path in enumerate(video_paths):
            video_name = os.path.splitext(os.path.basename(video_path))[0]
            print(f"\n{'=' * 70}")
            print(f"  [{vi + 1}/{len(video_paths)}] {video_name}")
            print(f"{'=' * 70}")

            t_video = time.time()
            try:
                sub_dir = self.process_video(video_path, output_dir, **kwargs)
                elapsed = time.time() - t_video
                results.append((video_name, "ok", elapsed, None))
                print(f"  [{video_name}] ok ({elapsed:.1f}s)")
            except Exception as e:
                elapsed = time.time() - t_video
                results.append((video_name, "failed", elapsed, str(e)))
                print(f"  [{video_name}] FAILED: {e} ({elapsed:.1f}s)")
                import traceback
                traceback.print_exc()

        # Summary
        print(f"\n{'=' * 70}")
        print(f"  Batch Summary")
        print(f"{'=' * 70}")
        print(f"  {'Video':<30s} {'Status':<10s} {'Time':>8s}  Error")
        print(f"  {'-' * 70}")
        for name, status, elapsed, error_msg in results:
            err = error_msg or ""
            print(f"  {name:<30s} {status:<10s} {elapsed:>7.1f}s  {err}")
        print(f"{'=' * 70}")

        return results


# Helper

def _cb(progress_cb: Callable | None, progress: int, description: str):
    if progress_cb is not None:
        progress_cb(progress, description)
