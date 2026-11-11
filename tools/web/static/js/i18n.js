// Add shared UI text to each catalog in ./locales, using stable keys and
// identical {placeholders}. HTML uses data-i18n and its attribute variants.
// Code can bind a message (or a function for composite text) to an existing
// element so switching language never recreates inputs, controls or previews.
import zhCN from "./locales/zh-CN.js";
import zhTW from "./locales/zh-TW.js";
import en from "./locales/en.js";
import ja from "./locales/ja.js";
import ko from "./locales/ko.js";
import ru from "./locales/ru.js";
import fr from "./locales/fr.js";

export const catalogs = Object.freeze({ "zh-CN": zhCN, "zh-TW": zhTW, en, ja, ko, ru, fr });
const storageKey = "flowhmr-language";
let language = "zh-CN";
try {
  const stored = localStorage.getItem(storageKey);
  if (Object.hasOwn(catalogs, stored)) language = stored;
} catch { /* preferences may be unavailable in some WebViews */ }
const bindings = new WeakMap();

export const getLanguage = () => language;
export const message = (key, params = {}, fallback = "") => ({ key, params, fallback });
const interpolate = (template, params) =>
  template.replace(/\{(\w+)\}/g, (match, name) => Object.hasOwn(params, name) ? localize(params[name]) : match);

export function t(key, params = {}, fallback = key) {
  const own = (catalog) => Object.hasOwn(catalog, key) ? catalog[key] : undefined;
  return interpolate(own(catalogs[language]) ?? own(zhCN) ?? localize(fallback), params ?? {});
}

export function localize(value) {
  if (typeof value === "function") return localize(value());
  if (value instanceof Error) return localize(value.i18n ?? value.message);
  if (value && typeof value === "object" && typeof value.key === "string") {
    return t(value.key, value.params, value.fallback);
  }
  return value == null ? "" : String(value);
}

export function appError(key, params, fallback) {
  const i18n = message(key, params, fallback);
  return Object.assign(new Error(localize(i18n)), { i18n });
}

export function serverMessage(code, params = {}, fallback) {
  if (!code) return fallback;
  return message(`server.${code}`, params?.field == null ? params : {
    ...params,
    field: message(`field.${params.field}`, {}, params.field),
  }, fallback);
}

function apply(node, target, value) {
  const localized = localize(value);
  if (target === "text") node.textContent = localized;
  else node.setAttribute(target, localized);
}

function bind(node, target, value) {
  let props = bindings.get(node);
  if (typeof value === "function" || (value !== null && typeof value === "object")) {
    if (!props) { props = new Map(); bindings.set(node, props); }
    props.set(target, value);
    node.setAttribute("data-live-text", "");
  } else if (props) {
    props.delete(target);
    if (!props.size) node.removeAttribute("data-live-text");
  }
  apply(node, target, value);
}

export const setText = (node, value) => bind(node, "text", value);
export const setAttr = (node, attr, value) => bind(node, attr, value);

export function setLanguage(next) {
  if (!Object.hasOwn(catalogs, next) || next === language) return;
  language = next;
  document.documentElement.lang = next;
  try { localStorage.setItem(storageKey, next); } catch { /* still switch for this session */ }
  for (const node of document.querySelectorAll("[data-live-text]")) {
    for (const [target, value] of bindings.get(node) ?? []) apply(node, target, value);
  }
  window.dispatchEvent(new CustomEvent("flowhmr:languagechange", { detail: { language: next } }));
}

export function setupLocalization() {
  document.documentElement.lang = language;
  for (const node of document.querySelectorAll("[data-i18n], [data-i18n-title], [data-i18n-label], [data-i18n-placeholder]")) {
    if (node.dataset.i18n) setText(node, message(node.dataset.i18n));
    if (node.dataset.i18nTitle) setAttr(node, "title", message(node.dataset.i18nTitle));
    if (node.dataset.i18nLabel) setAttr(node, "aria-label", message(node.dataset.i18nLabel));
    if (node.dataset.i18nPlaceholder) setAttr(node, "placeholder", message(node.dataset.i18nPlaceholder));
  }
  const control = document.getElementById("languageSelect");
  control.value = language;
  let changed = false;
  let writeQueue = Promise.resolve();
  const syncNative = (next) => {
    writeQueue = writeQueue.then(() => window.pywebview?.api?.set_ui_language?.(next)).catch(() => {});
  };
  const connectNative = async () => {
    try {
      const saved = await window.pywebview?.api?.get_ui_language?.();
      if (!changed && Object.hasOwn(catalogs, saved)) {
        setLanguage(saved);
        control.value = language;
      } else syncNative(language);
    } catch { /* keep the locale already saved for this WebView origin */ }
  };
  window.addEventListener("pywebviewready", connectNative, { once: true });
  control.addEventListener("change", () => {
    changed = true;
    setLanguage(control.value);
    syncNative(language);
  });
  if (window.pywebview?.api) void connectNative();
}
