// "Settings" card: person selection, camera mode, sampling and post-processing.
import { $, show } from "./utils.js";
import { appError, message as m, setAttr, setText } from "./i18n.js";

const MAX_SMOOTHING_FRAMES = 3;
const validStrength = (value) => Number.isFinite(value) && value >= 0 && value <= 1;
const STORAGE = "flowhmr-generation-preferences";
const CAMERA_MODES = ["static", "auto", "manual"];
const cameraModeFor = (p) => (p.static_camera ?? !(p.vggt_interval > 0)) ? "static" : p.vggt_interval > 0 ? "manual" : "auto";

function bindToggle(id) {
  const btn = $(id);
  btn.addEventListener("click", () => {
    btn.setAttribute("aria-checked", btn.getAttribute("aria-checked") === "true" ? "false" : "true");
  });
  return {
    get: () => btn.getAttribute("aria-checked") === "true",
    set: (v) => btn.setAttribute("aria-checked", v ? "true" : "false"),
  };
}

function samChunkSize(value) {
  if (typeof value === "string" && /^\d+$/.test(value.trim())) value = Number(value.trim());
  if (!Number.isSafeInteger(value) || value < 1) {
    throw appError("server.param_integer", { field: m("field.sam_chunk_size"), min: 1 });
  }
  return value;
}

export function initParams(defaults, manualBatchDefault) {
  const manual = bindToggle("optManual");
  const post = bindToggle("optPostprocess");
  const hamer = bindToggle("optHamer");
  const hamerButton = $("optHamer"), hamerStride = $("optHamerStride");
  const hamerSamplingSupported = defaults?.hamer_stride != null;
  const syncHamer = () => show("hamerSamplingRow", hamerSamplingSupported && !hamerButton.disabled && hamer.get());
  hamerButton.addEventListener("click", syncHamer);
  const soleFitSlider = $("optSoleFitStrength"), soleFit = $("optSoleFitValue");
  const autoBatch = bindToggle("optSamAuto");
  const cameraMode = $("optCameraMode");
  const defaultCamera = cameraModeFor(defaults || {});
  let preferredCamera = null;
  const footLockPreset = $("optFootLockPreset");
  const correctionGround = $("optCorrectionGround"), correctionHeight = $("optCorrectionHeight");
  const seed = $("optSeed"), cfg = $("optCfg"), stride = $("optVggt");
  const steps = $("optSteps"), samChunk = $("optSamChunk");
  const smoothingSlider = $("optSmoothing"), smoothing = $("optSmoothingValue");
  const smoothingHint = $("smoothingHint");
  let restoredSmoothingFrames = null;
  const readSmoothingFrames = () => restoredSmoothingFrames ?? Number((smoothing.valueAsNumber * MAX_SMOOTHING_FRAMES).toPrecision(15));
  function syncSmoothingSlider(legacyFrames = 0) {
    const value = smoothing.valueAsNumber;
    if (!validStrength(value)) return;
    smoothingSlider.value = value;
    const frames = Number(readSmoothingFrames().toPrecision(6));
    setAttr(smoothingSlider, "aria-valuetext", value === 0 ? m("common.off") : m("settings.smoothing_aria", { value, frames }));
    setText(smoothingHint, legacyFrames > MAX_SMOOTHING_FRAMES
      ? m("settings.smoothing_old", { frames: legacyFrames, max: MAX_SMOOTHING_FRAMES })
      : value === 0 ? m("settings.smoothing_default_hint") : m("settings.smoothing_frames", { frames }));
  }
  function smoothingEdited() {
    restoredSmoothingFrames = null;
    syncSmoothingSlider();
  }
  smoothingSlider.addEventListener("input", () => {
    smoothing.value = smoothingSlider.value;
    smoothingEdited();
  });
  smoothing.addEventListener("input", smoothingEdited);
  function syncSoleFitSlider() {
    const value = soleFit.valueAsNumber;
    if (!validStrength(value)) return;
    soleFitSlider.value = value;
    setAttr(soleFitSlider, "aria-valuetext", value === 0 ? m("common.off") : soleFitSlider.value);
  }
  soleFitSlider.addEventListener("input", () => {
    soleFit.value = soleFitSlider.value;
    syncSoleFitSlider();
  });
  soleFit.addEventListener("input", syncSoleFitSlider);
  const syncBatch = () => {
    samChunk.disabled = autoBatch.get();
    show("samChunkRow", !samChunk.disabled);
  };
  $("optSamAuto").addEventListener("click", syncBatch);
  const syncCamera = () => {
    stride.disabled = cameraMode.value !== "manual";
    show("manualCameraRow", !stride.disabled);
    setText($("cameraHint"), m(`camera.${cameraMode.value}_hint`));
  };
  cameraMode.addEventListener("change", syncCamera);
  const syncCorrection = () => {
    const enabled = post.get() && !$("optPostprocess").disabled;
    show("footLockRow", enabled);
    show("soleFitRow", enabled);
    show("correctionFloorRow", enabled);
    show("correctionHeightRow", enabled && correctionGround.value === "manual");
    setText($("footLockHint"), m(footLockPreset.value === "strong" ? "correction.strong_hint" : "correction.normal_hint"));
  };
  $("optPostprocess").addEventListener("click", syncCorrection);
  footLockPreset.addEventListener("change", syncCorrection);
  correctionGround.addEventListener("change", syncCorrection);

  const apply = (p, correction, prefs) => {
    const chunkSize = samChunkSize(p.sam_chunk_size ?? prefs?.manualBatch ?? manualBatchDefault);
    manual.set(p.manual_select ?? true);
    post.set(!$("optPostprocess").disabled && !!p.postprocess);
    hamer.set(!hamerButton.disabled && !!p.hamer);
    hamerStride.value = p.hamer_stride ?? 1;
    syncHamer();
    soleFit.value = p.sole_fit_strength ?? (p.sole_fit ? 0.6 : 0);
    syncSoleFitSlider();
    footLockPreset.value = p.correction_preset === "strong" ? "strong" : "normal";
    correctionGround.value = p.correction_floor_m == null ? "auto" : "manual";
    correctionHeight.value = p.correction_floor_m ?? correction?.floor_m?.toFixed(3) ?? 0;
    autoBatch.set(p.sam_chunk_size == null);
    cameraMode.value = cameraModeFor(p);
    seed.value = p.seed ?? 0;
    cfg.value = p.cfg_scale ?? 1.0;
    stride.value = p.vggt_interval > 0 ? p.vggt_interval : prefs?.manualInterval ?? 10;
    steps.value = p.steps ?? 20;
    const savedFrames = Number(p.smoothing_frames ?? 0);
    // Keep the saved frame value until edited, even when its strength is rounded for display.
    restoredSmoothingFrames = Number.isFinite(savedFrames) ? Math.min(MAX_SMOOTHING_FRAMES, Math.max(0, savedFrames)) : 0;
    smoothing.value = Number((restoredSmoothingFrames / MAX_SMOOTHING_FRAMES).toPrecision(4));
    syncSmoothingSlider(savedFrames);
    samChunk.value = chunkSize;
    syncBatch();
    syncCamera();
    syncCorrection();
  };
  const read = (overrides = {}, remember = false) => {
    const strength = soleFit.valueAsNumber;
    if (post.get() && !validStrength(strength)) throw appError("server.param_sole_fit");
    if (!validStrength(smoothing.valueAsNumber)) throw appError("settings.smoothing_invalid");
    const out = {
      manual_select: manual.get(),
      postprocess: post.get(),
      hamer: !hamerButton.disabled && hamer.get(),
      hamer_stride: Number(hamerStride.value),
      correction_preset: footLockPreset.value,
      sole_fit_strength: validStrength(strength) ? strength : 0,
      correction_floor_m: !remember && post.get() && correctionGround.value === "manual" ? correctionHeight.valueAsNumber : null,
      static_camera: cameraMode.value === "static",
      seed: parseInt(seed.value, 10),
      cfg_scale: parseFloat(cfg.value),
      vggt_interval: overrides.vggt_interval ?? (cameraMode.value === "manual" ? Number(stride.value) : 0),
      sam_chunk_size: autoBatch.get() ? null : samChunkSize(samChunk.value),
      steps: parseInt(steps.value, 10),
      smoothing_frames: readSmoothingFrames(),
    };
    if (!Number.isInteger(out.seed) || out.seed < 0 || out.seed > 2147483647) throw appError("server.param_bounds", { field: m("field.seed"), min: 0, max: 2147483647 });
    if (out.correction_floor_m !== null && !Number.isFinite(out.correction_floor_m)) throw appError("server.param_floor");
    if (!Number.isFinite(out.cfg_scale) || out.cfg_scale < 0 || out.cfg_scale > 10) throw appError("server.param_bounds", { field: "CFG", min: 0, max: 10 });
    if (overrides.vggt_interval == null && cameraMode.value === "manual" && (!Number.isInteger(out.vggt_interval) || out.vggt_interval < 1 || out.vggt_interval > 120)) {
      throw appError("server.param_bounds", { field: m("field.vggt_interval"), min: 1, max: 120 });
    }
    if (!Number.isInteger(out.steps) || out.steps < 1 || out.steps > 100) throw appError("server.param_bounds", { field: m("field.steps"), min: 1, max: 100 });
    return out;
  };

  let remembered = null, jobId = null;
  function preferencesForNewSource(prefs) {
    const mode = preferredCamera ?? defaultCamera;
    return { ...defaults, ...prefs.params, correction_floor_m: null, static_camera: mode === "static",
      vggt_interval: mode === "manual" ? (prefs.manualInterval ?? prefs.params.vggt_interval ?? 10) : 0 };
  }
  try {
    const saved = JSON.parse(localStorage.getItem(STORAGE) || "null");
    if (saved?.params && typeof saved.params === "object") {
      // Older versions saved "auto" with every edit, even if nobody selected it.
      const mode = Object.hasOwn(saved, "cameraMode") ? saved.cameraMode : cameraModeFor(saved.params);
      preferredCamera = CAMERA_MODES.includes(mode) && (Object.hasOwn(saved, "cameraMode") || mode !== "auto") ? mode : null;
      apply(preferencesForNewSource(saved), null, saved);
      read({}, true);
      remembered = saved;
    }
  } catch { preferredCamera = null; }
  if (!remembered) apply(defaults || {});

  function rememberSettings() {
    try {
      const { correction_floor_m, ...params } = read({}, true);
      const manualBatch = samChunk.valueAsNumber;
      const manualInterval = stride.valueAsNumber;
      remembered = { jobId, params, cameraMode: preferredCamera,
        manualBatch: Number.isSafeInteger(manualBatch) && manualBatch > 0 ? manualBatch : manualBatchDefault,
        manualInterval: Number.isInteger(manualInterval) && manualInterval >= 1 && manualInterval <= 120 ? manualInterval : 10,
      };
      localStorage.setItem(STORAGE, JSON.stringify(remembered));
    } catch { /* Do not remember an incomplete edit. */ }
  }
  for (const input of [seed, cfg, stride, steps, samChunk, smoothingSlider, smoothing, soleFitSlider, soleFit]) {
    input.addEventListener("input", rememberSettings);
  }
  cameraMode.addEventListener("change", () => {
    preferredCamera = cameraMode.value;
    rememberSettings();
  });
  for (const input of [footLockPreset, hamerStride]) input.addEventListener("change", rememberSettings);
  for (const id of ["optManual", "optPostprocess", "optHamer", "optSamAuto"]) $(id).addEventListener("click", rememberSettings);

  const suggestCorrectionHeight = (report) => {
    if (report?.automatic && correctionGround.value === "auto") correctionHeight.value = report.floor_m.toFixed(3);
  };

  return { read, suggestCorrectionHeight,
    refreshHamer() {
      if (hamerButton.disabled) hamer.set(false);
      syncHamer();
    },
    openJob(id, params, correction, restoreDraft) {
      jobId = id;
      const draft = restoreDraft && remembered?.jobId === id ? remembered : null;
      apply({ ...params, ...draft?.params }, correction, draft);
    },
    reset() { preferredCamera = null; apply(defaults || {}); rememberSettings(); },
    newSource() {
      jobId = null;
      if (remembered) {
        apply(preferencesForNewSource(remembered), null, remembered);
        return;
      }
      // A manual floor belongs to the previous video.
      correctionGround.value = "auto";
      correctionHeight.value = 0;
      cameraMode.value = defaultCamera;
      syncCamera();
      syncCorrection();
    },
  };
}
