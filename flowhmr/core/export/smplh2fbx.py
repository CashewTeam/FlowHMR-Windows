"""
smplh2fbx.py (Constructive Converter)
Converts SMPL-H motion to FBX by constructing the mesh and skeleton from scratch.
"""

import logging
import os
from pathlib import Path
from typing import Optional, Tuple

import fbx
import numpy as np
import torch

from ..math.geometry import angle_axis_to_rotation_matrix
from ..bodymodels.smplh_data import load_smplh_data, validated_smplh_reference
from .fbx_utils import (
    animate_rotation_from_rotmats,
    animate_translation,
    apply_skinning,
    build_mesh_node,
    build_skeleton_nodes,
    create_manager,
    export_scene_atomic,
    store_bind_pose,
)

logger = logging.getLogger(__name__)

ASSETS = Path(__file__).resolve().parents[3] / "assets/body_models/smplh"
DEFAULT_OBJ_TEMPLATE_PATH = ASSETS / "textures/male_smplh.obj"

# The source rig vocabulary understood by FlowHMR 2 MMR and RGB MoCap.
# Order follows SMPL-H, including the order of its finger joints and weights.
FBX_JOINT_NAMES = (
    "Hips", "LeftUpLeg", "RightUpLeg", "Spine", "LeftLeg", "RightLeg", "Spine1",
    "LeftFoot", "RightFoot", "Spine2", "LeftToeBase", "RightToeBase", "Neck",
    "LeftShoulder", "RightShoulder", "Head", "LeftArm", "RightArm", "LeftForeArm",
    "RightForeArm", "LeftHand", "RightHand",
    *(f"{side}Hand{finger}{joint}" for side in ("Left", "Right")
      for finger in ("Index", "Middle", "Pinky", "Ring", "Thumb") for joint in (1, 2, 3)),
)
HEAD_JOINT = FBX_JOINT_NAMES.index("Head")


def _align_direction(source, target):
    source = source / np.linalg.norm(source)
    x, y, z = np.cross(source, target)
    cross = np.array([[0., -z, y], [z, 0., -x], [-y, x, 0.]])
    # Each target points out along the same side as its rest arm.
    return np.eye(3) + cross + cross @ cross / (1. + source @ target)


def _reference_rotations(joints, initial_root, pose):
    """Level the body and set both arm segments to the requested angle from horizontal."""
    rotations = np.repeat(np.eye(3)[None], len(joints), axis=0)
    forward = initial_root[[0, 2], 2]
    yaw = (np.arctan2(*forward) if np.linalg.norm(forward) > 1e-6
           else np.arctan2(-initial_root[2, 0], initial_root[0, 0]))
    c, s = np.cos(yaw), np.sin(yaw)
    rotations[0] = [[c, 0., s], [0., 1., 0.], [-s, 0., c]]
    angle = 0. if pose == "t" else np.pi / 4
    for upper, lower, hand in ((16, 18, 20), (17, 19, 21)):
        arm = joints[lower] - joints[upper]
        target = np.array([np.copysign(np.cos(angle), arm[0]), -np.sin(angle), 0.])
        rotations[upper] = _align_direction(arm, target)
        rotations[lower] = _align_direction(joints[hand] - joints[lower], rotations[upper].T @ target)
    return rotations


def _display_frames(joints, parents, *, head_top_y):
    """A right-handed frame with +Y along each bone's reference direction."""
    main_child = {0: 3, 9: 12, 20: 25, 21: 40}
    frames, tips = [], {}
    for joint, parent in enumerate(parents):
        children = np.flatnonzero(parents == joint)
        if len(children):
            direction = joints[main_child.get(joint, children[0])] - joints[joint]
        elif joint == HEAD_JOINT:
            # SMPL-H's Neck -> Head offset points toward the face. It is
            # the neck axis, not the skull's up axis used by retargeters.
            direction = np.array([0., head_top_y - joints[joint, 1], 0.])
            if direction[1] <= 0.:
                raise ValueError("Invalid SMPL-H head height for FBX export")
        else:
            direction = joints[joint] - joints[parent]
            if joint in (10, 11):  # Toe tips follow the floor.
                direction[1] = 0.
                direction *= .65
        length = float(np.linalg.norm(direction))
        if length < 1e-6:
            raise ValueError(f"Degenerate rest bone: {FBX_JOINT_NAMES[joint]}")
        if not len(children):
            tips[joint] = length
        y = direction / length
        guide = (0., 0., 1.) if abs(y[2]) < .95 else (0., 1., 0.)
        x = np.cross(y, guide)
        x /= np.linalg.norm(x)
        frames.append(np.stack((x, y, np.cross(x, y)), axis=1))
    return np.asarray(frames), tips


def blend_shapes(betas: torch.Tensor, shape_disps: torch.Tensor) -> torch.Tensor:
    """Calculates the per vertex displacement due to the blend shapes


    Parameters
    ----------
    betas : torch.tensor Bx(num_betas)
        Blend shape coefficients
    shape_disps: torch.tensor Vx3x(num_betas)
        Blend shapes

    Returns
    -------
    torch.tensor BxVx3
        The per-vertex displacement due to shape deformation
    """

    # Displacement[b, m, k] = sum_{l} betas[b, l] * shape_disps[m, k, l]
    # i.e. Multiply each shape displacement by its corresponding beta and
    # then sum them.
    blend_shape = torch.einsum("bl,mkl->bmk", [betas, shape_disps])
    return blend_shape


def vertices2joints(J_regressor: torch.Tensor, vertices: torch.Tensor) -> torch.Tensor:
    """Calculates the 3D joint locations from the vertices

    Parameters
    ----------
    J_regressor : torch.tensor JxV
        The regressor array that is used to calculate the joints from the
        position of the vertices
    vertices : torch.tensor BxVx3
        The tensor of mesh vertices

    Returns
    -------
    torch.tensor BxJx3
        The location of the joints
    """

    return torch.einsum("bik,ji->bjk", [vertices, J_regressor])


def get_offsets_from_beta(
    beta: torch.Tensor, smplx_params: dict, return_template_mesh: bool = True
) -> tuple[np.ndarray, torch.Tensor] | np.ndarray:
    v_template = torch.FloatTensor(smplx_params["v_template"]).unsqueeze(0)
    shape_dirs = torch.FloatTensor(smplx_params["shapedirs"][..., :beta.shape[-1]])
    J_regressor = torch.FloatTensor(smplx_params["J_regressor"])

    v_shaped = v_template + blend_shapes(beta, shape_dirs)
    J = vertices2joints(J_regressor, v_shaped).squeeze(0).numpy()

    parents = np.asarray(smplx_params["kintree_table"][0], dtype=np.int64).copy()
    parents[0] = -1
    Translates = J.copy()
    Translates[1:] -= J[parents[1:]]
    if not return_template_mesh:
        return Translates
    else:
        return Translates, v_shaped


def _parse_obj_uv(obj_path: str) -> Tuple[np.ndarray, np.ndarray]:
    uv_coords = []
    uv_faces = []

    with open(obj_path, "r") as f:
        for line in f:
            line = line.strip()
            if line.startswith("vt "):
                parts = line.split()
                uv_coords.append([float(parts[1]), float(parts[2])])
            elif line.startswith("f "):
                parts = line.split()
                face_uvs = []
                for part in parts[1:]:
                    indices = part.split("/")
                    if len(indices) > 1 and indices[1]:
                        face_uvs.append(int(indices[1]) - 1)
                if len(face_uvs) == 3:
                    uv_faces.append(face_uvs)

    return np.array(uv_coords, dtype=np.float64), np.array(uv_faces, dtype=np.int32)


class SMPLH2FBX:
    def __init__(
        self,
        smplh_model_path: str | Path | None = None,
        obj_template_path: Optional[str] = DEFAULT_OBJ_TEMPLATE_PATH,
    ):
        model = load_smplh_data(smplh_model_path or validated_smplh_reference()[1])
        self.smpl_data = {k: model[k] for k in ("v_template", "J_regressor", "shapedirs", "weights", "kintree_table")}
        self.smpl_data["faces"] = model["f"]
        self.mgr = create_manager()

        # Load UV coordinates from OBJ template
        self.uv_coords: Optional[np.ndarray] = None
        self.uv_faces: Optional[np.ndarray] = None
        if obj_template_path and os.path.isfile(obj_template_path):
            self.uv_coords, self.uv_faces = _parse_obj_uv(obj_template_path)
            logger.info(
                "Loaded UV from OBJ: %d uv_coords, %d uv_faces",
                len(self.uv_coords), len(self.uv_faces),
            )

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.mgr.Destroy()

    def convert_npz_to_fbx(self, npz_data, outname: str, fps: int = 30, export_mesh: bool = True,
                           variant_path=None, *, first_pose="none") -> bool:
        if isinstance(npz_data, (str, os.PathLike)):
            with np.load(npz_data, allow_pickle=False) as data:
                npz_data = {k: data[k] for k in ("poses", "trans", "betas", "mocap_framerate") if k in data}

        fps = int(npz_data.get("mocap_framerate", fps))
        end = np.asarray(npz_data["poses"]).size // (52 * 3) - 1
        return self._write_motions([(None, 0, npz_data)], outname, fps, end, export_mesh, variant_path, first_pose)

    def convert_scene_to_fbx(self, npz_path, people, outname, *, export_mesh=True, variant_path=None,
                             first_pose="none") -> bool:
        """Build one FBX scene with a shared timeline for all people."""
        with np.load(npz_path, allow_pickle=False) as data:
            motions = []
            for person in people:
                number, first, last = person["person"], person["start"], person["end"]
                motion = {key: data[f"{key}_{number}"] for key in ("poses", "trans", "betas")}
                if motion["trans"].shape != (last - first + 1, 3):
                    raise ValueError("FBX source range does not match the scene motion")
                motions.append((number, first, motion))
            return self._write_motions(motions, outname, int(data["mocap_framerate"]),
                                       max(p["end"] for p in people), export_mesh, variant_path, first_pose)

    def _add_motion(self, scene, layer, npz_data, number, start, frame_dur, end, export_mesh, variant_path, first_pose):
        poses = np.asarray(npz_data["poses"], dtype=np.float32).reshape(-1, 52, 3)
        trans = np.asarray(npz_data["trans"], dtype=np.float64)
        betas = np.asarray(npz_data["betas"], dtype=np.float32)
        if not len(poses) or trans.shape != (len(poses), 3) or not all(np.isfinite(x).all() for x in (poses, trans, betas)):
            raise ValueError("Invalid SMPL-H animation for FBX export")
        betas = np.atleast_2d(betas)[:1, :self.smpl_data["shapedirs"].shape[-1]]
        offsets, v_shaped = get_offsets_from_beta(torch.from_numpy(betas), self.smpl_data)
        mesh = v_shaped[0].numpy().astype(np.float64)
        parents = np.asarray(self.smpl_data["kintree_table"][0], dtype=np.int64).copy()
        parents[0] = -1
        joints = offsets.astype(np.float64)
        for j in range(1, len(joints)):
            joints[j] += joints[parents[j]]
        head_surface = mesh[self.smpl_data["weights"][:, HEAD_JOINT] > .5]
        basis, tips = _display_frames(joints, parents, head_top_y=head_surface[:, 1].max())
        floor_offset = np.array([0., -mesh[:, 1].min(), 0.])
        local_offsets = offsets.astype(np.float64)
        local_offsets[0] += floor_offset
        local_offsets[1:] = (basis[parents[1:]].transpose(0, 2, 1) @ local_offsets[1:, :, None])[..., 0]
        local_rest = basis.copy()
        local_rest[1:] = basis[parents[1:]].transpose(0, 2, 1) @ basis[1:]
        # Rotate in each bone's new rest axes. Keeping the animated root in
        # the original world frame preserves the motion and skinned positions.
        rot_mats = angle_axis_to_rotation_matrix(torch.from_numpy(poses)).numpy()
        rotations = basis.transpose(0, 2, 1) @ rot_mats @ basis
        reference = first_pose != "none"
        reference_rotations = reference_translation = None
        if reference:
            reference_rotations = basis.transpose(0, 2, 1) @ _reference_rotations(joints, rot_mats[0, 0], first_pose) @ basis
            reference_translation = offsets[0] + np.array([trans[0, 0], floor_offset[1], trans[0, 2]])
        parent_node = None
        if number is not None:
            parent_node = fbx.FbxNode.Create(scene, f"Person {number}")
            scene.GetRootNode().AddChild(parent_node)
        skel_nodes = build_skeleton_nodes(self.mgr, scene, local_offsets, parents, FBX_JOINT_NAMES,
                                          rest_rotations=local_rest, tip_lengths=tips, parent_node=parent_node,
                                          reference_name="FlowHMR Motion" if number is None else f"FlowHMR Person {number}")
        if export_mesh:
            if variant_path is not None:
                from ..bodymodels.body_variants import fitted_skin

                mesh = fitted_skin(variant_path, mesh.astype(np.float32), joints.astype(np.float32),
                                   self.smpl_data["weights"], self.smpl_data["faces"], parents)
            geo_node = build_mesh_node(scene, mesh + floor_offset, self.smpl_data["faces"], uv_coords=self.uv_coords,
                                       uv_faces=self.uv_faces, parent_node=parent_node,
                                       node_name="Geometry" if number is None else f"Person {number} Mesh")
            apply_skinning(scene, geo_node, skel_nodes, self.smpl_data["weights"])
        else:
            store_bind_pose(scene, skel_nodes)

        animate_translation(layer, skel_nodes[0], trans + offsets[0], frame_dur, start_frame=start,
                            preroll=reference_translation)
        for i, node in enumerate(skel_nodes):
            animate_rotation_from_rotmats(layer, node, rotations[:, i], frame_dur, start_frame=start,
                                          preroll=reference_rotations[i] if reference else None)
        stop = start + len(poses) - 1
        delayed = start > 0
        if parent_node is not None and (delayed or stop < end):
            # A visibility curve preserves the track interval in FBX-capable scene viewers.
            curve = parent_node.Visibility.GetCurve(layer, True)
            keys = {0: 1., start: 1.}
            if delayed:
                keys[0] = 0.
            if reference:
                keys[-1] = 1.
            if stop < end:
                keys[stop + 1] = 0.

            def step_keys(channel, values):
                channel.KeyModifyBegin()
                time = fbx.FbxTime()
                for frame, value in values:
                    time.SetSecondDouble(frame * frame_dur)
                    key = channel.KeyAdd(time)[0]
                    channel.KeySetValue(key, value)
                    channel.KeySetInterpolation(key, fbx.FbxAnimCurveDef.EInterpolationType.eInterpolationConstant)
                channel.KeyModifyEnd()

            step_keys(curve, sorted(keys.items()))
            # Blender imports transforms but ignores animated FBX visibility. Scale only
            # the enclosing empty outside the track; keep every active frame at unit scale.
            hidden = 1e-5
            scale = {0: 1., start: 1., stop: 1.}
            if delayed:
                scale[0] = hidden
                scale[start - 1] = hidden
            if reference:
                scale[-1] = 1.
            if stop < end:
                scale[stop + 1] = hidden
            for axis in "XYZ":
                step_keys(parent_node.LclScaling.GetCurve(layer, axis, True), sorted(scale.items()))

    def _write_motions(self, motions, outname, fps, end, export_mesh, variant_path, first_pose):
        if not motions or fps <= 0 or end < 0:
            raise ValueError("Invalid FBX timeline")
        if first_pose not in ("none", "t", "a"):
            raise ValueError("Invalid FBX reference pose")
        scene = fbx.FbxScene.Create(self.mgr, "")
        try:
            settings = scene.GetGlobalSettings()
            settings.SetAxisSystem(fbx.FbxAxisSystem.MayaYUp)
            settings.SetSystemUnit(fbx.FbxSystemUnit.m)
            settings.SetOriginalSystemUnit(fbx.FbxSystemUnit.m)
            settings.SetTimeMode(fbx.FbxTime.ConvertFrameRateToTimeMode(fps))
            first, last = fbx.FbxTime(), fbx.FbxTime()
            first.SetSecondDouble(-1 / fps if first_pose != "none" else 0.)
            last.SetSecondDouble(end / fps)
            span = fbx.FbxTimeSpan(first, last)
            settings.SetTimelineDefaultTimeSpan(span)

            stack = fbx.FbxAnimStack.Create(scene, "Motion")
            stack.SetLocalTimeSpan(span)
            stack.SetReferenceTimeSpan(span)
            layer = fbx.FbxAnimLayer.Create(scene, "BaseLayer")
            stack.AddMember(layer)
            for number, start, motion in motions:
                self._add_motion(scene, layer, motion, number, start, 1.0 / fps, end, export_mesh, variant_path, first_pose)
            export_scene_atomic(self.mgr, scene, os.fspath(outname), embed_textures=export_mesh)
        finally:
            scene.Destroy()
        return True


if __name__ == "__main__":
    import argparse
    import glob

    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=str)
    args = parser.parse_args()

    smplh2fbx = SMPLH2FBX()

    if os.path.isdir(args.root):
        npzfiles = sorted(glob.glob(os.path.join(args.root, "*.npz")))
    else:
        if args.root.endswith(".npz"):
            npzfiles = [args.root]
        else:
            raise ValueError(f"Unknown file type: {args.root}")

    for npzfile in npzfiles:
        smplh2fbx.convert_npz_to_fbx(npzfile, npzfile.replace(".npz", ".fbx"), export_mesh=False)
