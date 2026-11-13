import { $, el, show } from "./utils.js";
import { appError, message as m, setText, t } from "./i18n.js";
import * as api from "./api.js";
import { readStoredPose, storedPose } from "./pose-offsets.js";

const kindOf = (name) => /\.(pmx|fbx|vrm)$/i.exec(name)?.[1]?.toLowerCase();
const emptyLibrary = () => ({ models: [], actors: {}, defaults: {}, presets: {} });
const slot = (actor) => String(actor.number ?? "single");
const editKey = (actor, id) => `flowhmr-character-edit:${actor.id}:${id}`;
const temporaryKey = (entry) => `${entry.asset.poseStorageKey}:${entry.actor.id}`;

export function initPreviewCharacter(getActors, onChange, onRestoreError) {
  const dialog = $("previewCharacterDialog"), button = $("previewCharacter");
  const folder = $("pmxFolder"), browse = $("pmxBrowse"), model = $("pmxFile");
  const input = $("characterModelFile"), browseFile = $("characterBrowse");
  const actorSelect = $("characterActor"), librarySelect = $("characterLibrary");
  const apply = $("pmxApply"), restore = $("pmxRestore"), error = $("pmxError");
  const remove = $("characterDelete"), confirm = $("characterDeleteConfirmButton");
  const cancelRemoval = $("characterDeleteCancel"), cancelDialog = $("characterDialogCancel");
  const picker = () => window.pywebview?.api?.choose_character_file;
  let files = [], candidates = [], catalog = emptyLibrary(), catalogReady = false, catalogRead = null;
  let focused = null, operation = null, restoring = null, picking = false, pickVersion = 0, removing = null;
  const refs = new Map(), writes = new Map();
  let remoteRead = null;
  let remotePending = false;

  function currentActor() {
    const actors = getActors();
    return actors.find((actor) => actor.id === focused) ?? actors[0];
  }
  function assignment(actor) {
    const record = catalog.actors[actor.id];
    if (record) return { id: record.id, offsets: record.poses?.[record.id] };
    return catalog.defaults[slot(actor)] ?? {};
  }
  function selection() {
    const item = libraryChoice();
    return item ? { kind: item.kind, libraryId: item.id } : candidates[Number(model.value)];
  }
  const libraryChoice = () => catalog.models.find(({ id }) => id === librarySelect.value);
  function setError(cause = "") {
    setText(error, cause);
    show(error, !!cause);
  }
  function sync() {
    const actor = currentActor(), busy = !!(operation || restoring || catalogRead || picking || removing);
    browse.disabled = browseFile.disabled = model.disabled = actorSelect.disabled = busy;
    librarySelect.disabled = busy || !catalog.models.length;
    restore.disabled = busy || !actor || (!actor.character && !assignment(actor).id);
    apply.disabled = busy || !actor || !selection();
    remove.disabled = busy || !libraryChoice();
    cancelDialog.disabled = confirm.disabled = cancelRemoval.disabled = !!(removing && operation);
    show(button, !!actor);
    show(model, candidates.length > 1);
    show(browse, !picker());
    if (remotePending && !remoteRead && !busy && !writes.size) queueMicrotask(syncRemoteCharacters);
  }
  function status() {
    const actor = currentActor(), candidate = candidates[Number(model.value)];
    if (candidate) return candidate.path;
    const asset = actor?.character?.asset;
    if (!asset) return actor && assignment(actor).id ? m("server.character_library_missing") : m("character.current", { name: "SMPL-H" });
    const { name, textureReport: { missing, failed }, rest } = asset;
    return () => [
      t("character.current", { name }),
      missing + failed ? t("character.textures", { count: missing + failed }) : "",
      rest.skinning?.approximate ? t("character.skinning") : "",
    ].filter(Boolean).join(" · ");
  }
  function renderPeople() {
    const actors = getActors();
    actorSelect.replaceChildren(...actors.map((actor) => {
      const option = el("option", "", () => `${t("person.number", { number: actor.number })} · ${actor.character?.asset.name ?? "SMPL-H"}`);
      option.value = actor.id;
      return option;
    }));
    actorSelect.value = currentActor()?.id ?? "";
    show("characterActorRow", actors.length > 1);
  }
  function renderLibrary(id = currentActor()?.character?.libraryId ?? "") {
    const blank = el("option", "", m(catalog.models.length ? "character.library_choose" : "character.library_empty"));
    blank.value = "";
    librarySelect.replaceChildren(blank, ...catalog.models.map(({ id, name, filename }) => {
      const option = el("option", "", `${name} · ${filename}`);
      option.value = id;
      return option;
    }));
    if (catalog.models.some((item) => item.id === id)) librarySelect.value = id;
    setText($("pmxStatus"), status());
  }
  function resetCandidates(items = []) {
    candidates = items;
    model.replaceChildren(...items.map((item, index) => {
      const option = el("option", "", item.path);
      option.value = String(index);
      return option;
    }));
  }
  function refresh() {
    renderPeople();
    renderLibrary();
    sync();
  }
  function notifyChange() {
    if (dialog.open) refresh();
    onChange();
    window.dispatchEvent(new Event("flowhmr:characterchange"));
  }
  function focus(id) {
    if (!getActors().some((actor) => actor.id === id)) return;
    focused = id;
    onChange();
  }

  function retain(asset) {
    refs.set(asset, (refs.get(asset) ?? 0) + 1);
    return asset;
  }
  function release(asset) {
    const count = refs.get(asset) - 1;
    if (count) refs.set(asset, count);
    else {
      refs.delete(asset);
      asset.dispose();
    }
  }
  function dispose(entry) {
    if (!entry) return;
    entry.asset.releaseClone(entry.mesh);
    release(entry.asset);
  }
  function attach(actor, entry) {
    dispose(actor.character);
    actor.character = entry;
    if (entry) actor.group.add(entry.mesh);
    notifyChange();
  }
  function finishRemoval() {
    removing = null;
    show("characterDeleteConfirm", false);
    sync();
  }

  async function loadAsset(selected, signal) {
    const used = selected.libraryId && getActors().find(({ character }) => character?.libraryId === selected.libraryId);
    if (used) return retain(used.character.asset);
    const load = selected.kind === "fbx" ? (await import("./fbx-character-asset.js")).loadFbxCharacter
      : selected.kind === "vrm" ? (await import("./vrm-asset.js")).loadVrmCharacter
        : (await import("./pmx-asset.js")).loadPmxAsset;
    const options = { signal };
    const native = selected.libraryId ? await api.openLibraryCharacter(selected.libraryId, signal) : selected.native;
    let file = selected.file, resources = files;
    if (native) {
      file = await api.getCharacterFile(native, signal);
      resources = [file];
      options.readTexture = (path) => api.getCharacterTexture(native.ticket, path, signal);
      options.textureUrl = (path) => api.characterResourceUrl(native.ticket, path);
    }
    const asset = await load(resources, file, options);
    asset.poseStorageKey = `flowhmr-pose-offsets:${selected.kind}:${file.name}:${file.size}:${asset.name}`;
    return retain(asset);
  }
  async function prepare(actor, selected, signal) {
    const asset = await loadAsset(selected, signal);
    let mesh;
    try {
      const isPmx = asset.kind === "pmx";
      const [retarget, motion] = await Promise.all([
        isPmx ? import("./pmx-retarget.js") : import("./fbx-character-retarget.js"),
        actor.character?.motion ?? api.getCharacterMotion(actor.id, signal),
      ]);
      signal.throwIfAborted();
      if (motion.frames !== actor.meta.num_frames) throw appError("server.pmx_motion");
      mesh = asset.clone();
      const rig = isPmx ? retarget.createPmxRetarget(mesh, asset.rest, motion)
        : retarget.createFbxCharacterRetarget(mesh, asset, motion);
      mesh.traverse((object) => {
        if (object.isMesh) object.castShadow = object.receiveShadow = true;
      });
      return { actor, asset, mesh, rig, motion, libraryId: selected.libraryId ?? null, poseOffsets: null };
    } catch (cause) {
      if (mesh) asset.releaseClone(mesh);
      release(asset);
      throw cause;
    }
  }

  function writeFor(actor, write) {
    const next = (writes.get(actor.id) ?? Promise.resolve()).catch(() => {}).then(write);
    writes.set(actor.id, next);
    const clear = () => {
      if (writes.get(actor.id) === next) writes.delete(actor.id);
      if (remotePending) void syncRemoteCharacters();
    };
    next.then(clear, clear);
    return next;
  }
  function applyPose(entry, offsets) {
    entry.poseOffsets = entry.rig.setPoseOffsets(offsets);
  }
  function editPose(entry, offsets) {
    applyPose(entry, offsets);
    const { actor, libraryId, poseOffsets } = entry;
    const key = libraryId ? editKey(actor, libraryId) : temporaryKey(entry);
    const record = storedPose(poseOffsets);
    try { localStorage.setItem(key, record); } catch { /* server still saves the pose */ }
    if (!libraryId) return;
    // Draw every slider move, but keep only the newest edit while a write is in flight.
    entry.queuedPose = { poseOffsets, record };
    if (entry.writingPose) return;
    entry.writingPose = true;
    void writeFor(actor, async () => {
      try {
        while (entry.queuedPose) {
          const next = entry.queuedPose;
          entry.queuedPose = null;
          await api.saveActorCharacter(actor, libraryId, next.poseOffsets, false);
          try {
            if (localStorage.getItem(key) === next.record) localStorage.removeItem(key);
          } catch { /* the server copy is already saved */ }
        }
      } finally {
        entry.writingPose = false;
      }
    }).catch((cause) => { if (actor.character === entry) onRestoreError(cause); });
  }

  // MCP edits use the same server preferences as the GUI. Only synchronize
  // idle actors; local pending edits keep ownership until their save completes.
  const remoteSignatures = new Map();
  async function syncRemoteCharacters() {
    const actors = getActors();
    if (!actors.length || remoteRead || operation || restoring || catalogRead || picking || writes.size) return;
    remotePending = false;
    const read = remoteRead = new AbortController();
    try {
      const next = await api.getCharacterLibrary(actors, read.signal);
      read.signal.throwIfAborted();
      if (actors.some((actor) => !getActors().includes(actor)) || operation || picking || writes.size) return;
      catalog = next;
      if (dialog.open) { renderLibrary(librarySelect.value); sync(); }
      for (const actor of actors) {
        const saved = assignment(actor);
        const signature = JSON.stringify(saved);
        if (remoteSignatures.get(actor.id) === signature) continue;
        if (saved.id && readStoredPose(editKey(actor, saved.id))) continue;
        if (!saved.id) {
          if (actor.character?.libraryId) attach(actor, null);
        } else if (actor.character?.libraryId === saved.id) {
          applyPose(actor.character, saved.offsets);
          notifyChange();
        } else {
          const item = catalog.models.find((model) => model.id === saved.id);
          if (!item) throw appError('server.character_library_missing');
          const entry = await prepare(actor, { kind: item.kind, libraryId: item.id }, read.signal);
          if (read.signal.aborted || !getActors().includes(actor) || operation || picking || writes.size) {
            dispose(entry);
            return;
          }
          applyPose(entry, saved.offsets);
          attach(actor, entry);
        }
        remoteSignatures.set(actor.id, signature);
      }
    } catch (cause) {
      if (!read.signal.aborted) onRestoreError(cause);
    } finally {
      if (remoteRead === read) remoteRead = null;
      if (remotePending) void syncRemoteCharacters();
    }
  }
  window.addEventListener('flowhmr:server-update', event => {
    if (['characters', 'resync'].includes(event.detail.kind)) {
      remotePending = true;
      void syncRemoteCharacters();
    }
  });
  window.addEventListener('pagehide', () => remoteRead?.abort());

  async function open() {
    if (dialog.open || !currentActor()) return;
    setError();
    refresh();
    dialog.showModal();
    if (restoring) return;
    const read = catalogRead = new AbortController();
    sync();
    try {
      await Promise.allSettled(getActors().map((actor) => writes.get(actor.id)));
      read.signal.throwIfAborted();
      catalog = await api.getCharacterLibrary(getActors(), read.signal);
      catalogReady = true;
      refresh();
    } catch (cause) {
      if (!read.signal.aborted && picker()) setError(cause);
    } finally {
      if (catalogRead === read) {
        catalogRead = null;
        sync();
      }
    }
  }
  button.onclick = () => void open();
  browse.onclick = () => folder.click();
  window.addEventListener("pywebviewready", sync);
  model.onchange = () => setText($("pmxStatus"), status());
  actorSelect.onchange = () => {
    focus(actorSelect.value);
    files = [];
    resetCandidates();
    setError();
    renderLibrary();
    sync();
  };
  librarySelect.onchange = () => {
    files = [];
    resetCandidates();
    setError();
    setText($("pmxStatus"), status());
    sync();
  };
  remove.onclick = () => {
    if (remove.disabled) return;
    removing = libraryChoice();
    setError();
    setText($("characterDeleteMessage"), m("character.library_confirm", { name: removing.name }));
    show("characterDeleteConfirm");
    sync();
    cancelRemoval.focus();
  };
  cancelRemoval.onclick = () => {
    finishRemoval();
    remove.focus();
  };
  confirm.onclick = async () => {
    if (!removing || operation) return;
    const item = removing, run = operation = new AbortController();
    setText($("pmxStatus"), m("character.library_deleting"));
    sync();
    try {
      await Promise.allSettled(writes.values());
      run.signal.throwIfAborted();
      const result = await api.deleteLibraryCharacter(item.id, getActors());
      run.signal.throwIfAborted();
      catalog = result.catalog;
      try {
        for (const key of Object.keys(localStorage)) {
          if (key.startsWith("flowhmr-character-edit:") && key.endsWith(`:${item.id}`)) localStorage.removeItem(key);
        }
      } catch { /* The server preferences are already updated. */ }
      for (const actor of getActors()) {
        if (actor.character?.libraryId !== item.id) continue;
        dispose(actor.character);
        actor.character = null;
      }
      notifyChange();
      setText($("pmxStatus"), m("character.library_deleted", { name: item.name }));
      if (result.cleanup_pending) setError(m("character.library_cleanup_pending"));
    } catch (cause) {
      if (!run.signal.aborted) {
        setText($("pmxStatus"), status());
        setError(cause);
      }
    } finally {
      if (operation === run) {
        operation = null;
        finishRemoval();
        if (dialog.open) (librarySelect.disabled ? browseFile : librarySelect).focus();
      }
    }
  };
  function chooseFiles(control) {
    if (operation || picking || !control.files.length) return;
    files = Array.from(control.files);
    control.value = "";
    resetCandidates(files.flatMap((file) => {
      const kind = kindOf(file.name);
      return kind ? [{ kind, file, path: file.webkitRelativePath || file.name }] : [];
    }).sort((a, b) => a.path.localeCompare(b.path, undefined, { numeric: true })));
    setError(candidates.length ? "" : m("character.no_model"));
    renderLibrary("");
    sync();
  }
  folder.onchange = () => chooseFiles(folder);
  input.onchange = () => chooseFiles(input);
  browseFile.onclick = async () => {
    const pick = picker();
    if (!pick) {
      input.click();
      return;
    }
    const version = ++pickVersion;
    picking = true;
    sync();
    try {
      const choice = await pick.call(window.pywebview.api);
      if (version !== pickVersion || !dialog.open || !choice) return;
      const kind = typeof choice.name === "string" ? kindOf(choice.name) : null;
      if (!kind || typeof choice.ticket !== "string") throw appError("server.character_file");
      files = [];
      resetCandidates([{ kind, path: choice.name, native: choice }]);
      setError();
      renderLibrary("");
    } catch (cause) {
      if (version === pickVersion && dialog.open) setError(cause);
    } finally {
      if (version === pickVersion) {
        picking = false;
        sync();
      }
    }
  };
  restore.onclick = async () => {
    const actor = currentActor();
    if (!actor || operation) return;
    const run = operation = new AbortController();
    setError();
    sync();
    try {
      if (catalogReady || actor.character?.libraryId) {
        await writeFor(actor, () => api.saveActorCharacter(actor, null, null, true));
        catalog.actors[actor.id] = { ...catalog.actors[actor.id], id: null };
        catalog.defaults[slot(actor)] = { id: null };
      }
      run.signal.throwIfAborted();
      attach(actor, null);
      dialog.close("restore");
    } catch (cause) {
      if (!run.signal.aborted) setError(cause);
    } finally {
      if (operation === run) operation = null;
      sync();
    }
  };
  dialog.addEventListener("cancel", (event) => {
    if (!removing) return;
    event.preventDefault();
    if (!operation) cancelRemoval.click();
  });
  dialog.addEventListener("close", () => {
    if (dialog.open) return;
    operation?.abort();
    catalogRead?.abort();
    operation = catalogRead = null;
    pickVersion++;
    picking = false;
    files = [];
    resetCandidates();
    folder.value = input.value = "";
    finishRemoval();
  });
  dialog.querySelector("form").addEventListener("submit", async (event) => {
    if (event.submitter !== apply) return;
    event.preventDefault();
    const selected = selection(), actor = currentActor();
    if (operation || restoring || !selected || !actor) return;
    const run = operation = new AbortController();
    let entry = null;
    setError();
    setText($("pmxStatus"), m("character.loading"));
    sync();
    try {
      entry = await prepare(actor, selected, run.signal);
      if (selected.native) {
        setText($("pmxStatus"), m("character.library_saving"));
        const item = await api.saveLibraryCharacter(selected.native.ticket, entry.asset.name);
        if (!catalog.models.some(({ id }) => id === item.id)) catalog.models.push(item);
        entry.libraryId = item.id;
      }
      run.signal.throwIfAborted();
      const id = entry.libraryId;
      const previous = actor.character?.libraryId === id && id ? actor.character.poseOffsets : null;
      const remembered = id ? readStoredPose(editKey(actor, id)) ?? catalog.actors[actor.id]?.poses?.[id]
        ?? catalog.presets[id] : readStoredPose(temporaryKey(entry));
      applyPose(entry, previous ?? remembered ?? readStoredPose(entry.asset.poseStorageKey));
      if (id) {
        await writeFor(actor, () => api.saveActorCharacter(actor, id, entry.poseOffsets, true));
        catalogReady = true;
      }
      run.signal.throwIfAborted();
      attach(actor, entry);
      entry = null;
      dialog.close("apply");
    } catch (cause) {
      if (!run.signal.aborted) {
        setText($("pmxStatus"), status());
        const code = ["no_skin", "unsupported_rig", "too_large", "parse"].includes(cause?.code) && selected.kind === "fbx"
          ? `character.fbx_${cause.code}` : "";
        setError(code ? m(code) : m("character.failed", { detail: cause }));
      }
    } finally {
      dispose(entry);
      if (operation === run) operation = null;
      sync();
    }
  });

  return {
    open,
    focus,
    editPose,
    get focused() { return currentActor(); },
    get active() { return currentActor()?.character?.asset ?? getActors().find((actor) => actor.character)?.character.asset; },
    releaseActors(actors) {
      remoteRead?.abort();
      remoteSignatures.clear();
      operation?.abort();
      restoring?.abort();
      catalogRead?.abort();
      operation = restoring = catalogRead = null;
      if (dialog.open) dialog.close("cancel");
      for (const actor of actors) {
        dispose(actor.character);
        actor.character = null;
      }
    },
    async showForJob() {
      const actors = getActors();
      if (!actors.length) return;
      const run = restoring = new AbortController();
      let error;
      sync();
      try {
        await Promise.allSettled(actors.map((actor) => writes.get(actor.id)));
        run.signal.throwIfAborted();
        catalog = await api.getCharacterLibrary(actors, run.signal);
        catalogReady = true;
        for (const actor of actors) {
          const saved = assignment(actor);
          if (!saved.id) continue;
          const model = catalog.models.find(({ id }) => id === saved.id);
          if (!model) {
            error = m("server.character_library_missing");
            continue;
          }
          let entry = null;
          try {
            entry = await prepare(actor, { kind: model.kind, libraryId: model.id }, run.signal);
            run.signal.throwIfAborted();
            const unsaved = readStoredPose(editKey(actor, model.id));
            applyPose(entry, unsaved ?? saved.offsets);
            attach(actor, entry);
            if (unsaved) editPose(entry, unsaved);
            entry = null;
          } catch (cause) {
            if (run.signal.aborted) break;
            error = m("character.failed", { detail: cause });
          } finally {
            dispose(entry);
          }
        }
      } catch (cause) {
        if (picker()) error = cause;
      } finally {
        if (restoring === run) {
          restoring = null;
          if (dialog.open) refresh();
          else sync();
          if (error && !run.signal.aborted) onRestoreError(error);
        }
      }
    },
  };
}
