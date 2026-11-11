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
export const startModelImport = (path, kind = "folder") => postJson("/api/models/import", { path, kind });
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
export async function getCharacterMotion(id, signal) {
  const response = await request(`/api/jobs/${id}/pmx-motion`, { signal });
  const meta = JSON.parse(response.headers.get("X-FlowHMR-PMX-Motion"));
  const data = new Float32Array(await response.arrayBuffer());
  const xyz = 52 * 3;
  const jointsEnd = xyz * (meta.frames + 1);
  const rotationsEnd = jointsEnd + meta.frames * 52 * 4;
  const contactEnd = rotationsEnd + meta.frames * 4;
  if (!Number.isInteger(meta.frames) || meta.frames < 1 || !Number.isFinite(meta.floor)
      || data.length !== contactEnd + (meta.hasSoles ? meta.frames * 4 : 0) || meta.parents?.length !== 52) {
    throw appError("server.pmx_motion");
  }
  return { ...meta, rest: data.subarray(0, xyz), joints: data.subarray(xyz, jointsEnd),
    quaternions: data.subarray(jointsEnd, rotationsEnd), contacts: data.subarray(rotationsEnd, contactEnd),
    soles: meta.hasSoles ? data.subarray(contactEnd) : null };
}
export function characterResourceUrl(ticket, texture) {
  const base = `/api/character-resource/${encodeURIComponent(ticket)}`;
  return texture == null ? base : `${base}?${new URLSearchParams({ texture })}`;
}
export async function getCharacterFile({ ticket, name }, signal) {
  const response = await request(characterResourceUrl(ticket), { signal, cache: "no-store" });
  return new File([await response.blob()], name);
}
export async function getCharacterTexture(ticket, texture, signal) {
  const response = await fetch(characterResourceUrl(ticket, texture), { signal, cache: "no-store", redirect: "error" });
  return response.ok ? new File([await response.blob()], texture.split(/[\\/]/).at(-1)) : null;
}
const characterActorsQuery = (actors) => new URLSearchParams(actors.map((actor) => ["actor", actor.id]));
export const getCharacterLibrary = (actors, signal) => json(`/api/character-library?${characterActorsQuery(actors)}`,
  { signal, cache: "no-store" });
export const saveLibraryCharacter = (ticket, name) => postJson("/api/character-library", { ticket, name });
export const openLibraryCharacter = (id, signal) => json(`/api/character-library/${encodeURIComponent(id)}/open`,
  { method: "POST", signal });
export const deleteLibraryCharacter = (id, actors) => json(
  `/api/character-library/${encodeURIComponent(id)}?${characterActorsQuery(actors)}`, { method: "DELETE" });
export const saveActorCharacter = (actor, id, offsets, select) => json(
  `/api/character-library/actors/${encodeURIComponent(actor.id)}`, {
    method: "PUT", headers: { "Content-Type": "application/json" }, keepalive: !select,
    body: JSON.stringify({ id, offsets, select, person: actor.number ?? null }),
  });
export const selectPeople = (id, people) => postJson(`/api/jobs/${id}/select`,
  people.length === 1 ? { people, person: people[0] } : { people });
export const rerunJob = (id, params) => postJson(`/api/jobs/${id}/rerun`, params).then((res) => res.job_id);
export const cancelJob = (id) => postJson(`/api/jobs/${id}/cancel`);
export const prepareScene = (id) => postJson(`/api/jobs/${id}/scene`);

export const videoUrl = (id, converted) => `/api/jobs/${id}/video?source=${converted ? "preview" : "original"}`;
export const npzUrl = (id) => `/api/jobs/${id}/npz`;
export const fbxUrl = (id, withSkin, body = "neutral", people = [], firstPose = "none", profile = "flowhmr") => {
  const query = new URLSearchParams({ mesh: withSkin ? "1" : "0", body, first_pose: firstPose });
  if (profile !== "flowhmr") query.set("profile", profile);
  for (const person of people) query.append("person", String(person));
  return `/api/jobs/${id}/fbx?${query}`;
};
export const vmdUrl = (id, people = []) => {
  const query = new URLSearchParams();
  for (const person of people) query.append("person", String(person));
  return `/api/jobs/${id}/vmd${people.length ? `?${query}` : ""}`;
};
export async function exportMetaHuman(id, people, template, signal) {
  const body = new FormData();
  body.append("template", template);
  const response = await request(fbxUrl(id, false, "source", people, "none", "metahuman"), { method: "POST", body, signal });
  const header = response.headers.get("Content-Disposition") ?? "";
  const encoded = /filename\*=UTF-8''([^;]+)/i.exec(header)?.[1];
  let filename = /filename="([^"]+)"|filename=([^;]+)/i.exec(header)?.slice(1).find(Boolean) ?? "metahuman.fbx";
  if (encoded) {
    try { filename = decodeURIComponent(encoded); } catch { /* use the plain name */ }
  }
  return { filename, blob: await response.blob() };
}
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
