import { Vector3 } from "three";

const clamp = (value) => Math.min(1, Math.max(0, value));
function smooth(value) {
  const t = clamp(value);
  return t * t * (3 - 2 * t);
}

/** Preserve the relationship of nearby hands after fitting the arms independently. */
export function makeHandPairFit(motion, map, bind, arms) {
  const hands = arms.map(({ chain }, side) => {
    const wrist = 20 + side, base = 22 + side * 15;
    const knuckles = [base, base + 3, base + 6, base + 9].filter((j) => map[j] >= 0);
    const points = [wrist, ...Array.from({ length: 15 }, (_, i) => base + i).filter((j) => map[j] >= 0)];
    const ends = [2, 5, 8, 11, 14].map((i) => base + i).filter((j) => map[j] >= 0 && map[j - 1] >= 0);
    return { chain, wrist, knuckles, points, ends };
  });
  if (hands.some(({ knuckles }) => knuckles.length < 2)) return () => false;
  const avgArm = (hands[0].chain.sourceLength + hands[1].chain.sourceLength) * 0.5;
  if (!(avgArm > 1e-5)) return () => false;
  const scale = hands.reduce((sum, { wrist, knuckles, chain }) => {
    let a = 0, b = 0;
    for (const j of knuckles) {
      a += new Vector3().fromArray(motion.rest, j * 3).distanceTo(new Vector3().fromArray(motion.rest, wrist * 3));
      b += bind[map[j]].distanceTo(bind[map[wrist]]);
    }
    return sum + 0.5 * (a > 1e-5 ? b / a : chain.length / chain.sourceLength);
  }, 0);
  const limit = Math.min(...arms.map(({ chain }) => chain.length)) * 0.16;
  const sample = new Vector3(), sourceDelta = new Vector3(), targetDelta = new Vector3();
  const other = new Vector3(), axis = new Vector3();

  function palm(hand, read, out) {
    read(hand.wrist, out).multiplyScalar(0.25);
    const w = 0.75 / hand.knuckles.length;
    for (const j of hand.knuckles) out.addScaledVector(read(j, sample), w);
    return out;
  }

  function edge(hand, read, side) {
    let max = -Infinity;
    for (const j of hand.points) max = Math.max(max, side * read(j, sample).dot(axis));
    // Use a short distal extension; SMPL-H stores the last knuckle, not the tip.
    for (const j of hand.ends) {
      const a = read(j, sample).dot(axis), b = read(j - 1, sample).dot(axis);
      max = Math.max(max, side * (a + 0.8 * (a - b)));
    }
    return max;
  }

  return (frame, worldPosition, correction) => {
    const base = frame * 52 * 3;
    const source = (j, out) => out.fromArray(motion.joints, base + j * 3);
    palm(hands[1], source, sourceDelta).sub(palm(hands[0], source, other));
    const distance = sourceDelta.length();
    const weight = smooth((0.70 - distance / avgArm) / 0.28);
    if (!weight) return false;

    const target = (j, out) => out.copy(worldPosition[map[j]]);
    palm(hands[1], target, targetDelta).sub(palm(hands[0], target, other));
    correction.copy(sourceDelta).multiplyScalar(scale).sub(targetDelta);
    if (distance > 1e-5) {
      axis.copy(sourceDelta).divideScalar(distance);
      const gap = -edge(hands[0], source, 1) - edge(hands[1], source, -1);
      // Respect deliberate contact. Only restore clearance if the captured
      // fingers actually have a gap along their approach direction.
      if (gap > 0.006) {
        const predicted = -edge(hands[0], target, 1) - edge(hands[1], target, -1) + correction.dot(axis);
        const extra = Math.max(0, 0.8 * gap * scale - predicted);
        correction.addScaledVector(axis, extra * smooth((gap - 0.006) / 0.007));
      }
    }
    correction.multiplyScalar(0.5 * weight).clampLength(0, limit);
    return correction.lengthSq() > 4e-6;
  };
}
