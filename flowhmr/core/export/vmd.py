"""Export FlowHMR's 30 fps SMPL-H motion for a standard humanoid PMX rig."""

from collections.abc import Mapping
from operator import index
import os
from pathlib import Path
from struct import Struct
from tempfile import NamedTemporaryFile

import numpy as np
from scipy.spatial.transform import Rotation


_UINT32_MAX = (1 << 32) - 1
_VMD_HEADER = Struct("<30s20sI")
_VMD_BONE_KEY = Struct("<15sI3f4f64s")
_VMD_COUNT = Struct("<I")
_VMD_DISPLAY_KEY = Struct("<IBI")
_VMD_IK_KEY = Struct("<20sB")

# VMD has no unit metadata. At the usual MMD Tools PMX/VMD import scale of
# 0.08, one MMD unit is 0.08 m. Both rigs are Y-up, with opposite Z directions.
_METRES_PER_UNIT = 0.08
_DEFAULT_SOURCE_FLOOR = -1.16
_POSITION_MIRROR = np.array((1.0, 1.0, -1.0))
_AXIAL_MIRROR = -_POSITION_MIRROR

# Diagonal curves in both the packed and shifted rows. Bytes 2 and 3 in
# the packed row also carry MMD's default physics flag (00 00).
_LINEAR_CHANNELS = bytes(8) + bytes([127]) * 8
_LINEAR = b"".join(_LINEAR_CHANNELS[i:].ljust(16, b"\0") for i in range(4))
_IK_NAMES = tuple(
    f"{side}{part}{suffix}".encode("shift_jis")
    for side in ("左", "右")
    for part in ("足", "つま先")
    for suffix in ("ＩＫ", "IK")
)


def _frame(value):
    try:
        frame = index(value)
    except TypeError as exc:
        raise ValueError("VMD frame indices must be integers") from exc
    if not 0 <= frame <= _UINT32_MAX:
        raise ValueError("VMD frame indices must be nonnegative 32-bit integers")
    return frame


def _motion_arrays(motion):
    try:
        fps = np.asarray(motion.get("mocap_framerate", 30))
        if fps.size != 1 or float(fps.item()) != 30:
            raise ValueError("VMD export requires a 30 fps motion")
        poses = np.asarray(motion["poses"], dtype=np.float64)
        trans = np.asarray(motion["trans"], dtype=np.float64)
    except (KeyError, TypeError, OverflowError) as exc:
        raise ValueError("Invalid SMPL-H motion for VMD export") from exc
    if poses.ndim == 2 and poses.shape[1] == 156:
        poses = poses.reshape(-1, 52, 3)
    if (poses.ndim != 3 or poses.shape[1:] != (52, 3) or not len(poses)
            or trans.shape != (len(poses), 3)
            or not np.isfinite(poses).all() or not np.isfinite(trans).all()):
        raise ValueError("Invalid SMPL-H poses or translations for VMD export")
    return poses, trans


def _source_floor(motion, reference_path):
    if reference_path is None:
        return _DEFAULT_SOURCE_FLOOR
    from ..bodymodels.smplh_data import load_smplh_data

    body = load_smplh_data(reference_path)
    betas = np.atleast_2d(np.asarray(motion.get("betas", ()), dtype=np.float64))
    if betas.ndim != 2 or not len(betas) or not np.isfinite(betas).all():
        raise ValueError("Invalid SMPL-H shape for VMD export")
    betas = betas[0, :body["shapedirs"].shape[-1]]
    rest_y = body["v_template"][:, 1] + body["shapedirs"][:, 1, :len(betas)] @ betas
    return float(rest_y.min())


def _rotation_tracks(poses):
    rotations = Rotation.from_rotvec(
        (poses * _AXIAL_MIRROR).reshape(-1, 3)
    ).as_matrix().reshape(-1, 52, 3, 3)
    if not np.isfinite(rotations).all():
        raise ValueError("Invalid SMPL-H rotations for VMD export")

    # Standard MMD 上半身 and 下半身 branch under the center; placing the
    # root rotation on センター preserves both branches and global heading.
    tracks = [
        ("センター", rotations[:, 0]),
        ("下半身", np.broadcast_to(np.eye(3), rotations[:, 0].shape)),
        ("上半身", rotations[:, 3]),
        ("上半身2", rotations[:, 6] @ rotations[:, 9]),
        ("首", rotations[:, 12]),
        ("頭", rotations[:, 15]),
    ]
    for side_index, side in enumerate(("左", "右")):
        sign = 1 if side_index == 0 else -1
        # The deforming toe on semi-standard D-leg rigs is 足先EX (a child
        # of 足首D), separate from the original toe used by toe IK.
        for name, joint in (("足", 1), ("ひざ", 4), ("足首", 7),
                            ("つま先", 10), ("足先EX", 10)):
            tracks.append((side + name, rotations[:, joint + side_index]))

        # Most PMX models rest with arms angled down while SMPL-H uses a
        # near-horizontal rest pose. These are nominal A-pose corrections,
        # expressed in the shared VMD rest axes, not target-specific fitting.
        parent_basis = np.eye(3)
        for name, joint, degrees in (("肩", 13, 26), ("腕", 16, 35),
                                     ("ひじ", 18, 40), ("手首", 20, 40)):
            basis = Rotation.from_euler("z", sign * degrees, degrees=True).as_matrix()
            tracks.append((side + name, parent_basis.T @ rotations[:, joint + side_index] @ basis))
            parent_basis = basis

        hand = 22 + side_index * 15
        for finger, name in enumerate(("人指", "中指", "小指", "薬指", "親指")):
            digits = "０１２" if name == "親指" else "１２３"
            for segment, digit in enumerate(digits):
                local = rotations[:, hand + finger * 3 + segment]
                tracks.append((side + name + digit, parent_basis.T @ local @ parent_basis))
    return tracks


def _quaternions(matrices):
    values = Rotation.from_matrix(matrices).as_quat()
    # Keep XYZW in the same hemisphere as the preceding key to avoid
    # unnecessary discontinuities for readers that interpolate components.
    dots = np.einsum("ij,ij->i", values[:-1], values[1:])
    values[1:] *= np.cumprod(np.where(dots < 0, -1., 1.))[:, None]
    return values


def _write(stream, poses, locations, start, scene_end):
    tracks = _rotation_tracks(poses)
    count = len(poses) * len(tracks)
    if count > _UINT32_MAX:
        raise ValueError("Too many VMD keys")
    stream.write(_VMD_HEADER.pack(b"Vocaloid Motion Data 0002", b"FlowHMR", count))
    for name, rotations in tracks:
        key_name = name.encode("shift_jis")
        if len(key_name) > 15:
            raise ValueError(f"VMD bone name exceeds 15 bytes: {name}")
        is_center = name == "センター"
        for i, quaternion in enumerate(_quaternions(rotations)):
            location = locations[i] if is_center else (0., 0., 0.)
            stream.write(_VMD_BONE_KEY.pack(key_name, start + i, *location, *quaternion, _LINEAR))

    # Empty morph, camera, light and self-shadow tracks, followed by model
    # display/IK keys. The captured legs are FK and must not be pulled toward
    # the PMX's default foot IK target.
    stream.write(b"\0" * (4 * _VMD_COUNT.size))
    end = start + len(poses) - 1
    display = {0: False, start - 1: False} if start else {}
    display[start] = True
    if end < scene_end:
        display[end] = True
        display[end + 1] = False
    stream.write(_VMD_COUNT.pack(len(display)))
    # Some importers interpolate these switches as ordinary curves. Adjacent
    # keys at each boundary keep the last visible/invisible frame exact.
    for frame, visible in display.items():
        stream.write(_VMD_DISPLAY_KEY.pack(frame, visible, len(_IK_NAMES)))
        for name in _IK_NAMES:
            stream.write(_VMD_IK_KEY.pack(name, False))


def export_vmd(npz_data, outname, *, start_frame=0, scene_end=None, reference_path=None):
    """Write one VMD. For a scene, pass one person's already aligned motion.

    `npz_data` is a path or a mapping with `poses`, `trans` and optional
    `mocap_framerate`/`betas`. A reference model is optional: supplying it
    uses the source body's exact rest floor rather than the nominal offset.
    """
    if not isinstance(npz_data, Mapping):
        with np.load(npz_data, allow_pickle=False) as data:
            motion = {key: data[key] for key in ("poses", "trans", "mocap_framerate", "betas") if key in data}
    else:
        motion = npz_data
    poses, trans = _motion_arrays(motion)
    start = _frame(start_frame)
    end = _frame(start + len(poses) - 1)
    scene_end = end if scene_end is None else _frame(scene_end)
    if scene_end < end:
        raise ValueError("VMD scene ends before the motion")
    locations = trans * _POSITION_MIRROR
    locations[:, 1] += _source_floor(motion, reference_path)
    locations /= _METRES_PER_UNIT
    if not np.isfinite(locations).all() or np.max(np.abs(locations)) > np.finfo(np.float32).max:
        raise ValueError("VMD translation exceeds float32 range")

    destination = Path(outname)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with NamedTemporaryFile(mode="wb", prefix=".vmd-", suffix=".part", dir=destination.parent,
                                delete=False) as stream:
            temporary = Path(stream.name)
            _write(stream, poses, locations, start, scene_end)
        os.replace(temporary, destination)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return destination
