import { Euler, Quaternion, Vector3 } from "three";

export const ROTATION_LIMIT = 180;
export const TRANSLATION_LIMIT = 5;
export const PMX_BASELINE_VERSION = 1;
const DEG = Math.PI / 180;
const HEAD_JOINT = 15;
const HEAD_X_BASE = -15;
const OFFSET_VERSION = 2;
const ZERO = [0, 0, 0];
const nonzero = (values) => values.some((value) => value !== 0);
const triple = (values, limit) => ZERO.map((_, i) => {
  const value = values?.[i];
  return Number.isFinite(value) ? Math.max(-limit, Math.min(limit, value)) : 0;
});

export function normalizePoseOffsets(settings) {
  const rotations = {};
  for (let i = 0; i < 37; i++) {
    const value = triple(settings?.rotations?.[i], ROTATION_LIMIT);
    if (nonzero(value)) rotations[i] = value;
  }
  const pose = { rotations, mirror: settings?.mirror === true, ikMode: settings?.ikMode === "ik" ? "ik" : "fk",
    rootRotation: triple(settings?.rootRotation, ROTATION_LIMIT),
    rootTranslation: triple(settings?.rootTranslation, TRANSLATION_LIMIT) };
  if (settings?.pmxBaselineVersion === PMX_BASELINE_VERSION) pose.pmxBaselineVersion = PMX_BASELINE_VERSION;
  return pose;
}

export function readStoredPose(key) {
  try {
    const saved = JSON.parse(localStorage.getItem(key));
    if (!saved) return null;
    const pose = normalizePoseOffsets(saved);
    if (saved.ikMode !== "ik" && saved.ikMode !== "fk") delete pose.ikMode;
    if (saved.version == null && pose.rotations[HEAD_JOINT]) {
      // Older previews used the slider to compensate for the uncorrected head.
      const head = pose.rotations[HEAD_JOINT];
      head[0] = 0;
      if (!nonzero(head)) delete pose.rotations[HEAD_JOINT];
    }
    return pose;
  } catch { return null; }
}

export const storedPose = (offsets) => JSON.stringify({ ...offsets, version: OFFSET_VERSION });

/** Reflect rotation offsets through the character's left/right plane in bind axes. */
export function makePoseOffsets(bindForJoint, lateral) {
  const axis = lateral.clone().normalize();
  const euler = new Euler(0, 0, 0, "XYZ"), vector = new Vector3(), reflected = new Vector3();
  const mirrored = new Quaternion(), inverse = new Quaternion();
  const joints = new Map(), rootRotation = new Quaternion(), translation = new Vector3();
  let rotated = false;
  const fromDegrees = (q, a) => q.setFromEuler(euler.set(a[0] * DEG, a[1] * DEG, a[2] * DEG));
  function reflect(q, bind, other) {
    mirrored.copy(bind).multiply(q).multiply(inverse.copy(bind).invert());
    vector.set(mirrored.x, mirrored.y, mirrored.z);
    // A rotation axis is an axial vector: reflection changes the two
    // components in the plane, rather than the one normal to it.
    reflected.copy(axis).multiplyScalar(2 * vector.dot(axis)).sub(vector);
    mirrored.set(reflected.x, reflected.y, reflected.z, mirrored.w);
    return other.clone().invert().multiply(mirrored).multiply(other).normalize();
  }
  return {
    joints,
    rootRotation,
    mirrorRotation(from, to, angles) {
      const bind = bindForJoint(from), other = bindForJoint(to);
      if (!bind || !other || !axis.lengthSq()) return null;
      euler.setFromQuaternion(reflect(fromDegrees(new Quaternion(), angles), bind, other), "XYZ");
      const [x, y, z] = [euler.x / DEG, euler.y / DEG, euler.z / DEG];
      const wrap = (a) => (a + 540) % 360 - 180;
      const primary = [x, y, z], alternate = [wrap(x + 180), wrap(180 - y), wrap(z + 180)];
      const magnitude = (a) => a.reduce((sum, angle) => sum + angle ** 2, 0);
      return (magnitude(alternate) < magnitude(primary) ? alternate : primary)
        .map((angle) => Number(angle.toFixed(2)));
    },
    set(settings) {
      const pose = normalizePoseOffsets(settings);
      // Apply the neutral head correction in the same local axes as the sliders.
      const [x, y, z] = pose.rotations[HEAD_JOINT] ?? ZERO;
      const head = [x + HEAD_X_BASE, y, z];
      if (nonzero(head)) pose.rotations[HEAD_JOINT] = head;
      else delete pose.rotations[HEAD_JOINT];
      joints.clear();
      for (const [id, a] of Object.entries(pose.rotations)) {
        const j = Number(id), bind = bindForJoint(j), q = fromDegrees(new Quaternion(), a);
        if (bind) joints.set(j, q);
        if (j < 22) continue;
        const other = bindForJoint(j + 15);
        if (!other) continue;
        if (!bind) {
          joints.set(j + 15, q);
          continue;
        }
        joints.set(j + 15, reflect(q, bind, other));
      }
      rotated = nonzero(pose.rootRotation);
      fromDegrees(rootRotation, pose.rootRotation);
      translation.fromArray(pose.rootTranslation);
    },
    position(point, offset, pivot, out) {
      out.copy(point);
      if (rotated) out.sub(pivot).applyQuaternion(rootRotation).add(pivot);
      return out.add(offset).add(translation);
    },
  };
}
