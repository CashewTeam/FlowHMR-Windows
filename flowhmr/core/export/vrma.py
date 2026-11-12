"""Write a VRM Animation 1.0 GLB from a saved SMPL-H motion."""

from collections.abc import Mapping
import json
from operator import index
import os
from pathlib import Path
from struct import pack
from tempfile import NamedTemporaryFile

import numpy as np
from scipy.spatial.transform import Rotation

from ..bodymodels.smplh_data import load_smplh_data


# A +15 degree neck adjustment on the calibrated VRM 0.x avatars becomes
# -15 in VRM Animation's +Z-forward space. The old head bias is canceled by
# the user's +15 degree head adjustment, so the head keeps its captured turn.
VRM_NECK_PITCH = -15

_EXTENSION = "VRMC_vrm_animation"
_JOINT_NAMES = (
    "hips", "leftUpperLeg", "rightUpperLeg", "spine", "leftLowerLeg", "rightLowerLeg", "chest",
    "leftFoot", "rightFoot", "upperChest", "leftToes", "rightToes", "neck",
    "leftShoulder", "rightShoulder", "head", "leftUpperArm", "rightUpperArm", "leftLowerArm",
    "rightLowerArm", "leftHand", "rightHand",
    *(side + finger + segment for side in ("left", "right")
      for finger in ("Index", "Middle", "Little", "Ring", "Thumb")
      for segment in (("Metacarpal", "Proximal", "Distal") if finger == "Thumb"
                      else ("Proximal", "Intermediate", "Distal"))),
)
_JOINT_COUNT = len(_JOINT_NAMES)
_MIRROR_X = np.array((-1., 1., 1.))


def _motion_arrays(motion, start_frame):
    poses = np.asarray(motion["poses"], dtype=np.float64)
    if poses.ndim == 2 and poses.shape[1] == _JOINT_COUNT * 3:
        poses = poses.reshape(-1, _JOINT_COUNT, 3)
    translation = np.asarray(motion["trans"], dtype=np.float64)
    if (poses.ndim != 3 or poses.shape[1:] != (_JOINT_COUNT, 3) or not len(poses)
            or translation.shape != (len(poses), 3)
            or not np.isfinite(poses).all() or not np.isfinite(translation).all()):
        raise ValueError("Invalid SMPL-H poses or translations for VRMA export")
    fps = float(np.asarray(motion.get("mocap_framerate", 30)).item())
    start = index(start_frame)
    if not np.isfinite(fps) or fps <= 0 or start < 0:
        raise ValueError("Invalid VRMA timeline")
    seconds = (np.arange(len(poses), dtype=np.float64) + start) / fps
    times = _float32(seconds)
    # Some readers floor the first frame and ceil the last of a sequence.
    # Round the float32 endpoints toward the interior of the source interval.
    if times[0] < seconds[0]:
        times[0] = np.nextafter(times[0], np.float32(np.inf))
    if len(times) > 1 and times[-1] > seconds[-1]:
        times[-1] = np.nextafter(times[-1], np.float32(-np.inf))
    if np.any(np.diff(times) <= 0):
        raise ValueError("VRMA frame times exceed float32 precision")
    return poses, translation, times


def _float32(values):
    if not np.isfinite(values).all() or np.max(np.abs(values)) > np.finfo(np.float32).max:
        raise ValueError("VRMA data exceed float32 range")
    return values.astype("<f4", copy=False)


def _palm_frame(offsets, first):
    along = offsets[first + 3].copy()  # Wrist to middle finger.
    length = np.linalg.norm(along)
    if length <= 1e-7:
        raise ValueError("Degenerate SMPL-H hand basis for VRMA export")
    along /= length
    across = offsets[first] - offsets[first + 6]  # Little finger to index finger.
    across -= np.dot(across, along) * along
    width = np.linalg.norm(across)
    if width <= 1e-7:
        raise ValueError("Degenerate SMPL-H hand basis for VRMA export")
    across /= width
    return np.stack((across, along, np.cross(across, along)), axis=1)


def _rest_skeleton(reference, motion):
    model = load_smplh_data(reference)
    shape = model["shapedirs"]
    betas = np.atleast_2d(np.asarray(motion.get("betas", ()), dtype=np.float64))
    if betas.ndim != 2 or not len(betas) or not np.isfinite(betas).all():
        raise ValueError("Invalid SMPL-H shape for VRMA export")
    betas = betas[0, :shape.shape[-1]]
    vertices = model["v_template"] + shape[:, :, :len(betas)] @ betas
    joints = model["J_regressor"] @ vertices
    parents = model["kintree_table"][0].astype(np.int64, copy=True)
    parents[0] = -1
    original = joints.copy()
    original[1:] -= joints[parents[1:]]
    hip_height = joints[0, 1] - np.min(vertices[:, 1])
    if not np.isfinite(joints).all() or not np.isfinite(hip_height) or hip_height <= 0:
        raise ValueError("Invalid SMPL-H rest geometry for VRMA export")

    # The rest skeleton faces +Z, has horizontal arms and fingers, and is
    # mirrored across X. Keep segment lengths from the motion's body shape.
    rest = original.copy()
    rest[0] = (0., hip_height, 0.)
    rest[[3, 6, 9, 12, 15], 0] = 0.
    names = {name: i for i, name in enumerate(_JOINT_NAMES)}
    for name, joint in names.items():
        if name.startswith("left"):
            other = names["right" + name[4:]]
            rest[joint] = (original[joint] + original[other] * _MIRROR_X) * 0.5
            rest[other] = rest[joint] * _MIRROR_X

    basis = np.repeat(np.eye(3)[None], _JOINT_COUNT, axis=0)

    def align(joint, child, direction=None):
        if direction is not None:
            rest[child] = np.linalg.norm(rest[child]) * np.asarray(direction)
        if min(np.linalg.norm(original[child]), np.linalg.norm(rest[child])) <= 1e-7:
            raise ValueError("Degenerate SMPL-H bone for VRMA export")
        basis[joint] = Rotation.align_vectors(original[child:child + 1], rest[child:child + 1])[0].as_matrix()

    for side in (0, 1):
        axis = (1. if side == 0 else -1., 0., 0.)
        align(1 + side, 4 + side, (0., -1., 0.))
        align(4 + side, 7 + side, (0., -1., 0.))
        toe = 10 + side
        rest[toe, 2] = np.hypot(rest[toe, 0], rest[toe, 2])
        rest[toe, 0] = 0.
        align(7 + side, toe)
        basis[toe] = basis[7 + side]

        align(13 + side, 16 + side)
        align(16 + side, 18 + side, axis)
        align(18 + side, 20 + side, axis)
        first = 22 + 15 * side
        rest[first:first + 15:3, 1] = 0.
        rest[first + 3, 2] = 0.
        basis[20 + side] = _palm_frame(original, first) @ _palm_frame(rest, first).T
        for joint in range(first, first + 15, 3):
            direction = axis if joint != first + 12 else np.array((axis[0], 0., 1.)) / np.sqrt(2.)
            align(joint, joint + 1, direction)
            align(joint + 1, joint + 2, direction)
            basis[joint + 2] = basis[joint + 1]

    if not np.isfinite(basis).all():
        raise ValueError("Invalid SMPL-H hand basis for VRMA export")
    return rest, parents, basis, joints[0]


def _quaternions(rotations):
    values = Rotation.from_matrix(rotations).as_quat()
    dots = np.einsum("ij,ij->i", values[:-1], values[1:])
    values[1:] *= np.cumprod(np.where(dots < 0., -1., 1.))[:, None]
    return _float32(values)


def _encode(motion, reference_path, start_frame, floor):
    poses, translation, times = _motion_arrays(motion, start_frame)
    rest, parents, basis, source_hips = _rest_skeleton(reference_path, motion)
    locations = _float32(translation + source_hips - np.array((0., floor, 0.)))
    nodes = [{"name": name, "translation": offset.tolist()} for name, offset in zip(_JOINT_NAMES, rest)]
    for joint, parent in enumerate(parents[1:], 1):
        nodes[parent].setdefault("children", []).append(joint)
    data = bytearray()
    views, accessors, samplers, channels = [], [], [], []

    def add_accessor(values, kind, **bounds):
        length = values.nbytes
        view = len(views)
        views.append({"buffer": 0, "byteOffset": len(data), "byteLength": length})
        accessors.append({"bufferView": view, "componentType": 5126, "count": len(values), "type": kind, **bounds})
        data.extend(values.tobytes())
        return len(accessors) - 1

    time_input = add_accessor(times, "SCALAR", min=[float(times[0])], max=[float(times[-1])])

    def add_track(joint, path, values, kind):
        channels.append({"sampler": len(samplers), "target": {"node": joint, "path": path}})
        samplers.append({"input": time_input, "output": add_accessor(values, kind), "interpolation": "LINEAR"})

    add_track(0, "translation", locations, "VEC3")
    neck_pitch = Rotation.from_euler("x", VRM_NECK_PITCH, degrees=True).as_matrix()
    for joint, parent in enumerate(parents):
        # Convert a source local rotation between the corrected rest axes of
        # this joint and its parent while keeping the animated bone direction.
        rotations = Rotation.from_rotvec(poses[:, joint]).as_matrix() @ basis[joint]
        if parent >= 0:
            rotations = basis[parent].T @ rotations
        if _JOINT_NAMES[joint] == "neck":
            rotations = rotations @ neck_pitch
        add_track(joint, "rotation", _quaternions(rotations), "VEC4")

    document = {
        "asset": {"version": "2.0", "generator": "FlowHMR"},
        "extensionsUsed": [_EXTENSION],
        "extensions": {_EXTENSION: {"specVersion": "1.0", "humanoid": {
            "humanBones": {name: {"node": joint} for joint, name in enumerate(_JOINT_NAMES)},
        }}},
        "scene": 0, "scenes": [{"nodes": [0]}], "nodes": nodes,
        "animations": [{"name": "FlowHMR Motion", "samplers": samplers, "channels": channels}],
        "buffers": [{"byteLength": len(data)}], "bufferViews": views, "accessors": accessors,
    }
    header = json.dumps(document, separators=(",", ":"), allow_nan=False).encode("utf-8")
    header += b" " * (-len(header) % 4)
    return header, data


def export_vrma(npz_data, outname, *, reference_path, start_frame=0, floor=0.):
    """Export one person, using the final corrected motion and source time offset."""
    if not isinstance(npz_data, Mapping):
        with np.load(npz_data, allow_pickle=False) as source:
            npz_data = {key: source[key] for key in ("poses", "trans", "betas", "mocap_framerate") if key in source}
    header, data = _encode(npz_data, reference_path, start_frame, floor)
    size = 28 + len(header) + len(data)
    if size >= 1 << 32:
        raise ValueError("VRMA exceeds the GLB size limit")
    destination = Path(outname)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with NamedTemporaryFile(mode="wb", prefix=".vrma-", suffix=".part", dir=destination.parent,
                                delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(pack("<4sIII4s", b"glTF", 2, size, len(header), b"JSON"))
            stream.write(header)
            stream.write(pack("<I4s", len(data), b"BIN\0"))
            stream.write(data)
        os.replace(temporary, destination)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return destination
