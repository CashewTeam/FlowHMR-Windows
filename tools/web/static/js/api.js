// Thin client for the FlowHMR demo server (tools/web/server.py).
import { appError, localize, message, serverMessage } from "./i18n.js";

function httpError(payload, status, statusText = "") {
  const fallback = payload?.error || message("server.http_status", { status, statusText });
  const code = payload?.error_code ?? payload?.code;
  let params = payload?.error_params;
  if (code === "body_reference_missing" && typeof params?.body === "string") {
    params = { ...params, body: message(`view.${params.body}`, {}, params.body) };
  }
  const i18n = serverMessage(code, params, fallback);
  return Object.assign(new Error(localize(i18n)), { i18n, status });
}

async function request(path, options = {}) {
  let res;
  try { res = await fetch(path, options); }
  catch (error) {
    if (error.name === "AbortError") throw error;
    throw appError("server.network_error");
  }
  if (!res.ok) {
    let body;
    try { body = await res.json(); } catch { /* not JSON */ }
    throw httpError(body, res.status, res.statusText);
  }
  return res;
}

const json = (path, options) => request(path, options).then((r) => r.json());
const postJson = (path, body) => json(path, {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body || {}),
});

export const getInfo = () => json("/api/info", { cache: "no-store" });
export const getModelDownload = () => json("/api/models/download", { cache: "no-store" });
export const startModelDownload = (group) => postJson("/api/models/download", group ? { group } : {});
export const stopModelDownload = (id) => postJson("/api/models/download/stop", { id });
export const verifyModels = () => postJson("/api/models/verify");
export const getModelImport = () => json("/api/models/import", { cache: "no-store" });
export const startModelImport = (path) => postJson("/api/models/import", { path });
export const getRuntime = () => json("/api/runtime", { cache: "no-store" });
export const startRuntimeDownload = (version) => postJson("/api/runtime/download", { version });
export const stopRuntimeDownload = (id) => postJson("/api/runtime/download/stop", { id });
export const selectRuntime = (version) => postJson("/api/runtime/select", { version });
export function getJobs({ before, through } = {}) {
  const query = new URLSearchParams({ limit: "20" });
  if (before) query.set("before", before);
  if (through) query.set("through", through);
  return json(`/api/jobs?${query}`, { cache: "no-store" });
}
export const deleteJobs = (ids) => json("/api/jobs", {
  method: "DELETE",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(ids ? { ids } : {}),
});
export const getJob = (id) => json(`/api/jobs/${id}`);
export const getMeta = (id, signal) => json(`/api/jobs/${id}/meta`, { signal });
export const getVertices = (id, signal, body = "neutral") => request(`/api/jobs/${id}/vertices?body=${encodeURIComponent(body)}`, { signal }).then((r) => r.arrayBuffer());
export const getFaces = (id, signal) => request(`/api/jobs/${id}/faces`, { signal }).then((r) => r.arrayBuffer());
export async function getInspection(id, signal) {
  const response = await request(`/api/jobs/${id}/skeleton`, { signal });
  const info = JSON.parse(response.headers.get("X-FlowHMR-Inspection"));
  return { ...info, joints: new Float32Array(await response.arrayBuffer()) };
}
export const selectPeople = (id, people) => postJson(`/api/jobs/${id}/select`,
  people.length === 1 ? { people, person: people[0] } : { people });
export const rerunJob = (id, params) => postJson(`/api/jobs/${id}/rerun`, params).then((res) => res.job_id);
export const cancelJob = (id) => postJson(`/api/jobs/${id}/cancel`);
export const prepareScene = (id) => postJson(`/api/jobs/${id}/scene`);

export const videoUrl = (id) => `/api/jobs/${id}/video`;
export const npzUrl = (id) => `/api/jobs/${id}/npz`;
export const fbxUrl = (id, withSkin, body = "neutral", people = [], firstPose = "none") => {
  const query = new URLSearchParams({ mesh: withSkin ? "1" : "0", body, first_pose: firstPose });
  for (const person of people) query.append("person", String(person));
  return `/api/jobs/${id}/fbx?${query}`;
};
export const personThumbUrl = (id, i) => `/api/jobs/${id}/person/${i}.jpg`;
export const thumbnailUrl = (id) => `/api/jobs/${id}/thumbnail.jpg`;

/** Upload a video with per-job parameters. Resolves to the new job id. */
export function uploadVideo(file, params, onProgress) {
  return new Promise((resolve, reject) => {
    const form = new FormData();
    form.append("video", file);
    for (const [k, v] of Object.entries(params)) form.append(k, String(v));
    const xhr = new XMLHttpRequest();
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable && onProgress) onProgress(e.loaded / e.total);
    };
    xhr.onload = () => {
      let res = {};
      try { res = JSON.parse(xhr.responseText); } catch { /* noop */ }
      if (xhr.status === 200 && res.job_id) resolve(res.job_id);
      else reject(httpError(res, xhr.status));
    };
    xhr.onerror = () => reject(appError("source.upload_network"));
    xhr.open("POST", "/api/upload");
    xhr.send(form);
  });
}
