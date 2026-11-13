"""Place independent FlowHMR tracks in a shared scene without changing their local motion."""

import json
from collections import defaultdict
from pathlib import Path
from zipfile import BadZipFile

import numpy as np


SCENE_VERSION = 1
MHR_JOINTS = [5, 6, 9, 10, 11, 12, 13, 14]  # shoulders, hips, knees, ankles
SMPL_JOINTS = [16, 17, 1, 2, 4, 5, 7, 8]


def yaw_matrix(yaw):
    c, s = np.cos(yaw), np.sin(yaw)
    return np.array([[c, 0., s], [0., 1., 0.], [-s, 0., c]])


def _directions(p):
    ls, rs, lh, rh, lk, rk, la, ra = np.moveaxis(p, 1, 0)
    values = np.stack((ls - rs, lh - rh, (ls + rs - lh - rh) / 2,
                       lh - lk, rh - rk, lk - la, rk - ra), axis=1)
    return values, np.broadcast_to([2., 2., 2., .5, .5, .35, .35], values.shape[:2])


def _rotation(source, target, weights):
    """Fit a proper rotation to matched directions, downweighting pose outliers."""
    dimension = source.shape[-1]
    source, target, weights = source.reshape(-1, dimension), target.reshape(-1, dimension), weights.ravel()
    norm_s, norm_t = np.linalg.norm(source, axis=1), np.linalg.norm(target, axis=1)
    good = (norm_s > .08) & (norm_t > .08)
    if np.count_nonzero(good) < 2:
        return np.eye(dimension)
    a, b, w = source[good] / norm_s[good, None], target[good] / norm_t[good, None], weights[good]
    effective = w
    for _ in range(4):
        u, _, vt = np.linalg.svd((b * effective[:, None]).T @ a)
        u[:, -1] *= np.linalg.det(u @ vt)
        rot = u @ vt
        error = np.linalg.norm(a @ rot.T - b, axis=1)
        effective = w * np.minimum(1., .35 / np.maximum(error, 1e-8))
    return rot


def _camera_scale(paths):
    """Estimate the one VGGT-to-motion scale, eliminating each actor's unknown offset."""
    shifts, residuals = [], []
    for camera, target in paths:
        shifts.append(camera - np.median(camera, axis=0))
        residuals.append(target - np.median(target, axis=0))
    x, y = np.concatenate(shifts), np.concatenate(residuals)
    if np.quantile(np.linalg.norm(x, axis=1), .9) < .008:
        return 0.
    w = np.ones((len(x), 1))
    for _ in range(5):
        scale = max(0., float(np.sum(w * x * y) / max(1e-10, np.sum(w * x * x))))
        error = np.linalg.norm(y - scale * x, axis=1, keepdims=True)
        w = np.minimum(1., .25 / np.maximum(error, 1e-8))
    return scale


def align_tracks(tracks):
    """Fit a shared camera orientation, then one fixed yaw and offset per track.

    Each entry contains paired FlowHMR and camera-relative SAM joints on the same
    source frames, plus the shared camera extrinsics on those frames.
    """
    for t in tracks:
        rotation, translation = t["rt"][:, :3, :3], t["rt"][:, :3, 3]
        t["world"] = t["observed"] @ rotation
        t["camera"] = np.einsum("tji,tj->ti", rotation, -translation)

    ref = tracks[0]
    a, w = _directions(ref["world"])
    b, _ = _directions(ref["motion"])
    shared = _rotation(a, b, w)
    paths = []
    horizontal = [0, 2]
    for index, t in enumerate(tracks):
        world = t["world"] @ shared.T
        a, w = _directions(t["motion"])
        b, _ = _directions(world)
        rot = _rotation(a[..., horizontal], b[..., horizontal], w) if index else np.eye(2)
        t["yaw"] = float(np.arctan2(rot[0, 1], rot[0, 0]))
        pelvis = t["motion"][:, 2:4].mean(axis=1) @ yaw_matrix(t["yaw"]).T
        observed = world[:, 2:4].mean(axis=1)
        camera = (t["camera"] @ shared.T)[:, horizontal]
        delta = (observed - pelvis)[:, horizontal]
        paths.append((camera, -delta))
        t["placement"] = delta
        t["camera_xz"] = camera
    scale = _camera_scale(paths)
    offsets = [np.median(t["placement"] + scale * t["camera_xz"], axis=0) for t in tracks]
    origin = offsets[0]
    for t, fitted in zip(tracks, offsets):
        delta = t["placement"] + scale * t["camera_xz"] - fitted
        t["error"] = float(np.median(np.linalg.norm(delta, axis=1)))
        x, z = fitted - origin
        t["offset"] = [float(x), -float(t["floor"]), float(z)]
    return scale


def _shared_camera(video, entries, destination, frame_range, device, check, notify):
    from .runtime_vggt_camera import extract_vggt_camera_isolated, make_static_camera

    first, last = frame_range
    mode = "static" if entries[0][0]["params"].get("static_camera") else "vggt"
    for _, directory in entries:
        path = directory / "input" / f"input_{mode}_camera.npz"
        try:
            with np.load(path, allow_pickle=False) as data:
                start, end = map(int, data["frame_range"])
                if start <= first <= last <= end:
                    sample = slice(first - start, last - start + 1)
                    return data["K"][sample], data["camera_RT"][sample], mode
        except (OSError, ValueError, KeyError, BadZipFile):
            pass
    if mode == "static":
        import cv2

        cap = cv2.VideoCapture(str(video))
        try:
            hw = (int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)), int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)))
        finally:
            cap.release()
        result = make_static_camera(hw, frame_range=frame_range)
    else:
        result = extract_vggt_camera_isolated(str(video), output_npz=str(destination / "camera.npz"),
                                             frame_range=frame_range, expected_len=last - first + 1,
                                             device=device, frame_interval=0, cancel_check=check, on_progress=notify)
    return result.K, result.camera_RT, mode


def _observe(video, tracks, intrinsics, first, extractor, check, notify):
    import cv2
    import torch

    samples = defaultdict(list)
    for t in tracks:
        t["samples"] = []
        start, end = t["range"]
        for frame in np.linspace(start, end, min(25, end - start + 1), dtype=int):
            samples[int(frame)].append(t)
    cap = cv2.VideoCapture(str(video))
    position = 0
    try:
        for index, (frame, selected) in enumerate(sorted(samples.items()), 1):
            check()
            if frame - position > 10 and cap.set(cv2.CAP_PROP_POS_FRAMES, frame):
                position = frame
            for _ in range(position, frame):
                if not cap.grab():
                    raise ValueError("原视频帧无法读取，无法对齐多人站位。")
            ok, image = cap.read()
            position = frame + 1
            if not ok:
                raise ValueError("原视频帧无法读取，无法对齐多人站位。")
            image = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
            cursor, size = 0, 8
            while cursor < len(selected):
                chunk = selected[cursor:cursor + size]
                boxes = np.stack([t["boxes"][frame - t["range"][0]] for t in chunk])
                try:
                    points = extractor.scene_keypoints(image, boxes, intrinsics[frame - first], MHR_JOINTS)
                except torch.cuda.OutOfMemoryError:
                    if size == 1:
                        raise
                    points = None
                if points is None:
                    torch.cuda.empty_cache()
                    size //= 2
                    continue
                if points.shape != (len(chunk), len(MHR_JOINTS), 3):
                    raise ValueError("多人站位采样返回了不完整的人体位置。")
                for t, pose in zip(chunk, points):
                    if np.isfinite(pose).all() and (pose[:, 2] > .05).all():
                        t["samples"].append((frame, pose))
                cursor += len(chunk)
            if index == len(samples) or index % 8 == 0:
                notify(f"对齐多人站位 · {index}/{len(samples)} 个素材帧")
    finally:
        cap.release()


def _save_scene_motion(destination, tracks, people):
    from scipy.spatial.transform import Rotation

    payload = dict(mocap_framerate=np.int64(30),
                   people=np.array([p["person"] for p in people]),
                   track_ranges=np.array([[p["start"], p["end"]] for p in people]))
    for track, person in zip(tracks, people):
        with np.load(track["directory"] / "motion.npz", allow_pickle=False) as motion:
            poses = motion["poses"].copy().reshape(-1, 52, 3)
            rot = yaw_matrix(person["yaw"])
            poses[:, 0] = Rotation.from_matrix(rot @ Rotation.from_rotvec(poses[:, 0]).as_matrix()).as_rotvec()
            translations = (motion["trans"] + track["rest_root"]) @ rot.T - track["rest_root"] + person["offset"]
            suffix = person["person"]
            payload[f"poses_{suffix}"] = poses
            payload[f"trans_{suffix}"] = translations.astype(np.float32)
            payload[f"betas_{suffix}"] = motion["betas"]
            payload[f"body_reference_{suffix}"] = person["body_reference"]
    tmp = destination / "motion.npz.part"
    with tmp.open("wb") as stream:
        np.savez(stream, **payload)
    tmp.replace(destination / "motion.npz")


def build_group_scene(destination, members, inspection, acquire_sam, device, *, check=lambda: None, notify=lambda _: None):
    """Create one scene manifest and one aligned motion file for completed jobs."""
    entries = [(job, Path(directory)) for job, directory in members]
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    video = entries[0][1] / "input" / "input_30fps.mp4"
    tracks = []
    for job, directory in entries:
        check()
        with np.load(directory / "input" / "input_bbox.npz", allow_pickle=False) as data:
            start, end = map(int, data["start_end"])
            boxes = data["bbox"]
        reference = job.get("body_reference") or "neutral"
        stream, info = inspection.load(directory, body_reference=reference)
        if info["frames"] != end - start + 1 or len(boxes) != info["frames"]:
            raise ValueError("人物轨迹与原视频时间轴不一致。")
        with np.load(directory / "motion.npz", allow_pickle=False) as data:
            # The pelvis joint is the unrotated SMPL shape root plus translation.
            all_joints = np.fromfile(stream, dtype="<f4").reshape(info["frames"], -1, 3)
            rest_root = all_joints[0, 0] - data["trans"][0]
        ground = (job.get("correction") or {}).get("floor_m", info["ground"]["height_m"])
        tracks.append(dict(job=job, directory=directory, range=(start, end), boxes=boxes,
                           floor=ground, joints=all_joints, rest_root=rest_root, body_reference=reference))
    first = min(t["range"][0] for t in tracks)
    last = max(t["range"][1] for t in tracks)
    K, rt, camera = _shared_camera(video, entries, destination, (first, last), device, check, notify)
    check()
    with acquire_sam() as extractor:
        _observe(video, tracks, K, first, extractor, check, notify)
    for t in tracks:
        if not t["samples"]:
            raise ValueError(f"人物 {t['job']['selected_person'] + 1} 没有可用的站位观测。")
        frames = np.array([frame for frame, _ in t["samples"]])
        t["observed"] = np.stack([pose for _, pose in t["samples"]])
        t["rt"] = rt[frames - first]
        t["motion"] = t["joints"][frames - t["range"][0]][:, SMPL_JOINTS]
    scale = align_tracks(tracks)
    people = [dict(id=t["job"]["id"], person=t["job"]["selected_person"] + 1,
                   start=t["range"][0], end=t["range"][1], yaw=t["yaw"], offset=t["offset"],
                   body_reference=t["body_reference"],
                   samples=len(t["samples"]), error_m=round(t["error"], 4)) for t in tracks]
    scene = dict(version=SCENE_VERSION, track_start=first, track_end=last, num_frames=last - first + 1,
                 fps=30, people=people, camera=camera, camera_scale=scale, ground_m=0.)
    if len({track["body_reference"] for track in tracks}) == 1:
        scene["body_reference"] = tracks[0]["body_reference"]
    check()
    _save_scene_motion(destination, tracks, people)
    tmp = destination / "scene.json.part"
    tmp.write_text(json.dumps(scene, ensure_ascii=False, allow_nan=False), encoding="utf-8")
    tmp.replace(destination / "scene.json")
    return scene
