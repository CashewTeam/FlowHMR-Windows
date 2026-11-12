import { Box3, Group, LoadingManager } from "three";
import { VRMLoaderPlugin, VRMNodeConstraintManager, VRMUtils } from "../vendor/three/vrm/lib/three-vrm.module.min.js";
import { GLTFLoader } from "../vendor/three/vrm/loaders/GLTFLoader.js";
import { cloneSkinnedHierarchy } from "./character-skeleton.js";
import { appError } from "./i18n.js";

const MAX_VRM_BYTES = 256 * 1024 * 1024;

function inspectContainer(bytes) {
  if (bytes.byteLength < 20) throw appError("character.vrm_invalid");
  const header = new DataView(bytes);
  const length = header.getUint32(12, true);
  if (header.getUint32(0, true) !== 0x46546c67 || header.getUint32(4, true) !== 2
    || header.getUint32(8, true) !== bytes.byteLength || header.getUint32(16, true) !== 0x4e4f534a
    || !length || length > bytes.byteLength - 20) throw appError("character.vrm_invalid");
  const data = JSON.parse(new TextDecoder().decode(new Uint8Array(bytes, 20, length)));
  if (!data?.extensions?.VRM && !data?.extensions?.VRMC_vrm) throw appError("character.vrm_invalid");
  const resources = [...(data.buffers ?? []), ...(data.images ?? [])];
  if (resources.some((item) => item?.uri != null && !/^data:/i.test(item.uri))) {
    throw appError("character.vrm_external");
  }
}

function cloneConstraints(constraints, copies) {
  if (!constraints.length) return null;
  const manager = new VRMNodeConstraintManager();
  for (const original of constraints) {
    const destination = copies.get(original.destination), source = copies.get(original.source);
    if (!source || !destination) throw appError("character.vrm_invalid");
    const constraint = new original.constructor(destination, source);
    if ("aimAxis" in original) constraint.aimAxis = original.aimAxis;
    if ("rollAxis" in original) constraint.rollAxis = original.rollAxis;
    constraint.weight = original.weight;
    manager.addConstraint(constraint);
  }
  manager.setInitState();
  return manager;
}

export async function loadVrmCharacter(_files, file, { signal } = {}) {
  if (!file?.size || file.size > MAX_VRM_BYTES) throw appError("character.vrm_size");
  signal?.throwIfAborted();
  const clones = new Map();
  let template, disposed = false;
  const releaseClone = (root) => {
    const clone = clones.get(root);
    if (!clone) return false;
    clones.delete(root);
    root.removeFromParent();
    for (const skeleton of clone.skeletons.values()) skeleton.dispose();
    return true;
  };
  const dispose = () => {
    if (disposed) return;
    disposed = true;
    for (const root of clones.keys()) releaseClone(root);
    if (template) VRMUtils.deepDispose(template);
  };

  try {
    const bytes = await file.arrayBuffer();
    signal?.throwIfAborted();
    inspectContainer(bytes);
    const failures = new Set();
    const manager = new LoadingManager();
    manager.onError = (url) => failures.add(url);
    manager.setURLModifier((url) => {
      if (!/^(?:blob|data):/i.test(url)) throw appError("character.vrm_external");
      return url;
    });
    const loader = new GLTFLoader(manager);
    loader.register((parser) => new VRMLoaderPlugin(parser, { autoUpdateHumanBones: false }));
    const gltf = await loader.parseAsync(bytes, "");
    template = gltf.scene;
    signal?.throwIfAborted();
    const vrm = gltf.userData.vrm;
    if (!vrm?.humanoid) throw appError("character.vrm_invalid");
    VRMUtils.rotateVRM0(vrm);
    vrm.humanoid.normalizedHumanBonesRoot.removeFromParent();

    const name = file.name.replace(/\.vrm$/i, "");
    template = new Group().add(vrm.scene);
    template.name = name;
    const bones = new Set();
    for (const [semantic, { node }] of Object.entries(vrm.humanoid.rawHumanBones)) {
      node.userData.vrmHumanoidBone = semantic;
      bones.add(node);
    }
    let skinned = false;
    template.traverse((node) => {
      if (node.isLight || node.isCamera) node.visible = false;
      if (!node.isSkinnedMesh) return;
      skinned = true;
      node.frustumCulled = false;
      for (const bone of node.skeleton.bones) bones.add(bone);
    });
    if (!skinned) throw appError("character.vrm_invalid");
    template.updateMatrixWorld(true);
    const bounds = new Box3().setFromObject(template);
    if (bounds.isEmpty() || !bounds.min.toArray().concat(bounds.max.toArray()).every(Number.isFinite)) {
      throw appError("character.vrm_invalid");
    }
    const constraints = [...(vrm.nodeConstraintManager?.constraints ?? [])];
    return {
      kind: "vrm",
      profile: "vrm",
      name,
      rest: { bounds: { min: bounds.min.toArray(), max: bounds.max.toArray() } },
      textureReport: { missing: 0, failed: failures.size },
      clone() {
        if (disposed) throw appError("character.vrm_invalid");
        const { root, copies, skeletons } = cloneSkinnedHierarchy(template);
        const constraintManager = cloneConstraints(constraints, copies);
        const rig = { bones: Array.from(bones, (bone) => copies.get(bone)) };
        if (constraintManager) rig.updateConstraints = () => constraintManager.update();
        clones.set(root, { rig, skeletons });
        return root;
      },
      getRig(root) { return clones.get(root)?.rig ?? null; },
      releaseClone,
      dispose,
    };
  } catch (error) {
    dispose();
    if (error?.i18n || signal?.aborted) throw error;
    throw appError("character.vrm_invalid");
  }
}
