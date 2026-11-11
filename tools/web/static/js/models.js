import { $, el, formatBytes, show } from "./utils.js";
import { getModelDownload, getModelImport, startModelDownload, startModelImport, stopModelDownload, verifyModels } from "./api.js";
import { getLanguage, localize, message as m, serverMessage, setAttr, setText, t } from "./i18n.js";

const active = (state) => state?.status === "running" || state?.status === "stopping";
const sizeLabel = (n) => n >= 1024 ** 3 ? `${(n / 1024 ** 3).toFixed(2)} GiB` : n < 1024 ? `${n} B` : formatBytes(n);
const LINK_KEYS = new Map([
  ["MANO 官网下载（需登录）", "mano"], ["镜像入口", "mirror"], ["官方仓库", "official"],
  ["官方权重", "weights"], ["魔搭下载", "modelscope"], ["直接下载", "direct"],
  ["作者直链", "author"], ["备用下载", "backup"], ["官方说明", "docs"],
]);

export function initModelDialog(refreshInventory) {
  const dialog = $("modelDialog"), list = $("modelList");
  const directory = $("modelDirectory"), folderButton = $("modelOpenFolder");
  const refreshButton = $("modelRefresh"), status = $("modelCheckStatus"), verifyButton = $("modelVerify");
  const downloadButton = $("modelDownload"), stopButton = $("modelDownloadStop");
  const archiveButton = $("modelImportArchive"), importButton = $("modelImportFolder");
  const importBox = $("modelImport"), importLabel = $("modelImportLabel");
  const importBar = $("modelImportBar"), importGroups = $("modelImportGroups"), importResults = $("modelImportResults");
  const transferBox = $("modelTransfer"), transferLabel = $("modelTransferLabel");
  const transferBar = $("modelTransferBar"), transferErrors = $("modelTransferErrors");
  let inventory = null, transfer = null, imported = null, busy = false, pollTimer = null, version = 0;
  const rowDownloads = new Map();
  const importing = () => imported?.state === "running";
  const working = () => active(transfer) || importing();
  const groupName = (id, fallback) => m(`model_name.${id}`, {}, fallback ?? id);
  const group = (id) => groupName(id, inventory?.items.find((item) => item.id === id)?.name);
  const picker = (kind) => window.pywebview?.api?.[kind === "archive" ? "choose_model_archive" : "choose_model_folder"];

  function renderInventory(next) {
    inventory = next;
    transfer = next.download ?? null;
    imported = next.import ?? null;
    directory.value = inventory.directory;
    folderButton.disabled = false;
    rowDownloads.clear();
    const downloadable = new Set(inventory.downloadable);
    list.replaceChildren(...inventory.items.map((item) => {
      const row = el("div", "model-item");
      row.dataset.model = item.id;
      const heading = el("div", "model-row");
      const badge = el("span", "model-badge", m(`models.${item.status}`, {}, item.status));
      badge.dataset.state = item.status;
      const name = el("span", "model-name");
      name.append(el("strong", "", groupName(item.id, item.name)),
        el("span", "model-role", m(item.required ? "models.required" : "models.optional")));
      const actions = el("div", "model-row-actions");
      actions.append(badge);
      if (downloadable.has(item.id)) {
        const bytes = inventory.download_sizes?.[item.id];
        const download = el("button", "btn-secondary small", bytes > 0
          ? m("models.download_row_size", { bytes: sizeLabel(bytes) })
          : m("models.download_row"));
        download.type = "button";
        download.dataset.modelDownloadGroup = item.id;
        setAttr(download, "aria-label", m("models.download_one", { model: groupName(item.id, item.name) }));
        if (bytes > 0) setAttr(download, "title", m("download.up_to", { bytes: sizeLabel(bytes) }));
        download.addEventListener("click", () => {
          if (!working()) void changeDownload(() => startModelDownload(item.id));
        });
        rowDownloads.set(item.id, download);
        actions.append(download);
      }
      heading.append(name, actions);
      row.append(heading);
      if (item.status !== "ready") {
        if (item.archive) {
          const source = el("div", "model-install model-archive");
          source.append(el("span", "", m("models.archive")), el("strong", "", item.archive.entry),
            el("code", "", item.archive.name));
          row.append(source);
          if (item.id === "smplh") {
            if (!item.required) row.append(el("p", "model-install model-extract-hint", m("models.smplh_optional")));
            const hint = typeof picker("archive") === "function" ? "models.smplh_archive_hint" : "models.smplh_extract_hint";
            row.append(el("p", "model-install model-extract-hint", m(hint)));
          }
        }
        if (item.id === "hamer" && typeof picker("archive") === "function") {
          row.append(el("p", "model-install model-extract-hint", m("models.hamer_archive_hint")));
        }
        const help = el("div", "model-install");
        if (item.status === "missing") {
          help.append(el("span", "", m(item.archive ? "models.extract_to" : "models.place_in")), el("code", "", item.folder));
        }
        for (const { label, label_code, url } of item.links) {
          const link = el("a", "", m(`model_link.${label_code ?? LINK_KEYS.get(label)}`, {}, label));
          link.href = url;
          link.target = "_blank";
          link.rel = "noopener noreferrer";
          help.append(link);
        }
        row.append(help);
        if (item.missing?.length) {
          const missing = el("div", "model-install model-missing");
          missing.append(el("span", "", m("models.missing_label")), ...item.missing.map((file) => el("code", "", file)));
          row.append(missing);
        }
        if (item.damaged?.length) {
          const damaged = el("div", "model-install model-missing");
          damaged.append(el("span", "", m("models.damaged_label")),
            ...item.damaged.map((entry) => el("code", "", m("models.file_result", {
              file: entry.file,
              detail: serverMessage(entry.detail_code, {
                size: entry.size == null ? "?" : sizeLabel(entry.size),
                expected: entry.expected == null ? "?" : sizeLabel(entry.expected),
              }, entry.detail),
            }))));
          row.append(damaged);
        }
        if (item.issue || item.issue_code) row.append(el("p", "model-issue", serverMessage(item.issue_code, item.issue_params, item.issue)));
      }
      return row;
    }));
    const blocking = inventory.items.filter((item) => item.blocking);
    const damaged = blocking.filter((item) => item.status === "damaged").length;
    setText(status, !blocking.length ? m("models.required_ready") : damaged
      ? m("models.required_damaged", { count: blocking.length, damaged })
      : m("models.required_count", { count: blocking.length }));
  }

  function syncButtons() {
    const running = active(transfer);
    const archive = typeof picker("archive") === "function";
    const folder = typeof picker("folder") === "function";
    show("modelImportGuide", archive || folder);
    show("modelManualGuide", !archive && !folder);
    show("modelImportActions", archive || folder);
    show("modelImportFormats", archive);
    show(archiveButton, archive);
    show(importButton, folder);
    archiveButton.disabled = importButton.disabled = busy || working();
    refreshButton.disabled = busy || working();
    verifyButton.disabled = busy || working();
    show(downloadButton, !running && !!inventory?.downloadable?.length);
    downloadButton.disabled = busy || importing();
    show(stopButton, running);
    stopButton.disabled = busy || transfer?.status === "stopping";
    setAttr(downloadButton, "title", inventory?.download_bytes ? m("download.up_to", { bytes: sizeLabel(inventory.download_bytes) }) : "");
    for (const button of rowDownloads.values()) button.disabled = busy || working();
  }

  function renderTransfer() {
    const running = active(transfer);
    show(transferBox, !!transfer && transfer.status !== "idle");
    show(transferBar, running);
    const errors = transfer?.errors ?? [];
    show(transferErrors, errors.length > 0);
    transferErrors.replaceChildren(...errors.map((error) =>
      el("li", "", m("models.file_result", {
        file: () => [error.group ? localize(group(error.group)) : "", error.file].filter(Boolean).join(" · "),
        detail: serverMessage(error.error_code, error.error_params, error.message),
      }))));
    if (running) {
      const { group: groupId, file, bytes, total, index, count, phase } = transfer;
      if (total > 0) {
        transferBar.max = total;
        transferBar.value = bytes;
      } else transferBar.removeAttribute("value");
      setText(transferLabel, () => [
        transfer.status === "stopping" ? t("job.stopping") : t(phase === "download" ? "download.fetching" : `download.${phase}`, {}, ""),
        groupId ? `${localize(group(groupId))} (${index}/${count})` : "",
        file, total > 0 ? `${sizeLabel(bytes)} / ${sizeLabel(total)}` : "",
      ].filter(Boolean).join(" · "));
    } else setText(transferLabel, m(transfer?.status === "cancelled" ? "download.cancelled"
      : errors.length ? "models.download_failed" : "models.download_done"));
  }

  function renderImport() {
    const running = importing();
    show(importBox, !!imported && imported.state !== "idle");
    show(importBar, running);
    if (running && imported.bytes_total > 0) {
      importBar.max = imported.bytes_total;
      importBar.value = imported.bytes_done;
    } else importBar.removeAttribute("value");
    const results = imported?.results ?? [];
    const recognized = imported?.groups ?? [];
    show(importGroups, recognized.length > 0 && !running);
    setText(importGroups, recognized.length ? m("models.import_recognized", {
      models: () => new Intl.ListFormat(getLanguage()).format(recognized.map((id) => localize(group(id)))),
    }) : "");
    const visible = results.length > 0 && !running;
    show(importResults, visible);
    if (visible) {
      importResults.replaceChildren(...results.map((entry) => {
        const item = el("li", "", () => [
          t("models.file_result", { file: entry.file, detail: m(`import_status.${entry.status}`, {}, entry.status) }),
          entry.code ? t(`import_reason.${entry.code}`, {}, "") : "",
        ].filter(Boolean).join(" · "));
        item.dataset.status = entry.status;
        return item;
      }));
    }
    if (running) {
      const { phase, current_file: file, bytes_done: done, bytes_total: total } = imported;
      if (phase === "extract") {
        setText(importLabel, () => [
          t("models.import_extract"), file, total > 0 ? `${sizeLabel(done)} / ${sizeLabel(total)}` : "",
        ].filter(Boolean).join(" · "));
      } else setText(importLabel, phase === "copy" ? m("models.import_copy", {
        file: file || "",
        done: sizeLabel(done),
        total: sizeLabel(total),
      }) : m("models.import_scan"));
    } else if (imported?.state === "completed") {
      const installed = results.filter((row) => row.status === "installed").length;
      const skipped = results.filter((row) => row.status === "skipped").length;
      setText(importLabel, m("models.import_done", { installed, skipped, unresolved: results.length - installed - skipped }));
    } else if (imported?.state === "failed") {
      const detail = imported.error_code === "extract_smplh_tar" && typeof picker("archive") === "function"
        ? m("models.import_tar_hint")
        : serverMessage(imported.error_code, imported.error_params, imported.error);
      setText(importLabel, m("models.import_failed", { detail }));
    }
  }

  function renderWork() {
    syncButtons();
    renderTransfer();
    renderImport();
  }

  function schedulePoll() {
    clearTimeout(pollTimer);
    if (dialog.open && !busy && working()) pollTimer = setTimeout(pollWork, 700);
  }

  async function pollWork() {
    const token = version;
    const pollingImport = importing();
    try {
      const next = await (pollingImport ? getModelImport() : getModelDownload());
      if (token !== version || !dialog.open) return;
      if (pollingImport) imported = next; else transfer = next;
      renderWork();
      if (!working()) { await refresh(); return; }
    } catch {
      if (token !== version || !dialog.open) return;
      setText(pollingImport ? importLabel : transferLabel, m("connection.reconnecting"));
    }
    schedulePoll();
  }

  function begin() {
    busy = true;
    version++;
    clearTimeout(pollTimer);
    syncButtons();
  }
  function end() {
    busy = false;
    renderWork();
    schedulePoll();
  }

  async function refresh() {
    if (busy) return;
    begin();
    list.setAttribute("aria-busy", "true");
    setText(status, m("models.checking"));
    try { renderInventory(await refreshInventory()); }
    catch (error) { setText(status, m("models.check_failed", { detail: error })); }
    finally { list.removeAttribute("aria-busy"); end(); }
  }

  async function verifyHashes() {
    if (busy || working()) return;
    begin();
    setText(status, m("models.hash_running"));
    let result;
    try {
      const report = await verifyModels();
      result = report.problems?.length ? m("models.hash_issues", { count: report.problems.length })
        : m("models.hash_ok", { count: report.checked });
    } catch (error) {
      result = m("models.hash_failed", { detail: error });
    } finally {
      end();
    }
    await refresh(); // Refresh badges after recording the hash verdicts.
    setText(status, result);
  }

  async function changeDownload(action) {
    if (busy) return;
    begin();
    let updated = false;
    try {
      transfer = await action();
      updated = true;
    } catch (error) {
      setText(status, error);
    } finally {
      end();
    }
    if (updated && !working()) await refresh();
  }

  async function chooseImport(kind) {
    const choose = picker(kind);
    if (typeof choose !== "function" || busy || working()) return;
    begin();
    let started = false;
    try {
      const path = await choose.call(window.pywebview.api);
      if (path) {
        imported = await startModelImport(path, kind);
        started = true;
      }
    } catch (error) {
      setText(status, m("models.import_failed", { detail: error }));
    } finally {
      end();
    }
    if (started && !working()) await refresh();
  }

  function syncFolderButton() {
    setText(folderButton, m(window.pywebview?.api?.open_models_folder ? "common.open_folder" : "models.copy_path"));
    syncButtons();
  }
  window.addEventListener("pywebviewready", syncFolderButton);
  $("btnModels").addEventListener("click", () => {
    syncFolderButton();
    dialog.showModal();
    void refresh();
  });
  refreshButton.addEventListener("click", refresh);
  verifyButton.addEventListener("click", verifyHashes);
  downloadButton.addEventListener("click", () => {
    if (!working()) void changeDownload(() => startModelDownload("all"));
  });
  stopButton.addEventListener("click", () => {
    if (active(transfer)) void changeDownload(() => stopModelDownload(transfer.id));
  });
  archiveButton.addEventListener("click", () => { void chooseImport("archive"); });
  importButton.addEventListener("click", () => { void chooseImport("folder"); });
  dialog.addEventListener("close", () => { version++; clearTimeout(pollTimer); });
  folderButton.addEventListener("click", async () => {
    try {
      if (window.pywebview?.api?.open_models_folder) await window.pywebview.api.open_models_folder();
      else {
        await navigator.clipboard.writeText(directory.value);
        setText(status, m("models.path_copied"));
      }
    } catch {
      directory.select();
      setText(status, m("models.copy_keys"));
    }
  });
}
