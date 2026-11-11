import { Matrix4, Quaternion, Vector3 } from "three";
import { canWriteVmdBoneName } from "./pmx-vmd.js";
import { makeHandPairFit } from "./pmx-hand-fit.js";
import { makePmxNativeIk } from "./pmx-native-ik.js";
import { makePoseOffsets, normalizePoseOffsets, PMX_BASELINE_VERSION } from "./pose-offsets.js";

const JOINTS = 52;
const UP = new Vector3(0, 1, 0);
const EPSILON = 1e-8;
const ZERO_ANGLES = [0, 0, 0];
const wrapAngle = (value) => Math.abs(value) <= 180 ? value : (value + 540) % 360 - 180;
const key = (name = "") => name.normalize("NFKC").toLowerCase().replace(/[\s_.:\-]/g, "");
const vector = (data) => new Vector3().fromArray(data);
const quaternion = (data) => new Quaternion().fromArray(data).normalize();

function mapBones(bones) {
  const names = new Map();
  // Prefer the PMX bone's actual name over another bone's English alias.
  for (const field of ["name", "nameEn"]) for (const bone of bones) {
    const normalized = key(bone[field] ?? "");
    if (normalized && !names.has(normalized)) names.set(normalized, bone.index);
  }
  const find = (...aliases) => {
    for (const alias of aliases) {
      const index = names.get(key(alias));
      if (index !== undefined) return index;
    }
    return -1;
  };
  const map = new Int32Array(JOINTS).fill(-1);
  map[0] = find("下半身", "腰", "pelvis", "hips", "lowerbody");
  map[12] = find("首", "neck");
  map[15] = find("頭", "head");
  // Some PMX models insert 上半身3 below 上半身2. Use actual ancestry
  // so the waist is always mapped before the chest.
  const spineNames = new Set(["上半身", "上半身1", "上半身2", "上半身3", "spine", "spine1",
    "spine2", "spine3", "upperbody", "upperbody1", "upperbody2", "upperbody3", "chest", "upperchest"]
    .map((n) => find(n)).filter((i) => i >= 0));
  const spinePath = (from) => {
    const path = [];
    for (let i = from; i >= 0; i = bones[i].parent) if (spineNames.has(i)) path.unshift(i);
    return path;
  };
  let spine = spinePath(map[12] >= 0 ? map[12] : map[15]);
  if (!spine.length) {
    for (const bone of spineNames) {
      const candidate = spinePath(bone);
      if (candidate.length > spine.length) spine = candidate;
    }
  }
  map[3] = spine[0] ?? -1;
  if (spine.length > 1) map[9] = spine.at(-1);
  if (spine.length > 2) map[6] = spine[Math.floor((spine.length - 1) / 2)];
  for (const s of [0, 1]) {
    const jp = s === 0 ? "左" : "右";
    const en = s === 0 ? "left" : "right";
    const short = en[0];
    const side = (jpNames, enNames) => find(...jpNames.map((n) => jp + n),
      ...enNames.flatMap((n) => [en + n, n + en, n + short, short + n]));
    map[1 + s] = side(["足", "足D"], ["leg", "thigh", "upleg"]);
    map[4 + s] = side(["ひざ", "膝", "ひざD", "膝D"], ["knee", "shin", "lowerleg"]);
    map[7 + s] = side(["足首", "足首D"], ["ankle", "foot"]);
    map[10 + s] = side(["足先EX", "つま先", "足先"], ["toe", "toebase", "toes"]);
    map[13 + s] = side(["肩"], ["shoulder", "clavicle"]);
    map[16 + s] = side(["腕"], ["upperarm", "arm"]);
    map[18 + s] = side(["ひじ", "肘"], ["elbow", "forearm", "lowerarm"]);
    map[20 + s] = side(["手首"], ["wrist", "hand"]);

    const digits = [
      [["人指", "人差指", "人差し指"], "index"], [["中指"], "middle"],
      [["小指"], "pinky"], [["薬指"], "ring"], [["親指"], "thumb"],
    ];
    for (let digit = 0; digit < digits.length; digit++) {
      const [jpNames, enName] = digits[digit];
      const finger = (i) => side(jpNames.map((n) => n + i), [enName + i, "hand" + enName + i]);
      const start = digit === 4 && finger(0) >= 0 ? 0 : 1;
      for (let i = 0; i < 3; i++) map[22 + s * 15 + digit * 3 + i] = finger(start + i);
    }
  }
  const used = new Set();
  for (let i = 0; i < map.length; i++) {
    if (used.has(map[i])) map[i] = -1;
    if (map[i] >= 0) used.add(map[i]);
  }
  return map;
}

function lateralYaw(left, right) {
  const dx = left.x - right.x;
  const dz = left.z - right.z;
  if (dx * dx + dz * dz < EPSILON) throw new Error("PMX shoulder direction cannot be determined.");
  return Math.atan2(-dz, dx);
}

function palmBasis(points, wrist, index, middle, pinky) {
  if ([wrist, index, middle, pinky].some((i) => i < 0 || !points[i])) return null;
  const along = points[middle].clone().sub(points[wrist]);
  const across = points[index].clone().sub(points[pinky]);
  if (along.lengthSq() < EPSILON) return null;
  along.normalize();
  across.addScaledVector(along, -across.dot(along));
  if (across.lengthSq() < EPSILON) return null;
  across.normalize();
  return new Quaternion().setFromRotationMatrix(new Matrix4().makeBasis(
    across, along, across.clone().cross(along).normalize(),
  ));
}

function nextSpineJoint(map, joint) {
  if (joint === 3 && map[6] >= 0) return 6;
  if (joint < 9 && map[9] >= 0) return 9;
  return 12;
}

function restCorrection(joint, map, source, target) {
  const bone = map[joint];
  if (joint === 20 || joint === 21) {
    const base = 22 + (joint - 20) * 15;
    const ids = [joint, base, base + 3, base + 6];
    const from = palmBasis(target, ...ids.map((i) => map[i]));
    const to = palmBasis(source, ...ids);
    if (from && to) return to.multiply(from.invert());
  }

  // PMX and SMPL-H can have different bind angles and limb lengths. Match
  // the directions used by the source rotations, keeping the target lengths.
  // The neck and head use rest-relative rotation: a PMX head pivot is not
  // the SMPL-H head joint, so fitting that segment would push the face forward.
  // Clavicle pivot height varies between PMX rigs. Fit the arm height in
  // the posed chest frame instead of copying the clavicle's rest angle.
  // Keep the rest pitch of the shoe.
  // The forefoot of a high heel is not in the same place as an SMPL-H toe.
  const ends = { 1: 4, 2: 5, 3: nextSpineJoint(map, 3), 4: 7, 5: 8,
    6: nextSpineJoint(map, 6), 9: 12, 16: 18, 17: 19,
    18: 20, 19: 21, 20: 25, 21: 40 };
  const end = joint < 22 ? ends[joint] : ((joint - 22) % 3 === 2 ? joint - 1 : joint + 1);
  if (end === undefined || map[end] < 0) return new Quaternion();
  const from = target[map[end]].clone().sub(target[bone]);
  const to = source[end].clone().sub(source[joint]);
  if (from.lengthSq() < EPSILON || to.lengthSq() < EPSILON) return new Quaternion();
  return new Quaternion().setFromUnitVectors(from.normalize(), to.normalize());
}

function boneOrder(parents, grants) {
  const dependents = parents.map(() => []);
  const remaining = new Uint8Array(parents.length);
  const ready = [];
  for (let i = 0; i < parents.length; i++) {
    for (const dependency of [parents[i], grants[i]]) if (dependency >= 0) {
      dependents[dependency].push(i);
      remaining[i]++;
    }
    if (!remaining[i]) ready.push(i);
  }
  for (let i = 0; i < ready.length; i++) {
    for (const dependent of dependents[ready[i]]) if (--remaining[dependent] === 0) ready.push(dependent);
  }
  if (ready.length !== parents.length) throw new Error("PMX skeleton contains a cycle.");
  return ready;
}

function grantCycle(from, to, parents, grants) {
  const visited = new Set();
  const pending = [from];
  while (pending.length) {
    const bone = pending.pop();
    if (bone === to) return true;
    if (bone < 0 || visited.has(bone)) continue;
    visited.add(bone);
    pending.push(parents[bone], grants[bone]);
  }
  return false;
}

function scaleRotation(result, rotation, ratio) {
  const sign = rotation.w < 0 ? -1 : 1;
  const length = Math.hypot(rotation.x, rotation.y, rotation.z);
  if (length < EPSILON) return result.identity();
  const angle = ratio * Math.atan2(length, Math.abs(rotation.w));
  const scale = Math.sin(angle) * sign / length;
  return result.set(rotation.x * scale, rotation.y * scale, rotation.z * scale, Math.cos(angle));
}

function vmdRoot(bones, map, parents, grants) {
  const ancestors = (i) => {
    const result = [];
    for (; i >= 0; i = parents[i]) result.push(i);
    return result;
  };
  const common = ancestors(map[0]).reverse();
  const paths = Array.from(map).filter((i) => i >= 0).map((i) => new Set(ancestors(i)));
  // One common movable bone must carry the same placement that the preview
  // can otherwise put on the mesh object. VMD has no object transform track.
  return common.find((i) => (bones[i].pmx?.flags & 4) && paths.every((path) => path.has(i)) &&
    ancestors(parents[i]).every((p) => grants[p] < 0 && !map.includes(p))) ?? -1;
}

function footProbes(mesh, bones, map, restPosition, restQuaternion, meshPosition, meshRotation, scale) {
  const attrs = mesh.geometry?.attributes;
  const probes = [[[], []], [[], []]];
  if (!attrs?.position || !attrs.skinIndex || !attrs.skinWeight) return probes;
  const position = new Vector3();
  const indices = Array.from({ length: 4 }, (_, i) => i);
  const inverse = restQuaternion.map((q) => q.clone().invert());
  const footSide = new Int8Array(bones.length).fill(-1);
  const ankles = [restPosition[map[7]], restPosition[map[8]]];
  const axes = [0, 1].map((s) => {
    const toe = restPosition[map[10 + s]];
    const forward = toe ? toe.clone().sub(ankles[s]).setY(0) : new Vector3(0, 0, 1);
    const split = toe ? Math.max(0.024, Math.min(0.055, forward.length() * 0.5)) : 0.035;
    if (forward.lengthSq() <= EPSILON) forward.set(0, 0, 1);
    forward.normalize();
    return { forward, across: new Vector3().crossVectors(UP, forward), split };
  });
  for (const s of [0, 1]) {
    const roots = new Set([map[7 + s], map[10 + s]].filter((i) => i >= 0));
    const jp = s === 0 ? "左" : "右";
    for (const b of bones) {
      if (new RegExp(`^${jp}(足首d?|つま先|足先ex)$`).test(key(b.name))) roots.add(b.index);
    }
    for (const b of bones) {
      let i = b.index;
      while (i >= 0) {
        if (roots.has(i)) {
          footSide[b.index] = s;
          break;
        }
        i = bones[i].parent;
      }
    }
  }

  // Keep a spatially spread lower envelope instead of the N absolute lowest
  // rest vertices. UV seams can duplicate the same corner many times, and the
  // lowest point changes as the sole tilts, especially on a high heel.
  const bins = [[new Map(), new Map()], [new Map(), new Map()]];
  const fromAnkle = new Vector3();
  for (let i = 0; i < attrs.position.count; i++) {
    const sums = [0, 0];
    for (const k of indices) {
      const side = footSide[attrs.skinIndex.getComponent(i, k)];
      if (side >= 0) sums[side] += attrs.skinWeight.getComponent(i, k);
    }
    const side = sums[0] >= sums[1] ? 0 : 1;
    if (sums[side] < 0.55) continue;
    position.fromBufferAttribute(attrs.position, i).multiplyScalar(scale).applyQuaternion(meshRotation).add(meshPosition);
    if (position.y > ankles[side].y + 0.03) continue;
    fromAnkle.copy(position).sub(ankles[side]);
    const { forward, across, split } = axes[side];
    const along = fromAnkle.dot(forward), lateral = fromAnkle.dot(across);
    const selected = bins[side][along > split ? 1 : 0];
    const cell = `${Math.floor(along / 0.011)}:${Math.floor(lateral / 0.011)}`;
    if (!selected.has(cell) || position.y < selected.get(cell).y) selected.set(cell, { index: i, y: position.y });
  }
  for (const s of [0, 1]) for (const part of [0, 1]) {
    const selected = [...bins[s][part].values()].sort((a, b) => a.y - b.y);
    if (!selected.length) continue;
    const limit = selected[0].y + 0.06;
    for (const { index, y } of selected.slice(0, 96)) {
      if (y > limit) break;
      position.fromBufferAttribute(attrs.position, index).multiplyScalar(scale).applyQuaternion(meshRotation).add(meshPosition);
      const influences = [];
      for (const k of indices) {
        const weight = attrs.skinWeight.getComponent(index, k);
        const bone = attrs.skinIndex.getComponent(index, k);
        if (weight > 0 && restPosition[bone]) {
          influences.push({ bone, weight, offset: position.clone().sub(restPosition[bone]).applyQuaternion(inverse[bone]) });
        }
      }
      probes[s][part].push({ y, influences });
    }
  }
  return probes;
}

const easeContact = (x) => {
  const t = Math.min(1, Math.max(0, x));
  return t * t * (3 - 2 * t);
};

function bridgeSoleContacts(motion) {
  const { contacts, soles, joints, floor, frames } = motion;
  if (soles?.length !== frames * 4) return contacts;
  const bridged = contacts.slice();
  for (const side of [0, 1]) {
    const part = side * 2;
    const active = (f) => Math.max(contacts[f * 4 + part], contacts[f * 4 + part + 1]) > 0.75;
    const step = (f, joint) => {
      const k = (f * JOINTS + joint) * 3, p = k - JOINTS * 3;
      return Math.hypot(joints[k] - joints[p], joints[k + 2] - joints[p + 2]);
    };
    for (let f = 1; f < frames - 1;) {
      if (active(f) || !active(f - 1)) {
        f++;
        continue;
      }
      const start = f++;
      while (f < frames && !active(f)) f++;
      if (f - start > 2 || f === frames) continue;
      let nearFloor = true;
      for (let i = start; i < f; i++) {
        const heel = soles[i * 4 + part], toe = soles[i * 4 + part + 1];
        if (!(Math.min(heel, toe) < floor + 0.012 &&
              Math.min(step(i, 7 + side), step(i, 10 + side)) < 0.035)) nearFloor = false;
      }
      if (!nearFloor) continue;
      // Contact labels may drop for a frame or two as load transfers between
      // heel and toe. Bridge only low, slow soles bounded by actual support.
      for (let i = start; i < f; i++) {
        const mix = (i - start + 1) / (f - start + 1);
        for (const k of [part, part + 1]) {
          const before = contacts[(start - 1) * 4 + k], after = contacts[f * 4 + k];
          bridged[i * 4 + k] = Math.max(contacts[i * 4 + k], before + (after - before) * mix);
        }
      }
    }
  }
  return bridged;
}

function contactRuns(motion, part, sourceJoint) {
  const { frames, contacts, joints } = motion;
  const frameToRun = new Int32Array(frames).fill(-1);
  const runs = [];
  const step = (a, b) => {
    const i = (a * JOINTS + sourceJoint) * 3;
    const j = (b * JOINTS + sourceJoint) * 3;
    return Math.hypot(joints[i] - joints[j], joints[i + 2] - joints[j + 2]);
  };
  const stable = (f) => f < frames && contacts[f * 4 + part] > 0.75 &&
    (f < 1 || step(f - 1, f) < 0.025) && (f === frames - 1 || step(f, f + 1) < 0.025);
  for (let f = 0; f < frames;) {
    if (!stable(f)) {
      f++;
      continue;
    }
    const start = f++;
    while (stable(f)) f++;
    if (f - start >= 3) {
      frameToRun.fill(runs.length, start, f);
      runs.push({ start, end: f, frame: start + Math.min(2, Math.floor((f - start) / 2)), anchor: new Vector3() });
    }
  }
  return { frameToRun, runs };
}

/** Prepare a deterministic, actor-local PMX preview for one saved motion.
 * The caller owns clip timing and any external group transform for multiple actors.
 */
export function createPmxRetarget(mesh, rest, motion, { poseOffsets = null } = {}) {
  const { bones, unitScale: scale } = rest;
  const { frames, joints, quaternions, contacts, floor, soles } = motion;
  if (!Number.isFinite(scale) || scale <= 0 || !bones?.length || mesh.skeleton?.bones.length !== bones.length) {
    throw new Error("Invalid PMX bind skeleton.");
  }
  if (!Number.isInteger(frames) || frames < 1 || motion.rest?.length !== JOINTS * 3 ||
      joints?.length !== frames * JOINTS * 3 || quaternions?.length !== frames * JOINTS * 4) {
    throw new Error("Invalid motion for PMX preview.");
  }
  const parents = bones.map((bone, i) => {
    const p = bone.parent;
    if (bone.index !== i || !Number.isInteger(p) || p < -1 || p >= bones.length || p === i) {
      throw new Error("Invalid PMX bone parent.");
    }
    if (bone.scale?.some((value) => Math.abs(value - 1) > 1e-5)) throw new Error("Unsupported PMX bind bone scale.");
    return p;
  });
  const grants = new Int32Array(bones.length).fill(-1);
  boneOrder(parents, grants);
  const map = mapBones(bones);
  const needed = [0, 1, 2, 3, 4, 5, 7, 8, 13, 14, 15, 16, 17, 18, 19, 20, 21];
  if (needed.some((i) => map[i] < 0)) throw new Error("PMX requires standard humanoid torso, arm and leg bones.");

  const source = Array.from({ length: JOINTS }, (_, i) => new Vector3().fromArray(motion.rest, i * 3));
  const originalRest = bones.map((b) => vector(b.worldPosition));
  const align = new Quaternion().setFromAxisAngle(UP,
    lateralYaw(source[13], source[14]) - lateralYaw(originalRest[map[13]], originalRest[map[14]]));
  const meshPosition = mesh.position.clone();
  const originalMeshRotation = mesh.quaternion.clone();
  const meshRotation = align.clone().multiply(originalMeshRotation).normalize();
  const originalRotation = bones.map((b) => quaternion(b.worldQuaternion));
  const bindPosition = originalRest.map((p) => p.clone().sub(meshPosition).applyQuaternion(align).add(meshPosition));
  const bindRotation = originalRotation.map((q) => align.clone().multiply(q).normalize());
  const edits = makePoseOffsets((j) => bindRotation[map[j]], source[16].clone().sub(source[17]).setY(0));
  // Keep the sliders relative to the PMX fitting pose. Reflect in each model's
  // bind axes, just as the interactive mirror control does.
  const shoulderDefaults = { 13: [0, 0, 15], 16: [0, 0, -15] };
  shoulderDefaults[14] = edits.mirrorRotation(13, 14, shoulderDefaults[13]) ?? [0, 0, -15];
  shoulderDefaults[17] = edits.mirrorRotation(16, 17, shoulderDefaults[16]) ?? [0, 0, 15];
  const shoulderAngles = (joint, angles, direction = 1) => shoulderDefaults[joint]
    ? angles.map((angle, axis) => wrapAngle(angle + direction * shoulderDefaults[joint][axis])) : angles;
  const restLocal = bones.map((b) => quaternion(b.quaternion));
  const invRestLocal = restLocal.map((q) => q.clone().invert());
  const localPosition = bones.map((b) => vector(b.position));
  const reference = new Array(bones.length);
  const sourceForBone = new Int32Array(bones.length).fill(-1);
  for (let j = 0; j < JOINTS; j++) if (map[j] >= 0) {
    const i = map[j];
    sourceForBone[i] = j;
    reference[i] = restCorrection(j, map, source, bindPosition).multiply(bindRotation[i]).normalize();
  }

  const shoulderAccessories = new Map();
  const bodyAncestors = new Set();
  for (const mapped of map) for (let i = mapped; i >= 0 && !bodyAncestors.has(i); i = parents[i]) {
    bodyAncestors.add(i);
  }
  const nameCounts = new Map();
  for (const {name} of bones) nameCounts.set(name, (nameCounts.get(name) ?? 0) + 1);
  for (const shoulder of [map[13], map[14]]) {
    for (const {index, name, parent, pmx} of bones) {
      if (parent !== shoulder || bodyAncestors.has(index) || bodyAncestors.has(pmx?.grant?.parentIndex) ||
          /^(?:左|右)(?:腕|肩|ひじ|肘|手首)|(?:arm|shoulder|clavicle|elbow|wrist)/.test(key(name)) ||
          nameCounts.get(name) !== 1 || !canWriteVmdBoneName(name)) continue;
      // Cloth attached directly to a clavicle should follow the actor's
      // motion, without inheriting the extra shoulder fitting rotation.
      // Key that counterrotation in target VMD so preview and MMD agree.
      shoulderAccessories.set(index, shoulder);
    }
  }

  const grantBasis = new Array(bones.length);
  const invGrantBasis = new Array(bones.length);
  let skippedGrants = 0;
  for (const bone of bones) {
    const i = bone.index;
    const grant = bone.pmx?.grant;
    if (!grant) continue;
    const p = grant.parentIndex;
    if (grant.isLocal || grant.affectPosition) skippedGrants++;
    if (!grant.affectRotation || grant.isLocal || !Number.isFinite(grant.ratio)) continue;
    if (!Number.isInteger(p) || p < 0 || p >= bones.length || grantCycle(p, i, parents, grants)) {
      skippedGrants++;
      continue;
    }
    grants[i] = p;
    grantBasis[i] = bindRotation[i].clone().invert().multiply(bindRotation[p]);
    invGrantBasis[i] = grantBasis[i].clone().invert();
  }
  const order = boneOrder(parents, grants);
  const localRotation = bones.map(() => new Quaternion());
  const appendRotation = bones.map(() => new Quaternion());
  const worldRotation = bones.map(() => new Quaternion());
  const worldPosition = bones.map(() => new Vector3());
  const skin = mesh.skeleton.bones;
  const root = vmdRoot(bones, map, parents, grants);
  const nativeIk = makePmxNativeIk(mesh, bones, parents, bodyAncestors, root, nameCounts);
  let ikMode;
  setPoseOffsets(poseOffsets);
  const localInverse = new Quaternion();

  const toes = [7, 8, 10, 11];
  const sourceSole = Number.isFinite(motion.restFloor) ? motion.restFloor : Math.min(...toes.map((i) => source[i].y)) - 0.015;
  const probes = footProbes(mesh, bones, map, bindPosition, bindRotation, meshPosition, meshRotation, scale);
  const footJointY = Math.min(...toes.filter((i) => map[i] >= 0).map((i) => bindPosition[map[i]].y));
  const probeY = Math.min(...probes.flat().filter((p) => p.length).map((p) => p[0].y));
  const boundY = rest.bounds?.min?.[1];
  const targetSole = Number.isFinite(probeY) ? probeY :
    (Number.isFinite(boundY) && boundY <= footJointY && footJointY - boundY < 0.25 ? boundY : footJointY - 0.015);
  const hipHeightOffset = (bindPosition[map[0]].y - targetSole) - (source[0].y - sourceSole);
  const support = Number.isFinite(floor) && contacts?.length === frames * 4 && probes.flat().some((p) => p.length > 0);

  const sourceRotation = new Quaternion();
  const accessoryRotation = new Quaternion();
  const grantRotation = new Quaternion();
  const inverse = new Quaternion();
  const offset = new Vector3();
  const point = new Vector3();
  const soleContacts = support ? bridgeSoleContacts(motion) : contacts;
  const contactAt = (frame, part) => soleContacts[Math.min(frames - 1, Math.max(0, frame)) * 4 + part];

  const spineJoints = [3, 6, 9].filter((j) => map[j] >= 0);
  const torso = new Set(spineJoints.map((j) => map[j]));
  const spineSpans = new Map();
  for (const j of spineJoints) {
    const end = nextSpineJoint(map, j);
    if (map[end] >= 0 && end - j > 3) {
      spineSpans.set(map[j], { start: j, end, axis: source[end].clone().sub(source[j]).normalize(),
        adjustment: new Quaternion() });
    }
  }
  const torsoSwing = new Quaternion();
  const desiredTorso = new Vector3();
  const toNeck = new Vector3();
  const toPivot = new Vector3();
  const torsoDelta = new Quaternion();
  const spineFrom = new Vector3();
  const spineTo = new Vector3();
  const limbOverrides = new Map();
  const desiredAnkle = new Vector3();
  const limbGoal = new Vector3();
  const captureStart = new Vector3();
  const captureEnd = new Vector3();
  const captureAnchor = new Vector3();
  const anchorShift = new Vector3();
  const bodyShift = new Vector3();
  const legAxis = new Vector3();
  const pole = new Vector3();
  const thigh = new Vector3();
  const shin = new Vector3();
  const bentThigh = new Vector3();
  const bentShin = new Vector3();
  const hipLine = new Vector3();
  const captureHip = new Vector3();
  const captureThigh = new Vector3();
  const captureAxis = new Vector3();
  const sideAxis = new Vector3();
  const forwardAxis = new Vector3();
  const foot = new Vector3();
  const heel = new Vector3();
  const toe = new Vector3();
  const soleAxis = new Vector3();
  const groundAxis = new Vector3();
  const flatten = new Quaternion();
  const flattenDelta = new Quaternion();
  const soleTargets = [{ y: 0, remaining: 0 }, { y: 0, remaining: 0 }];
  const hasSourceSoles = soles?.length === frames * 4;
  const sourceProbe = new Vector3();
  const sourceFootOffsets = [7, 10, 8, 11].map((j) => new Vector3(0, sourceSole - source[j].y, 0));
  const vertex = new Vector3();
  const correction = new Vector3();
  function makeChain(a, b, c) {
    const start = map[a], middle = map[b], end = map[c];
    const sourceLength = source[a].distanceTo(source[b]) + source[b].distanceTo(source[c]);
    const length = bindPosition[start].distanceTo(bindPosition[middle]) + bindPosition[middle].distanceTo(bindPosition[end]);
    return { start, middle, end, a, c, sourceLength, length, upperQ: new Quaternion(), lowerQ: new Quaternion() };
  }
  const legs = [0, 1].map((s) => {
    const hip = map[1 + s], knee = map[4 + s], ankle = map[7 + s];
    const native = [...nativeIk.moving.values()].some(({ effector }) => effector === ankle);
    return { hip, knee, ankle, toe: map[10 + s], ik: parents[knee] === hip && parents[ankle] === knee,
      chain: makeChain(1 + s, 4 + s, 7 + s), ankleQ: new Quaternion(), toeQ: new Quaternion(),
      native, hasTarget: false, target: new Vector3(), targetDelta: new Vector3() };
  });
  const controllerLeg = new Map();
  for (const control of nativeIk.moving.values()) {
    for (let i = control.effector; i >= 0; i = parents[i]) {
      const leg = legs.find((candidate) => candidate.native && candidate.ankle === i);
      if (!leg) continue;
      controllerLeg.set(control, leg);
      break;
    }
  }
  const arms = [0, 1].map((s) => ({
    chain: makeChain(16 + s, 18 + s, 20 + s), anchors: [15, 12, 17 - s, 0, 1 + s].filter((j) => map[j] >= 0),
  }));
  const handPair = makeHandPairFit(motion, map, bindPosition, arms);
  const handCorrection = new Vector3();
  const neck = map[12], chest = map[9] >= 0 ? map[9] : map[6] >= 0 ? map[6] : map[3];
  const shoulderScale = bindPosition[map[16]].distanceTo(bindPosition[map[17]]) / source[16].distanceTo(source[17]);
  const shoulderAdjustments = [new Quaternion(), new Quaternion()];
  const sourceUp = new Vector3(), targetUp = new Vector3();
  const shoulderVector = new Vector3(), shoulderGoal = new Vector3();

  function pose(frame, swing, overrides) {
    const rotations = frame * JOINTS * 4;
    for (const i of order) {
      const parent = parents[i];
      const parentRotation = parent < 0 ? meshRotation : worldRotation[parent];
      const parentPosition = parent < 0 ? meshPosition : worldPosition[parent];
      const j = sourceForBone[i];
      const q = localRotation[i];
      const append = appendRotation[i];
      const p = grants[i];
      if (p >= 0) {
        // D legs and other PMX append bones receive a local rotation delta
        // expressed in their own bind axes. Mapped FK bones already have a pose.
        grantRotation.copy(grantBasis[i]).multiply(invRestLocal[p]).multiply(localRotation[p]).multiply(invGrantBasis[i]);
        scaleRotation(append, grantRotation, bones[i].pmx.grant.ratio);
      } else {
        append.identity();
      }
      if (j >= 0) {
        const worldOverride = overrides?.get(i);
        if (worldOverride) {
          q.copy(parentRotation).invert().multiply(worldOverride).normalize();
        } else {
          sourceRotation.fromArray(quaternions, rotations + j * 4).normalize();
          const span = spineSpans.get(i);
          if (span) sourceRotation.premultiply(span.adjustment);
          if (swing && torso.has(i)) sourceRotation.premultiply(swing);
          q.copy(parentRotation).invert().multiply(sourceRotation).multiply(reference[i]).normalize();
        }
      } else {
        q.multiplyQuaternions(restLocal[i], append).normalize();
        const shoulder = shoulderAccessories.get(i);
        if (shoulder !== undefined) {
          const j = sourceForBone[shoulder];
          sourceRotation.fromArray(quaternions, rotations + j * 4).normalize().multiply(bindRotation[shoulder]);
          q.premultiply(accessoryRotation.copy(worldRotation[shoulder]).invert().multiply(sourceRotation)).normalize();
        }
      }
      worldRotation[i].multiplyQuaternions(parentRotation, q);
      worldPosition[i].copy(localPosition[i]).multiplyScalar(scale).applyQuaternion(parentRotation).add(parentPosition);
    }
  }

  function alignRoot(frame) {
    offset.fromArray(joints, frame * JOINTS * 3).sub(worldPosition[map[0]]);
    offset.y += hipHeightOffset;
  }

  function footPoint(list, result, lowest = false) {
    let x = 0, y = Infinity, z = 0;
    for (const probe of list) {
      vertex.set(0, 0, 0);
      for (const { bone, weight, offset: local } of probe.influences) {
        vertex.addScaledVector(point.copy(local).applyQuaternion(worldRotation[bone]).add(worldPosition[bone]), weight);
      }
      x += vertex.x;
      if (vertex.y < y) {
        y = vertex.y;
        if (lowest) result.copy(vertex);
      }
      z += vertex.z;
    }
    return lowest ? result : result.set(x / list.length, y, z / list.length);
  }

  function contactStrength(frame, part) {
    return 0.25 * contactAt(frame - 1, part) + 0.5 * contactAt(frame, part) + 0.25 * contactAt(frame + 1, part);
  }

  function sourceFootY(frame, joint, part) {
    if (hasSourceSoles && Number.isFinite(soles[frame * 4 + part])) return soles[frame * 4 + part];
    sourceRotation.fromArray(quaternions, (frame * JOINTS + joint) * 4).normalize();
    return joints[(frame * JOINTS + joint) * 3 + 1] + sourceProbe.copy(sourceFootOffsets[part]).applyQuaternion(sourceRotation).y;
  }

  function levelFeet(frame, swing) {
    for (const s of [0, 1]) {
      const leg = legs[s];
      const goal = soleTargets[s];
      goal.remaining = 0;
      if (!leg.ik || leg.toe < 0 || !probes[s][0].length || !probes[s][1].length) continue;
      // Fit the visible sole on support. For single-part contact retain the
      // source's heel/toe lift, and leave an airborne foot's pitch untouched.
      const heelConfidence = contactStrength(frame, 2 * s);
      const toeConfidence = contactStrength(frame, 2 * s + 1);
      const confidence = Math.max(heelConfidence, toeConfidence);
      const weight = easeContact((confidence - 0.60) / 0.37);
      if (weight <= 0) continue;
      const sourcePitch = sourceFootY(frame, 10 + s, 2 * s + 1) - sourceFootY(frame, 7 + s, 2 * s);
      const lift = sourcePitch < 0
        ? Math.max(-0.11, sourcePitch) * (1 - heelConfidence) * easeContact((toeConfidence - 0.5) / 0.47)
        : Math.min(0.11, sourcePitch) * (1 - toeConfidence) * easeContact((heelConfidence - 0.5) / 0.47);
      const current = footPoint(probes[s][1], toe).y - footPoint(probes[s][0], heel).y;
      goal.y = current + (lift - current) * weight;
      goal.remaining = Math.PI * 0.18 * weight;
    }
    // Rotate around actual deformed support points. Re-evaluate after each
    // correction because the lowest corner can change as the shoe rolls.
    for (let step = 0; step < 3; step++) {
      let changed = false;
      for (const s of [0, 1]) {
        const leg = legs[s], goal = soleTargets[s];
        if (goal.remaining < 0.002) continue;
        footPoint(probes[s][0], heel, true);
        footPoint(probes[s][1], toe, true);
        soleAxis.copy(toe).sub(heel);
        groundAxis.copy(soleAxis).setY(0);
        if (groundAxis.lengthSq() < 0.0016 || Math.abs(soleAxis.y - goal.y) < 0.0004) continue;
        const length = soleAxis.length();
        const lift = Math.max(-length * 0.9, Math.min(length * 0.9, goal.y));
        groundAxis.setLength(Math.sqrt(length * length - lift * lift)).setY(lift);
        flatten.setFromUnitVectors(soleAxis.normalize(), groundAxis.normalize());
        const angle = 2 * Math.acos(Math.min(1, Math.abs(flatten.w)));
        if (angle < EPSILON) continue;
        const delta = Math.min(angle, goal.remaining);
        goal.remaining -= delta;
        scaleRotation(flattenDelta, flatten, delta / angle);
        limbOverrides.set(leg.ankle, leg.ankleQ.multiplyQuaternions(flattenDelta, worldRotation[leg.ankle]));
        limbOverrides.set(leg.toe, leg.toeQ.multiplyQuaternions(flattenDelta, worldRotation[leg.toe]));
        changed = true;
      }
      if (!changed) break;
      pose(frame, swing, limbOverrides);
    }
  }

  function limitKneeBow(frame, side, radius, length, flexion) {
    // Grounding can shorten a nearly straight leg and amplify a small
    // outward bend. Use the source knee's side deviation as the limit.
    const upright = Math.min(1, Math.max(0, (-legAxis.y - 0.65) / 0.25));
    const shallow = Math.min(1, Math.max(0, (1.05 - flexion) / 0.35));
    if (radius < 1e-5 || upright * shallow === 0) return;
    const base = frame * JOINTS * 3;
    hipLine.fromArray(joints, base + 3).sub(captureHip.fromArray(joints, base + 6)).setY(0);
    if (hipLine.lengthSq() < EPSILON) return;
    hipLine.normalize();
    captureHip.fromArray(joints, base + (1 + side) * 3);
    captureThigh.fromArray(joints, base + (4 + side) * 3).sub(captureHip);
    captureAxis.fromArray(joints, base + (7 + side) * 3).sub(captureHip);
    const captureLength = captureThigh.length();
    if (captureLength < 1e-5 || captureAxis.lengthSq() < EPSILON) return;
    captureAxis.normalize();
    captureThigh.addScaledVector(captureAxis, -captureThigh.dot(captureAxis));

    sideAxis.copy(hipLine).addScaledVector(legAxis, -hipLine.dot(legAxis));
    const sideLength = sideAxis.length();
    if (sideLength < 1e-5) return;
    sideAxis.divideScalar(sideLength);
    const sign = side === 0 ? 1 : -1;
    const sourceOut = Math.max(0, sign * captureThigh.dot(hipLine) / captureLength * length / sideLength);
    const targetOut = sign * pole.dot(sideAxis) * radius;
    if (targetOut <= sourceOut) return;
    const lateral = sign * (targetOut + upright * shallow * (sourceOut - targetOut)) / radius;
    forwardAxis.crossVectors(legAxis, sideAxis);
    const forward = (pole.dot(forwardAxis) < 0 ? -1 : 1) * Math.sqrt(Math.max(0, 1 - lateral * lateral));
    pole.copy(sideAxis).multiplyScalar(lateral).addScaledVector(forwardAxis, forward);
  }

  function pointLimb(chain, goal, frame, side = -1) {
    const { start, middle, end, upperQ, lowerQ } = chain;
    const base = worldPosition[start];
    thigh.copy(worldPosition[middle]).sub(base);
    shin.copy(worldPosition[end]).sub(worldPosition[middle]);
    const a = thigh.length(), b = shin.length();
    legAxis.copy(goal).sub(base);
    const distance = legAxis.length();
    if (distance < 1e-4 || Math.min(a, b) < 0.02) return;
    legAxis.multiplyScalar(1 / distance);
    pole.copy(thigh).addScaledVector(legAxis, -thigh.dot(legAxis));
    if (pole.lengthSq() < 1e-6) {
      pole.copy(thigh).addScaledVector(shin, -thigh.dot(shin) / shin.lengthSq())
        .addScaledVector(legAxis, -pole.dot(legAxis));
      if (pole.lengthSq() < 1e-6 && side >= 0) {
        pole.set(0, 0, 1).applyQuaternion(worldRotation[map[0]]).addScaledVector(legAxis, -pole.dot(legAxis));
      }
      if (pole.lengthSq() < 1e-6) return;
    }
    pole.normalize();
    const d = Math.max(Math.abs(a - b) + 0.001, Math.min(a + b - 0.001, distance));
    const bend = (a * a - b * b + d * d) / (2 * d);
    const radius = Math.sqrt(Math.max(0, a * a - bend * bend));
    const flexion = thigh.angleTo(shin);
    if (side >= 0) limitKneeBow(frame, side, radius, a, flexion);
    bentThigh.copy(legAxis).multiplyScalar(bend).addScaledVector(pole, radius);
    bentShin.copy(legAxis).multiplyScalar(d).sub(bentThigh);
    thigh.normalize();
    shin.normalize();
    upperQ.setFromUnitVectors(thigh, bentThigh.normalize());
    lowerQ.setFromUnitVectors(shin, bentShin.normalize());
    const upperAngle = 2 * Math.acos(Math.min(1, Math.abs(upperQ.w)));
    const lowerAngle = 2 * Math.acos(Math.min(1, Math.abs(lowerQ.w)));
    const limit = side >= 0 && flexion > 2.2 ? 0.25 : 0.55;
    const amount = Math.min(1, limit / Math.max(upperAngle, lowerAngle, EPSILON));
    scaleRotation(upperQ, upperQ, amount).multiply(worldRotation[start]);
    scaleRotation(lowerQ, lowerQ, amount).multiply(worldRotation[middle]);
    limbOverrides.set(start, upperQ);
    limbOverrides.set(middle, lowerQ);
  }

  function fitLimbProportions(frame, chain, anchors = [], ikTarget = null) {
    const { a, c, start, end, sourceLength, length } = chain;
    if (sourceLength < EPSILON || length < EPSILON) return false;
    const base = frame * JOINTS * 3;
    const ratio = length / sourceLength;
    captureStart.fromArray(joints, base + a * 3);
    captureEnd.fromArray(joints, base + c * 3);
    limbGoal.copy(captureEnd).sub(captureStart).multiplyScalar(ratio).add(worldPosition[start]);
    // Gestures near the face, shoulder or waist should stay near that
    // landmark on the new body. Fade into this fit as the hand approaches.
    bodyShift.set(0, 0, 0);
    let total = 0, strongest = 0;
    for (const j of anchors) {
      captureAnchor.fromArray(joints, base + j * 3);
      const distance = captureEnd.distanceTo(captureAnchor) / sourceLength;
      const t = Math.min(1, Math.max(0, (0.48 - distance) / 0.30));
      const weight = t * t * (3 - 2 * t);
      if (!weight) continue;
      anchorShift.copy(captureAnchor).sub(captureStart).multiplyScalar(-ratio)
        .add(worldPosition[map[j]]).sub(worldPosition[start]);
      bodyShift.addScaledVector(anchorShift, weight);
      total += weight;
      strongest = Math.max(strongest, weight);
    }
    if (total) limbGoal.add(bodyShift.multiplyScalar(0.8 * strongest / total).clampLength(0, 0.15 * length));
    if (ikTarget) ikTarget.copy(limbGoal);
    if (limbGoal.distanceToSquared(worldPosition[end]) > 4e-6) pointLimb(chain, limbGoal, frame);
    return true;
  }

  function setLegTarget(leg, goal) {
    const hip = worldPosition[leg.hip], knee = worldPosition[leg.knee], ankle = worldPosition[leg.ankle];
    const a = hip.distanceTo(knee), b = knee.distanceTo(ankle);
    leg.targetDelta.copy(goal).sub(hip);
    if (leg.targetDelta.lengthSq() > EPSILON && Math.min(a, b) > 0.02) {
      leg.targetDelta.clampLength(Math.abs(a - b) + 0.001, a + b - 0.001);
    }
    leg.target.copy(hip).add(leg.targetDelta);
    leg.targetDelta.copy(leg.target).sub(ankle);
  }

  function adjustCrouchHeight(frame) {
    let total = 0, depth = 0, weight = 0;
    for (const s of [0, 1]) {
      const leg = legs[s];
      if (!leg.ik) continue;
      let lowest = Infinity, confidence = 0;
      for (const part of [0, 1]) {
        const i = 2 * s + part;
        const strength = contactStrength(frame, i);
        if (strength <= 0.7 || !probes[s][part].length) continue;
        lowest = Math.min(lowest, footPoint(probes[s][part], foot).y);
        confidence = Math.max(confidence, Math.min(1, (strength - 0.7) / 0.25));
      }
      if (!confidence) continue;
      const flexion = thigh.copy(worldPosition[leg.knee]).sub(worldPosition[leg.hip])
        .angleTo(shin.copy(worldPosition[leg.ankle]).sub(worldPosition[leg.knee]));
      const crouch = Math.min(1, Math.max(0, (flexion - 1.65) / 0.62));
      depth += crouch * confidence;
      total += (floor - lowest - offset.y) * confidence;
      weight += confidence;
    }
    // Raising the pelvis slightly gives both bent legs room to support the
    // body. Forcing the same vertical change through a nearly folded knee
    // can swing it far upward and destroy the source crouch.
    if (weight > 0) offset.y += Math.max(-0.12, Math.min(0.12, total / weight)) * depth / Math.max(1.3, weight);
  }

  function plantFeet(frame, swing) {
    levelFeet(frame, swing);
    adjustCrouchHeight(frame);
    const feet = [0, 1];
    for (const s of feet) {
      if (!legs[s].ik) continue;
      let heelWeight = 0, toeWeight = 0, heelLift = 0, toeLift = 0, locked = 0;
      correction.set(0, 0, 0);
      for (const part of [0, 1]) {
        const i = s * 2 + part;
        const list = probes[s][part];
        const strength = contactStrength(frame, i);
        if (strength <= 0.5 || !list.length) continue;
        footPoint(list, foot).add(offset);
        const pull = Math.max(-0.18, Math.min(0.18, floor - foot.y));
        if (part === 0) {
          heelWeight = easeContact((strength - 0.5) * 2);
          heelLift = pull;
        } else {
          toeWeight = easeContact((strength - 0.5) * 2);
          toeLift = pull;
        }
        const track = contact[i];
        const run = track?.runs[track.frameToRun[frame]];
        if (run) {
          const blend = easeContact(Math.min((frame - run.start) / 3, (run.end - 1 - frame) / 3));
          const weight = blend * strength;
          correction.x += (run.anchor.x - foot.x) * weight;
          correction.z += (run.anchor.z - foot.z) * weight;
          locked += weight;
        }
      }
      if (heelWeight <= 0 && toeWeight <= 0) continue;
      correction.multiplyScalar(1 / Math.max(1, locked)).clampLength(0, 0.085);
      // Blend heel-only, toe-only and both planted continuously. A low heel
      // entering contact should not instantly become the vertical target.
      correction.y = heelLift * heelWeight * (1 - toeWeight) + toeLift * toeWeight * (1 - heelWeight) +
        Math.max(heelLift, toeLift) * heelWeight * toeWeight;
      if (correction.lengthSq() > 1e-8) {
        const leg = legs[s];
        desiredAnkle.copy(worldPosition[leg.ankle]).add(correction);
        if (leg.hasTarget) setLegTarget(leg, desiredAnkle);
        else pointLimb(leg.chain, desiredAnkle, frame, s);
      }
    }
    if (limbOverrides.size) pose(frame, swing, limbOverrides);

    // Only small residual reach errors move the body vertically. Horizontal
    // placement remains the original motion, even when both feet are planted.
    let sole = Infinity, confidence = 0;
    for (const s of feet) for (const part of [0, 1]) {
      const i = s * 2 + part;
      const strength = contactStrength(frame, i);
      if (strength <= 0.5 || !probes[s][part].length) continue;
      const lift = legs[s].hasTarget ? legs[s].targetDelta.y : 0;
      sole = Math.min(sole, footPoint(probes[s][part], foot).y + lift);
      confidence = Math.max(confidence, Math.min(1, (strength - 0.5) * 2));
    }
    if (confidence > 0) offset.y += confidence * Math.max(-0.025, Math.min(0.025, floor - sole - offset.y));
  }

  function poseTorso(frame) {
    const root = frame * JOINTS * 3;
    for (const { start, end, axis, adjustment } of spineSpans.values()) {
      // A two-bone torso covers a longer span of the source spine. Its
      // direction comes from both captured ends, including the missing joint.
      sourceRotation.fromArray(quaternions, (frame * JOINTS + start) * 4).normalize();
      spineFrom.copy(axis).applyQuaternion(sourceRotation);
      spineTo.fromArray(joints, root + end * 3).sub(point.fromArray(joints, root + start * 3));
      adjustment.identity();
      if (spineFrom.lengthSq() > EPSILON && spineTo.lengthSq() > EPSILON) {
        adjustment.setFromUnitVectors(spineFrom.normalize(), spineTo.normalize());
      }
    }
    pose(frame, null);
    let swing = null;
    if (map[12] >= 0) {
      // Gently align the overall torso while keeping the relative bends
      // between its segments. Rotating only the waist distorts that curve.
      const pivot = worldPosition[map[3]];
      toNeck.copy(worldPosition[map[12]]).sub(pivot);
      toPivot.copy(pivot).sub(worldPosition[map[0]]);
      desiredTorso.fromArray(joints, root + 12 * 3).sub(point.fromArray(joints, root));
      if (toNeck.lengthSq() > EPSILON && desiredTorso.lengthSq() > EPSILON) {
        desiredTorso.normalize();
        const projection = toPivot.dot(desiredTorso);
        const intersection = toNeck.lengthSq() - toPivot.lengthSq() + projection * projection;
        if (intersection > EPSILON) {
          desiredTorso.multiplyScalar(projection + Math.sqrt(intersection)).sub(toPivot);
          torsoSwing.setFromUnitVectors(toNeck.normalize(), desiredTorso.normalize());
          const angle = 2 * Math.acos(Math.min(1, Math.abs(torsoSwing.w)));
          if (angle > 1e-4) {
            swing = scaleRotation(torsoDelta, torsoSwing, Math.min(0.55, (Math.PI * 8 / 180) / angle));
            pose(frame, swing);
          }
        }
      }
    }
    return swing;
  }

  function poseBase(frame) {
    limbOverrides.clear();
    const swing = poseTorso(frame);
    if (fitShoulders(frame)) pose(frame, swing, limbOverrides);
    for (const { chain, anchors } of arms) fitLimbProportions(frame, chain, anchors);
    for (const leg of legs) {
      const target = leg.ik && leg.native && ikMode ? leg.target : null;
      leg.hasTarget = leg.ik && fitLimbProportions(frame, leg.chain, [], target) && target != null;
    }
    if (limbOverrides.size) pose(frame, swing, limbOverrides);
    for (const leg of legs) if (leg.hasTarget) setLegTarget(leg, leg.target);
    return swing;
  }

  function fitShoulders(frame) {
    if (neck < 0 || !Number.isFinite(shoulderScale)) return false;
    sourceUp.copy(UP).applyQuaternion(sourceRotation.fromArray(quaternions, (frame * JOINTS + 9) * 4).normalize());
    targetUp.copy(UP).applyQuaternion(sourceRotation.copy(bindRotation[chest]).invert().premultiply(worldRotation[chest]));
    const base = frame * JOINTS * 3;
    let changed = false;
    for (const side of [0, 1]) {
      const j = 16 + side, clavicle = map[13 + side], arm = map[j];
      const restY = bindPosition[arm].y - bindPosition[neck].y;
      const sourceY = shoulderGoal.fromArray(joints, base + j * 3).sub(point.fromArray(joints, base + 12 * 3)).dot(sourceUp);
      const wanted = restY + shoulderScale * (sourceY - (source[j].y - source[12].y));
      const actual = shoulderGoal.copy(worldPosition[arm]).sub(worldPosition[neck]).dot(targetUp);
      if (Math.abs(wanted - actual) < 0.0002) continue;
      shoulderVector.copy(worldPosition[arm]).sub(worldPosition[clavicle]);
      const length = shoulderVector.length(), height = shoulderVector.dot(targetUp);
      shoulderGoal.copy(shoulderVector).addScaledVector(targetUp, -height);
      if (length < 1e-5 || shoulderGoal.lengthSq() < EPSILON) continue;
      const next = Math.max(-0.92 * length, Math.min(0.92 * length, height + wanted - actual));
      shoulderGoal.setLength(Math.sqrt(length * length - next * next)).addScaledVector(targetUp, next);
      const q = shoulderAdjustments[side].setFromUnitVectors(shoulderVector.normalize(), shoulderGoal.normalize());
      limbOverrides.set(clavicle, q.multiply(worldRotation[clavicle]));
      changed = true;
    }
    return changed;
  }

  const contact = support ? probes.flat().map((list, i) => {
    const s = Math.floor(i / 2);
    if (!list.length || !legs[s].ik) return null;
    const track = contactRuns({ ...motion, contacts: soleContacts }, i, (i % 2 ? 10 : 7) + s);
    for (const run of track.runs) {
      const swing = poseBase(run.frame);
      levelFeet(run.frame, swing);
      alignRoot(run.frame);
      footPoint(list, run.anchor).add(offset);
    }
    return track;
  }) : [];

  function evaluate(frame) {
    if (!Number.isInteger(frame) || frame < 0 || frame >= frames) return false;
    const swing = poseBase(frame);
    if (handPair(frame, worldPosition, handCorrection)) {
      for (const [side, { chain }] of arms.entries()) {
        limbGoal.copy(worldPosition[chain.end]).addScaledVector(handCorrection, side === 0 ? -1 : 1);
        pointLimb(chain, limbGoal, frame);
      }
      pose(frame, swing, limbOverrides);
    }
    alignRoot(frame);
    if (support) plantFeet(frame, swing);
    applyJointEdits();
    if (ikMode) placeIkControllers();
    return true;
  }

  function placeIkControllers() {
    if (!nativeIk.moving.size) return;
    for (const control of nativeIk.moving.values()) {
      control.goal.copy(worldPosition[control.effector]);
      const leg = controllerLeg.get(control);
      // Use the source-proportional or planted ankle goal, never the position
      // the FK seed already reached. Toe targets travel with the sole.
      if (leg?.hasTarget) control.goal.add(leg.targetDelta);
    }
    // Toe IK controllers may be parented to the ankle controller. Use its
    // updated world position before converting the toe target to local units.
    for (const i of order) {
      const parent = parents[i];
      const rotation = parent < 0 ? meshRotation : worldRotation[parent];
      const position = parent < 0 ? meshPosition : worldPosition[parent];
      const target = nativeIk.moving.get(i);
      if (target) {
        target.local.copy(target.goal).sub(position).applyQuaternion(localInverse.copy(rotation).invert()).divideScalar(scale);
        worldPosition[i].copy(target.goal);
      } else {
        worldPosition[i].copy(localPosition[i]).multiplyScalar(scale).applyQuaternion(rotation).add(position);
      }
    }
  }

  function applyJointEdits() {
    if (!edits.joints.size) return;
    for (const i of order) {
      const parent = parents[i], p = grants[i], q = localRotation[i], append = appendRotation[i];
      if (p >= 0) {
        // Refresh append inheritance from edited drivers. Mapped FK joints
        // already have their final local orientation; helper bones follow.
        if (sourceForBone[i] < 0) q.multiply(inverse.copy(append).invert());
        grantRotation.copy(grantBasis[i]).multiply(invRestLocal[p]).multiply(localRotation[p]).multiply(invGrantBasis[i]);
        scaleRotation(append, grantRotation, bones[i].pmx.grant.ratio);
        if (sourceForBone[i] < 0) q.multiply(append);
      }
      const change = edits.joints.get(sourceForBone[i]);
      if (change) q.multiply(change);
      q.normalize();
      const parentRotation = parent < 0 ? meshRotation : worldRotation[parent];
      const parentPosition = parent < 0 ? meshPosition : worldPosition[parent];
      worldRotation[i].multiplyQuaternions(parentRotation, q);
      worldPosition[i].copy(localPosition[i]).multiplyScalar(scale).applyQuaternion(parentRotation).add(parentPosition);
    }
  }

  function writeSkeleton() {
    for (const i of order) {
      skin[i].position.copy((ikMode && nativeIk.moving.get(i)?.local) || localPosition[i]);
      skin[i].quaternion.copy(localRotation[i]);
    }
    mesh.quaternion.multiplyQuaternions(edits.rootRotation, meshRotation);
    edits.position(meshPosition, offset, worldPosition[map[0]], mesh.position);
  }

  function solveNativeSkeleton() {
    nativeIk.solve();
    // IK changes the driving leg bones after the FK pass. Refresh the PMX
    // append rotations so deform bones (e.g. 足首D) follow the solved joints.
    for (const i of order) {
      const driver = grants[i];
      if (driver < 0) continue;
      const append = appendRotation[i], q = skin[i].quaternion;
      const followsDriver = sourceForBone[i] < 0 && !nativeIk.baked.has(i);
      if (followsDriver) q.multiply(inverse.copy(append).invert());
      grantRotation.copy(grantBasis[i]).multiply(invRestLocal[driver]).multiply(skin[driver].quaternion)
        .multiply(invGrantBasis[i]);
      scaleRotation(append, grantRotation, bones[i].pmx.grant.ratio);
      if (followsDriver) q.multiply(append).normalize();
    }
  }

  function bindSolvedIkTargets() {
    // External solvers can take a different path toward an unreachable goal.
    // Key the actual endpoint from the preview, keeping the controller live.
    for (const i of order) {
      const control = nativeIk.moving.get(i);
      if (!control) continue;
      const joint = skin[i];
      skin[control.effector].getWorldPosition(point);
      joint.parent.worldToLocal(point);
      control.local.copy(point);
      joint.position.copy(point);
      joint.updateMatrixWorld(true);
    }
  }

  function apply(frame) {
    if (!evaluate(frame)) return false;
    writeSkeleton();
    if (ikMode) solveNativeSkeleton();
    return true;
  }

  let vmd = null;
  if (root >= 0) {
    const isTrack = (i) => i === root || sourceForBone[i] >= 0 || shoulderAccessories.has(i);
    const track = ({ index, name }) => ({ index, name });
    const fkTracks = bones.filter((b) => isTrack(b.index)).map(track);
    const ikTracks = bones.filter(({ index }) => isTrack(index) || nativeIk.moving.has(index) || nativeIk.baked.has(index)).map(track);
    const controllerTracks = bones.filter(({ index }) => nativeIk.moving.has(index)).map(({ name }) => name);
    // The native foot and toe controls stay editable. Bake unrelated chains
    // such as hair rather than relying on the receiving solver to replay them.
    const liveFootTracks = bones.filter(({ index }) => controllerLeg.has(nativeIk.moving.get(index)))
      .map(({ name }) => name);
    const ikNames = bones.filter((b) => b.pmx?.ik && nameCounts.get(b.name) === 1 &&
      canWriteVmdBoneName(b.name)).map((b) => b.name);
    const invParentRotation = (parents[root] < 0 ? originalMeshRotation : originalRotation[parents[root]]).clone().invert();
    const parentPosition = parents[root] < 0 ? meshPosition : originalRest[parents[root]];
    const authored = new Quaternion();
    const placementRotation = new Quaternion();
    const placementOffset = new Vector3();
    vmd = { get tracks() { return ikMode ? ikTracks : fkTracks; }, ikNames,
      get ikEnabledNames() { return ikMode ? liveFootTracks : []; },
      get controllerTracks() { return ikMode ? controllerTracks : []; }, rootBoneIndex: root, sample(frame, qs, ts, placement) {
      const tracks = ikMode ? ikTracks : fkTracks;
      const yaw = placement?.yaw ?? 0;
      const shift = placement?.offset ?? [0, 0, 0];
      if (qs?.length !== tracks.length * 4 || ts?.length !== tracks.length * 3 ||
          !Number.isFinite(yaw) || shift.length !== 3 || !Array.from(shift).every(Number.isFinite) || !evaluate(frame)) return false;
      placementRotation.setFromAxisAngle(UP, yaw);
      placementOffset.fromArray(shift);
      ts.fill(0);
      if (ikMode && nativeIk.baked.size) {
        writeSkeleton();
        // Keep the preview's limb rotations as the solver seed and write
        // the achieved foot targets with native IK enabled.
        solveNativeSkeleton();
        bindSolvedIkTargets();
      }
      for (let j = 0; j < tracks.length; j++) {
        const i = tracks[j].index;
        if (i === root) {
          edits.position(worldPosition[i], offset, worldPosition[map[0]], point)
            .applyQuaternion(placementRotation).add(placementOffset)
            .sub(parentPosition).applyQuaternion(invParentRotation)
            .divideScalar(scale).sub(localPosition[i]).toArray(ts, j * 3);
          authored.copy(invParentRotation).multiply(placementRotation).multiply(edits.rootRotation).multiply(worldRotation[i]);
        } else {
          const target = ikMode && nativeIk.moving.get(i);
          if (target) point.copy(target.local).sub(localPosition[i]).toArray(ts, j * 3);
          authored.copy(ikMode ? skin[i].quaternion : localRotation[i]);
        }
        // A PMX player applies append inheritance when it evaluates the rig.
        // Only write authored rotation so it is applied once after import.
        authored.premultiply(invRestLocal[i]).multiply(inverse.copy(appendRotation[i]).invert())
          .normalize().toArray(qs, j * 4);
      }
      return true;
    } };
  }

  function setPoseOffsets(settings) {
    const pose = normalizePoseOffsets(settings);
    pose.ikMode = nativeIk.available && settings?.ikMode !== "fk" ? "ik" : "fk";
    if (pose.pmxBaselineVersion !== PMX_BASELINE_VERSION) {
      for (const joint of Object.keys(shoulderDefaults)) {
        const saved = pose.rotations[joint];
        if (!saved) continue;
        // Preserve old manual fits, converting their absolute values to
        // offsets from the new default. Previously untouched joints use it.
        const relative = shoulderAngles(joint, saved, -1);
        if (relative.some((angle) => Math.abs(angle) > 1e-8)) pose.rotations[joint] = relative;
        else delete pose.rotations[joint];
      }
    }
    pose.pmxBaselineVersion = PMX_BASELINE_VERSION;
    const rotations = { ...pose.rotations };
    for (const joint of Object.keys(shoulderDefaults)) {
      rotations[joint] = shoulderAngles(joint, rotations[joint] ?? ZERO_ANGLES);
    }
    edits.set({ ...pose, rotations });
    ikMode = pose.ikMode === "ik";
    return pose;
  }
  function mirrorRotation(from, to, angles) {
    const mirrored = edits.mirrorRotation(from, to, shoulderAngles(from, angles));
    return mirrored && shoulderAngles(to, mirrored, -1).map((angle) => Number(angle.toFixed(2)));
  }
  return { apply, vmd, setPoseOffsets, nativeIkAvailable: nativeIk.available, mirrorRotation, matched: map.filter((i) => i >= 0).length,
    sourceToTarget: Array.from(map), skippedGrants, support };
}

