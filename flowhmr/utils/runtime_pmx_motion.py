"""CPU preview source for retargeting a saved job onto a local character."""
from pathlib import Path

import numpy as np
from scipy.spatial.transform import Rotation

from flowhmr.core.bodymodels.smplh_data import load_smplh_data


def source_sole_heights(directory, frames, rest_vertices, rest_joints, weights):
    """Use the same source mesh shown in the viewport to distinguish foot roll from flat support."""
    path = Path(directory) / "mesh/vertices.bin"
    nverts = len(rest_vertices)
    try:
        if path.stat().st_size != frames * nverts * 3 * 2:
            return None
    except FileNotFoundError:
        return None
    probes = []
    for ankle, toe in ((7, 10), (8, 11)):
        axis = (rest_joints[toe] - rest_joints[ankle])[[0, 2]]
        length = np.linalg.norm(axis)
        if length < 1e-6:
            return None
        along = (rest_vertices[:, [0, 2]] - rest_joints[ankle, [0, 2]]) @ (axis / length)
        foot = weights[:, ankle] + weights[:, toe] >= 0.55
        for part in (along < length * 0.4, along >= length * 0.4):
            ids = np.flatnonzero(foot & part)
            if len(ids) < 4:
                return None
            probes.append(ids[np.argsort(rest_vertices[ids, 1])[:16]])
    mesh = np.memmap(path, dtype="<f2", mode="r", shape=(frames, nverts, 3))
    try:
        result = np.stack([mesh[:, ids, 1].min(axis=1) for ids in probes], axis=1).astype("<f4")
    finally:
        del mesh
    return result if np.isfinite(result).all() else None


def pmx_motion(directory, reference_path, joint_stream, inspection, floor):
    """Pack rest and posed joints, world XYZW, contact tracks and optional source soles."""
    frames = int(inspection["frames"])
    joint_count = 52
    parents = inspection["parents"]
    if frames <= 0 or len(parents) != joint_count or parents[0] != -1 or not np.isfinite(floor):
        raise ValueError("Invalid source skeleton")
    if any(not 0 <= parent < i for i, parent in enumerate(parents[1:], 1)):
        raise ValueError("Invalid source skeleton hierarchy")

    with np.load(Path(directory) / "motion.npz", allow_pickle=False) as motion:
        poses = np.asarray(motion["poses"], dtype=np.float32).reshape(frames, joint_count, 3)
        betas = np.asarray(motion["betas"], dtype=np.float32).reshape(-1)
        contacts = motion.get("foot_contacts")
    if not np.isfinite(poses).all() or not np.isfinite(betas).all():
        raise ValueError("Invalid source motion values")

    model = load_smplh_data(reference_path)
    shape = np.asarray(model["shapedirs"], dtype=np.float32)
    betas = betas[:shape.shape[-1]]
    rest_vertices = np.asarray(model["v_template"], dtype=np.float32) + shape[:, :, :len(betas)] @ betas
    rest_joints = np.asarray(model["J_regressor"], dtype=np.float32) @ rest_vertices
    soles = source_sole_heights(directory, frames, rest_vertices, rest_joints, model["weights"])

    xyz = joint_count * 3
    xyzw = joint_count * 4
    joint_end = (frames + 1) * xyz
    rotation_end = joint_end + frames * xyzw
    contact_end = rotation_end + frames * 4
    output = np.zeros(contact_end + (frames * 4 if soles is not None else 0), dtype="<f4")
    output[:xyz] = rest_joints.reshape(-1)
    joints = np.fromfile(joint_stream, dtype="<f4")
    if joints.size != frames * xyz or not np.isfinite(joints).all():
        raise ValueError("Invalid source joint stream")
    output[xyz:joint_end] = joints
    global_tracks = output[joint_end:rotation_end].reshape(frames, xyzw)
    for start in range(0, frames, 2048):
        batch = poses[start:start+2048]
        matrices = Rotation.from_rotvec(batch.reshape(-1, 3)).as_matrix().reshape(-1, joint_count, 3, 3)
        for i, parent in enumerate(parents[1:], 1):
            matrices[:, i] = matrices[:, parent] @ matrices[:, i]
        global_tracks[start:start+len(batch)] = Rotation.from_matrix(matrices.reshape(-1, 3, 3)).as_quat().reshape(-1, xyzw)
    if contacts is not None and contacts.shape == (frames, 4) and np.isfinite(contacts).all():
        output[rotation_end:contact_end] = np.clip(contacts, 0, 1).reshape(-1)
    if soles is not None:
        output[contact_end:] = soles.reshape(-1)
    return output.tobytes(), {
        "frames": frames, "parents": parents, "floor": float(floor), "restFloor": float(rest_vertices[:, 1].min()),
        "hasSoles": soles is not None,
    }
