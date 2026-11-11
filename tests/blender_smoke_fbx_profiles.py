"""Load the SDK smoke files in Blender 3.6 or 4.x and evaluate their motion."""

import json
from pathlib import Path
import sys

import bpy


def load(path, *, animated=True):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    status = bpy.ops.import_scene.fbx(filepath=str(path), use_anim=animated, ignore_leaf_bones=False)
    assert "FINISHED" in status, status
    rigs = [obj for obj in bpy.context.scene.objects if obj.type == "ARMATURE"]
    assert len(rigs) == 1, [obj.name for obj in rigs]
    return rigs[0]


def position(rig, name):
    return rig.matrix_world @ rig.pose.bones[name].head


def sample(rig, names, frames):
    result = []
    for frame in frames:
        bpy.context.scene.frame_set(frame)
        result.append([position(rig, name).copy() for name in names])
    return result


def verify(out):
    baseline = load(out / "source.fbx")
    names = ("Hips", "LeftForeArm", "RightHand", "LeftToeBase", "Head")
    frames = [0, 1, 12, 36, 49]
    baseline_motion = sample(baseline, names, frames)
    mixamo = load(out / "mixamo.fbx")
    assert len(mixamo.data.bones) == 65
    assert mixamo.data.bones["mixamorig:LeftHandIndex4"].parent.name == "mixamorig:LeftHandIndex3"
    assert any(obj.type == "MESH" and any(mod.type == "ARMATURE" and mod.object == mixamo for mod in obj.modifiers)
               for obj in bpy.context.scene.objects)
    assert mixamo.animation_data and mixamo.animation_data.action
    mixamo_motion = sample(mixamo, ["mixamorig:" + name for name in names], frames)
    error = max((a - b).length for fr_a, fr_b in zip(baseline_motion, mixamo_motion) for a, b in zip(fr_a, fr_b))
    assert error < 1e-5, error

    template = load(out / "body-template.fbx", animated=False)
    template_rest = {bone.name: (bone.parent.name if bone.parent else None, bone.matrix_local.copy())
                     for bone in template.data.bones}
    target = load(out / "metahuman.fbx")
    assert not any(obj.type == "MESH" for obj in bpy.context.scene.objects)
    assert set(template_rest) == {bone.name for bone in target.data.bones}
    for bone in target.data.bones:
        parent_name, rest = template_rest[bone.name]
        assert (bone.parent.name if bone.parent else None) == parent_name, bone.name
        assert max(abs(a - b) for ra, rb in zip(rest, bone.matrix_local) for a, b in zip(ra, rb)) < 1e-5, bone.name
    action = target.animation_data.action
    assert action.frame_range[1] - action.frame_range[0] >= 47
    tracks = zip(*sample(target, ("mh:pelvis", "mh:head", "mh:hand_l"), [1, 25, 49]))
    for start, middle, end in tracks:
        assert max((middle - start).length, (end - start).length) > .015
    result = {"blender": bpy.app.version_string, "mixamo_poses_max_error_m": error, "metahuman_bones": len(template_rest),
              "metahuman_frame_range": list(action.frame_range)}
    (out / f"blender-{bpy.app.version[0]}.{bpy.app.version[1]}-check.json").write_text(
        json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print("PROFILE_SMOKE " + json.dumps(result))


verify(Path(sys.argv[sys.argv.index("--") + 1]))
