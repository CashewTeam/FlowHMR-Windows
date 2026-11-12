import { Matrix4, Quaternion, Vector3 } from "three";
import { makePoseOffsets, normalizePoseOffsets, normalizeVrmPoseOffsets } from "./pose-offsets.js";

const JOINTS = 52;
const UP = new Vector3(0, 1, 0);
const IDENTITY = new Quaternion();
const EPS = 1e-8;
const DIGITS = ["index", "middle", "pinky", "ring", "thumb"];
const BODY = [0, 1, 2, 3, 4, 5, 7, 8, 15, 16, 17, 18, 19, 20, 21];
const bound = (value, min, max) => Math.max(min, Math.min(max, value));
const ease = (value) => {
  const t = bound(value, 0, 1);
  return t * t * (3 - 2 * t);
};

function boneKey(bone, profile) {
  if (profile === "vrm") return bone.userData.vrmHumanoidBone?.toLowerCase() ?? "";
  const name = bone.userData?.originalName ?? bone.name;
  const key = (name ?? "").normalize("NFKC").split(/[|:]/).at(-1)
    .toLowerCase().replace(/[^a-z0-9]/g, "");
  return profile === "mixamo" ? key.replace(/^mixamorig\d*/, "") : key;
}

function layout(bones, profile) {
  const byName = new Map();
  for (const [i, bone] of bones.entries()) {
    const name = boneKey(bone, profile);
    if (!name) continue;
    byName.set(name, byName.has(name) ? -2 : i);
  }
  const find = (...names) => {
    for (const name of names) {
      const i = byName.get(name);
      if (i === -2) throw new Error(`Ambiguous ${profile} body bone: ${name}`);
      if (i != null) return i;
    }
    return -1;
  };
  const map = new Int32Array(JOINTS).fill(-1);
  const meta = profile === "metahuman";
  const vrm = profile === "vrm";
  map[0] = find(meta ? "pelvis" : "hips");
  map[15] = find("head");
  const spine = (meta ? ["spine01", "spine02", "spine03", "spine04", "spine05"]
    : vrm ? ["spine", "chest", "upperchest"]
    : ["spine", "spine1", "spine2"]).map((name) => find(name)).filter((i) => i >= 0);
  const neck = (meta ? ["neck01", "neck02"] : ["neck", "neck1"])
    .map((name) => find(name)).filter((i) => i >= 0);
  map[3] = spine[0] ?? -1;
  map[6] = spine.length >= 3 ? spine[Math.floor((spine.length - 1) / 2)] : -1;
  map[9] = spine.length >= 2 ? spine.at(-1) : -1;
  map[12] = neck[0] ?? -1;
  for (let s = 0; s < 2; s++) {
    const side = s === 0 ? "left" : "right";
    const short = side[0];
    const limb = (mh, vrmName, ...mixamo) => meta ? find(mh + short)
      : vrm ? find(side + vrmName) : find(...mixamo.map((name) => side + name));
    map[1 + s] = limb("thigh", "upperleg", "upleg");
    map[4 + s] = limb("calf", "lowerleg", "leg");
    map[7 + s] = limb("foot", "foot", "foot");
    map[10 + s] = limb("ball", "toes", "toebase", "toe");
    map[13 + s] = limb("clavicle", "shoulder", "shoulder");
    map[16 + s] = limb("upperarm", "upperarm", "arm");
    map[18 + s] = limb("lowerarm", "lowerarm", "forearm");
    map[20 + s] = limb("hand", "hand", "hand");
    for (const [digit, name] of DIGITS.entries()) for (let part = 1; part <= 3; part++) {
      const names = name === "pinky" ? [name, "little"] : [name];
      const segments = name === "thumb" ? ["metacarpal", "proximal", "distal"] : ["proximal", "intermediate", "distal"];
      const key = 22 + 15 * s + 3 * digit + part - 1;
      map[key] = vrm ? find(side + names.at(-1) + segments[part - 1])
        : meta ? find(...names.map((n) => `${n}0${part}${short}`))
          : find(...names.map((n) => `${side}hand${n}${part}`));
    }
  }
  if (BODY.some((j) => map[j] < 0)) throw new Error(`The character is missing required ${profile} body bones.`);
  const descendant = (child, parent) => {
    for (let node = bones[child]?.parent; node; node = node.parent) if (node === bones[parent]) return true;
    return false;
  };
  const verify = (indices) => {
    const present = indices.filter((i) => i >= 0);
    for (let i = 1; i < present.length; i++) if (!descendant(present[i], present[i - 1])) {
      throw new Error(`Unexpected ${profile} body hierarchy: ${bones[present[i]].name}`);
    }
  };
  verify([map[0], ...spine, ...neck, map[15]]);
  for (let s = 0; s < 2; s++) {
    verify([map[0], map[1 + s], map[4 + s], map[7 + s], map[10 + s]]);
    verify([spine[0], map[13 + s], map[16 + s], map[18 + s], map[20 + s]]);
    for (let d = 0; d < 5; d++) {
      const first = 22 + 15 * s + 3 * d;
      verify([map[20 + s], ...map.slice(first, first + 3)]);
    }
  }
  return { map, spine, neck };
}

function heading(left, right) {
  const x = left.x - right.x, z = left.z - right.z;
  if (x * x + z * z < EPS) throw new Error("The FBX body's left and right sides cannot be determined.");
  return Math.atan2(-z, x);
}

function rotationBetween(from, to) {
  return from.lengthSq() > EPS && to.lengthSq() > EPS
    ? new Quaternion().setFromUnitVectors(from.normalize(), to.normalize()) : new Quaternion();
}

function palm(points, wrist, index, middle, pinky) {
  if ([wrist, index, middle, pinky].some((i) => !points[i])) return null;
  const along = points[middle].clone().sub(points[wrist]);
  if (along.lengthSq() < EPS) return null;
  along.normalize();
  const across = points[index].clone().sub(points[pinky]);
  across.addScaledVector(along, -across.dot(along));
  if (across.lengthSq() < EPS) return null;
  across.normalize();
  return new Quaternion().setFromRotationMatrix(new Matrix4().makeBasis(across, along, across.clone().cross(along)));
}

function restAlignment(joint, map, source, target) {
  if (joint === 20 || joint === 21) {
    const base = 22 + (joint - 20) * 15;
    const ids = [joint, base, base + 3, base + 6];
    const a = palm(target, ...ids.map((i) => map[i]));
    const b = palm(source, ...ids);
    if (a && b) return b.multiply(a.invert());
  }
  // Match segment directions for limbs. The spine, neck, and head keep
  // their own bind offsets, which differ from SMPL-H joint centres.
  const ends = { 1: 4, 2: 5, 4: 7, 5: 8, 7: 10, 8: 11, 10: 7, 11: 8,
    13: 16, 14: 17, 16: 18, 17: 19, 18: 20, 19: 21, 20: 25, 21: 40 };
  const end = joint >= 22 ? joint + ((joint - 22) % 3 === 2 ? -1 : 1) : ends[joint];
  if (!(map[end] >= 0)) return new Quaternion();
  const from = target[map[end]].clone().sub(target[map[joint]]);
  const to = source[end].clone().sub(source[joint]);
  // A character's ankle and ball pivots may sit at a different height
  // inside its shoe. Transfer the heading and keep the bind sole pitch.
  if (joint >= 7 && joint <= 11) { from.y = 0; to.y = 0; }
  return rotationBetween(from, to);
}

function finiteVector(vector) {
  return Number.isFinite(vector.x) && Number.isFinite(vector.y) && Number.isFinite(vector.z);
}

function checkScale(scale) {
  if (!finiteVector(scale) || scale.x <= 0 || scale.y <= 0 || scale.z <= 0 ||
      (Math.max(scale.x, scale.y, scale.z) - Math.min(scale.x, scale.y, scale.z)) / scale.x > 0.002) {
    throw new Error("The FBX body skeleton must use positive, uniform bone scaling.");
  }
}

function hierarchy(root) {
  // Work in the actor's coordinates. The caller owns the scene group yaw,
  // translation and entry time, and those must not enter the solve again.
  root.parent?.updateWorldMatrix(true, false);
  root.updateMatrixWorld(true);
  const outerInverse = root.parent ? root.parent.matrixWorld.clone().invert() : new Matrix4();
  const records = new Map(), order = [];
  function include(node, depth = 0) {
    if (!node || depth > 128) throw new Error("The FBX body bones are outside the selected character hierarchy.");
    if (records.has(node)) return records.get(node);
    const parent = node === root ? null : include(node.parent, depth + 1);
    const restMatrix = outerInverse.clone().multiply(node.matrixWorld);
    const bindPosition = new Vector3(), bindRotation = new Quaternion(), worldScale = new Vector3();
    restMatrix.decompose(bindPosition, bindRotation, worldScale);
    checkScale(worldScale);
    const record = {
      node, parent, bindPosition, bindRotation: bindRotation.normalize(), restMatrix,
      localPosition: node.position.clone(), localRotation: node.quaternion.clone(), localScale: node.scale.clone(),
      world: new Matrix4(), local: new Matrix4(), position: new Vector3(), rotation: new Quaternion(),
      scale: new Vector3(), pose: new Quaternion(), goal: new Quaternion(), override: null, driver: null, copy: null,
    };
    if (!finiteVector(bindPosition)) throw new Error("The FBX body has an invalid bind transform.");
    records.set(node, record);
    order.push(record);
    return record;
  }
  return { include, records, order, base: include(root) };
}

const component = (attr, index, i) => i === 0 ? attr.getX(index) : i === 1 ? attr.getY(index)
  : i === 2 ? attr.getZ(index) : attr.getW(index);

function soleProbes(root, primary, records, legs, bounds) {
  const candidates = [[], []];
  const up = new Vector3(), point = new Vector3();
  const heading = legs.map(({ ankle, toe }) => {
    const direction = toe?.bindPosition.clone().sub(ankle.bindPosition).setY(0);
    return direction && direction.lengthSq() > EPS ? direction.normalize() : new Vector3(0, 0, 1);
  });
  const onFoot = (node) => {
    for (; node && node !== root; node = node.parent) {
      if (node === legs[0].ankle.node) return 0;
      if (node === legs[1].ankle.node) return 1;
    }
    return -1;
  };
  const primaryBones = new Set(primary.bones);
  root.traverseVisible((mesh) => {
    const attrs = mesh.geometry?.attributes;
    if (!mesh.isSkinnedMesh || !attrs?.position || !attrs.skinIndex || !attrs.skinWeight ||
        !mesh.skeleton.bones.some((bone) => primaryBones.has(bone))) return;
    const skinned = mesh.skeleton.bones.map((node, i) => ({
      record: records.get(node), side: onFoot(node),
      inverse: mesh.skeleton.boneInverses[i].clone().multiply(mesh.bindMatrix),
    }));
    for (let v = 0; v < attrs.position.count; v++) {
      let left = 0, right = 0;
      for (let i = 0; i < 4; i++) {
        const binding = skinned[component(attrs.skinIndex, v, i)];
        if (binding?.side === 0) left += component(attrs.skinWeight, v, i);
        if (binding?.side === 1) right += component(attrs.skinWeight, v, i);
      }
      const side = left > 0.6 ? 0 : right > 0.6 ? 1 : -1;
      if (side < 0) continue;
      point.fromBufferAttribute(attrs.position, v);
      up.set(0, 0, 0);
      let total = 0;
      const influences = [];
      for (let i = 0; i < 4; i++) {
        const weight = component(attrs.skinWeight, v, i);
        const binding = skinned[component(attrs.skinIndex, v, i)];
        if (!binding?.record || !(weight > 0)) continue;
        const { inverse, record } = binding;
        const local = point.clone().applyMatrix4(inverse);
        influences.push({ record, local, weight });
        up.addScaledVector(local.clone().applyMatrix4(record.restMatrix), weight);
        total += weight;
      }
      if (Math.abs(total - 1) > 0.02 || !finiteVector(up)) continue;
      candidates[side].push({ y: up.y,
        along: up.sub(legs[side].ankle.bindPosition).dot(heading[side]), influences });
    }
  });

  return legs.map(({ ankle, toe }, side) => {
    const options = candidates[side].sort((a, b) => a.y - b.y);
    if (options.length) {
      const low = options.filter((option) => option.y - options[0].y < 0.065);
      const along = low.map((option) => option.along).sort((a, b) => a - b);
      const split = (along[Math.floor(along.length * 0.15)] + along[Math.floor(along.length * 0.85)]) / 2;
      const heel = low.filter((option) => option.along <= split).slice(0, 10);
      const front = low.filter((option) => option.along > split).slice(0, 10);
      if (heel.length && front.length) return [heel, front];
    }
    const low = toe?.bindPosition.y ?? ankle.bindPosition.y - 0.05;
    const min = bounds?.min?.[1];
    const y = Number.isFinite(min) && min <= low + 0.025 && low - min < 0.15 ? min : low - 0.02;
    return [ankle, toe ?? ankle].map((record, part) => {
      const p = record.bindPosition.clone().setY(y);
      if (!part) p.addScaledVector(heading[side], -0.035);
      if (part && !toe) p.addScaledVector(heading[side], 0.10);
      return [{ y, influences: [{ record, local: p.applyMatrix4(record.restMatrix.clone().invert()), weight: 1 }] }];
    });
  });
}

/**
 * Drive an independent, normalized character clone from a saved SMPL-H motion.
 * The file's own skin weights, bone proportions, and bind offsets remain in use.
 */
export function createFbxCharacterRetarget(root, asset, motion, { poseOffsets = null } = {}) {
  const profile = asset?.profile;
  if (!["mixamo", "metahuman", "vrm"].includes(profile)) throw new Error("Unknown character skeleton.");
  const { frames, joints, quaternions, contacts, floor, restFloor } = motion;
  if (!Number.isSafeInteger(frames) || frames < 1 || motion.rest?.length !== JOINTS * 3 ||
      joints?.length !== frames * JOINTS * 3 || quaternions?.length !== frames * JOINTS * 4) {
    throw new Error("Invalid saved motion for the character.");
  }
  const primary = asset.getRig(root);
  if (!primary?.bones?.length) throw new Error("The character has no body skeleton.");
  const { map, spine, neck } = layout(primary.bones, profile);
  const { include, records, order, base } = hierarchy(root);
  const primaryRecords = primary.bones.map((bone) => include(bone));
  const source = Array.from({ length: JOINTS }, (_, i) => new Vector3().fromArray(motion.rest, i * 3));
  if (!source.every(finiteVector)) throw new Error("Invalid source rest position.");
  const slot = (j) => primaryRecords[map[j]];
  const shoulders = [slot(13) ?? slot(16), slot(14) ?? slot(17)];
  const align = new Quaternion().setFromAxisAngle(UP,
    heading(source[13], source[14]) - heading(shoulders[0].bindPosition, shoulders[1].bindPosition));
  const bindForJoint = (j) => slot(j) && align.clone().multiply(slot(j).bindRotation);
  const calibratedVrm = profile === "vrm" && Number.isFinite(motion.vrmNeckPitch);
  const neckBind = calibratedVrm && bindForJoint(12);
  const neckBaseline = neckBind ? neckBind.clone().invert()
    .multiply(new Quaternion().setFromAxisAngle(new Vector3(1, 0, 0), motion.vrmNeckPitch * Math.PI / 180))
    .multiply(neckBind).normalize() : null;
  const poseSettings = (settings) => calibratedVrm ? normalizeVrmPoseOffsets(settings, neckBaseline) : normalizePoseOffsets(settings);
  const edits = makePoseOffsets(bindForJoint, source[16].clone().sub(source[17]).setY(0),
    calibratedVrm ? { headPitch: 0 } : undefined);
  edits.set(poseSettings(poseOffsets));
  const editTargets = new Map(Array.from(map, (i, j) => [primaryRecords[i], j]).filter(([record]) => record));
  const target = primaryRecords.map((record) => record.bindPosition.clone().applyQuaternion(align));
  const attachDriver = (i, a, b = a, weight = 0, correction = IDENTITY, lowerSpine = false) => {
    const record = primaryRecords[i];
    record.driver = { a, b, weight, lowerSpine,
      reference: correction.clone().multiply(align).multiply(record.bindRotation).normalize() };
  };
  for (let j = 0; j < JOINTS; j++) {
    if (map[j] >= 0 && !spine.includes(map[j]) && !neck.includes(map[j])) {
      attachDriver(map[j], j, j, 0, restAlignment(j, map, source, target));
    }
  }
  const sourceSpine = [3, 6, 9];
  spine.forEach((bone, i) => {
    const coordinate = spine.length === 1 ? 1 : 2 * i / (spine.length - 1);
    const k = Math.floor(coordinate);
    attachDriver(bone, sourceSpine[k], sourceSpine[Math.min(k + 1, 2)], coordinate - k,
      IDENTITY, i < spine.length - 1);
  });
  // Both MetaHuman neck bones follow the captured neck. Head rotation is
  // applied at its own pivot, so a nod cannot push the neck forward.
  for (const i of neck) attachDriver(i, 12);

  const primaryNames = new Map(primaryRecords.filter((r) => r.driver)
    .map((r) => [boneKey(r.node, profile), r]));
  for (const secondary of primary.additionalRigs ?? []) {
    for (const bone of secondary.bones ?? []) {
      const copy = primaryNames.get(boneKey(bone, profile));
      if (!copy || copy.node === bone) continue;
      const record = include(bone);
      if (!record.driver && !record.copy) {
        record.copy = { source: copy, inverse: align.clone().multiply(copy.bindRotation).invert(),
          rest: align.clone().multiply(record.bindRotation),
          offsetBasis: record.bindRotation.clone().invert().multiply(copy.bindRotation) };
      }
    }
  }

  const legs = [0, 1].map((s) => ({ hip: slot(1 + s), knee: slot(4 + s), ankle: slot(7 + s), toe: slot(10 + s),
    thighGoal: new Quaternion(), kneeGoal: new Quaternion(), footGoal: new Quaternion(), toeGoal: new Quaternion() }));
  const probes = soleProbes(root, primary, records, legs, asset.rest?.bounds);
  const targetSole = Math.min(...probes.flat(2).map((item) => item.y));
  const sourceSole = Number.isFinite(restFloor) ? restFloor : Math.min(source[10].y, source[11].y) - 0.02;
  const sourceHipHeight = source[0].y - sourceSole;
  const hipDelta = slot(0).bindPosition.y - targetSole - sourceHipHeight;
  if (!Number.isFinite(hipDelta) || !(sourceHipHeight > 0)) throw new Error("Invalid FBX or motion body height.");
  const support = Number.isFinite(floor) && contacts?.length === frames * 4;
  const rootPosition = base.localPosition.clone().applyQuaternion(align);
  const rootRotation = align.clone().multiply(base.localRotation).normalize();
  const srcRotation = Array.from({ length: JOINTS }, () => new Quaternion());
  const inverse = new Quaternion(), delta = new Quaternion(), torso = new Quaternion();
  const rootOffset = new Vector3(), point = new Vector3(), vertex = new Vector3();
  const desired = new Vector3(), first = new Vector3(), second = new Vector3();
  const axis = new Vector3(), pole = new Vector3(), bentA = new Vector3(), bentB = new Vector3();
  const correction = new Vector3(), footA = new Vector3(), footB = new Vector3();
  const from = new Vector3(), to = new Vector3(), pivot = new Vector3();
  const pelvis = slot(0), lowerSpine = primaryRecords[spine[0]], upperSpine = primaryRecords[spine.at(-1)];
  const neckBase = primaryRecords[neck[0]];
  const torsoRestBias = neckBase && rotationBetween(source[12].clone().sub(source[0]),
    target[neck[0]].clone().sub(target[map[0]]));
  const srcFeet = [7, 10, 8, 11];
  const srcFootOffsets = srcFeet.map((j) => new Vector3(0, sourceSole - source[j].y, 0));
  const rootsAt = (frame, joint, result) => result.fromArray(joints, (frame * JOINTS + joint) * 3);

  function pose(withTorso) {
    base.world.compose(rootPosition, rootRotation, base.localScale);
    base.rotation.copy(rootRotation);
    for (const record of order) {
      if (!record.parent) continue;
      const { parent, driver, copy } = record;
      if (driver) {
        if (record.override) record.goal.copy(record.override);
        else {
          record.goal.copy(srcRotation[driver.a]);
          if (driver.weight) record.goal.slerp(srcRotation[driver.b], driver.weight);
          if (withTorso && driver.lowerSpine) record.goal.premultiply(torso);
          record.goal.multiply(driver.reference);
        }
      } else if (copy) record.goal.copy(copy.source.rotation).multiply(copy.inverse).multiply(copy.rest);
      if (driver || copy) record.pose.copy(parent.rotation).invert().multiply(record.goal).normalize();
      else record.pose.copy(record.localRotation);
      record.local.compose(record.localPosition, record.pose, record.localScale);
      record.world.multiplyMatrices(parent.world, record.local);
      record.world.decompose(record.position, record.rotation, record.scale);
      record.rotation.normalize();
    }
  }

  function fk(frame) {
    for (let j = 0; j < JOINTS; j++) srcRotation[j].fromArray(quaternions, (frame * JOINTS + j) * 4).normalize();
    for (const leg of legs) for (const record of [leg.hip, leg.knee, leg.ankle, leg.toe]) {
      if (record) record.override = null;
    }
    pose(false);
    let withTorso = false;
    if (neckBase && upperSpine !== lowerSpine) {
      // Fit lower torso tilt to the captured pelvis-to-neck line while
      // keeping the character's own rest bias, upper neck and head rotation.
      desired.copy(rootsAt(frame, 12, first)).sub(rootsAt(frame, 0, second));
      from.copy(upperSpine.position).sub(lowerSpine.position);
      pivot.copy(lowerSpine.position).sub(pelvis.position).add(neckBase.position).sub(upperSpine.position);
      if (desired.lengthSq() > EPS && from.lengthSq() > EPS) {
        desired.applyQuaternion(inverse.copy(srcRotation[0]).invert()).applyQuaternion(torsoRestBias)
          .applyQuaternion(srcRotation[0]).normalize();
        const projection = pivot.dot(desired);
        const intersection = from.lengthSq() - pivot.lengthSq() + projection * projection;
        if (intersection > EPS) {
          to.copy(desired).multiplyScalar(projection + Math.sqrt(intersection)).sub(pivot);
          if (to.lengthSq() > EPS) {
            torso.setFromUnitVectors(from.normalize(), to.normalize());
            const angle = torso.angleTo(IDENTITY);
            if (angle > 0.001) {
              torso.slerp(IDENTITY, 1 - Math.min(1, 0.24 / angle));
              withTorso = true;
              pose(true);
            }
          }
        }
      }
    }
    rootsAt(frame, 0, rootOffset).sub(pelvis.position);
    rootOffset.y += hipDelta;
    return withTorso;
  }

  function sole(side, part, out) {
    const list = probes[side][part];
    let x = 0, y = Infinity, z = 0;
    for (const sample of list) {
      vertex.set(0, 0, 0);
      for (const { record, local, weight } of sample.influences) {
        vertex.addScaledVector(point.copy(local).applyMatrix4(record.world), weight);
      }
      x += vertex.x;
      y = Math.min(y, vertex.y);
      z += vertex.z;
    }
    return out.set(x / list.length, y, z / list.length);
  }

  function pointLeg(leg, goal) {
    first.copy(leg.knee.position).sub(leg.hip.position);
    second.copy(leg.ankle.position).sub(leg.knee.position);
    axis.copy(goal).sub(leg.hip.position);
    const a = first.length(), b = second.length(), distance = axis.length();
    if (Math.min(a, b, distance) < 0.001) return false;
    axis.multiplyScalar(1 / distance);
    const reach = bound(distance, Math.abs(a - b) + 0.0001, a + b - 0.0001);
    pole.copy(first).addScaledVector(axis, -first.dot(axis));
    if (pole.lengthSq() < EPS) return false;
    const along = (reach * reach + a * a - b * b) / (2 * reach);
    bentA.copy(axis).multiplyScalar(along)
      .addScaledVector(pole.normalize(), Math.sqrt(Math.max(0, a * a - along * along)));
    bentB.copy(axis).multiplyScalar(reach).sub(bentA);
    leg.thighGoal.setFromUnitVectors(first.normalize(), bentA.normalize()).multiply(leg.hip.rotation);
    leg.kneeGoal.setFromUnitVectors(second.normalize(), bentB.normalize()).multiply(leg.knee.rotation);
    leg.hip.override = leg.thighGoal;
    leg.knee.override = leg.kneeGoal;
    if (!leg.ankle.override) {
      leg.ankle.override = leg.footGoal.copy(leg.ankle.rotation);
      if (leg.toe) leg.toe.override = leg.toeGoal.copy(leg.toe.rotation);
    }
    return true;
  }

  const spans = support ? Array.from({ length: 4 }, () => new Int32Array(frames).fill(-1)) : [];
  const anchors = Array.from({ length: 4 }, () => new Map());
  const spanEnds = Array.from({ length: 4 }, () => new Map());
  if (support) {
    for (let part = 0; part < 4; part++) {
      for (let first = 0; first < frames;) {
        if (!(contacts[first * 4 + part] > 0.75)) { first++; continue; }
        let end = first + 1;
        while (end < frames && contacts[end * 4 + part] > 0.75) end++;
        if (end - first >= 3) {
          spans[part].fill(first, first, end);
          spanEnds[part].set(first, end);
        }
        first = end;
      }
    }
  }
  const contact = (frame, part) => contacts[bound(frame, 0, frames - 1) * 4 + part];
  function strength(frame, part, deep) {
    const smooth = 0.25 * (contact(frame - 1, part) + contact(frame + 1, part)) + 0.5 * contact(frame, part);
    if (!(smooth > 0.55)) return 0;
    const j = srcFeet[part];
    const gap = rootsAt(frame, j, vertex).add(point.copy(srcFootOffsets[part]).applyQuaternion(srcRotation[j])).y - floor;
    if (deep && gap > 0.05) return 0;
    return bound((smooth - 0.55) / 0.40, 0, 1);
  }

  function plants(frame, withTorso) {
    const deep = rootsAt(frame, 0, point).y - floor < 0.58 * sourceHipHeight;
    let flattened = false;
    for (let s = 0; s < 2; s++) {
      const heel = strength(frame, 2 * s, deep), toe = strength(frame, 2 * s + 1, deep);
      if (deep || Math.min(heel, toe) < 0.52) continue;
      from.copy(sole(s, 1, footB)).sub(sole(s, 0, footA));
      to.copy(from).setY(0);
      if (to.lengthSq() < 0.002 || from.lengthSq() < EPS) continue;
      delta.setFromUnitVectors(from.normalize(), to.normalize());
      const angle = delta.angleTo(IDENTITY);
      if (angle < 0.015 || angle > 0.37) continue;
      const blend = ease((Math.min(heel, toe) - 0.52) / 0.36) * ease((0.37 - angle) / 0.09);
      delta.slerp(IDENTITY, 1 - blend);
      const leg = legs[s];
      leg.ankle.override = leg.footGoal.multiplyQuaternions(delta, leg.ankle.rotation);
      if (leg.toe) leg.toe.override = leg.toeGoal.multiplyQuaternions(delta, leg.toe.rotation);
      flattened = true;
    }
    if (flattened) pose(withTorso);

    let adjusted = false;
    for (let s = 0; s < 2; s++) {
      correction.set(0, 0, 0);
      let ground = Infinity, confidence = 0, locks = 0;
      for (let part = 0; part < 2; part++) {
        const i = s * 2 + part, weight = strength(frame, i, deep);
        if (!weight) continue;
        const p = sole(s, part, footA).add(rootOffset);
        ground = Math.min(ground, p.y);
        confidence = Math.max(confidence, weight);
        const start = spans[i][frame], anchor = anchors[i].get(start);
        if (anchor && !deep) {
          // The capture already has foot correction. Nudge residual drift
          // without pulling a moving foot hard back to its first contact.
          const lock = 0.35 * weight * ease(Math.min(frame - start + 1, spanEnds[i].get(start) - frame) / 4);
          correction.x += (anchor.x - p.x) * lock;
          correction.z += (anchor.z - p.z) * lock;
          locks += lock;
        }
      }
      if (!confidence) continue;
      correction.multiplyScalar(1 / Math.max(1, locks)).clampLength(0, 0.07);
      const maxY = deep ? 0.045 : 0.13;
      correction.y = bound(floor - ground, -maxY, maxY) * confidence;
      if (correction.lengthSq() > 1e-6) {
        adjusted = pointLeg(legs[s], desired.copy(legs[s].ankle.position).add(correction)) || adjusted;
      }
    }
    if (adjusted) pose(withTorso);
    let ground = Infinity, confidence = 0;
    for (let s = 0; s < 2; s++) for (let part = 0; part < 2; part++) {
      const weight = strength(frame, 2 * s + part, deep);
      if (weight) {
        ground = Math.min(ground, sole(s, part, footA).y + rootOffset.y);
        confidence = Math.max(confidence, weight);
      }
    }
    if (confidence) {
      const margin = deep ? 0.008 : 0.025;
      rootOffset.y += bound(floor - ground, -margin, margin) * confidence;
    }
  }

  // Fixed targets are captured from this character's uncorrected first
  // contact frame, so scrubbing produces the same pose as playing in order.
  if (support) {
    const starts = new Map();
    for (let part = 0; part < 4; part++) {
      for (let frame = 0; frame < frames; frame++) if (spans[part][frame] === frame) {
        if (!starts.has(frame)) starts.set(frame, []);
        starts.get(frame).push(part);
      }
    }
    for (const [frame, parts] of starts) {
      fk(frame);
      for (const part of parts) {
        anchors[part].set(frame, sole(part >> 1, part % 2, new Vector3()).add(rootOffset));
      }
    }
  }

  let lastFrame = -1;
  function setPoseOffsets(settings) {
    const pose = poseSettings(settings);
    edits.set(pose);
    lastFrame = -1;
    return pose;
  }
  function apply(frame) {
    if (!Number.isInteger(frame) || frame < 0 || frame >= frames) return false;
    if (frame === lastFrame) return true;
    const withTorso = fk(frame);
    if (!finiteVector(rootOffset)) return false;
    if (support) plants(frame, withTorso);
    for (const record of order) if (record.driver || record.copy) {
      const { node, pose: rotation, copy } = record;
      node.quaternion.copy(rotation);
      const joint = editTargets.get(copy?.source ?? record);
      if (joint === 12 && neckBaseline) node.quaternion.multiply(neckBaseline);
      const change = edits.joints.get(joint);
      if (change) {
        if (copy) node.quaternion.multiply(delta.copy(copy.offsetBasis).multiply(change).multiply(inverse.copy(copy.offsetBasis).invert()));
        else node.quaternion.multiply(change);
        node.quaternion.normalize();
      }
      if (!node.matrixAutoUpdate) node.updateMatrix();
    }
    root.quaternion.multiplyQuaternions(edits.rootRotation, rootRotation);
    edits.position(rootPosition, rootOffset, pelvis.position, root.position);
    if (!root.matrixAutoUpdate) root.updateMatrix();
    primary.updateConstraints?.();
    lastFrame = frame;
    return true;
  }
  return { apply, setPoseOffsets, mirrorRotation: edits.mirrorRotation, matched: map.filter((i) => i >= 0).length,
    sourceToTarget: Array.from(map), support };
}
