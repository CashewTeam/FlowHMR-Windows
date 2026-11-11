import { Euler, Quaternion, Vector3 } from "three";
import { canWriteVmdBoneName } from "./pmx-vmd.js";

/** Use the IK chains and right-handed angle limits read from the PMX. */
export function makePmxNativeIk(mesh, bones, parents, bodyAncestors, root, nameCounts) {
  const skin = mesh.skeleton.bones;
  const ancestorOf = (a, b) => {
    for (let i = b; i >= 0; i = parents[i]) if (i === a) return true;
    return false;
  };
  const writable = (i) => nameCounts.get(bones[i]?.name) === 1 && canWriteVmdBoneName(bones[i].name);
  const definitions = mesh.geometry?.userData?.MMD?.iks ?? [];
  const chains = [];
  const moving = new Map(), baked = new Set();
  for (const { target, effector, links, iteration, maxAngle } of definitions) {
    if (!skin[target] || !skin[effector] || target === effector || !links?.length || !writable(target)) continue;
    if (links.some(({ index }) => !skin[index] || !writable(index) || index === effector ||
      !ancestorOf(index, effector) || ancestorOf(index, target))) continue;
    const tracked = links.some(({ index }) => bodyAncestors.has(index));
    if (tracked) {
      // A motion-driven chain must have a movable controller under the
      // same root as the character so VMD preserves its placement.
      if (!(bones[target].pmx.flags & 4) || bodyAncestors.has(target) ||
          (root >= 0 && !ancestorOf(root, target))) continue;
      moving.set(target, { effector, goal: new Vector3(), local: new Vector3() });
    }
    for (const { index } of links) baked.add(index);
    chains.push({ target, effector, links,
      iterations: Math.max(1, Math.min(64, Number.isFinite(iteration) ? iteration : 1)),
      maxAngle: Number.isFinite(maxAngle) && maxAngle > 0 ? Math.min(Math.PI, maxAngle) : Math.PI });
  }
  chains.sort((a, b) => (bones[a.target].pmx.transformationClass ?? 0) -
    (bones[b.target].pmx.transformationClass ?? 0) || a.target - b.target);

  const goal = new Vector3(), end = new Vector3(), origin = new Vector3(), from = new Vector3(), to = new Vector3();
  const axis = new Vector3(), inv = new Quaternion(), turn = new Quaternion(), euler = new Euler(0, 0, 0, "XYZ");
  const limits = new Vector3(), clamp = (x, min, max) => Math.max(min, Math.min(max, x));
  function limitRotation(joint, { rotationMin, rotationMax }) {
    if (!rotationMin || !rotationMax) return false;
    euler.setFromQuaternion(joint.quaternion);
    limits.set(euler.x, euler.y, euler.z).clamp(rotationMin, rotationMax);
    if (Math.abs(limits.x - euler.x) + Math.abs(limits.y - euler.y) + Math.abs(limits.z - euler.z) < 1e-7) return false;
    joint.quaternion.setFromEuler(euler.set(limits.x, limits.y, limits.z));
    return true;
  }
  function solve() {
    if (!chains.length) return;
    mesh.updateWorldMatrix(true, true);
    for (const chain of chains) {
      const tip = skin[chain.effector];
      // A target can already coincide with its effector while the seed knee
      // still violates the PMX hinge. Resolve that before the distance check.
      if (moving.has(chain.target)) for (const link of chain.links) {
        if (link.enabled === false) break;
        const joint = skin[link.index];
        if (limitRotation(joint, link)) joint.updateMatrixWorld(true);
      }
      skin[chain.target].getWorldPosition(goal);
      for (let iteration = 0; iteration < chain.iterations; iteration++) {
        if (tip.getWorldPosition(end).distanceToSquared(goal) < 1e-10) break;
        let changed = false;
        for (const link of chain.links) {
          const { index, enabled } = link;
          if (enabled === false) break;
          const joint = skin[index];
          joint.getWorldPosition(origin);
          joint.getWorldQuaternion(inv).invert();
          tip.getWorldPosition(from).sub(origin).applyQuaternion(inv);
          to.copy(goal).sub(origin).applyQuaternion(inv);
          if (from.lengthSq() < 1e-12 || to.lengthSq() < 1e-12) continue;
          from.normalize(); to.normalize();
          const angle = Math.min(chain.maxAngle, Math.acos(clamp(from.dot(to), -1, 1)));
          if (angle < 1e-6) continue;
          axis.crossVectors(from, to);
          if (axis.lengthSq() < 1e-12) {
            axis.set(Math.abs(from.x) < .9 ? 1 : 0, Math.abs(from.x) < .9 ? 0 : 1, 0).cross(from);
          }
          joint.quaternion.multiply(turn.setFromAxisAngle(axis.normalize(), angle)).normalize();
          limitRotation(joint, link);
          joint.updateMatrixWorld(true);
          changed = true;
        }
        if (!changed) break;
      }
    }
  }
  return { available: chains.length > 0, moving, baked, solve };
}
