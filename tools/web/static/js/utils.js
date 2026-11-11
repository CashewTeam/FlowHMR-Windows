import { getLanguage, setText, t } from "./i18n.js";

export const $ = (id) => document.getElementById(id);

export function show(el, visible = true) {
  (typeof el === "string" ? $(el) : el).classList.toggle("hidden", !visible);
}

export function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) setText(node, text);
  return node;
}

export function formatSeconds(s) {
  if (s == null || !isFinite(s)) return "";
  if (s < 60) return `${s.toFixed(1)}s`;
  return `${Math.floor(s / 60)}m ${Math.round(s % 60)}s`;
}

export function formatRelative(tsSeconds, nowSeconds) {
  const d = Math.max(0, (nowSeconds ?? Date.now() / 1000) - tsSeconds);
  if (d < 60) return t("time.now");
  const relative = new Intl.RelativeTimeFormat(getLanguage(), { numeric: "always" });
  if (d < 3600) return relative.format(-Math.floor(d / 60), "minute");
  if (d < 86400) return relative.format(-Math.floor(d / 3600), "hour");
  return new Date(tsSeconds * 1000).toLocaleString(getLanguage());
}

export function formatBytes(n) {
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

export const statusLabel = (status) => t(`job.${status}`, {}, status);

export function jobLabel(job) {
  if (job.person_count > 1) return `${job.filename} · ${t("person.count", { count: job.person_count })}`;
  return job.selected_person == null ? job.filename : `${job.filename} · ${t("person.number", { number: job.selected_person + 1 })}`;
}

/** Distinct colours for person boxes; index = candidate index. */
export const PERSON_COLORS = ["#3b82f6", "#f59e0b", "#8b5cf6", "#ec4899", "#14b8a6", "#ef4444", "#84cc16", "#0ea5e9"];
export const personColor = (i) => PERSON_COLORS[i % PERSON_COLORS.length];

/** Minimal event bus shared by the page modules. */
export function createBus() {
  const handlers = {};
  return {
    on(type, fn) { (handlers[type] ||= []).push(fn); },
    emit(type, payload) { (handlers[type] || []).forEach((fn) => fn(payload)); },
  };
}
