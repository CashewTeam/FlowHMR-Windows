// Page controller: wires the cards together and polls the current job.
import { $, show, el, createBus, jobLabel, personColor } from "./utils.js";
import { appError, message as m, setText, setupLocalization } from "./i18n.js";
import * as api from "./api.js";
import { initUpload } from "./upload.js";
import { initParams } from "./params.js";
import { initProgress } from "./progress.js";
import { initPersonSelect } from "./person-select.js";
import { initPreview } from "./preview.js";
import { initHistory } from "./history.js";
import { initViewer } from "./viewer.js";
import { initLayout } from "./layout.js";
import { initTrim } from "./trim.js";
import { initModelDialog } from "./models.js";
import { initRuntimeDialog } from "./runtime.js";

setupLocalization();
const themeOptions = $("themeOptions");
const themeButtons = [...themeOptions.querySelectorAll("[data-theme-choice]")];
const systemTheme = window.matchMedia("(prefers-color-scheme: dark)");
let themePreference = localStorage.getItem("flowhmr-theme-mode");
if (!["light", "dark", "system"].includes(themePreference)) themePreference = "dark";

function setTheme(preference) {
  themePreference = preference;
  const dark = preference === "dark" || (preference === "system" && systemTheme.matches);
  document.documentElement.dataset.theme = dark ? "dark" : "light";
  themeButtons.forEach((button) => {
    button.setAttribute("aria-pressed", String(button.dataset.themeChoice === preference));
  });
  window.dispatchEvent(new CustomEvent("flowhmr:themechange", { detail: { dark } }));
}
setTheme(themePreference);
themeOptions.addEventListener("click", (event) => {
  const button = event.target.closest("[data-theme-choice]");
  if (!button) return;
  setTheme(button.dataset.themeChoice);
  localStorage.setItem("flowhmr-theme-mode", themePreference);
});
systemTheme.addEventListener("change", () => {
  if (themePreference === "system") setTheme("system");
});

const bus = createBus();
initLayout();
let info = null;
let currentJob = null;
let pollTimer = null;
let loadedResult = null;
let paramsShownFor = null;
let restoreParameterDraft = false;
let submitting = false;
let pollVersion = 0;
let lastStatus = null;
let historyStamp = "";
const ACTIVE = new Set(["queued", "running", "waiting_user", "cancel_requested"]);

const upload = initUpload(bus);
const fbxDialog = $("fbxExportDialog");
const fbxWithSkin = $("fbxWithSkin");
const fbxFirstPose = $("fbxFirstPose");
const fbxPeople = $("fbxPeople"), fbxConfirm = $("fbxConfirm");
let exportPeople = [];
const FBX_SKIN_STORAGE = "flowhmr-fbx-with-skin";
const FBX_POSE_STORAGE = "flowhmr-fbx-first-pose";
fbxWithSkin.checked = localStorage.getItem(FBX_SKIN_STORAGE) === "on";
const savedFbxPose = localStorage.getItem(FBX_POSE_STORAGE);
fbxFirstPose.value = ["t", "a"].includes(savedFbxPose) ? savedFbxPose : "none";
function setExportPeople(scene) {
  exportPeople = (scene?.people ?? []).map(({ person }) => {
    const label = el("label", "fbx-export-person");
    const input = el("input");
    const dot = el("span", "person-dot");
    dot.style.background = personColor(person - 1);
    input.type = "checkbox";
    input.value = person;
    input.checked = true;
    label.append(input, dot, el("span", "", m("person.number", { number: person })));
    return { label, input };
  });
  $("fbxPeopleList").replaceChildren(...exportPeople.map(({ label }) => label));
  show(fbxPeople, !!exportPeople.length);
  fbxConfirm.disabled = false;
}
fbxPeople.addEventListener("change", () => { fbxConfirm.disabled = !exportPeople.some(({ input }) => input.checked); });
$("dlFbx").onclick = () => {
  if (!currentJob || loadedResult !== currentJob || fbxDialog.open) return;
  fbxDialog.returnValue = "";
  fbxDialog.showModal();
};
fbxDialog.querySelector("form").addEventListener("submit", (event) => {
  if (event.submitter !== fbxConfirm) return;
  event.preventDefault();
  if (!currentJob || loadedResult !== currentJob) return;
  const people = exportPeople.filter(({ input }) => input.checked).map(({ input }) => input.value);
  if (exportPeople.length && !people.length) return;
  localStorage.setItem(FBX_SKIN_STORAGE, fbxWithSkin.checked ? "on" : "off");
  localStorage.setItem(FBX_POSE_STORAGE, fbxFirstPose.value);
  const link = document.createElement("a");
  link.href = api.fbxUrl(currentJob, fbxWithSkin.checked, viewer.bodyVariant, people, fbxFirstPose.value);
  link.download = "";
  fbxDialog.close("export");
  link.click();
});
for (const [menu, target] of [["menuImport", "fileInput"], ["menuFbx", "dlFbx"], ["menuNpz", "dlNpz"]]) {
  $(menu).onclick = () => $(target).click();
}
document.addEventListener("click", (event) => {
  for (const menu of document.querySelectorAll(".editor-menu[open]")) {
    if (!menu.contains(event.target) || event.target.closest(".editor-menu-popup button")) menu.open = false;
  }
});
const progress = initProgress();
const person = initPersonSelect(bus);
const preview = initPreview(bus);
const viewer = initViewer(preview);
const trim = initTrim({ ...preview, seek: viewer.seekSourceFrame });
const history = initHistory({ onOpen: openJob, onRerun: rerun, onDeleted: clearDeletedJobs });
const runtime = initRuntimeDialog();
let params = null;

function renderModelStatus(loaded, loading = false) {
  const missing = info?.model_files?.items.some((item) => item.blocking);
  $("btnModels").classList.toggle("needs-models", !!missing);
  setText($("modelInfo"), !info ? m("connection.offline") : missing ? m("models.required_missing")
    : loading ? m("models.loading") : loaded ? info.device : m("models.deferred"));
}

function applyModelInfo(next) {
  const variantsChanged = info?.body_variants?.join() !== next?.body_variants?.join();
  info = next;
  renderModelStatus(info?.models_loaded);
  runtime.summarize(info?.runtime);
  const hamerReady = !!info?.hamer_available;
  $("optHamer").disabled = !hamerReady;
  setText($("hamerHint"), m(hamerReady ? "hamer.hint" : "hamer.missing_hint"));
  params?.refreshHamer();
  if (variantsChanged) viewer.setBodyVariants(info?.body_variants, info?.body_reference);
}

initModelDialog(async () => {
  const next = await api.getInfo();
  if (!next.model_files) throw appError("connection.restart");
  applyModelInfo(next);
  return next.model_files;
});

// ------------------------------------------------------------------ boot
try {
  applyModelInfo(await api.getInfo());
  setText($("uploadHint"), m("source.size_limit", { max: info.max_upload_mb }));
} catch {
  applyModelInfo(null);
}
if (info?.postprocess_available === false) {
  $("optPostprocess").disabled = true;
  setText($("postprocessHint"), m("correction.unavailable"));
}
params = initParams(info?.defaults ?? {}, info?.sam_manual_default ?? 24);
if (info?.runtime?.supported) runtime.restore();
trim.setCapacity(info?.motion_frame_capacity);
const jobs = (await history.refresh()) ?? [];
const savedJob = localStorage.getItem("flowhmr-last-job");
let initialJob = jobs.find((job) => job.status === "waiting_user" || job.status === "running")
  ?? jobs.find((job) => ACTIVE.has(job.status))
  ?? jobs.find((job) => job.id === savedJob);
if (!initialJob && savedJob) {
  try { initialJob = await api.getJob(savedJob); } catch { /* job may have been deleted */ }
}
initialJob ??= jobs.find((job) => job.status === "done") ?? jobs[0];
if (initialJob) void openJob(initialJob.id, true);
syncSubmit();

// ------------------------------------------------------------------ wiring
bus.on("file:selected", (file) => {
  if (currentJob) {
    resetJobView();
    history.setCurrent(null);
    show("jobCard", false);
    viewer.setStatus(m("view.new_video"));
  }
  params.newSource();
  trim.begin();
  preview.showLocal(file);
  syncSubmit();
});
bus.on("file:cleared", () => {
  if (!currentJob) { trim.clear(); preview.clear(); }
  syncSubmit();
});
bus.on("preview:pick", (i) => person.select(i));
bus.on("person:confirmed", () => poll());
bus.on("person:reselect", () => rerunCurrent({ manual_select: true, reselect_person: true }));

$("btnSubmit").addEventListener("click", submit);
$("btnDefaults").addEventListener("click", () => params.reset());
$("btnAlignScene").addEventListener("click", async () => {
  const id = currentJob;
  if (!id) return;
  const button = $("btnAlignScene");
  button.disabled = true;
  try { await api.prepareScene(id); if (id === currentJob) await poll(); }
  catch (e) { if (id === currentJob) progress.showActionError(e); }
  finally { button.disabled = false; }
});
$("btnStopRun").addEventListener("click", async () => {
  const id = currentJob;
  if (!id) return;
  $("btnStopRun").disabled = true;
  try {
    await api.cancelJob(id);
    if (id === currentJob) await poll();
  } catch (e) {
    if (id === currentJob) progress.showActionError(e.message ? e : m("job.stop_failed"));
  }
});
$("btnRerun").addEventListener("click", () => rerunCurrent());
$("btnSafeRerun").addEventListener("click", () => rerunCurrent({ vggt_interval: 0, static_camera: false }));

async function rerunCurrent(overrides) {
  if (!currentJob) return;
  try {
    await rerun(currentJob, overrides);
  } catch (e) {
    progress.showActionError(e.message ? e : m("job.rerun_failed"));
  }
}

function syncSubmit() {
  const btn = $("btnSubmit");
  const needsRuntime = info?.runtime?.supported && !info.runtime.ready;
  show(btn, !currentJob || !!upload.getFile());
  btn.disabled = submitting || (!needsRuntime && !upload.getFile());
  setText(btn, m(submitting ? "job.submitting" : needsRuntime ? "runtime.setup" : "settings.generate"));
  $("btnRerun").disabled = $("btnSafeRerun").disabled = $("personReselect").disabled = submitting;
}

async function submit() {
  if (info?.runtime?.supported && !info.runtime.ready) { runtime.open(); return; }
  const file = upload.getFile();
  if (!file) return;
  let p;
  try { p = { ...params.read(), ...trim.read() }; } catch (e) { upload.setError(e); return; }
  submitting = true;
  syncSubmit();
  resetJobView();
  try {
    const id = await api.uploadVideo(file, p, (frac) => progress.showUploading(file.name, frac));
    upload.clear();
    await openJob(id);
  } catch (e) {
    progress.showError(e);
  } finally {
    submitting = false;
    syncSubmit();
  }
}

async function rerun(jobId, overrides = {}) {
  if (submitting) return;
  if (info?.runtime?.supported && !info.runtime.ready) { runtime.open(); return; }
  const p = { ...params.read(overrides), ...(jobId === currentJob ? trim.read() : {}), ...overrides };
  submitting = true;
  syncSubmit();
  try {
    const id = await api.rerunJob(jobId, p);
    await openJob(id);
  } finally {
    submitting = false;
    syncSubmit();
  }
}

function resetJobView() {
  if (fbxDialog.open) fbxDialog.close("cancel");
  setExportPeople(null);
  $("dlFbx").disabled = $("menuFbx").disabled = true;
  show("dlFbx", false);
  clearTimeout(pollTimer);
  pollVersion++;
  currentJob = null;
  lastStatus = null;
  historyStamp = "";
  loadedResult = null;
  person.reset();
  preview.setTrack(null);
  viewer.clear();
  trim.clear();
  show("btnRerun", false);
  show("btnAlignScene", false);
  show("btnSafeRerun", false);
  show("btnStopRun", false);
  setText($("workspaceState"), "FlowHMR");
}

function clearDeletedJobs(ids) {
  if (ids.includes(localStorage.getItem("flowhmr-last-job"))) localStorage.removeItem("flowhmr-last-job");
  if (!ids.includes(currentJob)) return;
  resetJobView();
  history.setCurrent(null);
  preview.clear();
  show("jobCard", false);
  viewer.setStatus(m("view.empty"));
  syncSubmit();
}

// ------------------------------------------------------------------ job polling
async function openJob(id, restoreDraft = false) {
  if (upload.getFile()) upload.clear();
  resetJobView();
  currentJob = id;
  restoreParameterDraft = restoreDraft === true;
  trim.begin();
  paramsShownFor = null;
  localStorage.setItem("flowhmr-last-job", id);
  history.setCurrent(id);
  syncSubmit();
  await poll();
}

async function poll() {
  clearTimeout(pollTimer);
  const id = currentJob;
  if (!id) return;
  const version = ++pollVersion;
  let job;
  try {
    job = await api.getJob(id);
  } catch (e) {
    if (version === pollVersion) {
      setText($("modelInfo"), m("connection.reconnecting"));
      pollTimer = setTimeout(poll, 2000);
    }
    return;
  }
  if (version !== pollVersion) return;
  renderModelStatus(job.models_loaded ?? info?.models_loaded ?? true, job.status === "running" && job.loading_models);

  progress.render(job);
  viewer.setStatus(m(job.status === "done" ? "view.loading"
    : job.status === "needs_alignment" ? "view.alignment_needed"
    : job.status === "failed" ? "view.job_failed"
    : job.status === "cancelled" ? "job.stopped"
    : job.status === "cancel_requested" ? "job.stopping"
    : job.status === "waiting_user" ? "view.choose_people"
    : job.status === "queued" ? "view.job_queued"
    : job.scene_state === "running" ? "job.scene_aligning"
    : job.loading_models ? "job.model_loading"
    : "job.generating"));
  const videoChanged = ["waiting_user", "done"].includes(job.status) && lastStatus !== job.status;
  preview.showJob(api.videoUrl(id), () => jobLabel(job), { refresh: videoChanged });
  person.update(job);
  trim.setSource(job.source_frames);
  trim.setEditable(!ACTIVE.has(job.status));
  lastStatus = job.status;
  if (paramsShownFor !== id) {
    params.openJob(id, job.params, job.correction, restoreParameterDraft);
    trim.apply(job.params);
    paramsShownFor = id;
  }
  const stamp = `${id}:${job.status}:${Math.floor(job.progress / 5)}`;
  if (stamp !== historyStamp) { historyStamp = stamp; void history.refresh(); }

  if (ACTIVE.has(job.status)) {
    pollTimer = setTimeout(poll, job.status === "waiting_user" ? 1500 : 1000);
  } else if (job.status === "done" && loadedResult !== id) {
    loadedResult = id;
    params.suggestCorrectionHeight(job.correction);
    setExportPeople(job.scene);
    $("dlNpz").href = api.npzUrl(id);
    $("dlFbx").disabled = $("menuFbx").disabled = !job.has_fbx;
    $("menuNpz").disabled = false;
    show("dlFbx", job.has_fbx);
    show("dlNpz", true);
    try {
      const meta = await viewer.load(id, job.scene);
      if (meta && id === currentJob) void loadTracks(id, job.scene);
    } catch (e) {
      if (id === currentJob) {
        viewer.setStatus(m("view.preview_failed"));
        progress.showActionError(m("view.preview_detail", { detail: e }));
      }
    }
  }
}

async function loadTracks(id, scene) {
  const tracks = await Promise.all((scene?.people ?? [{ id }]).map(async (p) => {
    try {
      const r = await fetch(`/api/jobs/${p.id}/track`);
      return r.ok ? { ...await r.json(), person: p.person } : null;
    } catch { return null; }
  }));
  if (id === currentJob) preview.setTracks(tracks.filter(Boolean));
}
