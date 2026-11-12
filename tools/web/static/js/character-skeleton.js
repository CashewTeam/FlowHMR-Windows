import { Skeleton } from "three";

export function cloneSkinnedHierarchy(source, isSkinned = (node) => node.isSkinnedMesh) {
  const root = source.clone(true);
  const copies = new Map();
  const pair = (original, copy) => {
    copies.set(original, copy);
    original.children.forEach((child, index) => pair(child, copy.children[index]));
  };
  pair(source, root);
  const skeletons = new Map();
  for (const [original, copy] of copies) {
    if (!isSkinned(original)) continue;
    if (!skeletons.has(original.skeleton)) {
      skeletons.set(original.skeleton, new Skeleton(
        original.skeleton.bones.map((bone) => copies.get(bone)),
        original.skeleton.boneInverses.map((matrix) => matrix.clone()),
      ));
    }
    copy.bind(skeletons.get(original.skeleton), original.bindMatrix);
  }
  root.updateMatrixWorld(true);
  return { root, copies, skeletons };
}
