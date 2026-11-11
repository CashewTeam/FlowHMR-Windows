"""Run the official HaMeR backbone and pose head in a separate local process."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import json
import os
import pickle
import sys
import time


def _progress(frame, message):
    print("@@HAMER@@" + json.dumps({"frame": frame, "message": message}), flush=True)


def _runs(mask):
    import numpy as np

    return np.flatnonzero(np.diff(np.r_[False, mask, False])).reshape(-1, 2)


def _sample_mask(valid, stride):
    import numpy as np

    if stride == 1:
        return valid.copy()
    sampled = np.zeros_like(valid)
    for side in range(2):
        for a, b in _runs(valid[:, side]):
            sampled[a:b:stride, side] = True
            sampled[b - 1, side] = True  # Anchor both ends of each visible span.
    return sampled


def _fill_sampled_rotations(rotations, valid, sampled):
    import numpy as np
    from scipy.spatial.transform import Rotation, Slerp

    for side in range(2):
        for a, b in _runs(valid[:, side]):
            anchors = a + np.flatnonzero(sampled[a:b, side])
            if not len(anchors):
                valid[a:b, side] = False
                continue
            first, last = anchors[0], anchors[-1]
            # A failed prediction can leave an unanchored edge. Keep the
            # original FlowHMR pose there, just as for a missing hand box.
            valid[a:first, side] = False
            valid[last + 1:b, side] = False
            missing = first + np.flatnonzero(~sampled[first:last + 1, side])
            if len(missing):
                for joint in range(rotations.shape[2]):
                    interp = Slerp(anchors, Rotation.from_matrix(rotations[anchors, side, joint]))
                    rotations[missing, side, joint] = interp(missing).as_matrix()


def _load_model(device):
    import torch
    from yacs.config import CfgNode

    from flowhmr.core.bodymodels.licensed_data import read_registered_arrays
    from flowhmr.model_files import HAMER_FILES, HAMER_RUNTIME, MANO_FILES, resolve_model
    from flowhmr.utils.runtime_hamer import HAMER_ISSUES

    sys.path.insert(0, str(HAMER_RUNTIME))
    from hamer.configs import get_config
    from hamer.models.backbones import create_backbone
    from hamer.models.heads import build_mano_head

    checkpoint, config, mean_params = map(resolve_model, HAMER_FILES)
    # The head predicts MANO rotations; no mesh reconstruction or generated MANO file is needed.
    try:
        mano = read_registered_arrays(resolve_model(MANO_FILES[1]),
                                      ("v_template", "hands_components", "kintree_table"))
    except FileNotFoundError as error:
        raise ValueError(HAMER_ISSUES["hamer_mano_missing"]) from error
    except (OSError, EOFError, pickle.UnpicklingError, ValueError, TypeError, KeyError, AttributeError) as error:
        raise ValueError(HAMER_ISSUES["hamer_mano_invalid"]) from error
    if (mano["v_template"].shape != (778, 3) or mano["hands_components"].shape != (45, 45)
            or mano["kintree_table"].shape != (2, 16)):
        raise ValueError(HAMER_ISSUES["hamer_mano_invalid"])

    cfg = get_config(str(config))
    cfg.defrost()
    cfg.MANO.MEAN_PARAMS = str(mean_params)
    if "BBOX_SHAPE" not in cfg.MODEL:
        cfg.MODEL.BBOX_SHAPE = [192, 256]
    cfg.MODEL.BACKBONE.pop("PRETRAINED_WEIGHTS", None)
    cfg.freeze()
    with torch.device("cpu"):
        backbone = create_backbone(cfg)
        head = build_mano_head(cfg)
    with torch.serialization.safe_globals([CfgNode]):
        state = torch.load(checkpoint, map_location="cpu", weights_only=True)["state_dict"]
    for module, prefix in ((backbone, "backbone."), (head, "mano_head.")):
        module.load_state_dict({name.removeprefix(prefix): value for name, value in state.items()
                                if name.startswith(prefix)}, strict=True)
        module.to(device)
        module.eval()
    del state
    return backbone, head, cfg


def _prefetch(batches):
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(next, batches, None)
        while (batch := future.result()) is not None:
            future = pool.submit(next, batches, None)
            yield batch


def _crop_hand(image, center, extent, shape, resolution, flip):
    import cv2
    import numpy as np

    # Expand the box to the model's aspect ratio before the square crop.
    size = max(float(2 * extent[0] * shape[1] / shape[0]), float(2 * extent[1]))
    sy = resolution / size
    sx = -sy if flip else sy
    cx, cy = center
    transform = np.array([[sx, 0, resolution / 2 - sx * cx],
                          [0, sy, resolution / 2 - sy * cy]], dtype=np.float32)
    return cv2.warpAffine(image, transform, (resolution, resolution), flags=cv2.INTER_LINEAR)


def run(video, boxes_path, start, gpu, target, stride=1):
    import cv2
    import numpy as np
    import torch
    from scipy.ndimage import gaussian_filter1d
    from scipy.spatial.transform import Rotation

    torch.set_num_threads(min(4, os.cpu_count() or 1))
    boxes = np.load(boxes_path, allow_pickle=False)
    frames = len(boxes)
    if frames < 1 or boxes.shape != (frames, 2, 4) or start < 0 or not 1 <= stride <= 4:
        raise ValueError("Invalid HaMeR frame range or SAM hand boxes")
    rotations = np.tile(np.eye(3, dtype=np.float32), (frames, 2, 15, 1, 1))
    cap = cv2.VideoCapture(str(video))
    try:
        if not cap.isOpened():
            raise ValueError("Cannot open the canonical video for HaMeR")
        width, height = cap.get(cv2.CAP_PROP_FRAME_WIDTH), cap.get(cv2.CAP_PROP_FRAME_HEIGHT)
        finite = np.isfinite(boxes).all(-1)
        clean = np.where(finite[..., None], boxes, 0)
        size = clean[..., 2:] - clean[..., :2]
        inside = np.maximum(0, np.minimum(clean[..., 2:], [width, height]) - np.maximum(clean[..., :2], 0))
        valid = finite & (size.min(-1) > 2) & (inside.min(-1) >= 4) & (inside.prod(-1) >= .1 * size.prod(-1))
        sampled = _sample_mask(valid, stride)
        if sampled.any():
            from flowhmr.runtime_rocm import configure_windows_hip

            _progress(0, "正在加载 HaMeR 手指模型")
            device = torch.device("cuda", gpu)
            configure_windows_hip(torch, attention=True)
            torch.cuda.set_device(device)
            backbone, head, cfg = _load_model(device)
            mean = 255 * np.asarray(cfg.MODEL.IMAGE_MEAN, dtype=np.float32)[:, None, None]
            std = 255 * np.asarray(cfg.MODEL.IMAGE_STD, dtype=np.float32)[:, None, None]
            mirror = np.diag([-1., 1., 1.])
            if start and not cap.set(cv2.CAP_PROP_POS_FRAMES, start):
                for _ in range(start):
                    if not cap.grab():
                        raise ValueError(f"Cannot reach video frame {start + 1}")

            def predict(items):
                image = torch.from_numpy(np.stack([crop for _, _, crop in items])).to(device)
                features = backbone(image[..., 32:-32])
                params, _, _ = head(features)
                return params["hand_pose"].float().cpu().numpy()

            prepared_size = batch_size = 8  # Hand crops, independent of the SAM frame batch.

            def infer_prepared(items):
                nonlocal batch_size
                offset = 0
                while offset < len(items):
                    batch = items[offset:offset + batch_size]
                    failed = False
                    try:
                        hands = predict(batch)
                    except torch.cuda.OutOfMemoryError:
                        if batch_size == 1:
                            raise
                        failed = True
                    if failed:
                        batch_size = max(1, batch_size // 2)
                        torch.clear_autocast_cache()
                        torch.cuda.empty_cache()
                        continue
                    for (frame, side, _), hand in zip(batch, hands):
                        if not np.isfinite(hand).all():
                            valid[frame, side] = False
                            continue
                        if side == 0:
                            hand = mirror @ hand @ mirror
                        rotations[frame, side] = Rotation.from_matrix(hand).as_matrix()
                    offset += len(batch)

            def prepare_batches():
                pending = []
                for i in range(frames):
                    sides = np.flatnonzero(sampled[i])
                    if len(sides):
                        ok, img = cap.read()
                    else:
                        ok = cap.grab()  # Advance without converting a skipped frame to BGR.
                    if not ok:
                        raise ValueError(f"Cannot decode HaMeR video frame {start + i + 1}")
                    for side in sides:
                        b = boxes[i, side]
                        center = (b[:2] + b[2:]) / 2
                        crop = _crop_hand(img, center, size[i, side], cfg.MODEL.BBOX_SHAPE,
                                          cfg.MODEL.IMAGE_SIZE, flip=side == 0)
                        crop = crop[:, :, ::-1].transpose(2, 0, 1).astype(np.float32)
                        pending.append((i, side, (crop - mean) / std))
                    if len(pending) >= prepared_size or i == frames - 1:
                        yield i + 1, pending
                        pending = []

            # Decode and crop ahead on CPU while the previous batch is on GPU.
            # Keep autocast's FP16 copies of the weights between batches.
            last_progress = 0.
            with torch.inference_mode(), torch.autocast("cuda", dtype=torch.float16):
                for current, items in _prefetch(prepare_batches()):
                    infer_prepared(items)
                    now = time.monotonic()
                    if now - last_progress >= .5 or current == frames:
                        _progress(current, f"HaMeR 手指精修 · {current} / {frames} 帧")
                        last_progress = now
            del backbone, head
    finally:
        cap.release()

    if stride > 1:
        _fill_sampled_rotations(rotations, valid, sampled)

    # A missing crop keeps the FlowHMR pose and splits smoothing at that gap.
    for side in range(2):
        for a, b in _runs(valid[:, side]):
            if b - a < 2:
                continue
            quats = Rotation.from_matrix(rotations[a:b, side].reshape(-1, 3, 3)).as_quat().reshape(b - a, 15, 4)
            signs = np.where((quats[:-1] * quats[1:]).sum(axis=-1) < 0, -1, 1)
            quats[1:] *= np.cumprod(signs, axis=0)[..., None]
            smooth = gaussian_filter1d(quats, sigma=1., axis=0, mode="nearest", truncate=3.)
            rotations[a:b, side] = Rotation.from_quat(smooth.reshape(-1, 4)).as_matrix().reshape(b - a, 15, 3, 3)
    np.savez(target, rotations=rotations, valid=valid, sampled=sampled)
    _progress(frames, "HaMeR 手指精修完成" if valid.any() else "HaMeR 未找到有效手部画面，已保留原动作")


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from flowhmr.runtime_environments import RUNTIME

    RUNTIME.configure_process()
    video, boxes, start, gpu, target, stride = sys.argv[1:]
    run(Path(video), Path(boxes), int(start), int(gpu), Path(target), int(stride))
