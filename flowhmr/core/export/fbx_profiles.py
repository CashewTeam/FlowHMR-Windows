"""Mixamo-style source names and animation on imported FBX skeletons."""

from __future__ import annotations

import os
from pathlib import Path

import fbx
import numpy as np
from scipy.spatial.transform import Rotation
from transforms3d.euler import euler2mat

from .fbx_utils import (
    animate_rotation_from_rotmats,
    animate_translation,
    clear_animations,
    create_manager,
    export_scene_atomic,
    store_bind_pose,
)


def mixamo_bone_names(names, tips):
    """The body hierarchy already matches; add the namespace and Mixamo leaf names."""
    prefix = "mixamorig:"
    end_names = {"Head": "HeadTop_End", "LeftToeBase": "LeftToe_End", "RightToeBase": "RightToe_End"}
    return tuple(prefix + name for name in names), {
        i: prefix + end_names.get(names[i], names[i][:-1] + "4") for i in tips
    }


_PIVOT = fbx.FbxNode.EPivotSet.eSourcePivot
_BODY_CHAINS = (
    (("thigh", "calf", "foot", "ball"), (1, 4, 7, 10), (2, 5, 8, 11)),
    (("clavicle", "upperarm", "lowerarm", "hand"), (13, 16, 18, 20), (14, 17, 19, 21)),
)
_FINGERS = ("index", "middle", "pinky", "ring", "thumb")
_PALMS = ((20, 25, 22, 28), (21, 40, 37, 43))
_LEGS = ((1, 4, 7), (2, 5, 8))
_LIMB_PARENTS = {1, 2, 4, 5, 7, 8, 13, 14, 16, 17, 18, 19}


def _id(node):
    return node.GetUniqueID()


def _xyz(value):
    return np.array([value[i] for i in range(3)], dtype=np.float64)


def _unit(vector):
    length = np.linalg.norm(vector)
    if not np.isfinite(length) or length < 1e-7:
        raise ValueError("Degenerate FBX body reference geometry")
    return vector / length


def _direction_frame(left, up, forward):
    """Infer the facing sign from the feet, even for a left-handed FBX axis system."""
    side = _unit(left - np.dot(left, up) * up)
    facing = np.cross(side, up)
    component = np.dot(facing, _unit(forward))
    if abs(component) < .2:
        raise ValueError("Could not determine the FBX body forward direction")
    return np.stack((side, up, facing * np.sign(component)), axis=1)


def _palm_frame(main, across):
    lengthwise = _unit(main)
    widthwise = _unit(across - np.dot(across, lengthwise) * lengthwise)
    return np.stack((widthwise, lengthwise, np.cross(widthwise, lengthwise)), axis=1)


def _align_vectors(source, target):
    source, target = _unit(source), _unit(target)
    cosine = np.clip(np.dot(source, target), -1., 1.)
    if cosine < -1. + 1e-7:
        axis = _unit(np.cross(source, np.eye(3)[np.argmin(np.abs(source))]))
        return 2. * np.outer(axis, axis) - np.eye(3)
    cross = np.cross(source, target)
    x, y, z = cross
    skew = np.array([[0., -z, y], [z, 0., -x], [-y, x, 0.]])
    return np.eye(3) + skew + skew @ skew / (1. + cosine)


def _walk(root):
    nodes, pending = [], [(root, 0)]
    while pending:
        node, depth = pending.pop()
        if depth > 128 or len(nodes) >= 5000:
            raise ValueError("FBX template hierarchy is too large")
        nodes.append(node)
        pending.extend((node.GetChild(i), depth + 1) for i in reversed(range(node.GetChildCount())))
    return nodes


def _is_bone(node):
    attr = node.GetNodeAttribute()
    return attr is not None and attr.GetAttributeType() == fbx.FbxNodeAttribute.EType.eSkeleton


def _under(node, ancestor):
    ancestor_id = _id(ancestor)
    while node is not None:
        if _id(node) == ancestor_id:
            return True
        node = node.GetParent()
    return False


def _body_layout(scene):
    nodes = _walk(scene.GetRootNode())
    pelvis = [n for n in nodes if _is_bone(n) and n.GetName().rsplit(":", 1)[-1].lower() == "pelvis"]
    if len(pelvis) != 1:
        raise ValueError("MetaHuman template must have one body skeleton with a pelvis")
    root = pelvis[0].GetParent()
    while root is not None and (not _is_bone(root) or root.GetName().rsplit(":", 1)[-1].lower() != "root"):
        root = root.GetParent()
    if root is None:
        raise ValueError("MetaHuman body skeleton is missing its root bone")
    bones = {}
    for node in _walk(root):
        if not _is_bone(node):
            continue
        name = node.GetName().rsplit(":", 1)[-1].lower()
        if name in bones:
            raise ValueError(f"Duplicate bone in MetaHuman template: {name}")
        bones[name] = node

    def verify_chain(chain):
        for name in chain:
            if name not in bones:
                raise ValueError(f"Missing MetaHuman body bone: {name}")
        for parent, child in zip(chain, chain[1:]):
            if not _under(bones[child], bones[parent]):
                raise ValueError(f"Unexpected MetaHuman bone hierarchy: {parent} / {child}")

    spine = tuple(f"spine_{i:02}" for i in range(1, 6) if i <= 3 or f"spine_{i:02}" in bones)
    if "spine_05" in spine and "spine_04" not in spine:
        raise ValueError("MetaHuman spine chain is incomplete")
    neck = ("neck_01", "neck_02") if "neck_02" in bones else ("neck_01",)
    verify_chain(("root", "pelvis", *spine, *neck, "head"))
    mappings = {"pelvis": (0, 0, 0.), "head": (15, 15, 0.)}
    primary = {}

    def map_axial(names, source_joints, endpoint=True):
        for name, position in zip(names, np.linspace(0., len(source_joints) - 1, len(names), endpoint=endpoint)):
            lo, hi = int(position), min(int(position) + 1, len(source_joints) - 1)
            mappings[name] = (source_joints[lo], source_joints[hi], position - lo)

    map_axial(spine, (3, 6, 9))
    map_axial(neck, (12, 15), endpoint=False)
    for side, base, source_side in (("l", 22, 0), ("r", 37, 1)):
        for index, (chain, left, right) in enumerate(_BODY_CHAINS):
            names = tuple(f"{name}_{side}" for name in chain)
            verify_chain(("pelvis" if index == 0 else spine[-1], *names))
            joints = (left, right)[source_side]
            mappings.update((name, (joint, joint, 0.)) for name, joint in zip(names, joints))
            primary.update((name, (child, src_child)) for name, child, src_child in zip(names, names[1:], joints[1:]))
        for i, finger in enumerate(_FINGERS):
            names = tuple(f"{finger}_{j:02}_{side}" for j in (1, 2, 3))
            verify_chain((f"hand_{side}", *names))
            joints = tuple(base + 3 * i + j for j in range(3))
            mappings.update((name, (joint, joint, 0.)) for name, joint in zip(names, joints))
            primary.update((name, (child, src_child)) for name, child, src_child in zip(names, names[1:], joints[1:]))
            # The last phalanx has no matched child. Use its incoming shaft
            # direction to align its bind axes, as the FBX preview does.
            primary[names[-1]] = (names[-2], joints[-2])
    return nodes, bones, mappings, primary


def _template_bind_matrices(scene, nodes, kept):
    matrices = {}
    for index in range(scene.GetPoseCount()):
        pose = scene.GetPose(index)
        if not pose.IsBindPose():
            continue
        for entry in range(pose.GetCount()):
            node = pose.GetNode(entry)
            if node is not None and _id(node) in kept and not pose.IsLocalMatrix(entry):
                matrices[_id(node)] = fbx.FbxMatrix(pose.GetMatrix(entry))
    # The skin bind matrix is authoritative when the template's default and bind poses differ.
    kind = fbx.FbxDeformer.EDeformerType.eSkin
    for node in nodes:
        mesh = node.GetMesh()
        if mesh is None:
            continue
        for index in range(mesh.GetDeformerCount(kind)):
            skin = mesh.GetDeformer(index, kind)
            for entry in range(skin.GetClusterCount()):
                cluster = skin.GetCluster(entry)
                bone = cluster.GetLink()
                if bone is not None and _id(bone) in kept:
                    matrix = fbx.FbxAMatrix()
                    cluster.GetTransformLinkMatrix(matrix)
                    matrices[_id(bone)] = fbx.FbxMatrix(matrix)
    return matrices


def _retain_body(scene, nodes, bones):
    # Retain every target bone, including the ones driven only by their parents, and keep
    # any null wrappers they inherit from. No mesh, cameras, existing takes or poses leave.
    kept = set()
    for node in bones.values():
        while node is not None and _id(node) not in kept:
            kept.add(_id(node))
            node = node.GetParent()
    retained = [node for node in nodes if _id(node) in kept]
    bind_matrices = _template_bind_matrices(scene, nodes, kept)
    clear_animations(scene)
    for i in reversed(range(scene.GetPoseCount())):
        scene.RemovePose(i)
    criteria = fbx.FbxCriteria.ObjectType(fbx.FbxConstraint.ClassId)
    for i in reversed(range(scene.GetSrcObjectCount(criteria))):
        scene.GetSrcObject(criteria, i).Destroy()
    for node in reversed(nodes[1:]):
        if _id(node) not in kept:
            node.GetParent().RemoveChild(node)
            node.Destroy()
        elif (attr := node.GetNodeAttribute()) is not None and attr.GetAttributeType() not in (
            fbx.FbxNodeAttribute.EType.eSkeleton, fbx.FbxNodeAttribute.EType.eNull,
        ):
            node.SetNodeAttribute(None)
            attr.Destroy()
    for object_type in (fbx.FbxSurfaceMaterial, fbx.FbxTexture, fbx.FbxVideo):
        criteria = fbx.FbxCriteria.ObjectType(object_type.ClassId)
        for i in reversed(range(scene.GetSrcObjectCount(criteria))):
            scene.GetSrcObject(criteria, i).Destroy()
    scene.GetAnimationEvaluator().Reset()
    store_bind_pose(scene, list(bones.values()), bind_matrices)
    return retained


def _rest_transforms(scene, nodes):
    positions, rotations, scales = {}, {}, {}
    evaluator = scene.GetAnimationEvaluator()
    for node in nodes:
        matrix = evaluator.GetNodeGlobalTransform(node)
        pos = _xyz(matrix.GetT())
        q = matrix.GetQ()
        quat = np.array([q[i] for i in range(4)])
        scale = _xyz(matrix.GetS())
        if (not np.isfinite(np.concatenate((pos, quat, scale))).all() or scale[0] < 1e-8
                or not np.allclose(scale, scale[0], rtol=1e-5, atol=1e-8)):
            raise ValueError(f"Unsupported scaling in the FBX template: {node.GetName()}")
        positions[_id(node)] = pos
        rotations[_id(node)] = Rotation.from_quat(quat).as_matrix()
        scales[_id(node)] = float(scale[0])
    return positions, rotations, scales


def _property_rotation(node, local_rest, local_animated):
    # FBX combines these three rotations as Pre * Lcl * inverse(Post).
    # Keep the imported pivots/order and solve the authored Lcl channel.
    order = node.GetRotationOrder(_PIVOT).name.removeprefix("eEuler").lower()
    if len(order) != 3 or set(order) != {"x", "y", "z"}:
        raise ValueError(f"Unsupported rotation order in the FBX template: {node.GetName()}")
    axes = "s" + order
    euler = np.deg2rad(_xyz(node.LclRotation.Get()))
    base = euler2mat(*(euler["xyz".index(c)] for c in order), axes=axes)
    pre = euler2mat(*np.deg2rad(_xyz(node.GetPreRotation(_PIVOT))))
    post = euler2mat(*np.deg2rad(_xyz(node.GetPostRotation(_PIVOT))))
    if np.allclose(pre @ base @ post.T, local_rest, atol=2e-5):
        return pre.T @ local_animated @ post, axes
    if np.allclose(base, local_rest, atol=2e-5):
        return local_animated, axes
    raise ValueError(f"Unsupported rotation pivots in the FBX template: {node.GetName()}")


def _local_translation(node, rest_rotations, rest_scales, delta):
    parent_id = _id(node.GetParent())
    return _xyz(node.LclTranslation.Get()) + delta @ rest_rotations[parent_id] / rest_scales[parent_id]


def _import_template(mgr, template_path):
    ios = mgr.GetIOSettings()
    # The Python SDK does not export the IO path macros. Disable embedded-media
    # extraction and texture/material import; the export only needs skeletons.
    options = ("Material", "Texture", "ExtractEmbeddedData", "Animation", "Audio", "Constraint", "Character")
    prefix = "Import|AdvOptGrp|FileFormat|Fbx|"
    importer = fbx.FbxImporter.Create(mgr, "")
    try:
        for option in options:
            ios.SetBoolProp(prefix + option, False)
        if not importer.Initialize(os.fspath(template_path), -1, ios) or not importer.IsFBX():
            raise ValueError("Could not open the FBX skeleton template")
        for option in options:
            ios.SetBoolProp(prefix + option, False)
        scene = fbx.FbxScene.Create(mgr, "")
        if not importer.Import(scene):
            raise ValueError("Could not read the FBX skeleton template")
        return scene
    finally:
        importer.Destroy()


def _check_template_paths(template_path, outname):
    if Path(template_path).suffix.lower() != ".fbx":
        raise ValueError("Choose an FBX skeleton template")
    if Path(template_path).resolve() == Path(outname).resolve():
        raise ValueError("The output must not replace the FBX template")


def _target_basis(settings, hips, head, left_hip, right_hip, feet_forward):
    up_index, sign = settings.GetAxisSystem().GetUpVector()
    up = np.eye(3)[int(up_index.value) - 1] * sign
    if np.dot(head - hips, up) <= 0.:
        raise ValueError("The FBX template is not standing along its up axis")
    # FlowHMR is +X left, +Y up, +Z forward. Use only the target's
    # orientation so an asymmetric source does not yaw the exported motion.
    return _direction_frame(left_hip - right_hip, up, feet_forward)


def _target_units(settings):
    unit = settings.GetSystemUnit().GetScaleFactor()
    if not np.isfinite(unit) or unit <= 0.:
        raise ValueError("Invalid FBX template units")
    return 100. / unit


def _root_displacement(trans, joints, target_legs, units, floor_offset):
    source_legs = joints[np.array(_LEGS)]
    source_length = np.linalg.norm(np.diff(source_legs, axis=1), axis=2).sum()
    target_length = np.linalg.norm(np.diff(target_legs, axis=1), axis=2).sum()
    if source_length < 1e-6 or target_length < 1e-6:
        raise ValueError("The FBX template has degenerate leg lengths")
    origin = trans[0].copy()
    origin[1] = floor_offset
    delta = (trans - origin) * (target_length / (source_length * units))
    delta[:, [0, 2]] += origin[[0, 2]]
    return delta


def _source_rotations(rotations, parents, basis):
    world = np.empty_like(rotations, dtype=np.float64)
    world[:, 0] = rotations[:, 0]
    for i in range(1, len(parents)):
        world[:, i] = world[:, parents[i]] @ rotations[:, i]
    return basis @ world @ basis.T


def _animate_template(mgr, scene, nodes, bones, mappings, rest, source_world, bind_rotation, translations,
                      outname, fps, start, *, file_version=None):
    settings = scene.GetGlobalSettings()
    first, last = fbx.FbxTime(), fbx.FbxTime()
    first.SetSecondDouble(0.)
    last.SetSecondDouble((start + len(source_world) - 1) / fps)
    span = fbx.FbxTimeSpan(first, last)
    settings.SetTimeMode(fbx.FbxTime.ConvertFrameRateToTimeMode(fps))
    settings.SetTimelineDefaultTimeSpan(span)
    stack = fbx.FbxAnimStack.Create(scene, "Motion")
    stack.SetLocalTimeSpan(span)
    stack.SetReferenceTimeSpan(span)
    layer = fbx.FbxAnimLayer.Create(scene, "BaseLayer")
    stack.AddMember(layer)
    scene.SetCurrentAnimationStack(stack)

    active_names = {_id(bones[name]): name for name in mappings}
    needed = set()
    for name in mappings:
        node = bones[name]
        while node is not None and _id(node) not in needed:
            needed.add(_id(node))
            node = node.GetParent()
    animated = {_id(nodes[0]): rest[_id(nodes[0])]}
    for node in nodes[1:]:
        node_id, parent_id = _id(node), _id(node.GetParent())
        if node_id not in needed:
            continue
        local_rest = rest[parent_id].T @ rest[node_id]
        parent_motion = animated[parent_id]
        if (name := active_names.get(node_id)) is None:
            animated[node_id] = parent_motion @ local_rest
            continue
        lo, hi, weight = mappings[name]
        motion = source_world[:, lo]
        if weight:
            difference = Rotation.from_matrix(motion.transpose(0, 2, 1) @ source_world[:, hi])
            motion = motion @ Rotation.from_rotvec(difference.as_rotvec() * weight).as_matrix()
        animated[node_id] = motion @ bind_rotation[name]
        for getter in (node.GetRotationOffset, node.GetRotationPivot, node.GetScalingOffset, node.GetScalingPivot):
            if np.linalg.norm(_xyz(getter(_PIVOT))) > 1e-6:
                raise ValueError(f"Unsupported moving pivot in the FBX template: {node.GetName()}")
        local_animated = np.swapaxes(parent_motion, -1, -2) @ animated[node_id]
        property_mats, axes = _property_rotation(node, local_rest, local_animated)
        animate_rotation_from_rotmats(layer, node, property_mats, 1. / fps, axes=axes, start_frame=start)
    for name, local in translations.items():
        animate_translation(layer, bones[name], local, 1. / fps, start_frame=start)

    ios = mgr.GetIOSettings()
    for flag in (fbx.EXP_FBX_MATERIAL, fbx.EXP_FBX_TEXTURE, fbx.EXP_FBX_EMBEDDED):
        ios.SetBoolProp(flag, False)
    export_scene_atomic(mgr, scene, os.fspath(outname), file_version=file_version)


def write_metahuman_animation(template_path, outname, rotations, trans, joints, parents, *, floor_offset, fps, start):
    """Bake body motion to the supplied FBX's exact bone hierarchy and bind transforms."""
    _check_template_paths(template_path, outname)
    mgr = create_manager()
    try:
        scene = _import_template(mgr, template_path)
        nodes, bones, mappings, primary = _body_layout(scene)
        nodes = _retain_body(scene, nodes, bones)
        positions, rest, scales = _rest_transforms(scene, nodes)
        rest_pos = {name: positions[_id(node)] for name, node in bones.items()}
        settings = scene.GetGlobalSettings()
        basis = _target_basis(settings, rest_pos["pelvis"], rest_pos["head"], rest_pos["thigh_l"], rest_pos["thigh_r"],
                              rest_pos["ball_l"] - rest_pos["foot_l"] + rest_pos["ball_r"] - rest_pos["foot_r"])
        units = _target_units(settings)
        source_world = _source_rotations(rotations, parents, basis)
        bind_rotation = {name: rest[_id(bones[name])] for name in mappings}
        for name, (child, child_joint) in primary.items():
            src_joint = mappings[name][0]
            align = _align_vectors(rest_pos[child] - rest_pos[name], basis @ (joints[child_joint] - joints[src_joint]))
            bind_rotation[name] = align @ bind_rotation[name]
        for side, (wrist, middle, index, pinky) in zip(("l", "r"), _PALMS):
            hand = f"hand_{side}"
            palm_source = _palm_frame(basis @ (joints[middle] - joints[wrist]), basis @ (joints[pinky] - joints[index]))
            palm_target = _palm_frame(rest_pos[f"middle_01_{side}"] - rest_pos[hand],
                                      rest_pos[f"pinky_01_{side}"] - rest_pos[f"index_01_{side}"])
            bind_rotation[hand] = palm_source @ palm_target.T @ bind_rotation[hand]

        # Unreal can extract horizontal root motion, while body height stays on pelvis.
        legs = [[rest_pos[f"{name}_{side}"] for name in ("thigh", "calf", "foot")] for side in ("l", "r")]
        delta = _root_displacement(trans, joints, legs, units, floor_offset)
        horizontal = delta.copy()
        horizontal[:, 1] = 0.
        vertical = np.zeros_like(delta)
        vertical[:, 1] = delta[:, 1]
        translations = {name: _local_translation(bones[name], rest, scales, motion @ basis.T * units)
                        for name, motion in (("root", horizontal), ("pelvis", vertical))}
        _animate_template(mgr, scene, nodes, bones, mappings, rest, source_world, bind_rotation, translations,
                          outname, fps, start)
    finally:
        mgr.Destroy()


def _mixamo_layout(scene, names, parents):
    nodes = _walk(scene.GetRootNode())

    def key(node):
        return node.GetName().rsplit(":", 1)[-1].casefold()

    hips = [node for node in nodes if _is_bone(node) and key(node) == "hips"]
    if len(hips) != 1:
        raise ValueError("The Mixamo template must have exactly one Hips skeleton")
    bones = {}
    for node in _walk(hips[0]):
        if _is_bone(node):
            name = key(node)
            if name in bones:
                raise ValueError(f"Duplicate bone in the Mixamo template: {node.GetName()}")
            bones[name] = node
    mappings = {}
    for index, name in enumerate(names):
        if name not in bones:
            if index < 22:
                raise ValueError(f"Missing Mixamo body bone: {name}")
            continue
        parent = parents[index]
        if parent >= 0 and (names[parent] not in bones or not _under(bones[name], bones[names[parent]])):
            raise ValueError(f"Unexpected Mixamo hierarchy at {bones[name].GetName()}")
        mappings[name] = (index, index, 0.)
    return nodes, bones, mappings


def write_mixamo_animation(template_path, outname, rotations, trans, joints, parents, *, source_names,
                           floor_offset, fps, start):
    """Keep the Mixamo character's static axes and bake one person onto that rig."""
    _check_template_paths(template_path, outname)
    names = tuple(name.casefold() for name in source_names)
    mgr = create_manager()
    try:
        scene = _import_template(mgr, template_path)
        nodes, bones, mappings = _mixamo_layout(scene, names, parents)
        nodes = _retain_body(scene, nodes, bones)
        positions, rest, scales = _rest_transforms(scene, nodes)
        rest_pos = {name: positions[_id(node)] for name, node in bones.items()}
        settings = scene.GetGlobalSettings()
        basis = _target_basis(settings, rest_pos["hips"], rest_pos["head"], rest_pos["leftupleg"],
                              rest_pos["rightupleg"], rest_pos["lefttoebase"] - rest_pos["leftfoot"]
                              + rest_pos["righttoebase"] - rest_pos["rightfoot"])
        units = _target_units(settings)
        bind_rotation = {name: rest[_id(bones[name])] for name in mappings}
        children = {int(parent): i for i, parent in enumerate(parents)
                    if parent in _LIMB_PARENTS or (parent >= 22 and names[i] in mappings)}
        for name, (i, _, _) in mappings.items():
            # A finger tip without a matched child uses its incoming shaft.
            child = children.get(i, int(parents[i]) if i >= 22 else None)
            if child is not None:
                align = _align_vectors(rest_pos[names[child]] - rest_pos[name], basis @ (joints[child] - joints[i]))
                bind_rotation[name] = align @ bind_rotation[name]
        for wrist, middle, index, pinky in _PALMS:
            hand, mid, fore, little = (names[i] for i in (wrist, middle, index, pinky))
            if all(name in rest_pos for name in (mid, fore, little)):
                source_palm = _palm_frame(basis @ (joints[middle] - joints[wrist]),
                                          basis @ (joints[pinky] - joints[index]))
                target_palm = _palm_frame(rest_pos[mid] - rest_pos[hand], rest_pos[little] - rest_pos[fore])
                bind_rotation[hand] = source_palm @ target_palm.T @ bind_rotation[hand]

        # The template's own leg lengths determine stride. Preserve the
        # source's starting location and use the template's floor calibration.
        legs = [[rest_pos[names[joint]] for joint in leg] for leg in _LEGS]
        delta = _root_displacement(trans, joints, legs, units, floor_offset)
        translations = {"hips": _local_translation(bones["hips"], rest, scales, delta @ basis.T * units)}
        # Use the FBX 7.4 container format of the original Mixamo sample.
        _animate_template(mgr, scene, nodes, bones, mappings, rest, _source_rotations(rotations, parents, basis),
                          bind_rotation, translations, outname, fps, start, file_version="FBX201400")
    finally:
        mgr.Destroy()
