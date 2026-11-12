// "Result" card: three.js viewer for the generated SMPL-H mesh sequence, playback controls
// and synchronisation with the source video.
import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { $, show, personColor } from "./utils.js";
import { appError, message as m, setAttr, setText, t } from "./i18n.js";
import * as api from "./api.js";
import { initPreviewCharacter } from "./preview-character.js";
import { initPoseEditor } from "./pose-editor.js";

const BODY_STORAGE = "flowhmr-body-variant";
const MOUSE_MODE_STORAGE = "flowhmr-mouse-mode";

function rememberToggle(toggle, key, onChange) {
  const saved = localStorage.getItem(key);
  if (saved === "on" || saved === "off") toggle.checked = saved === "on";
  const save = () => localStorage.setItem(key, toggle.checked ? "on" : "off");
  toggle.addEventListener("change", () => {
    save();
    onChange?.(toggle.checked);
  });
  return save;
}

function halfFloatTable() {
  const out = new Float32Array(65536);
  for (let h = 0; h < out.length; h++) {
    const s = (h & 0x8000) ? -1 : 1;
    const e = (h >> 10) & 0x1f;
    const f = h & 0x3ff;
    out[h] = e === 0 ? s * 5.960464477539063e-8 * f
      : e === 31 ? (f ? NaN : s * Infinity)
      : s * Math.pow(2, e - 15) * (1 + f / 1024);
  }
  return out;
}

function floorGridTexture(renderer, size, dark) {
  const canvas = document.createElement("canvas");
  canvas.width = canvas.height = 256;
  const ctx = canvas.getContext("2d");
  ctx.fillStyle = dark ? "#414141" : "#e4e4e4";
  ctx.fillRect(0, 0, 256, 256);
  ctx.fillStyle = dark ? "#6b6b6b" : "#ababab";
  ctx.fillRect(0, 0, 6, 256);
  ctx.fillRect(0, 0, 256, 6);
  ctx.fillStyle = dark ? "#555555" : "#c4c4c4";
  ctx.fillRect(127, 0, 3, 256);
  ctx.fillRect(0, 127, 256, 3);
  const texture = new THREE.CanvasTexture(canvas);
  texture.colorSpace = THREE.SRGBColorSpace;
  texture.wrapS = texture.wrapT = THREE.RepeatWrapping;
  texture.repeat.set(size, size);
  texture.anisotropy = renderer.capabilities.getMaxAnisotropy();
  return texture;
}

export function initViewer(preview) {
  const { video, clock } = preview;
  const container = $("viewer");
  const scrub = $("scrub");
  const renderer = new THREE.WebGLRenderer({ antialias: true });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = THREE.PCFSoftShadowMap;
  renderer.shadowMap.autoUpdate = false;
  container.appendChild(renderer.domElement);
  const fromHalf = halfFloatTable();
  let renderRequest = 0, dirty = false, loadController = null;
  let bodyController = null;
  function invalidate() {
    dirty = true;
    if (!renderRequest) renderRequest = requestAnimationFrame(tick);
  }

  const darkTheme = document.documentElement.dataset.theme === "dark";
  const LIGHT_BG = 0xe4e4e4;
  const DARK_BG = 0x414141;
  const BG = darkTheme ? DARK_BG : LIGHT_BG;
  const scene = new THREE.Scene();
  scene.background = new THREE.Color(BG);
  scene.fog = new THREE.Fog(BG, 10, 40);  // near/far follow the camera distance (see tick)

  const perspective = new THREE.PerspectiveCamera(40, 1, 0.05, 200);
  const orthographic = new THREE.OrthographicCamera(-1, 1, 1, -1, 0.05, 200);
  const defaultTarget = new THREE.Vector3(0, 0.9, 0);
  const frontOffset = new THREE.Vector3(0, -0.43, 4.8);
  const frontDirection = frontOffset.clone().normalize();
  let camera = perspective;
  camera.position.copy(defaultTarget).add(frontOffset);
  const controls = new OrbitControls(camera, renderer.domElement);
  controls.target.copy(defaultTarget);
  controls.enableDamping = true;
  controls.dampingFactor = 0.08;
  controls.minDistance = 0.8;
  controls.maxDistance = 25;

  // Custom wheel zoom. The stock OrbitControls (r160) divides the wheel delta by
  // (devicePixelRatio | 0), which is 0 at browser zoom < 100% -> infinite jumps, and it
  // applies large trackpad/free-spin deltas in a single step. Here each wheel event only
  // moves a target distance by a bounded factor, and the camera eases toward it per frame.
  controls.enableZoom = false;
  const zoom = { target: camera.position.distanceTo(controls.target) };
  renderer.domElement.addEventListener("wheel", (e) => {
    e.preventDefault();
    const unit = e.deltaMode === 1 ? 16 : e.deltaMode === 2 ? container.clientHeight : 1;
    const px = Math.max(-120, Math.min(120, e.deltaY * unit));  // cap per event (~1 notch)
    zoom.target = THREE.MathUtils.clamp(zoom.target * Math.pow(1.0015, px),
      controls.minDistance, controls.maxDistance);
    invalidate();
  }, { passive: false });
  const zoomDir = new THREE.Vector3();
  function applyZoom() {
    const dist = camera.position.distanceTo(controls.target);
    if (Math.abs(dist - zoom.target) < 1e-4) return false;
    const next = dist + (zoom.target - dist) * 0.2;
    zoomDir.subVectors(camera.position, controls.target).normalize();
    camera.position.copy(controls.target).addScaledVector(zoomDir, next);
    if (camera === orthographic) updateOrthographic();
    dirty = true;
    return true;
  }
  window.__viewer = { get camera() { return camera; }, controls, zoom,
    get renderCount() { return renderer.info.render.frame; },
    get groundHeight() { return floor.position.y; },
    get skeleton() { return actors[0]?.skeleton?.group; }, get actors() { return actors; } };  // for automated tests

  // Soft studio lighting: sky/ground ambient, a front-top key light that casts the floor
  // shadow (it follows the body, see setFrame), a cool fill from the side and a rim light.
  const ambient = new THREE.HemisphereLight(0xf5f7ff, 0xc8ccd8, 1.4);
  scene.add(ambient);
  const sun = new THREE.DirectionalLight(0xffffff, 1.9);
  const SUN_OFFSET = new THREE.Vector3(-2.2, 6, 3.6);  // front-left: shadow falls back-right
  sun.position.copy(SUN_OFFSET);
  sun.castShadow = true;
  sun.shadow.mapSize.set(2048, 2048);
  sun.shadow.bias = -0.0004;
  sun.shadow.normalBias = 0.02;
  const fill = new THREE.DirectionalLight(0xe6ebff, 0.55);
  fill.position.set(4, 2, 2);
  const rim = new THREE.DirectionalLight(0xffffff, 0.7);
  rim.position.set(1.5, 3, -5);
  scene.add(fill, rim);
  Object.assign(sun.shadow.camera, { left: -4, right: 4, top: 4, bottom: -4, near: 0.5, far: 20 });
  scene.add(sun, sun.target);

  function syncCharacterLighting() {
    const custom = !!character.active;
    ambient.intensity = custom ? .62 : 1.4;
    sun.intensity = custom ? 1.42 : 1.9;
    fill.intensity = custom ? .28 : .55;
    rim.intensity = custom ? .45 : .7;
  }

  // Editor grid over the shadow plane, with the two horizontal world axes.
  const FLOOR_SIZE = 200;
  const floor = new THREE.Mesh(new THREE.PlaneGeometry(FLOOR_SIZE, FLOOR_SIZE),
    new THREE.MeshBasicMaterial({ map: floorGridTexture(renderer, FLOOR_SIZE, darkTheme) }));
  floor.rotation.x = -Math.PI / 2;
  const axes = new THREE.BufferGeometry();
  axes.setAttribute("position", new THREE.Float32BufferAttribute([
    -100, 0.01, 0, 100, 0.01, 0, 0, 0.01, -100, 0, 0.01, 100,
  ], 3));
  const red = new THREE.Color(0xad5757);
  const green = new THREE.Color(0x55875a);
  axes.setAttribute("color", new THREE.Float32BufferAttribute([
    ...red.toArray(), ...red.toArray(), ...green.toArray(), ...green.toArray(),
  ], 3));
  const shadowPlane = new THREE.Mesh(new THREE.PlaneGeometry(FLOOR_SIZE, FLOOR_SIZE),
    new THREE.ShadowMaterial({ color: 0x4b5468, opacity: 0.34 }));
  shadowPlane.rotation.x = -Math.PI / 2;
  shadowPlane.position.y = 0.001;
  shadowPlane.receiveShadow = true;
  const axisLines = new THREE.LineSegments(axes, new THREE.LineBasicMaterial({ vertexColors: true }));
  scene.add(floor, shadowPlane, axisLines);
  window.addEventListener("flowhmr:themechange", ({ detail }) => {
    const dark = detail.dark;
    const bg = dark ? DARK_BG : LIGHT_BG;
    scene.background.setHex(bg);
    scene.fog.color.setHex(bg);
    floor.material.map.dispose();
    floor.material.map = floorGridTexture(renderer, FLOOR_SIZE, dark);
    invalidate();
  });

  const bodyMat = new THREE.MeshStandardMaterial({ color: 0xa9a9a9, roughness: 0.75, metalness: 0.0 });
  let actors = [], estimatedGround = null;
  const skeletonToggle = $("optSkeleton"), coneToggle = $("optBoneCones"), skinToggle = $("optSkin");
  const bodySelect = $("bodyVariant");
  const groundAuto = $("optGroundAuto"), groundInput = $("groundHeight"), wireToggle = $("toggleWire");
  let showSkeletonOverlay = localStorage.getItem("flowhmr-skeleton") !== "off";
  const savedSkin = localStorage.getItem("flowhmr-skin");
  let showSkin = savedSkin === null ? localStorage.getItem("flowhmr-skeleton-only") !== "on" : savedSkin === "on";
  skeletonToggle.checked = showSkeletonOverlay;
  coneToggle.checked = localStorage.getItem("flowhmr-bone-cones") === "on";
  const state = {
    jobId: null, meta: null, scene: null, bodyVariant: "neutral", frame: 0, playing: false, speed: 1, lastT: 0, acc: 0,
    prevRoot: new THREE.Vector3(),
  };
  const poseMenu = $("characterPoseMenu");
  const poseEditor = initPoseEditor((entry, settings) => {
    character.editPose(entry, settings);
    drawActorFrame(entry.actor, state.frame);
    renderer.shadowMap.needsUpdate = true;
    invalidate();
  }, (id) => character.focus(id));
  poseMenu.addEventListener("toggle", () => syncSkeletonDisplay());
  const character = initPreviewCharacter(() => actors, () => {
    poseEditor.setActors(actors, character.focused?.id);
    syncSkeletonDisplay();
    if (state.meta && state.frame >= 0) updateFrameRoot(state.frame);
    labelBody();
  }, (error) => setText($("viewportObject"), error));

  function updateOrthographic() {
    const halfHeight = camera.position.distanceTo(controls.target) * Math.tan(THREE.MathUtils.degToRad(perspective.fov / 2));
    const halfWidth = halfHeight * perspective.aspect;
    Object.assign(orthographic, { left: -halfWidth, right: halfWidth, top: halfHeight, bottom: -halfHeight });
    orthographic.updateProjectionMatrix();
  }

  function resize() {
    const w = container.clientWidth, h = container.clientHeight;
    renderer.setSize(w, h, false);
    perspective.aspect = Math.max(w, 1) / Math.max(h, 1);
    perspective.updateProjectionMatrix();
    updateOrthographic();
    invalidate();
  }
  new ResizeObserver(resize).observe(container);
  resize();

  const views = {
    front: new THREE.Vector3(0, 0, 1),
    back: new THREE.Vector3(0, 0, -1),
    right: new THREE.Vector3(1, 0, 0),
    left: new THREE.Vector3(-1, 0, 0),
    top: new THREE.Vector3(0, 1, 0),
    bottom: new THREE.Vector3(0, -1, 0),
  };
  const gizmo = $("viewGizmo");
  const gizmoButtons = [...gizmo.querySelectorAll("[data-view]")];
  const inverseView = new THREE.Quaternion();
  const projected = new THREE.Vector3();
  function updateGizmo() {
    inverseView.copy(camera.quaternion).invert();
    for (const button of gizmoButtons) {
      projected.copy(views[button.dataset.view]).applyQuaternion(inverseView);
      const x = 46 + projected.x * 28;
      const y = 46 - projected.y * 28;
      button.style.left = `${x}px`;
      button.style.top = `${y}px`;
      button.style.zIndex = Math.round(10 + projected.z * 9);
      button.style.opacity = projected.z < -0.28 ? "0.62" : "1";
      if (button.textContent) {
        const line = gizmo.querySelector(`line.gizmo-${button.textContent.toLowerCase()}`);
        line.setAttribute("x2", x);
        line.setAttribute("y2", y);
      }
    }
  }
  controls.addEventListener("change", () => { updateGizmo(); invalidate(); });
  controls.addEventListener("start", () => {
    setText($("viewportName"), m(camera === orthographic ? "view.user_orthographic" : "view.user_perspective"));
  });
  controls.update();
  updateGizmo();

  function setProjection(next) {
    if (next !== camera) {
      next.position.copy(camera.position);
      next.quaternion.copy(camera.quaternion);
      controls.object = camera = next;
    }
    updateOrthographic();
    controls.update();
    updateGizmo();
    setText($("viewportName"), m(camera === orthographic ? "view.user_orthographic" : "view.user_perspective"));
    invalidate();
  }
  function clearOrbitInertia() {
    const damping = controls.enableDamping;
    controls.enableDamping = false;
    controls.update();
    controls.enableDamping = damping;
  }
  function setView(name) {
    const direction = views[name];
    clearOrbitInertia();
    setProjection(orthographic);
    const distance = camera.position.distanceTo(controls.target);
    camera.position.copy(controls.target).addScaledVector(direction, distance);
    controls.update();
    updateOrthographic();
    updateGizmo();
    zoom.target = distance;
    setText($("viewportName"), m("view.orthographic_direction", { direction: m(`view.${name}`) }));
  }
  function toggleProjection() {
    setProjection(camera === perspective ? orthographic : perspective);
  }
  $("toggleProjection").onclick = toggleProjection;
  document.querySelectorAll("[data-view]").forEach((button) => {
    button.onclick = () => setView(button.dataset.view);
  });
  const mouseTools = [...document.querySelectorAll("[data-mouse-mode]")];
  function setMouseMode(mode) {
    const pan = mode === "pan";
    controls.mouseButtons.LEFT = pan ? THREE.MOUSE.PAN : THREE.MOUSE.ROTATE;
    controls.mouseButtons.MIDDLE = pan ? THREE.MOUSE.ROTATE : THREE.MOUSE.PAN;
    controls.mouseButtons.RIGHT = pan ? THREE.MOUSE.ROTATE : THREE.MOUSE.PAN;
    for (const button of mouseTools) button.setAttribute("aria-pressed", String(button.dataset.mouseMode === mode));
    setText($("viewControlHint"), m(pan ? "view.pan_status" : "view.rotate_status"));
  }
  for (const tool of mouseTools) {
    tool.onclick = () => {
      const mode = tool.dataset.mouseMode;
      setMouseMode(mode);
      localStorage.setItem(MOUSE_MODE_STORAGE, mode);
    };
  }
  setMouseMode(localStorage.getItem(MOUSE_MODE_STORAGE) === "rotate" ? "rotate" : "pan");
  $("toggleGrid").onclick = (event) => {
    floor.visible = axisLines.visible = !floor.visible;
    shadowPlane.visible = floor.visible && (!actors.length || actors.some(({ body, character }) => (character?.mesh ?? body).visible));
    event.currentTarget.setAttribute("aria-pressed", String(floor.visible));
    invalidate();
  };
  function syncCharacterWire(root) {
    root?.traverse((object) => {
      if (!object.isMesh) return;
      for (const material of [].concat(object.material)) {
        if (!material || material.wireframe === bodyMat.wireframe) continue;
        material.wireframe = bodyMat.wireframe;
        material.needsUpdate = true;
      }
    });
  }

  wireToggle.onclick = (event) => {
    bodyMat.wireframe = !bodyMat.wireframe;
    bodyMat.needsUpdate = true;
    for (const { body, character } of actors) {
      body.material.wireframe = bodyMat.wireframe;
      body.material.needsUpdate = true;
      syncCharacterWire(character?.mesh);
    }
    event.currentTarget.setAttribute("aria-pressed", String(bodyMat.wireframe));
    setText($("viewportMode"), m(bodyMat.wireframe ? "view.wire_preview" : "view.solid"));
    renderer.shadowMap.needsUpdate = true;
    invalidate();
  };

  function removeActors() {
    character.releaseActors(actors);
    for (const actor of actors) {
      removeSkeleton(actor);
      scene.remove(actor.group);
      actor.body.geometry.dispose();
      if (actor.body.material !== bodyMat) actor.body.material.dispose();
    }
    actors = [];
    show("previewCharacter", false);
    poseEditor.setActors([]);
    window.dispatchEvent(new Event("flowhmr:characterchange"));
  }

  function setGround(height) {
    floor.position.y = axisLines.position.y = height;
    shadowPlane.position.y = height + 0.001;
    invalidate();
  }

  function syncGround() {
    groundInput.disabled = groundAuto.checked;
    if (groundAuto.checked) {
      const height = estimatedGround?.height_m ?? 0;
      setGround(height);
      groundInput.value = height.toFixed(3);
      const source = ["correction", "shared", "contacts", "soles"].includes(estimatedGround?.source)
        ? estimatedGround.source : "unavailable";
      setText($("groundSource"), m(`view.ground_${source}`));
    } else {
      setText($("groundSource"), estimatedGround
        ? m("view.ground_reference", { height: estimatedGround.height_m.toFixed(3) }) : m("view.ground_manual"));
    }
  }
  groundAuto.addEventListener("change", syncGround);
  groundInput.addEventListener("input", () => {
    if (!groundAuto.checked && Number.isFinite(Math.fround(groundInput.valueAsNumber))) {
      setGround(groundInput.valueAsNumber);
    }
  });

  function removeSkeleton(actor) {
    const { skeleton } = actor;
    if (!skeleton) return;
    actor.group.remove(skeleton.group);
    for (const item of skeleton.group.children) {
      item.dispose?.();
      item.geometry.dispose();
      item.material.dispose();
    }
    actor.skeleton = null;
  }

  function makeBoneCones(count, xray) {
    // Square shoulder close to the head and a long tapered tail, as in
    // Blender's Octahedral display. The bone's local Z runs from head to tail.
    const points = [
      [0, 0, 0], [-.1, -.1, .1], [.1, -.1, .1], [.1, .1, .1], [-.1, .1, .1], [0, 0, 1],
    ];
    const faces = [[0, 2, 1], [0, 3, 2], [0, 4, 3], [0, 1, 4], [5, 1, 2], [5, 2, 3], [5, 3, 4], [5, 4, 1]];
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute("position", new THREE.Float32BufferAttribute(faces.flatMap((face) => face.flatMap((i) => points[i])), 3));
    geometry.setAttribute("boneBarycentric", new THREE.Float32BufferAttribute(faces.flatMap(() => [1, 0, 0, 0, 1, 0, 0, 0, 1]), 3));
    geometry.computeVertexNormals();
    const material = new THREE.MeshStandardMaterial({ color: 0xb2b2b2, roughness: 0.88, ...xray });
    material.onBeforeCompile = (shader) => {
      // Shade a thin contour on each visible face in the same draw call.
      shader.uniforms.boneEdgeColor = { value: new THREE.Color(0x27282b) };
      shader.vertexShader = "attribute vec3 boneBarycentric; varying vec3 vBoneBarycentric;\n" + shader.vertexShader
        .replace("#include <begin_vertex>", "#include <begin_vertex>\nvBoneBarycentric = boneBarycentric;");
      shader.fragmentShader = "uniform vec3 boneEdgeColor; varying vec3 vBoneBarycentric;\n" + shader.fragmentShader
        .replace("#include <opaque_fragment>", `
          vec3 pixelWidth = max(fwidth(vBoneBarycentric), vec3(0.0001));
          vec3 edgeFade = smoothstep(vec3(0.06), vec3(0.96), vBoneBarycentric / pixelWidth);
          outgoingLight = mix(boneEdgeColor, outgoingLight, min(edgeFade.x, min(edgeFade.y, edgeFade.z)));
          #include <opaque_fragment>
        `);
    };
    const cones = new THREE.InstancedMesh(geometry, material, count);
    cones.name = "skeleton-cones";
    cones.instanceMatrix.setUsage(THREE.DynamicDrawUsage);
    cones.frustumCulled = false;
    cones.renderOrder = 4;
    return cones;
  }

  const boneAxis = new THREE.Vector3(0, 0, 1), boneHead = new THREE.Vector3(), boneDirection = new THREE.Vector3();
  const boneScale = new THREE.Vector3(), boneRotation = new THREE.Quaternion(), boneMatrix = new THREE.Matrix4();
  const inverseActor = new THREE.Matrix4();
  function drawSkeletonFrame(actor, frame) {
    const { skeleton } = actor;
    if (!skeleton?.group.visible) return;
    const { points, lines, cones, stride, joints, edges, parents } = skeleton;
    if (poseMenu.open && actor.character) {
      const { asset, mesh, rig } = actor.character;
      const bones = asset.kind === "pmx" ? mesh.skeleton.bones : asset.getRig(mesh).bones;
      actor.group.updateWorldMatrix(true, false);
      mesh.updateMatrixWorld(true);
      inverseActor.copy(actor.group.matrixWorld).invert();
      for (let j = 0; j < parents.length; j++) {
        const bone = bones[rig.sourceToTarget[j]];
        if (bone) boneHead.setFromMatrixPosition(bone.matrixWorld).applyMatrix4(inverseActor).toArray(points.array, j * 3);
        else if (parents[j] >= 0) points.array.copyWithin(j * 3, parents[j] * 3, parents[j] * 3 + 3);
      }
    } else points.array.set(joints.subarray(frame * stride, (frame + 1) * stride));
    points.needsUpdate = true;
    if (cones.visible) {
      let count = 0;
      for (let i = 0; i < edges.length; i += 2) {
        boneHead.fromArray(points.array, edges[i] * 3);
        boneDirection.fromArray(points.array, edges[i + 1] * 3).sub(boneHead);
        const length = boneDirection.length();
        if (!Number.isFinite(length) || length < 1e-6) continue;
        boneScale.setScalar(length);
        boneRotation.setFromUnitVectors(boneAxis, boneDirection.divideScalar(length));
        cones.setMatrixAt(count++, boneMatrix.compose(boneHead, boneRotation, boneScale));
      }
      cones.count = count;
      cones.instanceMatrix.needsUpdate = true;
    } else {
      for (let i = 0; i < edges.length; i++) {
        const from = edges[i] * 3;
        lines.array.set(points.array.subarray(from, from + 3), i * 3);
      }
      lines.needsUpdate = true;
    }
  }

  function syncSkeletonDisplay() {
    syncCharacterLighting();
    const ready = actors.some((a) => a.skeleton);
    const only = ready && !showSkin;
    const visible = ready && (only || showSkeletonOverlay);
    skeletonToggle.checked = only || showSkeletonOverlay;
    skeletonToggle.disabled = !ready || only;
    skinToggle.checked = !only;
    skinToggle.disabled = !ready;
    coneToggle.disabled = !visible;
    shadowPlane.visible = floor.visible && !only;
    wireToggle.disabled = only;
    setText($("viewportMode"), m(only ? "view.skeleton_only" : bodyMat.wireframe ? "view.wire_preview" : "view.solid"));
    setAttr($("skeletonOverlayLabel"), "title", m(character.active
      ? poseMenu.open ? "pose.target_overlay" : "character.skeleton" : "view.skeleton_hint"));
    for (const actor of actors) {
      const mesh = actor.character?.mesh;
      actor.body.visible = !mesh && (!only || !actor.skeleton);
      if (mesh) {
        mesh.visible = !only || !actor.skeleton;
        syncCharacterWire(mesh);
      }
      if (actor.skeleton) {
        const { group, bones, cones, jointMarkers } = actor.skeleton;
        group.visible = visible;
        bones.visible = !coneToggle.checked;
        jointMarkers.visible = !coneToggle.checked;
        cones.visible = coneToggle.checked;
        cones.material.depthTest = cones.material.depthWrite = only;
      }
      drawActorFrame(actor, state.frame);
    }
    renderer.shadowMap.needsUpdate = true;
    invalidate();
  }

  function setInspection(actor, info) {
    if (!state.scene) {
      if (info && Number.isFinite(info.ground?.height_m)) estimatedGround = info.ground;
      syncGround();
    }
    const parents = info?.parents;
    if (!Array.isArray(parents) || info.frames !== actor.meta.num_frames || parents[0] !== -1
        || !parents.slice(1).every((parent, i) => Number.isInteger(parent) && parent >= 0 && parent <= i)
        || info.joints.length !== info.frames * parents.length * 3) return;
    removeSkeleton(actor);
    const edges = parents.slice(1).flatMap((parent, i) => [parent, i + 1]);
    const stride = parents.length * 3;
    const attribute = (length) => new THREE.BufferAttribute(new Float32Array(length), 3).setUsage(THREE.DynamicDrawUsage);
    const points = attribute(stride), lines = attribute(edges.length * 3);
    const pointsGeometry = new THREE.BufferGeometry().setAttribute("position", points);
    const linesGeometry = new THREE.BufferGeometry().setAttribute("position", lines);
    const xray = { depthTest: false, depthWrite: false };
    const bones = new THREE.LineSegments(linesGeometry, new THREE.LineBasicMaterial({ color: 0xffb860, ...xray }));
    const joints = new THREE.Points(pointsGeometry, new THREE.PointsMaterial({ color: 0xffe8a9, size: 4, sizeAttenuation: false, ...xray }));
    const cones = makeBoneCones(edges.length / 2, xray);
    bones.name = "skeleton-bones"; joints.name = "skeleton-joints";
    bones.renderOrder = 4; joints.renderOrder = 5;
    bones.frustumCulled = joints.frustumCulled = false;
    const group = new THREE.Group();
    group.add(bones, joints, cones);
    actor.group.add(group);
    actor.skeleton = { group, points, lines, bones, cones, jointMarkers: joints, stride, joints: info.joints, edges, parents };
    syncSkeletonDisplay();
  }
  skeletonToggle.addEventListener("change", () => {
    showSkeletonOverlay = skeletonToggle.checked;
    localStorage.setItem("flowhmr-skeleton", showSkeletonOverlay ? "on" : "off");
    syncSkeletonDisplay();
  });
  coneToggle.addEventListener("change", () => {
    localStorage.setItem("flowhmr-bone-cones", coneToggle.checked ? "on" : "off");
    syncSkeletonDisplay();
  });
  skinToggle.addEventListener("change", () => {
    showSkin = skinToggle.checked;
    localStorage.setItem("flowhmr-skin", showSkin ? "on" : "off");
    syncSkeletonDisplay();
  });

  const sourceStart = (meta) => meta.track_start ?? 0;
  const actorFrame = (actor, frame) => sourceStart(state.meta) + frame - sourceStart(actor.meta);

  function frameRoot(f) {
    const root = new THREE.Vector3();
    for (const actor of actors) {
      const i = Math.min(actor.meta.num_frames - 1, Math.max(0, actorFrame(actor, f)));
      let [x, z] = actor.meta.root_trajectory[i];
      const scale = actor.character?.rig.rootMotionScale ?? 1;
      if (scale !== 1) {
        const [startX, startZ] = actor.meta.root_trajectory[0];
        x = startX + (x - startX) * scale;
        z = startZ + (z - startZ) * scale;
      }
      root.x += x * actor.cos + z * actor.sin + actor.group.position.x;
      root.z += z * actor.cos - x * actor.sin + actor.group.position.z;
    }
    return root.divideScalar(Math.max(1, actors.length));
  }

  function drawActorFrame(actor, globalFrame) {
    const { body, group, meta, verts } = actor;
    const f = actorFrame(actor, globalFrame);
    group.visible = globalFrame >= 0 && f >= 0 && f < meta.num_frames;
    if (!group.visible) return;
    if (actor.character) {
      if (actor.character.mesh.visible || (poseMenu.open && actor.skeleton?.group.visible)) actor.character.rig.apply(f);
      drawSkeletonFrame(actor, f);
      return;
    }
    drawSkeletonFrame(actor, f);
    if (!body.visible) return;
    const pos = body.geometry.attributes.position;
    const n = pos.array.length;
    const offset = f * n;
    for (let i = 0; i < n; i++) pos.array[i] = fromHalf[verts[offset + i]];
    pos.needsUpdate = true;
    body.geometry.computeVertexNormals();
  }

  function updateFrameRoot(f) {
    const root = frameRoot(f);
    sun.position.copy(root).add(SUN_OFFSET);
    sun.target.position.copy(root);
    if ($("optFollow").checked) {
      const delta = root.clone().sub(state.prevRoot);
      camera.position.add(delta);
      controls.target.add(delta);
    }
    state.prevRoot.copy(root);
  }

  function setFrame(f) {
    const m = state.meta;
    if (!m || !actors.length) return;
    f = Math.max(0, Math.min(m.num_frames - 1, f));
    if (f === state.frame) return;
    state.frame = f;
    for (const actor of actors) drawActorFrame(actor, f);
    updateFrameRoot(f);
    scrub.value = f;
    const displayFrame = (state.scene ? sourceStart(m) : 0) + f + 1;
    $("frameLabel").textContent = `${displayFrame} / ${(state.scene ? sourceStart(m) : 0) + m.num_frames}`;
    $("playhead").textContent = displayFrame;
    const position = f / Math.max(1, m.num_frames - 1);
    $("timelineTrack").style.setProperty("--playhead", `calc(${position * 100}% + ${12 - 24 * position}px)`);
    renderer.shadowMap.needsUpdate = true;
    invalidate();
  }

  const frameButtons = [...document.querySelectorAll("[data-frame-action]")];
  function setTimeline(frameCount) {
    const ready = frameCount > 0;
    scrub.max = Math.max(0, frameCount - 1);
    scrub.value = 0;
    scrub.disabled = $("btnPlay").disabled = $("speed").disabled = !ready;
    for (const button of frameButtons) button.disabled = !ready;
    $("timelineTrack").classList.toggle("has-motion", ready);
    const start = ready && state.scene ? sourceStart(state.meta) + 1 : 1;
    $("timelineRange").textContent = ready ? `${start}–${start + frameCount - 1} · ${state.meta.fps} FPS` : "30 FPS";
    const tickCount = Math.min(5, frameCount);
    const ticks = Array.from({ length: tickCount }, (_, i) => {
      const tick = document.createElement("span");
      tick.textContent = Math.round(i * (frameCount - 1) / Math.max(1, tickCount - 1)) + start;
      return tick;
    });
    $("timelineTicks").replaceChildren(...ticks);
    if (!ready) $("frameLabel").textContent = "– / –";
  }

  function frameView(direction) {
    const m = state.meta;
    const root = m ? frameRoot(state.frame) : new THREE.Vector3();
    const height = m ? Math.max(1.2, m.bbox_max[1] - m.bbox_min[1]) : defaultTarget.y * 2;
    const width = state.scene ? Math.hypot(m.bbox_max[0] - m.bbox_min[0], m.bbox_max[2] - m.bbox_min[2]) : 0;
    const distance = m ? Math.max(4, height * 2.4, width * 1.7) : frontOffset.length();
    controls.target.set(root.x, height * 0.5, root.z);
    camera.position.copy(controls.target).addScaledVector(direction, distance);
    zoom.target = camera.position.distanceTo(controls.target);
    state.prevRoot.copy(root);
    controls.update();
    updateOrthographic();
    invalidate();
  }

  function resetView() {
    frameView(camera.position.clone().sub(controls.target).normalize());
  }

  function frameDefaultView() {
    clearOrbitInertia();
    setProjection(perspective);
    frameView(frontDirection);
  }

  // On loop: move camera + target back with the body, keeping the user's zoom and angle.
  function recenterOnFrame(f) {
    const root = frameRoot(f);
    const delta = root.clone().sub(state.prevRoot);
    camera.position.add(delta);
    controls.target.add(delta);
    state.prevRoot.copy(root);
  }

  // ---------------------------------------------------------------- video sync
  const syncToggle = $("optSync");
  const media = { time: NaN, lastMove: 0, retried: false, fallback: false, play: null };
  const mediaEnabled = () => state.meta && syncToggle.checked && !!video.getAttribute("src");
  const resetMediaWatch = () => { media.time = NaN; media.lastMove = performance.now(); };
  const videoTimeFor = (f) => clock.timeOf((state.meta.track_start || 0) + f + 0.5, state.meta.fps);
  const videoFrame = () => clock.frameAt(video.currentTime, state.meta.fps) - (state.meta.track_start || 0);
  function playVideo() {
    if (!video.paused || media.play) return;
    const pending = media.play = video.play();
    pending.catch(() => {}).finally(() => { if (media.play === pending) media.play = null; });
  }

  function seekVideo(frame) {
    const time = videoTimeFor(frame);
    video.currentTime = Number.isFinite(video.duration) ? Math.min(time, video.duration) : time;
    resetMediaWatch();
  }

  function syncVideo(force) {
    if (!mediaEnabled() || media.fallback || video.readyState < 1 || !clock.ready) return;
    const different = Math.abs(video.currentTime - videoTimeFor(state.frame));
    if (state.playing) {
      video.playbackRate = state.speed;
      if (force || different > 0.15) seekVideo(state.frame);
      playVideo();
    } else {
      if (!video.paused) video.pause();
      if (force || different > 0.02) seekVideo(state.frame);
    }
  }

  function resetMediaSync() {
    if (media.fallback) preview.showPreviewIssue("");
    media.fallback = media.retried = false;
    resetMediaWatch();
  }

  function setPlaying(p, driveVideo = true) {
    if (state.playing !== p) {
      state.acc = state.lastT = 0;
      resetMediaWatch();
    }
    state.playing = p;
    $("iconPlay").classList.toggle("hidden", p);
    $("iconPause").classList.toggle("hidden", !p);
    if (driveVideo) syncVideo(true);
    invalidate();
  }

  video.addEventListener("flowhmr:sourcechanging", ({ detail }) => {
    media.play = null;
    if (!detail.retry) resetMediaSync(); else resetMediaWatch();
  });
  video.addEventListener("flowhmr:clockready", () => syncVideo(true));
  // Browsers without requestVideoFrameCallback use the zero-origin clock.
  video.addEventListener("loadedmetadata", () => syncVideo(true));
  video.addEventListener("play", () => {
    if (!mediaEnabled() || !clock.ready || video.paused || state.playing) return;
    resetMediaSync();
    const next = videoFrame();
    if (next < 0 || next >= state.meta.num_frames) {
      setFrame(0);
      seekVideo(0);
    } else setFrame(next);
    video.playbackRate = state.speed;
    setPlaying(true, false);
  });
  video.addEventListener("pause", () => {
    if (!mediaEnabled() || media.fallback || !video.paused || video.error || video.ended
        || !state.playing || !clock.ready) return;
    setFrame(videoFrame());
    setPlaying(false, false);
  });
  video.addEventListener("seeked", () => {
    if (mediaEnabled() && !state.playing && clock.ready) setFrame(videoFrame());
  });

  $("btnPlay").onclick = () => {
    if (!state.meta) return;
    if (!state.playing) {
      resetMediaSync();
      if (state.frame >= state.meta.num_frames - 1) setFrame(0);
    }
    setPlaying(!state.playing);
  };
  function jumpTo(frame) {
    if (!state.meta) return;
    setFrame(frame);
    setPlaying(false);
  }
  scrub.addEventListener("input", (e) => jumpTo(Number(e.target.value)));
  for (const button of frameButtons) {
    button.onclick = () => jumpTo({
      first: 0, previous: state.frame - 1, next: state.frame + 1, last: state.meta.num_frames - 1,
    }[button.dataset.frameAction]);
  }
  const speedSelect = $("speed");
  const savedSpeed = localStorage.getItem("flowhmr-playback-speed");
  if ([...speedSelect.options].some((option) => option.value === savedSpeed)) {
    speedSelect.value = savedSpeed;
    state.speed = Number(savedSpeed);
  }
  speedSelect.onchange = () => {
    state.speed = Number(speedSelect.value);
    localStorage.setItem("flowhmr-playback-speed", speedSelect.value);
    syncVideo(true);
  };
  $("btnReset").onclick = resetView;
  rememberToggle($("optFollow"), "flowhmr-follow");
  rememberToggle(syncToggle, "flowhmr-sync-video", (enabled) => {
    resetMediaSync();
    if (enabled) syncVideo(true); else video.pause();
  });
  const audioToggle = $("optAudio");
  const saveAudio = rememberToggle(audioToggle, "flowhmr-original-audio", (enabled) => { video.muted = !enabled; });
  video.muted = !audioToggle.checked;
  video.addEventListener("volumechange", () => {
    // Person selection mutes its looping preview temporarily.
    if (preview.temporaryMute) return;
    audioToggle.checked = !video.muted;
    saveAudio();
  });
  window.addEventListener("keydown", (e) => {
    const target = e.target;
    if (e.defaultPrevented || e.isComposing || !(target instanceof Element)) return;
    if (e.code === "Space") {
      const timelineControl = target.matches('.timeline-editor input[type="range"], #controls .transport, #btnPlay');
      if (!state.meta || e.altKey || e.ctrlKey || e.metaKey || e.shiftKey || target.isContentEditable
          || document.querySelector("dialog[open]")
          || (!timelineControl && target.closest("input, select, textarea, button, summary, a[href]"))) return;
      e.preventDefault();
      if (!e.repeat) $("btnPlay").click();
      return;
    }
    if (target.isContentEditable || ["INPUT", "SELECT", "TEXTAREA"].includes(target.tagName)) return;
    if (e.code === "Numpad1") { e.preventDefault(); setView(e.ctrlKey ? "back" : "front"); }
    if (e.code === "Numpad3") { e.preventDefault(); setView(e.ctrlKey ? "left" : "right"); }
    if (e.code === "Numpad7") { e.preventDefault(); setView(e.ctrlKey ? "bottom" : "top"); }
    if (e.code === "Numpad5") { e.preventDefault(); toggleProjection(); }
    if (e.code === "Home") { e.preventDefault(); resetView(); }
    if (!state.meta) return;
    if (e.code === "ArrowRight") { e.preventDefault(); jumpTo(state.frame + 1); }
    if (e.code === "ArrowLeft") { e.preventDefault(); jumpTo(state.frame - 1); }
  });

  function tickMedia(t) {
    if (clock.ready && video.readyState >= 1 && !video.seeking && (!video.paused || video.ended)) {
      const time = video.currentTime;
      if (!Number.isFinite(media.time) || Math.abs(media.time - time) > .001) {
        media.time = time;
        media.lastMove = t;
      }
      const next = videoFrame();
      if (video.ended || next >= state.meta.num_frames) {
        if ($("optFollow").checked) recenterOnFrame(0);
        setFrame(0);
        seekVideo(0);
        playVideo();
        return;
      }
      if (next >= 0 && next !== state.frame) setFrame(next);
    }
    // A stalled decoder can report paused=false indefinitely. Give the same
    // source one fresh load at the current frame, then keep the motion usable.
    if (t - media.lastMove < 1200) return;
    if (!media.retried && preview.retryPlayback()) {
      media.retried = true;
      return;
    }
    media.fallback = true;
    video.pause();
    preview.showPreviewIssue(m("source.sync_stalled"));
  }

  function tick(t) {
    renderRequest = 0;
    const dt = state.lastT ? (t - state.lastT) / 1000 : 0;
    state.lastT = t;
    if (state.playing && state.meta) {
      if (mediaEnabled() && !media.fallback) tickMedia(t);
      else {
        state.acc += dt * state.meta.fps * state.speed;
        if (state.acc >= 1) {
          const steps = Math.floor(state.acc);
          state.acc -= steps;
          let next = state.frame + steps;
          if (next >= state.meta.num_frames) {
            next = 0;
            if ($("optFollow").checked) recenterOnFrame(0);
          }
          setFrame(next);
        }
      }
    }
    const zooming = applyZoom();
    const orbiting = controls.update();
    if (dirty) {
      dirty = false;
      const d = camera.position.distanceTo(controls.target);
      scene.fog.near = d + 6;
      scene.fog.far = d + 30;
      renderer.render(scene, camera);
    }
    if (state.playing || zooming || orbiting) {
      if (!renderRequest) renderRequest = requestAnimationFrame(tick);
    } else state.lastT = 0;
  }

  // ---------------------------------------------------------------- public API
  function labelBody() {
    const custom = actors.filter((actor) => actor.character).length;
    const asset = character.active;
    setText($("viewportObject"), () => [
      t("view.scene"),
      [state.scene ? t("person.count", { count: actors.length }) : "",
        ...(custom > 1 ? [t("character.custom_count", { count: custom })]
          : asset ? [({ mixamo: "Mixamo", metahuman: "MetaHuman", vrm: "VRM" })[asset.profile] ?? "PMX", asset.name]
            : ["SMPL-H", t(`view.${state.bodyVariant}`)])].filter(Boolean).join(" · "),
    ].join(" | "));
  }

  async function loadActor(person, variant, signal) {
    const [meta, vertices, faces] = await Promise.all([
      api.getMeta(person.id, signal), api.getVertices(person.id, signal, variant), api.getFaces(person.id, signal),
    ]);
    if (!meta.num_frames || vertices.byteLength !== meta.num_frames * meta.num_vertices * 6
        || faces.byteLength !== meta.num_faces * 12 || (person.start != null &&
          (sourceStart(meta) !== person.start || person.end - person.start + 1 !== meta.num_frames))) {
      throw appError("view.mesh_incomplete");
    }
    return { person, meta, vertices, faces };
  }

  function addActor({ person, meta, vertices, faces }, multiple) {
    const geometry = new THREE.BufferGeometry();
    for (const key of ["position", "normal"]) {
      geometry.setAttribute(key, new THREE.BufferAttribute(new Float32Array(meta.num_vertices * 3), 3).setUsage(THREE.DynamicDrawUsage));
    }
    geometry.setIndex(new THREE.BufferAttribute(new Uint32Array(faces), 1));
    const material = multiple ? bodyMat.clone() : bodyMat;
    if (multiple) material.color.lerp(new THREE.Color(personColor(person.person - 1)), 0.28);
    const body = new THREE.Mesh(geometry, material);
    body.name = "motion-skin";
    body.castShadow = true;
    body.frustumCulled = false;
    const group = new THREE.Group();
    const yaw = person.yaw ?? 0;
    group.rotation.y = yaw;
    group.position.fromArray(person.offset ?? [0, 0, 0]);
    group.name = multiple ? `person-${person.person}` : "motion-actor";
    group.add(body);
    scene.add(group);
    return { id: person.id, number: person.person, meta, verts: new Uint16Array(vertices), body, group, skeleton: null, character: null,
      cos: Math.cos(yaw), sin: Math.sin(yaw) };
  }

  function groupBounds() {
    const bounds = new THREE.Box3();
    for (const { meta, group } of actors) {
      group.updateMatrix();
      const { bbox_min: a, bbox_max: b } = meta;
      for (const x of [a[0], b[0]]) for (const y of [a[1], b[1]]) for (const z of [a[2], b[2]]) {
        bounds.expandByPoint(new THREE.Vector3(x, y, z).applyMatrix4(group.matrix));
      }
    }
    return { bbox_min: bounds.min.toArray(), bbox_max: bounds.max.toArray() };
  }

  bodySelect.addEventListener("change", async () => {
    const selected = bodySelect.value;
    if (!state.meta) {
      localStorage.setItem(BODY_STORAGE, selected);
      return;
    }
    bodyController?.abort();
    const change = bodyController = new AbortController();
    const jobId = state.jobId;
    bodySelect.disabled = true;
    setText($("viewportObject"), m("view.body_loading", { body: m(`view.${selected}`) }));
    try {
      const vertices = await Promise.all(actors.map((a) => api.getVertices(a.id, change.signal, selected)));
      if (change.signal.aborted || state.jobId !== jobId) return;
      if (vertices.some((data, i) => data.byteLength !== actors[i].meta.num_frames * actors[i].meta.num_vertices * 6)) {
        throw appError("view.body_incomplete");
      }
      vertices.forEach((data, i) => { actors[i].verts = new Uint16Array(data); });
      state.bodyVariant = selected;
      localStorage.setItem(BODY_STORAGE, selected);
      for (const actor of actors) drawActorFrame(actor, state.frame);
      invalidate();
      labelBody();
    } catch (error) {
      if (change.signal.aborted || state.jobId !== jobId) return;
      bodySelect.value = state.bodyVariant;
      setText($("viewportObject"), m("view.body_failed", { detail: error }));
    } finally {
      if (bodyController === change) { bodyController = null; bodySelect.disabled = false; }
    }
  });

  return {
    openCharacter: character.open,
    getPmxActors(people) {
      const selected = new Set(people.map(String));
      const targets = actors.filter((actor) => !state.scene || selected.has(String(actor.number)));
      if ((state.scene && targets.length !== selected.size) ||
          targets.some((actor) => actor.character?.asset.kind !== "pmx")) return [];
      return targets.map((actor) => ({
        person: actor.number,
        start: state.scene ? sourceStart(actor.meta) : 0,
        sceneEnd: state.scene ? sourceStart(state.meta) + state.meta.num_frames - 1 : null,
        fps: actor.meta.fps,
        yaw: actor.group.rotation.y,
        offset: actor.group.position.toArray(),
        asset: actor.character.asset,
        motion: actor.character.motion,
        poseOffsets: actor.character.poseOffsets,
      }));
    },
    setBodyVariants(names = ["neutral"], preferred = "neutral") {
      const available = new Set(names);
      for (const option of bodySelect.options) option.disabled = !available.has(option.value);
      const saved = state.meta ? state.bodyVariant : localStorage.getItem(BODY_STORAGE);
      bodySelect.value = available.has(saved) ? saved : available.has(preferred) ? preferred : names[0] ?? "";
      show("bodyVariantRow", available.size > 1);
      if (state.meta && bodySelect.value && bodySelect.value !== state.bodyVariant) {
        bodySelect.dispatchEvent(new Event("change"));
      }
    },
    get bodyVariant() { return state.meta ? state.bodyVariant : bodySelect.value || "neutral"; },
    seekSourceFrame(frame) {
      const first = state.meta?.track_start ?? 0;
      if (state.meta && frame >= first && frame < first + state.meta.num_frames) jumpTo(frame - first);
      else if (clock.ready && video.readyState >= 1) {
        setPlaying(false, false);
        video.pause();
        video.currentTime = Math.min(video.duration, clock.timeOf(frame + 0.5));
      }
    },
    async load(jobId, groupScene = null) {
      loadController?.abort();
      bodyController?.abort();
      bodyController = null;
      bodySelect.disabled = true;
      const load = loadController = new AbortController();
      const members = groupScene?.people ?? [{ id: jobId }];
      const inspections = members.map((p) => api.getInspection(p.id, load.signal).catch(() => null));
      let variant = bodySelect.value || "source";
      let data;
      try {
        data = await Promise.all(members.map((p) => loadActor(p, variant, load.signal))).catch((error) => {
          if (load.signal.aborted || variant === "source") throw error;
          variant = "source";
          return Promise.all(members.map((p) => loadActor(p, variant, load.signal)));
        });
      } catch (error) {
        if (load.signal.aborted) return null;
        load.abort();
        loadController = null;
        bodySelect.disabled = false;
        throw error;
      }
      if (load.signal.aborted) return null;
      removeActors();
      actors = data.map((entry) => addActor(entry, !!groupScene));
      const meta = groupScene ? { ...groupScene, ...groupBounds() } : actors[0].meta;
      state.jobId = jobId;
      state.meta = meta;
      state.scene = groupScene;
      state.frame = -1;
      state.acc = 0;
      state.lastT = 0;
      resetMediaSync();
      state.bodyVariant = bodySelect.value = variant === "source"
        ? groupScene?.body_reference ?? data[0].meta.body_reference ?? "neutral" : variant;
      bodySelect.disabled = false;
      syncSkeletonDisplay();

      setTimeline(meta.num_frames);
      show("viewerEmpty", false);
      show("viewerTools", true);
      show("groundMenu", true);
      if (groupScene) {
        estimatedGround = { height_m: groupScene.ground_m, source: "shared" };
        syncGround();
      } else setText($("groundSource"), m("view.ground_pending"));
      setText($("resultInfo"), () => [
        groupScene ? t("person.count", { count: actors.length }) : "",
        t("view.duration", { frames: meta.num_frames, seconds: (meta.num_frames / meta.fps).toFixed(1) }),
      ].filter(Boolean).join(" · "));
      setAttr($("resultInfo"), "title", m("timeline.source_span", {
        start: (meta.track_start || 0) + 1, end: (meta.track_start || 0) + meta.num_frames,
      }));
      labelBody();
      setFrame(0);
      frameDefaultView();
      setPlaying(false);
      void character.showForJob();
      void Promise.all(inspections).then((results) => {
        if (!load.signal.aborted && state.jobId === jobId) results.forEach((info, i) => setInspection(actors[i], info));
        if (loadController === load) loadController = null;
      });
      return meta;
    },
    clear() {
      loadController?.abort();
      loadController = null;
      bodyController?.abort();
      bodyController = null;
      bodySelect.disabled = false;
      state.jobId = null; state.meta = null; state.scene = null;
      removeActors();
      syncSkeletonDisplay();
      estimatedGround = null;
      groundAuto.checked = true;
      syncGround();
      $("groundMenu").open = false;
      show("groundMenu", false);
      video.pause();
      setPlaying(false);
      renderer.shadowMap.needsUpdate = true;
      show("viewerEmpty", true);
      show("viewerTools", false);
      show("dlNpz", false);
      show("dlFbx", false);
      $("menuNpz").disabled = $("menuFbx").disabled = true;
      setTimeline(0);
      setText($("viewportObject"), m("view.scene"));
      setText($("resultInfo"), "");
      setAttr($("resultInfo"), "title", "");
      frameDefaultView();
    },
    setStatus(text) { if (!state.meta) setText($("viewerEmpty"), text); },
    get jobId() { return state.jobId; },
  };
}
