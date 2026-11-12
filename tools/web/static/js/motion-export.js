import { $, el, personColor, show } from "./utils.js";
import { appError, message as m, setText } from "./i18n.js";
import * as api from "./api.js";

const FORMAT_STORAGE = "flowhmr-export-format";
const SKIN_STORAGE = "flowhmr-fbx-with-skin";
const POSE_STORAGE = "flowhmr-fbx-first-pose";
const TEMPLATE_MAX_MB = 128;

export function initMotionExport(getBodyVariant, viewer) {
  const dialog = $("fbxExportDialog"), button = $("dlFbx"), menu = $("menuFbx");
  const format = $("motionExportFormat"), confirm = $("fbxConfirm");
  const skin = $("fbxWithSkin"), pose = $("fbxFirstPose"), peopleField = $("fbxPeople");
  const template = $("fbxTemplate"), error = $("motionExportError");
  const clearTemplate = $("fbxTemplateClear"), templateHint = $("fbxTemplateHint");
  const vmdTarget = $("vmdTarget"), characterOption = $("vmdTargetCharacter");
  const chooseCharacter = $("vmdChooseCharacter");
  const formats = [...format.options];
  let preferred = localStorage.getItem(FORMAT_STORAGE) || "flowhmr";
  if (preferred === "mixamo_iclone") preferred = "mixamo";
  let result = null, pending = null, entries = [], multiPeople = [], single = false;

  skin.checked = localStorage.getItem(SKIN_STORAGE) === "on";
  const savedPose = localStorage.getItem(POSE_STORAGE);
  pose.value = ["t", "a"].includes(savedPose) ? savedPose : "none";

  const selected = () => entries.filter(({ input }) => input.checked).map(({ input }) => input.value);
  const targets = () => viewer.getPmxActors(selected());
  function selectMode(next) {
    if (next === single) return;
    if (next) multiPeople = selected();
    const keep = next ? [multiPeople[0] ?? entries[0]?.input.value] : multiPeople;
    for (const { input } of entries) {
      input.type = next ? "radio" : "checkbox";
      if (next) input.name = "fbx-template-person"; else input.removeAttribute("name");
      input.checked = keep.includes(input.value);
    }
    single = next;
  }
  function showError(value = "") {
    setText(error, value);
    show(error, !!value);
  }
  function validTemplate() {
    const file = template.files[0];
    return file && /\.fbx$/i.test(file.name) && file.size > 0 && file.size <= TEMPLATE_MAX_MB * 1024 * 1024;
  }
  const usesTemplate = (profile) => profile === "metahuman" || (profile === "mixamo" && template.files.length > 0);
  function sync() {
    const profile = format.value;
    const mixamo = profile === "mixamo";
    const useTemplate = usesTemplate(profile);
    const showTemplate = mixamo || profile === "metahuman";
    const singlePerson = useTemplate || profile === "maya_adv";
    selectMode(singlePerson);
    show("standardFbxOptions", !useTemplate && (profile === "flowhmr" || mixamo));
    show("fbxTemplateRow", showTemplate);
    show(clearTemplate, showTemplate && template.files.length > 0);
    if (showTemplate) setText($("fbxTemplateLabel"), m(`export.${profile}_template`));
    show(templateHint, mixamo);
    if (mixamo) template.setAttribute("aria-describedby", templateHint.id);
    else template.removeAttribute("aria-describedby");
    show("vmdOptions", profile === "vmd");
    const roles = targets();
    characterOption.disabled = characterOption.hidden = !roles.length;
    if (roles.length) setText(characterOption, roles.length === 1
      ? m("export.vmd_character", { name: roles[0].asset.name }) : m("export.vmd_characters"));
    else vmdTarget.value = "generic";
    let hint = profile;
    if (profile === "vmd") hint = vmdTarget.value === "target" ? "vmd_target" : "vmd_generic";
    if (mixamo) hint = useTemplate ? "mixamo_target" : "";
    setText($("motionExportHint"), hint ? m(`export.${hint}_hint`) : "");
    setText($("exportPeopleLabel"), m(singlePerson ? "export.select_person" : "export.select_people"));
    const busy = !!pending;
    format.disabled = peopleField.disabled = template.disabled = vmdTarget.disabled = chooseCharacter.disabled = busy;
    clearTemplate.disabled = busy;
    confirm.disabled = !result || !profile || busy || (entries.length > 0 && !selected().length)
      || (useTemplate && !validTemplate());
    setText(confirm, m(busy ? "export.preparing" : "common.export"));
  }
  function setPeople(scene) {
    single = false;
    entries = (scene?.people ?? []).map(({ person }) => {
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
    multiPeople = selected();
    $("fbxPeopleList").replaceChildren(...entries.map(({ label }) => label));
    show(peopleField, !!entries.length);
  }
  function setButtons(enabled) {
    button.disabled = menu.disabled = !enabled;
    show(button, enabled);
  }
  function download(href, filename = "") {
    const link = document.createElement("a");
    link.href = href;
    link.download = filename;
    link.click();
  }
  function downloadBlob(blob, filename) {
    const href = URL.createObjectURL(blob);
    dialog.close("export");
    download(href, filename);
    setTimeout(() => URL.revokeObjectURL(href), 60_000);
  }

  button.onclick = () => {
    if (!result || dialog.open) return;
    dialog.returnValue = "";
    showError();
    vmdTarget.value = targets().length ? "target" : "generic";
    sync();
    dialog.showModal();
  };
  menu.onclick = () => button.click();
  peopleField.addEventListener("change", sync);
  vmdTarget.addEventListener("change", sync);
  chooseCharacter.onclick = () => {
    dialog.close("character");
    viewer.openCharacter();
  };
  window.addEventListener("flowhmr:characterchange", () => {
    if (!dialog.open || pending) return;
    vmdTarget.value = targets().length ? "target" : "generic";
    sync();
  });
  format.addEventListener("change", () => {
    template.value = "";
    showError();
    sync();
  });
  template.addEventListener("change", () => {
    const file = template.files[0];
    const kind = format.value;
    showError(!file || validTemplate() ? "" : file.size > TEMPLATE_MAX_MB * 1024 * 1024
      ? m(`server.${kind}_template_size`, { max: TEMPLATE_MAX_MB }) : m(`server.${kind}_template_required`));
    sync();
  });
  clearTemplate.onclick = () => { template.value = ""; showError(); sync(); };
  dialog.addEventListener("close", () => {
    if (dialog.open) return;
    pending?.abort();
    pending = null;
    template.value = "";
    sync();
  });
  dialog.querySelector("form").addEventListener("submit", async (event) => {
    if (event.submitter !== confirm) return;
    event.preventDefault();
    if (!result || confirm.disabled || pending) return;
    const { id } = result;
    const profile = format.value;
    const people = selected();
    preferred = profile;
    localStorage.setItem(FORMAT_STORAGE, profile);
    const targetVmd = profile === "vmd" && vmdTarget.value === "target";
    if (profile === "vrma" || (profile === "vmd" && !targetVmd)) {
      dialog.close("export");
      download(api.motionUrl(id, profile, people));
      return;
    }
    if (!usesTemplate(profile) && !targetVmd) {
      const advancedSkeleton = profile === "maya_adv";
      if (!advancedSkeleton) {
        localStorage.setItem(SKIN_STORAGE, skin.checked ? "on" : "off");
        localStorage.setItem(POSE_STORAGE, pose.value);
      }
      dialog.close("export");
      download(api.fbxUrl(id, !advancedSkeleton && skin.checked, getBodyVariant(), people,
        advancedSkeleton ? "t" : pose.value, profile));
      return;
    }
    const operation = pending = new AbortController();
    showError();
    sync();
    try {
      const { blob, filename } = targetVmd
        ? await makePmxVmd(viewer, people, result.filename, operation.signal)
        : await api.exportTemplateFbx(id, people, profile, template.files[0], operation.signal);
      if (pending !== operation || result?.id !== id || !dialog.open) return;
      downloadBlob(blob, filename);
    } catch (cause) {
      if (!operation.signal.aborted && dialog.open) {
        showError(targetVmd ? m("export.pmx_vmd_failed", { detail: cause }) : cause);
      }
    } finally {
      if (pending === operation) {
        pending = null;
        sync();
      }
    }
  });

  return {
    setResult(id, job) {
      result = { id, filename: job.filename };
      const availableByFormat = { vmd: !!job.has_vmd, vrma: !!job.has_vrma };
      for (const option of formats) option.disabled = !(availableByFormat[option.value] ?? job.has_fbx);
      const available = formats.filter((option) => !option.disabled);
      format.value = (available.find((option) => option.value === preferred) ?? available[0])?.value ?? "";
      setPeople(job.scene);
      setButtons(!!available.length);
      sync();
    },
    clear() {
      pending?.abort();
      pending = null;
      result = null;
      if (dialog.open) dialog.close("cancel");
      template.value = "";
      setPeople(null);
      setButtons(false);
      sync();
    },
  };
}

async function makePmxVmd(viewer, people, filename, signal) {
  const actors = viewer.getPmxActors(people);
  if (!actors.length) throw appError("export.vmd_character_unavailable");
  const [{ createPmxRetarget }, { encodePmxVmd, packPmxVmdZip }] = await Promise.all([
    import("./pmx-retarget.js"), import("./pmx-vmd.js"),
  ]);
  const stem = (filename || "FlowHMR").replace(/\.[^.]+$/, "").replace(/[<>:"/\\|?*\x00-\x1f]/g, "_").slice(0, 100);
  const files = [];
  for (const actor of actors) {
    signal.throwIfAborted();
    const { asset } = actor;
    const clone = asset.clone();
    try {
      const solver = createPmxRetarget(clone, asset.rest, actor.motion, { poseOffsets: actor.poseOffsets });
      const bytes = await encodePmxVmd({
        solver, rest: asset.rest, modelName: asset.name,
        frameCount: actor.motion.frames, fps: actor.fps, startFrame: actor.start, sceneEndFrame: actor.sceneEnd,
        placement: { yaw: actor.yaw, offset: actor.offset }, signal,
      });
      const tag = actor.person == null ? "" : `_person${actor.person}`;
      files.push({ name: `${stem}${tag}_pmx.vmd`, bytes });
    } finally {
      asset.releaseClone(clone);
    }
  }
  return files.length === 1
    ? { blob: new Blob([files[0].bytes], { type: "application/octet-stream" }), filename: files[0].name }
    : { blob: packPmxVmdZip(files), filename: `${stem}_pmx_vmd.zip` };
}
