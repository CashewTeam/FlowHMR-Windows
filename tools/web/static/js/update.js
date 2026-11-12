import { $, el, show } from "./utils.js";
import { message as m, setText } from "./i18n.js";

const CHECK_INTERVAL = 12 * 60 * 60 * 1000;
const AVAILABLE = new Set(["available", "legacy_available"]);
const TRANSFER = new Set(["download", "verify", "stage", "stopping"]);
const RETRY = new Set(["available", "failed", "cancelled", "offline"]);
const STATUS = {
  download: "download.fetching", verify: "download.verify", stopping: "job.stopping", cancelled: "download.cancelled",
};

export function initUpdates() {
  const menu = $("menuUpdates"), updateButton = $("btnUpdate"), dialog = $("updateDialog");
  const check = $("updateCheck"), download = $("updateDownload"), stop = $("updateStop"), progress = $("updateProgress");
  const manual = $("updateManual"), versions = $("updateVersionSelect"), applyVersion = $("updateApplyVersion");
  const olderZip = $("updateOlderZip");
  const announced = new Set();
  let bridge, state, timer, revision = 0, workspaceReady = false;
  let releases = [], historyStamp = "";

  function announceUpdate() {
    const target = state?.target;
    if (!workspaceReady || !target || !AVAILABLE.has(state.status) || announced.has(target)) return;
    if (!dialog.open) {
      if (document.querySelector("dialog[open]")) return;
      const focused = document.activeElement;
      dialog.show();
      if (focused instanceof HTMLElement && focused !== document.body) focused.focus({ preventScroll: true });
    }
    announced.add(target);
  }

  function syncVersion() {
    const entry = releases.find((item) => item.version === versions.value);
    show(applyVersion, !!entry?.installable);
    show(olderZip, !!entry?.url && !entry.installable);
    show("updateLegacyNote", !!entry?.url && !entry.installable);
    if (entry?.url && !entry.installable) olderZip.href = entry.url;
    else olderZip.removeAttribute("href");
  }

  function renderHistory(active) {
    releases = state.saved ? [{ version: state.saved, local: true, installable: true }, ...state.releases] : state.releases;
    const stamp = JSON.stringify(releases);
    if (stamp !== historyStamp) {
      const keep = versions.value;
      versions.replaceChildren(...releases.map(({ version, local }) => {
        const option = el("option", "", local ? m("update.local_version", { version }) : `v${version}`);
        option.value = version;
        return option;
      }));
      if (releases.some((item) => item.version === keep)) versions.value = keep;
      historyStamp = stamp;
    }
    show("updateHistory", releases.length > 0);
    versions.disabled = applyVersion.disabled = active || state.status === "ready";
    syncVersion();
  }

  function render(next) {
    clearTimeout(timer);
    state = next;
    const supported = !!state?.version;
    show(menu, supported);
    show(updateButton, supported);
    if (!supported) return;
    const { status, version, target, received, total } = state;
    const active = TRANSFER.has(status);
    check.disabled = active || status === "checking" || status === "ready";
    manual.disabled = active || status === "ready";
    stop.disabled = status === "stopping";
    show(stop, active);
    show(download, state.downloadable && !!target && RETRY.has(status));
    setText(download, m(status === "cancelled" && !state.message ? "download.resume" : "download.download"));
    setText($("updateVersion"), m("update.installed", { version }));
    const key = state.message ? `update.${state.message}` : STATUS[status] ?? `update.${status}`;
    setText($("updateStatus"), m(key, { version: target, size: Math.round(total / 1e6) }));
    show("updateNotes", !!target && (state.notes != null || AVAILABLE.has(status)));
    setText($("updateNotesText"), state.notes || m("update.notes_empty"));
    const buttonLabel = status === "ready" ? "update.staged" : target ? "update.badge" : "update.check";
    setText(updateButton, m(buttonLabel));
    updateButton.classList.toggle("has-update", !!target);
    show(progress, active);
    if (total > 0 && (status === "download" || status === "verify")) {
      progress.max = total;
      progress.value = received;
    } else progress.removeAttribute("value");
    renderHistory(active);
    announceUpdate();
    if (active || status === "checking") timer = setTimeout(() => request("get_update_state"), 550);
  }

  async function request(method, ...args) {
    clearTimeout(timer);
    const current = ++revision;
    try {
      const next = await bridge?.[method]?.(...args);
      if (next && current === revision) render(next);
    } catch {
      if (state?.version && current === revision) render({ ...state, status: "offline" });
    }
  }

  function confirmBoot() {
    if (workspaceReady && bridge) void bridge.confirm_update_boot().catch(() => {});
  }

  async function connect() {
    if (bridge || !window.pywebview?.api?.check_updates) return;
    bridge = window.pywebview.api;
    confirmBoot();
    await request("check_updates", false);
    if (state?.version) setInterval(() => request("check_updates", false), CHECK_INTERVAL);
  }

  function openUpdates() {
    if (!dialog.open) dialog.show();
    if (!state?.target) void request("check_updates", true);
  }

  menu.addEventListener("click", openUpdates);
  updateButton.addEventListener("click", openUpdates);
  check.addEventListener("click", () => request("check_updates", true));
  manual.addEventListener("click", () => request("manual_update"));
  versions.addEventListener("change", syncVersion);
  applyVersion.addEventListener("click", () => request("select_update_version", versions.value));
  download.addEventListener("click", () => request("download_update"));
  stop.addEventListener("click", () => request("stop_update"));
  const dialogs = new MutationObserver(announceUpdate);
  for (const other of document.querySelectorAll("dialog")) {
    if (other !== dialog) dialogs.observe(other, { attributes: true, attributeFilter: ["open"] });
  }
  window.addEventListener("pywebviewready", connect, { once: true });
  void connect();
  return () => { workspaceReady = true; confirmBoot(); announceUpdate(); };
}
