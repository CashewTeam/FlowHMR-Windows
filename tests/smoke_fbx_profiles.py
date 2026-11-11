"""Run with the Windows FBX runtime and a local SMPL-H model/motion NPZ."""

import argparse
import json
from pathlib import Path
import sys

import fbx
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from flowhmr.core.export.fbx_utils import (
    animate_translation,
    build_mesh_node,
    collect_all_nodes,
    collect_skeleton_nodes,
    create_manager,
    export_scene_atomic,
    load_scene,
    store_bind_pose,
)
from flowhmr.core.export.smplh2fbx import SMPLH2FBX


# FBX is stored Z-up, in cm, facing +X, with +Y to the right as in Unreal.
TO_TEMPLATE = np.array([[0., 0., 1.], [-1., 0., 0.], [0., 1., 0.]])
PIVOT = fbx.FbxNode.EPivotSet.eSourcePivot


def xyz(value):
    return np.array([value[i] for i in range(3)])


def fixture(path, *, incomplete=False):
    """A distinct authored A-pose with five spine joints, metacarpals and twist bones."""
    mgr = create_manager()
    try:
        scene = fbx.FbxScene.Create(mgr, "Body fixture")
        settings = scene.GetGlobalSettings()
        settings.SetAxisSystem(fbx.FbxAxisSystem(fbx.FbxAxisSystem.EUpVector.eZAxis,
                                                fbx.FbxAxisSystem.EFrontVector.eParityOdd,
                                                fbx.FbxAxisSystem.ECoordSystem.eLeftHanded))
        settings.SetSystemUnit(fbx.FbxSystemUnit.cm)
        settings.SetOriginalSystemUnit(fbx.FbxSystemUnit.cm)
        root = scene.GetRootNode()
        wrapper = fbx.FbxNode.Create(scene, "Body Armature")
        wrapper.LclRotation.Set(fbx.FbxDouble3(7., -10., 18.))
        wrapper.LclTranslation.Set(fbx.FbxDouble3(4., -3., 2.))
        wrapper.LclScaling.Set(fbx.FbxDouble3(1.1, 1.1, 1.1))
        root.AddChild(wrapper)
        nodes = {"wrapper": wrapper}

        def add(name, parent, coords):
            parent_node = nodes[parent]
            parent_matrix = parent_node.EvaluateGlobalTransform()
            point = fbx.FbxVector4(*(TO_TEMPLATE @ np.asarray(coords) * 100.))
            local = parent_matrix.Inverse().MultT(point)
            node = fbx.FbxNode.Create(scene, "mh:" + name)
            bone = fbx.FbxSkeleton.Create(scene, "")
            bone.SetSkeletonType(fbx.FbxSkeleton.EType.eRoot if name == "root" else fbx.FbxSkeleton.EType.eLimbNode)
            node.SetNodeAttribute(bone)
            node.LclTranslation.Set(fbx.FbxDouble3(*xyz(local)))
            if name == "upperarm_l":
                node.SetRotationActive(True)
                node.SetPivotState(PIVOT, fbx.FbxNode.EPivotState.ePivotActive)
                # Deliberately non-XYZ, with both pre and post rotations.
                order = node.GetRotationOrder(PIVOT).__class__.eEulerYZX
                node.SetRotationOrder(PIVOT, order)
                node.SetPreRotation(PIVOT, fbx.FbxVector4(13., -7., 24.))
                node.SetPostRotation(PIVOT, fbx.FbxVector4(-5., 9., 12.))
                node.LclRotation.Set(fbx.FbxDouble3(14., 8., -12.))
            parent_node.AddChild(node)
            nodes[name] = node

        add("root", "wrapper", (0., 0., 0.))
        add("pelvis", "root", (0., .92, 0.))
        parent = "pelvis"
        for i in range(1, 6):
            name = f"spine_{i:02}"
            add(name, parent, (0., .92 + .09 * i, 0.))
            parent = name
        add("neck_01", parent, (0., 1.46, 0.))
        add("neck_02", "neck_01", (0., 1.50, 0.))
        add("head", "neck_02", (0., 1.60, 0.))
        for sign, side in ((1., "l"), (-1., "r")):
            add(f"thigh_{side}", "pelvis", (sign * .10, .91, 0.))
            add(f"calf_{side}", f"thigh_{side}", (sign * .10, .49, .025))
            add(f"foot_{side}", f"calf_{side}", (sign * .10, .10, 0.))
            add(f"ball_{side}", f"foot_{side}", (sign * .10, .035, .12))
            add(f"clavicle_{side}", parent, (sign * .045, 1.36, 0.))
            add(f"upperarm_{side}", f"clavicle_{side}", (sign * .20, 1.36, 0.))
            add(f"lowerarm_{side}", f"upperarm_{side}", (sign * .39, 1.17, 0.))
            add(f"upperarm_twist_01_{side}", f"upperarm_{side}", (sign * .29, 1.27, 0.))
            add(f"hand_{side}", f"lowerarm_{side}", (sign * .56, 1., 0.))
            for i, finger in enumerate(("index", "middle", "ring", "pinky", "thumb")):
                if incomplete and finger == "thumb" and side == "l":
                    continue
                spread = .035 - .022 * i
                branch = f"{finger}_metacarpal_{side}"
                add(branch, f"hand_{side}", (sign * .59, .97, spread))
                for j in range(1, 4):
                    name = f"{finger}_{j:02}_{side}"
                    add(name, branch, (sign * (.605 + .022 * j), .956 - .022 * j, spread))
                    branch = name
        store_bind_pose(scene, list(nodes.values()))
        # An unrelated mesh and old take should not leak into an animation-only export.
        build_mesh_node(scene, np.array([[0., 0., 0.], [2., 0., 0.], [0., 2., 0.]]), [[0, 1, 2]])
        stack = fbx.FbxAnimStack.Create(scene, "Template Take")
        layer = fbx.FbxAnimLayer.Create(scene, "Layer")
        stack.AddMember(layer)
        animate_translation(layer, nodes["root"], [[17., 18., 19.], [20., 21., 22.]], 1. / 30.)
        export_scene_atomic(mgr, scene, str(path))
    finally:
        mgr.Destroy()


def time(frame, fps):
    value = fbx.FbxTime()
    value.SetSecondDouble(frame / fps)
    return value


def read(mgr, path):
    scene = load_scene(mgr, str(path))
    stack = scene.GetSrcObject(fbx.FbxCriteria.ObjectType(fbx.FbxAnimStack.ClassId), 0)
    scene.SetCurrentAnimationStack(stack)
    layer = stack.GetMember(fbx.FbxCriteria.ObjectType(fbx.FbxAnimLayer.ClassId), 0)
    return scene, stack, layer, collect_skeleton_nodes(scene.GetRootNode())


def position(scene, node, frame=None, fps=30):
    matrix = node.EvaluateGlobalTransform() if frame is None else node.EvaluateGlobalTransform(time(frame, fps))
    return xyz(matrix.GetT())


def unit(v):
    return v / np.linalg.norm(v)


def verify(motion, model, out):
    out.mkdir(parents=True, exist_ok=True)
    tpl, invalid = out / "body-template.fbx", out / "incomplete-body.fbx"
    fixture(tpl)
    fixture(invalid, incomplete=True)
    with np.load(motion, allow_pickle=False) as data:
        fps = int(data.get("mocap_framerate", 30))
        original = {key: data[key] for key in ("poses", "trans", "betas")}
    frames = len(original["trans"])
    assert frames > 2
    root_shift = np.array([1.7, 0., -.8])
    scene_source = out / "late-person.npz"
    np.savez(scene_source, mocap_framerate=fps, poses_1=original["poses"], trans_1=original["trans"],
             betas_1=original["betas"], poses_2=original["poses"], trans_2=original["trans"] + root_shift,
             betas_2=original["betas"])
    paths = {name: out / f"{name}.fbx" for name in ("source", "mixamo", "mixamo-scene", "metahuman", "metahuman-late")}
    original_template = tpl.read_bytes()
    with SMPLH2FBX(smplh_model_path=model) as converter:
        converter.convert_npz_to_fbx(motion, paths["source"], first_pose="t")
        converter.convert_npz_to_fbx(motion, paths["mixamo"], first_pose="t", profile="mixamo")
        converter.convert_scene_to_fbx(scene_source, [{"person": 1, "start": 0, "end": frames - 1},
                                                      {"person": 2, "start": 17, "end": frames + 16}],
                                       paths["mixamo-scene"], export_mesh=False, profile="mixamo")
        converter.convert_npz_to_fbx(motion, paths["metahuman"], export_mesh=False, profile="metahuman", template_path=tpl)
        converter.convert_scene_to_fbx(scene_source, [{"person": 2, "start": 17, "end": frames + 16}],
                                       paths["metahuman-late"], export_mesh=False, profile="metahuman", template_path=tpl)
        try:
            converter.convert_npz_to_fbx(motion, out / "must-not-write.fbx", export_mesh=False,
                                         profile="metahuman", template_path=invalid)
        except ValueError as exc:
            assert "thumb_01_l" in str(exc), str(exc)
        else:
            raise AssertionError("Missing required bones were accepted")
    assert tpl.read_bytes() == original_template and not (out / "must-not-write.fbx").exists()

    mgr = create_manager()
    try:
        src, src_stack, _, src_bones = read(mgr, paths["source"])
        mix, mix_stack, _, mix_bones = read(mgr, paths["mixamo"])
        assert mix_stack.GetLocalTimeSpan().GetStart().GetFrameCount() == src_stack.GetLocalTimeSpan().GetStart().GetFrameCount()
        assert abs(mix_stack.GetLocalTimeSpan().GetStart().GetSecondDouble() + 1 / fps) < 1e-7
        assert len(mix_bones) == 65 and "mixamorig:LeftHandIndex4" in mix_bones and "mixamorig:HeadTop_End" in mix_bones
        assert mix_bones["mixamorig:LeftToe_End"].GetParent().GetName() == "mixamorig:LeftToeBase"
        assert mix_bones["mixamorig:LeftArm"].GetParent().GetName() == "mixamorig:LeftShoulder"
        mesh_nodes = [n.GetMesh() for n in collect_all_nodes(mix.GetRootNode()).values() if n.GetMesh()]
        assert len(mesh_nodes) == 1 and mesh_nodes[0].GetControlPointsCount() == 6890 and mesh_nodes[0].GetDeformerCount() == 1
        for frame in (-1, 0, frames // 2, frames - 1):
            for bone in ("Hips", "LeftArm", "RightForeArm", "LeftHandIndex3", "RightToeBase", "Head"):
                assert np.allclose(position(src, src_bones[bone], frame, fps),
                                   position(mix, mix_bones["mixamorig:" + bone], frame, fps), atol=2e-5), (bone, frame)

        group, group_stack, group_layer, _ = read(mgr, paths["mixamo-scene"])
        people = [group.GetRootNode().FindChild(f"Person {i}") for i in (1, 2)]
        for actor, start in zip(people, (0, 17)):
            actor_bones = collect_skeleton_nodes(actor)
            assert len(actor_bones) == 65
            hips = actor_bones["mixamorig:Hips"]
            curve = hips.LclTranslation.GetCurve(group_layer, "X")
            assert abs(curve.KeyGetTime(0).GetSecondDouble() - start / fps) < 1e-7
            assert curve.KeyGetCount() == frames
        assert abs(group_stack.GetLocalTimeSpan().GetStop().GetSecondDouble() - (frames + 16) / fps) < 1e-7

        original_scene = load_scene(mgr, str(tpl))
        original_bones = collect_skeleton_nodes(original_scene.GetRootNode())
        target, stack, layer, target_bones = read(mgr, paths["metahuman"])
        assert set(original_bones) == set(target_bones)
        assert abs(target.GetGlobalSettings().GetSystemUnit().GetScaleFactor() - 1.) < 1e-7
        assert target.GetGlobalSettings().GetAxisSystem().GetUpVector() == original_scene.GetGlobalSettings().GetAxisSystem().GetUpVector()
        assert stack.GetName() == "Motion" and target.GetSrcObjectCount(fbx.FbxCriteria.ObjectType(fbx.FbxAnimStack.ClassId)) == 1
        assert not any(n.GetMesh() for n in collect_all_nodes(target.GetRootNode()).values())
        for name, node in target_bones.items():
            assert node.GetParent().GetName() == original_bones[name].GetParent().GetName()
            assert np.allclose(position(target, node), position(original_scene, original_bones[name]), atol=2e-5), name
            assert np.allclose(xyz(node.GetPreRotation(PIVOT)), xyz(original_bones[name].GetPreRotation(PIVOT)))
            assert np.allclose(xyz(node.GetPostRotation(PIVOT)), xyz(original_bones[name].GetPostRotation(PIVOT)))
            assert node.GetRotationOrder(PIVOT) == original_bones[name].GetRotationOrder(PIVOT)
        assert target_bones["mh:index_01_l"].LclRotation.GetCurve(layer, "X").KeyGetCount() == frames
        assert target_bones["mh:head"].LclRotation.GetCurve(layer, "X").KeyGetCount() == frames
        late, _, late_layer, late_bones = read(mgr, paths["metahuman-late"])
        assert abs(late_bones["mh:root"].LclTranslation.GetCurve(late_layer, "X").KeyGetTime(0).GetSecondDouble() - 17 / fps) < 1e-7
        max_error = 0.
        for frame in (0, frames // 2, frames - 1):
            root_offset = original["trans"][frame].copy()
            root_offset[1] = 0.
            expected = position(original_scene, original_bones["mh:root"]) + (TO_TEMPLATE @ root_offset) * 100.
            assert np.allclose(position(target, target_bones["mh:root"], frame, fps), expected, atol=2e-4)
            actual_shift = position(late, late_bones["mh:root"], frame + 17, fps) - position(target, target_bones["mh:root"], frame, fps)
            assert np.allclose(actual_shift, (TO_TEMPLATE @ root_shift) * 100., atol=3e-4)
            for s0, s1, t0, t1 in (
                ("LeftArm", "LeftForeArm", "upperarm_l", "lowerarm_l"),
                ("LeftForeArm", "LeftHand", "lowerarm_l", "hand_l"),
                ("RightArm", "RightForeArm", "upperarm_r", "lowerarm_r"),
                ("LeftLeg", "LeftFoot", "calf_l", "foot_l"),
                ("LeftHandIndex1", "LeftHandIndex2", "index_01_l", "index_02_l"),
            ):
                expected = TO_TEMPLATE @ unit(position(src, src_bones[s1], frame, fps) - position(src, src_bones[s0], frame, fps))
                actual = unit(position(target, target_bones["mh:" + t1], frame, fps) - position(target, target_bones["mh:" + t0], frame, fps))
                error = float(np.linalg.norm(actual - expected))
                max_error = max(error, max_error)
                assert error < 3e-4, (s0, frame, error)
        return {"frames": frames, "fps": fps, "mixamo_bones": len(mix_bones), "template_bones": len(target_bones),
                "late_start": 17, "max_direction_error": max_error,
                "profile_files": {name: str(path) for name, path in paths.items()}}
    finally:
        mgr.Destroy()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--motion", type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    result = verify(args.motion, args.model, args.out)
    (args.out / "sdk-check.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result))
