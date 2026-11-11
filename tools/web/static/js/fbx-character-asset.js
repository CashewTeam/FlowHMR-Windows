import { Box3, Group, Matrix4, Quaternion, Skeleton, Texture, Vector3 } from "three";
import { FBXLoader } from "../vendor/three/fbx/loaders/FBXLoader.js";
import { TGALoader } from "../vendor/three/mmd/loaders/TGALoader.js";

const MAX_FBX_BYTES = 256 * 1024 * 1024;
const Y_UP = new Vector3(0, 1, 0);
const MAX_TEXTURE_DIMENSION = 16384;
const MAX_TEXTURE_PIXELS = 67108864;

class FbxCharacterError extends Error {
  constructor(code, message, cause) {
    super(message, { cause });
    this.name = "FbxCharacterError";
    this.code = code;
  }
}

function abortIfNeeded(signal) {
  if (signal?.aborted) throw new DOMException("FBX character loading aborted.", "AbortError");
}

function partsOf(path) {
  if (typeof path !== "string" || !path || /^[\\/]|[:\x00-\x1f\x7f]/.test(path)) return null;
  const parts = path.replace(/\\/g, "/").normalize("NFC").split("/");
  return parts.at(-1) ? parts : null;
}

function fileEntry(file) {
  const parts = partsOf(file?.webkitRelativePath || file?.name);
  if (!parts || parts.some((part) => !part || part === "." || part === "..")) return null;
  return { file, parts, path: parts.join("/"), root: parts.length > 1 ? parts[0] : null };
}

export function listFbxCandidates(files) {
  return Array.from(files ?? [], fileEntry)
    .filter((entry) => entry && /\.fbx$/i.test(entry.path))
    .map(({ file, path }) => ({ file, path, name: file.name }))
    .sort((a, b) => a.path.localeCompare(b.path) || (a.path < b.path ? -1 : a.path > b.path ? 1 : 0));
}

function localFiles(files, model) {
  const exact = new Map();
  const folded = new Map();
  const remember = (map, key, file) => {
    if (!map.has(key)) map.set(key, file);
    else if (map.get(key) !== file) map.set(key, null);
  };
  for (const file of files) {
    const entry = fileEntry(file);
    if (!entry || entry.root !== model.root) continue;
    remember(exact, entry.path, file);
    remember(folded, entry.path.toLowerCase(), file);
  }
  const folder = model.parts.slice(0, -1);
  const floor = model.root ? 1 : 0;
  const lookup = (path) => exact.has(path) ? exact.get(path) : folded.get(path.toLowerCase());
  return (path) => {
    const input = partsOf(path);
    if (!input) return { blocked: true };
    const result = folder.slice();
    for (const part of input) {
      if (!part || part === ".") continue;
      if (part === "..") {
        if (result.length <= floor) return { blocked: true };
        result.pop();
      } else {
        result.push(part);
      }
    }
    const resolved = lookup(result.join("/"));
    if (resolved !== undefined || input.length !== 1 || input[0] === "..") {
      return { file: resolved, ambiguous: resolved === null };
    }
    // FBX exporters often unpack embedded media into a sibling <model>.fbm folder.
    const mediaFolder = model.parts.at(-1).replace(/\.fbx$/i, ".fbm");
    const media = lookup([...folder, mediaFolder, input[0]].join("/"));
    return { file: media, ambiguous: media === null };
  };
}

function imageMime(bytes) {
  const has = (...prefix) => prefix.every((value, i) => bytes[i] === value);
  if (has(137, 80, 78, 71, 13, 10, 26, 10)) return "image/png";
  if (has(255, 216, 255)) return "image/jpeg";
  if (has(71, 73, 70, 56)) return "image/gif";
  if (has(66, 77)) return "image/bmp";
  if (has(82, 73, 70, 70) && bytes[8] === 87 && bytes[9] === 69 && bytes[10] === 66 && bytes[11] === 80) return "image/webp";
  return null;
}

function validDimensions(width, height) {
  return width > 0 && height > 0 && Math.max(width, height) <= MAX_TEXTURE_DIMENSION && width * height <= MAX_TEXTURE_PIXELS;
}

function embeddedBlob(content) {
  if (content instanceof ArrayBuffer) return new Blob([content]);
  if (typeof content !== "string") throw new Error("Invalid embedded image.");
  return new Blob([Uint8Array.from(atob(content), (value) => value.charCodeAt(0))]);
}

async function decodeImage(blob, path, signal) {
  const header = new Uint8Array(await blob.slice(0, 18).arrayBuffer());
  abortIfNeeded(signal);
  const mime = imageMime(header);
  if (mime) {
    const raster = new Blob([blob], { type: mime });
    if (typeof createImageBitmap === "function") {
      let image;
      try {
        // ImageBitmap does not honor Texture.flipY. Match the regular FBX TextureLoader orientation.
        image = await createImageBitmap(raster, {
          imageOrientation: "flipY", colorSpaceConversion: "none", premultiplyAlpha: "none",
        });
      } catch {
        // Some WebViews decode additional BMP variants through an image element.
      }
      if (image) {
        if (!validDimensions(image.width, image.height)) {
          image.close();
          throw new Error("Unsupported image dimensions.");
        }
        return { image, flipY: false };
      }
    }
    const url = URL.createObjectURL(raster);
    try {
      const image = new Image();
      image.src = url;
      await image.decode();
      if (!validDimensions(image.width, image.height)) throw new Error("Unsupported image dimensions.");
      return { image, flipY: true };
    } finally {
      URL.revokeObjectURL(url);
    }
  }
  if (!/\.tga$/i.test(path ?? "") || header.length < 18) throw new Error("Unsupported FBX texture type.");
  const width = header[12] | (header[13] << 8);
  const height = header[14] | (header[15] << 8);
  if (!validDimensions(width, height)) throw new Error("Unsupported TGA dimensions.");
  const bytes = await blob.arrayBuffer();
  abortIfNeeded(signal);
  const { data } = new TGALoader().parse(bytes);
  const image = document.createElement("canvas");
  image.width = width;
  image.height = height;
  image.getContext("2d").putImageData(new ImageData(new Uint8ClampedArray(data.buffer), width, height), 0, 0);
  return { image, flipY: true };
}

function textureBindings(materials) {
  const bindings = [];
  for (const material of materials) {
    for (const [key, texture] of Object.entries(material)) {
      if (texture?.isTexture) bindings.push({ material, key, texture });
    }
  }
  return bindings;
}

async function nativeTexture(paths, makeUrl, signal) {
  if (typeof makeUrl !== "function" || !globalThis.location) return null;
  for (const path of paths) {
    const value = makeUrl(path);
    if (typeof value !== "string" || !value) continue;
    const url = new URL(value, globalThis.location.href);
    // Only the app's authorized per-file resource route is accepted. Even a
    // local server redirect must not send the request outside this route.
    if (url.origin !== globalThis.location.origin || url.username || url.password
      || !/^\/api\/character-resource\/[A-Za-z0-9_-]+\/?$/.test(url.pathname)) continue;
    const response = await fetch(url, { credentials: "same-origin", redirect: "error", signal });
    if ([400, 403, 404, 410].includes(response.status)) continue;
    if (!response.ok) throw new Error("The local FBX texture could not be read.");
    return { blob: await response.blob(), path };
  }
  return null;
}

async function loadTextures(bindings, requests, resolve, images, signal, makeUrl) {
  const entries = new Map();
  const byTexture = new Map();
  for (const { texture } of bindings) {
    if (byTexture.has(texture)) continue;
    const request = requests.get(texture);
    if (!request) continue;
    const paths = [...new Set(request.paths)];
    // Generated sampler nodes can have no image reference at all.
    if (!request.content && !paths.length) continue;
    const candidates = request.content ? [] : paths.map(resolve);
    const file = candidates.find((value) => value.file)?.file;
    const path = file?.name || paths[0] || request.name || "Embedded image";
    const blocked = !file && candidates.length > 0 && candidates.every((value) => value.blocked);
    const nativePaths = file || request.content ? [] : paths.filter((_, index) => {
      const candidate = candidates[index];
      return !candidate.blocked && !candidate.ambiguous;
    });
    const key = file || request.content || paths.join("\0");
    if (!entries.has(key)) entries.set(key, { file, path, blocked, content: request.content, nativePaths });
    byTexture.set(texture, entries.get(key));
  }

  const pending = [...entries.values()];
  let next = 0;
  await Promise.all(Array.from({ length: Math.min(3, pending.length) }, async () => {
    while (next < pending.length && !signal?.aborted) {
      const entry = pending[next++];
      try {
        const source = entry.file ? { blob: entry.file, path: entry.path }
          : entry.content ? { blob: embeddedBlob(entry.content), path: entry.path }
            : await nativeTexture(entry.nativePaths, makeUrl, signal);
        if (!source) {
          entry.state = "missing";
          continue;
        }
        entry.value = await decodeImage(source.blob, source.path, signal);
        if (typeof entry.value.image.close === "function") images.add(entry.value.image);
        entry.state = "loaded";
      } catch {
        entry.state = "failed";
      }
    }
  }));
  abortIfNeeded(signal);

  for (const { material, key, texture } of bindings) {
    const entry = byTexture.get(texture);
    if (entry?.state === "loaded") {
      texture.image = entry.value.image;
      texture.flipY = entry.value.flipY;
      texture.needsUpdate = true;
    } else if (requests.has(texture)) {
      material[key] = null;
      material.needsUpdate = true;
    }
  }
  const missingPaths = pending.filter(({ state }) => state === "missing").map(({ path }) => path);
  const failedPaths = pending.filter(({ state }) => state === "failed").map(({ path }) => path);
  return {
    requested: pending.length,
    loaded: pending.length - missingPaths.length - failedPaths.length,
    missing: missingPaths.length,
    failed: failedPaths.length,
    missingPaths,
    failedPaths,
    blockedPaths: pending.filter(({ blocked, state }) => blocked && state === "missing").map(({ path }) => path),
  };
}

function sourceName(bone) {
  return bone.userData.originalName || bone.name;
}

function boneKey(bone) {
  return sourceName(bone).split(/[:|]/).at(-1).replace(/^mixamorig\d*/i, "").replace(/[^a-z0-9]/gi, "").toLowerCase();
}

function matchingProfile(bones) {
  const keys = new Set(bones.map(boneKey));
  if (["hips", "spine", "head", "leftarm", "rightarm", "leftforearm", "rightforearm", "leftupleg", "rightupleg",
    "leftleg", "rightleg"].every((name) => keys.has(name))) return "mixamo";
  if (["pelvis", "spine01", "head", "upperarml", "upperarmr", "lowerarml", "lowerarmr", "thighl",
    "thighr", "calfl", "calfr"].every((name) => keys.has(name))) return "metahuman";
  return null;
}

function lodInfo(mesh) {
  let current = mesh;
  while (current) {
    const name = current.userData.originalName || current.name;
    const match = /(?:^|[\s_.-])lod[\s_.-]*(\d+)(?=$|[\s_.-])/i.exec(name);
    if (match) {
      const part = name.slice(0, match.index) + name.slice(match.index + match[0].length);
      return { key: (current.parent?.uuid || "") + ":" + part.toLowerCase(), level: Number(match[1]) };
    }
    if (current.parent?.userData.fbxModelType?.toLowerCase() === "lodgroup") {
      return { key: current.parent.uuid, level: current.parent.children.indexOf(current) };
    }
    current = current.parent;
  }
  return null;
}

function lowestLodMeshes(meshes) {
  const descriptors = new Map(meshes.map((mesh) => [mesh, lodInfo(mesh)]));
  const lowest = new Map();
  for (const value of descriptors.values()) {
    if (value) lowest.set(value.key, Math.min(lowest.get(value.key) ?? Infinity, value.level));
  }
  return meshes.filter((mesh) => {
    const value = descriptors.get(mesh);
    return !value || value.level === lowest.get(value.key);
  });
}

function isUsableSkin(mesh) {
  if (!mesh.isSkinnedMesh || !mesh.skeleton?.bones?.length) return false;
  const geometry = mesh.geometry;
  const count = geometry.getAttribute("position")?.count;
  return count && geometry.getAttribute("skinIndex")?.count === count
    && geometry.getAttribute("skinWeight")?.count === count
    && Array.from(mesh.skeleton.bones).every((bone) => bone?.isBone);
}

function hasLegWeights(mesh, profile) {
  const keys = profile === "mixamo"
    ? [/^left(?:upleg|leg|foot|toebase)$/, /^right(?:upleg|leg|foot|toebase)$/]
    : [/^(?:thigh|calf|foot|ball)l$/, /^(?:thigh|calf|foot|ball)r$/];
  const sides = mesh.skeleton.bones.map((bone) => keys.findIndex((pattern) => pattern.test(boneKey(bone))));
  const indices = mesh.geometry.getAttribute("skinIndex").array;
  const weights = mesh.geometry.getAttribute("skinWeight").array;
  const evidence = [0, 0];
  for (let i = 0; i < indices.length; i++) {
    const side = sides[indices[i]];
    if (side >= 0 && weights[i] > 0) evidence[side] += weights[i];
    if (evidence[0] >= 4 && evidence[1] >= 4) return true;
  }
  return false;
}

function meshRole(mesh, bones, profile) {
  const faceLike = /(?:^|[^a-z])(?:face|head)(?:[^a-z]|$)|face_mesh|headmesh/i.test(mesh.userData.originalName || mesh.name)
    || bones.some((bone) => /^facial/i.test(sourceName(bone).split(/[:|]/).at(-1)));
  // A merged MetaHuman mesh can carry the entire facial rig as well as its
  // body. Face-only meshes can also list unweighted copies of the body bones.
  return faceLike && !(profile && hasLegWeights(mesh, profile)) ? "face" : "body";
}

function rigBones(mesh) {
  // Include unweighted ancestor bones (such as a MetaHuman root) in the same
  // stable order, after the skin's ordered cluster list. Scene parents stay live.
  const result = [...new Set(mesh.skeleton.bones)];
  const seen = new Set(result);
  for (const bone of result) {
    if (bone.parent?.isBone && !seen.has(bone.parent)) {
      seen.add(bone.parent);
      result.push(bone.parent);
    }
  }
  return result;
}

function retainScene(scene, meshes) {
  const keep = new Set([scene]);
  const retain = (node) => {
    for (let current = node; current && !keep.has(current); current = current.parent) keep.add(current);
  };
  for (const mesh of meshes) {
    retain(mesh);
    if (isUsableSkin(mesh)) for (const bone of mesh.skeleton.bones) retain(bone);
  }
  const prune = (node) => {
    node.animations = [];
    for (const child of [...node.children]) {
      if (!keep.has(child)) node.remove(child);
      else prune(child);
    }
  };
  prune(scene);
}

function normalization(scene, main) {
  const global = scene.userData.fbxCharacterGlobal ?? {};
  const axis = global.UpAxis ?? 1;
  const sign = global.UpAxisSign ?? 1;
  if (![0, 1, 2].includes(axis) || ![-1, 1].includes(sign)) {
    throw new FbxCharacterError("invalid_scale", "The FBX up axis is invalid.");
  }
  const sourceUp = new Vector3().setComponent(axis, sign);
  scene.updateMatrixWorld(true);
  const lengths = main.bones.filter((bone) => /^(head|hips|pelvis|leftfoot|rightfoot|foot[lr]|lefttoe?base|righttoe?base|ball[lr])$/.test(boneKey(bone)))
    .map((bone) => bone.getWorldPosition(new Vector3()).dot(sourceUp));
  const skeletalHeight = Math.max(...lengths) - Math.min(...lengths);
  const cmPerUnit = global.UnitScaleFactor;
  // Older files may omit GlobalSettings. In that case use their measured
  // humanoid skeleton to distinguish conventional metres from centimetres.
  const unitScale = cmPerUnit === undefined ? (skeletalHeight > 10 ? 0.01 : 1) : cmPerUnit / 100;
  if (!Number.isFinite(unitScale) || unitScale <= 0
    || !Number.isFinite(skeletalHeight) || skeletalHeight * unitScale < 0.05 || skeletalHeight * unitScale > 20) {
    throw new FbxCharacterError("invalid_scale", "The FBX unit scale or character size is invalid.");
  }
  return { unitScale, axis, sign, global, rotation: new Quaternion().setFromUnitVectors(sourceUp, Y_UP) };
}

function restPose(rig, norm, bounds) {
  const indices = new Map(rig.bones.map((bone, index) => [bone, index]));
  const pos = new Vector3();
  const quat = new Quaternion();
  const identity = new Matrix4();
  return {
    unitScale: norm.unitScale,
    sourceUpAxis: norm.axis,
    sourceUpSign: norm.sign,
    sourceAxes: { ...norm.global },
    axisRotation: norm.rotation.toArray(),
    bounds: { min: bounds.min.toArray(), max: bounds.max.toArray() },
    bones: rig.bones.map((bone, index) => ({
      index,
      name: sourceName(bone),
      nodeName: bone.name,
      parent: indices.get(bone.parent) ?? -1,
      position: bone.position.toArray(),
      quaternion: bone.quaternion.toArray(),
      scale: bone.scale.toArray(),
      worldPosition: bone.getWorldPosition(pos).toArray(),
      worldQuaternion: bone.getWorldQuaternion(quat).toArray(),
      worldMatrix: bone.matrixWorld.toArray(),
      parentWorldMatrix: (bone.parent?.matrixWorld ?? identity).toArray(),
    })),
  };
}

function cloneActor(source, primary, others) {
  const root = source.clone(true);
  const copies = new Map();
  const pair = (a, b) => {
    copies.set(a, b);
    a.children.forEach((child, index) => pair(child, b.children[index]));
  };
  pair(source, root);
  const skeletons = new Map();
  for (const [original, clone] of copies) {
    if (!isUsableSkin(original)) continue;
    if (!skeletons.has(original.skeleton)) {
      skeletons.set(original.skeleton, new Skeleton(
        original.skeleton.bones.map((bone) => copies.get(bone)),
        original.skeleton.boneInverses.map((matrix) => matrix.clone()),
      ));
    }
    clone.bind(skeletons.get(original.skeleton), original.bindMatrix);
  }
  const bindRig = (rig) => ({
    bones: rig.bones.map((bone) => copies.get(bone)),
    mesh: copies.get(rig.mesh),
    skeleton: skeletons.get(rig.mesh.skeleton),
    rest: rig.rest,
    role: rig.role,
    profile: rig.profile,
  });
  root.updateMatrixWorld(true);
  return {
    root,
    rig: { ...bindRig(primary), additionalRigs: others.map(bindRig) },
    skeletons: [...skeletons.values()],
  };
}

/**
 * Load a local Mixamo or MetaHuman-style skinned FBX. The returned template
 * keeps the FBX bind pose. Clones own their bones and skeletons, and share the
 * asset's original geometry, materials and textures.
 */
export async function loadFbxCharacter(files, selectedFile, { signal, textureUrl } = {}) {
  abortIfNeeded(signal);
  const entries = Array.from(files ?? []);
  const entry = fileEntry(selectedFile);
  if (!entry || !/\.fbx$/i.test(entry.path) || !entries.includes(selectedFile)) {
    throw new FbxCharacterError("selection", "Select an FBX file from the chosen files.");
  }
  if (selectedFile.size > MAX_FBX_BYTES) {
    const error = new FbxCharacterError("too_large", "The FBX is larger than 256 MiB.");
    error.maxBytes = MAX_FBX_BYTES;
    throw error;
  }

  const name = selectedFile.name.replace(/\.fbx$/i, "");
  const requests = new Map();
  const textures = new Set();
  const images = new Set();
  const materials = new Set();
  const geometries = new Set();
  const sourceSkeletons = new Set();
  const clones = new Map();
  let template;
  let disposed = false;
  const releaseClone = (root) => {
    const clone = clones.get(root);
    if (!clone) return false;
    clones.delete(root);
    root.removeFromParent();
    for (const skeleton of clone.skeletons) skeleton.dispose();
    return true;
  };
  const dispose = () => {
    if (disposed) return;
    disposed = true;
    for (const root of clones.keys()) releaseClone(root);
    template?.removeFromParent();
    for (const skeleton of sourceSkeletons) skeleton.dispose();
    for (const geometry of geometries) geometry.dispose();
    for (const material of materials) material.dispose();
    for (const texture of textures) {
      texture.dispose();
      texture.image = null;
    }
    for (const image of images) image.close();
    textures.clear();
    images.clear();
  };

  try {
    const bytes = await selectedFile.arrayBuffer();
    abortIfNeeded(signal);
    let scene;
    try {
      scene = new FBXLoader().parse(bytes, "", {
        characterOnly: true,
        loadLocalTexture(request) {
          const texture = new Texture();
          textures.add(texture);
          requests.set(texture, request);
          return texture;
        },
      });
    } catch (cause) {
      throw new FbxCharacterError("parse", cause instanceof Error ? cause.message : String(cause), cause);
    }

    const sceneMeshes = [];
    scene.traverse((node) => {
      if (!node.isMesh || !node.geometry) return;
      geometries.add(node.geometry);
      delete node.geometry.FBX_Deformer;
      for (const material of Array.isArray(node.material) ? node.material : [node.material]) {
        if (material) materials.add(material);
      }
      if (node.skeleton) sourceSkeletons.add(node.skeleton);
      if (node.geometry.getAttribute("position")?.count) sceneMeshes.push(node);
    });
    const visibleMeshes = lowestLodMeshes(sceneMeshes);
    const skins = visibleMeshes.filter(isUsableSkin);
    if (!skins.length) throw new FbxCharacterError("no_skin", "No skinned character mesh was found in the FBX.");
    const rigList = skins.map((mesh) => {
      const bones = rigBones(mesh);
      const profile = matchingProfile(bones);
      return { mesh, bones, profile, role: meshRole(mesh, bones, profile) };
    });
    const main = rigList.filter((rig) => rig.profile && rig.role !== "face").sort((a, b) =>
      b.bones.length - a.bones.length || b.mesh.geometry.getAttribute("position").count - a.mesh.geometry.getAttribute("position").count,
    )[0];
    if (!main) throw new FbxCharacterError("unsupported_rig", "No recognizable Mixamo or MetaHuman body skeleton was found.");
    const known = new Set(main.bones);
    const additional = rigList.filter((rig) => {
      if (rig === main || rig.bones.every((bone) => known.has(bone))) return false;
      for (const bone of rig.bones) known.add(bone);
      return true;
    });

    retainScene(scene, visibleMeshes.filter((mesh) => !mesh.isSkinnedMesh || isUsableSkin(mesh)));
    const norm = normalization(scene, main);
    const axisRoot = new Group();
    axisRoot.quaternion.copy(norm.rotation);
    axisRoot.scale.setScalar(norm.unitScale);
    axisRoot.add(scene);
    template = new Group();
    template.name = name;
    template.add(axisRoot);
    template.updateMatrixWorld(true);
    for (const mesh of visibleMeshes) mesh.frustumCulled = false;
    const bounds = new Box3().setFromObject(template);
    if (bounds.isEmpty() || !bounds.min.toArray().concat(bounds.max.toArray()).every(Number.isFinite)) {
      throw new FbxCharacterError("invalid_scale", "The FBX mesh bounds are invalid.");
    }
    main.rest = restPose(main, norm, bounds);
    for (const rig of additional) rig.rest = restPose(rig, norm, bounds);

    const usedMaterials = new Set();
    template.traverse((node) => {
      if (node.isMesh) for (const material of Array.isArray(node.material) ? node.material : [node.material]) {
        if (material) usedMaterials.add(material);
      }
    });
    const bindings = textureBindings(usedMaterials);
    for (const { texture } of textureBindings(materials)) textures.add(texture);
    const textureReport = await loadTextures(bindings, requests, localFiles(entries, entry), images, signal, textureUrl);
    requests.clear();
    const skinWeights = [...new Set(skins.map((mesh) => mesh.geometry))].map((geometry) => geometry.userData.fbxSkinWeights);
    const rigReport = {
      skinMeshes: skins.length,
      lodMeshesSkipped: sceneMeshes.length - visibleMeshes.length,
      additionalRigs: additional.length,
      maxSkinInfluences: 4,
      sourceMaxSkinInfluences: Math.max(0, ...skinWeights.map((value) => value?.maxInfluences ?? 0)),
      trimmedSkinVertices: skinWeights.reduce((sum, value) => sum + (value?.overFour ?? 0), 0),
    };
    return {
      kind: "fbx",
      profile: main.profile,
      name,
      path: entry.path,
      rest: main.rest,
      textureReport,
      rigReport,
      clone() {
        if (disposed) throw new FbxCharacterError("disposed", "The FBX character is no longer available.");
        const clone = cloneActor(template, main, additional);
        clones.set(clone.root, clone);
        return clone.root;
      },
      getRig(root) { return clones.get(root)?.rig ?? null; },
      releaseClone,
      dispose,
    };
  } catch (cause) {
    dispose();
    if (cause instanceof FbxCharacterError || cause?.name === "AbortError") throw cause;
    throw new FbxCharacterError("load", cause instanceof Error ? cause.message : String(cause), cause);
  }
}
