"""Optional SMPL-H skin templates fitted to the job's original bind skeleton."""

import numpy as np

from ...model_files import SMPLH_FILES, smplh_available, smplh_reference_path
from .smplh_data import load_smplh_data


def available_variants():
    return [name for name in SMPLH_FILES if smplh_available(name)]


def model_path(name):
    if name not in SMPLH_FILES:
        raise ValueError("请选择中性、女性或男性 SMPL-H 体型。")
    if not smplh_available(name):
        raise ValueError("本机未安装所选 SMPL-H 体型。")
    return smplh_reference_path(name)


def fitted_skin(variant_path, reference_vertices, reference_joints, weights, faces, parents):
    """Fit the official mean template to the captured skeleton without using its betas."""
    variant = load_smplh_data(variant_path)
    vertices = np.asarray(variant["v_template"], dtype=np.float32)
    variant_weights = np.asarray(variant["weights"], dtype=np.float32)
    regressor = np.asarray(variant["J_regressor"], dtype=np.float32)
    tree = np.asarray(variant["kintree_table"][0], dtype=np.int64)
    if (vertices.shape != reference_vertices.shape or variant_weights.shape != weights.shape
            or regressor.shape != (len(parents), len(vertices))
            or not np.array_equal(tree[1:], parents[1:])
            or not np.array_equal(variant["f"], faces)):
        raise ValueError("所选体型与本次动作的 SMPL-H 骨架不兼容。")

    joints = regressor @ vertices
    fitted = vertices + variant_weights @ (reference_joints - joints)
    # Keep the original foot surfaces around the joints used for ground contact.
    foot = weights[:, [7, 8, 10, 11]].sum(axis=1)
    blend = np.clip(foot / .6, 0., 1.)[:, None]
    fitted = np.where(blend >= 1., reference_vertices, fitted + (reference_vertices - fitted) * blend)
    if not np.isfinite(fitted).all():
        raise ValueError("所选 SMPL-H 体型包含无效顶点。")
    return fitted


def write_preview_vertices(motion_path, variant_path, stream, *, reference_path=None):
    """Fit a different skin while keeping the job's original shaped skeleton."""
    import torch

    from .lbs_function import batch_rigid_transform, batch_rodrigues

    with np.load(motion_path, allow_pickle=False) as motion:
        poses = torch.from_numpy(motion["poses"].astype(np.float32)).reshape(-1, 52, 3)
        trans = torch.from_numpy(motion["trans"].astype(np.float32))
        betas = motion["betas"].astype(np.float32).reshape(1, -1)[0]
    reference = load_smplh_data(reference_path)
    shape = np.asarray(reference["shapedirs"], dtype=np.float32)
    betas = betas[:shape.shape[-1]]
    vertices = np.asarray(reference["v_template"], dtype=np.float32) + shape[..., :len(betas)] @ betas
    joints = np.asarray(reference["J_regressor"], dtype=np.float32) @ vertices
    weights = np.asarray(reference["weights"], dtype=np.float32)
    parents = np.asarray(reference["kintree_table"][0], dtype=np.int64).copy()
    parents[0] = -1
    surface = fitted_skin(variant_path, vertices, joints, weights, reference["f"], parents)

    weights = torch.from_numpy(weights)
    parents = torch.from_numpy(parents)
    joints = torch.from_numpy(joints)[None]
    points = torch.from_numpy(surface)
    points = torch.cat((points, torch.ones((len(points), 1), dtype=points.dtype)), dim=-1)
    with torch.inference_mode():
        for first in range(0, len(poses), 32):
            batch = poses[first:first + 32]
            matrices = batch_rodrigues(batch)
            _, transforms = batch_rigid_transform(matrices, joints.expand(len(batch), -1, -1), parents)
            skin = (weights @ transforms.flatten(2)).view(len(batch), len(points), 4, 4)
            vertices = (skin @ points[..., None])[..., :3, 0] + trans[first:first + len(batch), None]
            vertices.numpy().astype("<f2").tofile(stream)
