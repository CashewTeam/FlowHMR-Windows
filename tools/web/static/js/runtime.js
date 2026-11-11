import { $, el, show } from "./utils.js";
import { getRuntime, selectRuntime, startRuntimeDownload, stopRuntimeDownload } from "./api.js";
import { appError, localize, message as m, serverMessage, setText, t } from "./i18n.js";

const GB = (value) => `${(value / 1e9).toFixed(2)} GB`;
const active = (transfer) => ["running", "stopping"].includes(transfer?.status);
const REOPEN = "flowhmr-runtime-reopen";

export function initRuntimeDialog() {
  const button = $("btnRuntime"), dialog = $("runtimeDialog"), list = $("runtimeList");
  const status = $("runtimeCheckStatus"), directory = $("runtimeDirectory"), folder = $("runtimeOpenFolder");
  const stop = $("runtimeDownloadStop"), restart = $("runtimeRestart"), refresh = $("runtimeRefresh");
  const transferBox = $("runtimeTransfer"), transferBar = $("runtimeTransferBar");
  let state = null, busy = false, timer = null, generation = 0;

  function summarize(next) {
    show(button, !!next?.supported);
    button.classList.toggle("needs-models", next?.supported && !next.ready);
    setText($("runtimeInfo"), next?.pending_restart ? m("runtime.pending") : next?.label ?? m("runtime.disabled"));
  }

  function render() {
    if (!state) return;
    const transfer = state.download;
    const running = active(transfer);
    summarize(state);
    directory.value = state.directory;
    folder.disabled = !state.directory;
    setText($("runtimeGpu"), state.gpu ? m("runtime.gpu_driver", { name: state.gpu.name, driver: state.gpu.driver })
      : m("runtime.gpu_unknown"));
    list.replaceChildren(...state.options.map((option) => {
      const item = el("div", "model-item runtime-option");
      item.dataset.version = option.id;
      const info = el("div", "runtime-option-info");
      const title = el("div", "runtime-option-title");
      const isActive = option.id === state.active;
      const pending = state.pending_restart && option.id === state.selected;
      const label = m(isActive ? "runtime.active" : pending ? "runtime.pending" : option.installed ? "runtime.installed" : "runtime.not_installed");
      const badge = el("span", "model-badge", label);
      badge.dataset.state = option.installed ? "ready" : "missing";
      title.append(el("strong", "", option.label), badge);
      if (option.recommended) title.append(el("span", "runtime-recommended", m("common.recommended")));
      info.append(title, el("p", "model-issue", () => [
        localize(serverMessage(option.detail_code, option.detail_params, option.detail)),
        option.bytes ? t("runtime.download_size", { bytes: GB(option.bytes) }) : "",
      ].filter(Boolean).join(" · ")));
      if (option.source && !option.installed) {
        const link = el("a", "runtime-source", m(option.detail_code === "runtime_rocm_experimental" ? "runtime.amd_source" : "runtime.source"));
        link.href = option.source;
        link.target = "_blank";
        link.rel = "noopener noreferrer";
        info.append(link);
      }
      const action = el("button", option.recommended && !option.installed ? "btn-primary small" : "btn-secondary small",
        m(option.installed ? "runtime.enable" : transfer?.version === option.id && transfer.status === "cancelled" ? "download.resume" : "download.download"));
      action.type = "button";
      action.disabled = busy || running || isActive || pending || !option.compatible || (!option.installed && state.pending_restart);
      action.addEventListener("click", () => transact(async () => {
        if (option.installed) {
          Object.assign(state, await selectRuntime(option.id));
          if (state.pending_restart) await applySwitch();
        } else state.download = await startRuntimeDownload(option.id);
      }));
      item.append(info, action);
      return item;
    }));
    show(stop, running);
    stop.disabled = busy || transfer?.status === "stopping";
    show(restart, !!state.pending_restart && !!window.pywebview?.api?.restart_runtime);
    restart.disabled = busy;
    refresh.disabled = busy;
    show(transferBox, !!transfer && transfer.status !== "idle");
    show(transferBar, running);
    if (running && transfer.total > 0) {
      transferBar.max = transfer.total;
      transferBar.value = transfer.bytes;
    } else transferBar.removeAttribute("value");
    const target = state.options.find((option) => option.id === transfer?.version)?.label ?? "";
    setText($("runtimeTransferLabel"), running
      ? () => [transfer.status === "stopping" ? t("job.stopping") : t(transfer.phase === "download" ? "download.fetching" : `download.${transfer.phase}`, {}, ""),
        target, transfer.total > 0 ? `${GB(transfer.bytes)} / ${GB(transfer.total)}` : ""].filter(Boolean).join(" · ")
      : transfer?.status === "failed" ? serverMessage(transfer.error_code, transfer.error_params, transfer.error)
        : transfer?.status === "cancelled" ? m("download.cancelled")
          : transfer?.status === "done" ? m("runtime.download_done", { target }) : "");
    setText(status, state.pending_restart ? m("runtime.restart_pending")
      : state.ready ? m("runtime.using", { label: state.label }) : m("runtime.choose"));
  }

  async function applySwitch() {
    const apply = window.pywebview?.api?.restart_runtime;
    if (!apply) return;
    sessionStorage.setItem(REOPEN, "1");
    if (!await apply()) {
      sessionStorage.removeItem(REOPEN);
      throw appError("runtime.restart_failed");
    }
  }

  function poll() {
    clearTimeout(timer);
    if (dialog.open && !busy && active(state?.download)) timer = setTimeout(load, 800);
  }

  async function load() {
    const token = ++generation;
    try {
      const next = await getRuntime();
      if (token !== generation || !dialog.open) return;
      state = next;
      render();
    } catch (error) {
      if (token === generation) setText(status, error);
    }
    if (token === generation) poll();
  }

  async function transact(action) {
    if (busy) return;
    busy = true;
    generation++;
    clearTimeout(timer);
    render();
    let problem;
    try { await action(); }
    catch (error) { problem = error; }
    finally { busy = false; render(); poll(); }
    if (problem) setText(status, problem);
  }

  function open() {
    if (dialog.open) return;
    dialog.showModal();
    void load();
  }
  button.addEventListener("click", open);
  refresh.addEventListener("click", load);
  dialog.addEventListener("close", () => { generation++; clearTimeout(timer); });
  stop.addEventListener("click", () => transact(async () => { state.download = await stopRuntimeDownload(state.download.id); }));
  restart.addEventListener("click", () => transact(applySwitch));
  folder.addEventListener("click", async () => {
    if (window.pywebview?.api?.open_runtime_folder) await window.pywebview.api.open_runtime_folder();
    else { directory.focus(); directory.select(); }
  });
  return { summarize, open, restore: () => {
    if (sessionStorage.getItem(REOPEN)) { sessionStorage.removeItem(REOPEN); open(); }
  } };
}
