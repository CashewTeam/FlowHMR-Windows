// "Pipeline" card: queue state, stage progress and elapsed time, errors.
import { $, show, el, formatSeconds, statusLabel, jobLabel } from "./utils.js";
import { localize, message as m, serverMessage, setAttr, setText, t } from "./i18n.js";

// Older histories and older runtimes carry human-readable progress strings.
// Prefer structured message_code/message_params when present.
const LEGACY = new Map(Object.entries({
  "Starting": "job.preparing",
  "Starting bbox extraction": "stage.detection",
  "Bbox cached": "job.cached_detection",
  "Transcoded, detecting": "stage.detection",
  "Waiting for person selection": "job.waiting_user",
  "Bbox done": "job.detection_done",
  "Using a static camera": "camera.static",
  "Static camera: estimation skipped": "camera.static",
  "Starting VGGT camera extraction": "stage.camera",
  "VGGT camera ready": "job.camera_ready",
  "已复用相机估计": "job.cached_camera",
  "Starting SAM extraction": "stage.feature_extract",
  "SAM cached": "job.cached_features",
  "SAM done": "job.features_done",
  "Starting generation": "job.generating",
  "ODE sampling done, exporting": "job.exporting",
  "正在平滑动作": "job.smoothing",
  "Origin FBX saved": "job.fbx_saved",
  "正在修正脚底接触": "job.correcting",
  "动作生成完成": "job.generated",
  "Generation complete": "job.generated",
  "正在启动 HaMeR 手指精修": "job.hamer_start",
  "正在加载 HaMeR 手指模型": "job.hamer_start",
  "正在保存手指精修结果": "job.hamer_saving",
  "手指精修完成": "job.hamer_done",
  "HaMeR 手指精修完成": "job.hamer_done",
  "HaMeR 未找到有效手部画面，已保留原动作": "progress.hamer_untracked",
  "Building 3D mesh": "job.building",
  "Waiting in queue": "progress.queue",
  "Cancelled": "job.stopped",
  "Stopped by user": "job.stopped",
  "Run stopped when FlowHMR restarted.": "job.restarted",
  "Stop requested; stopping at the next safe step": "job.stopping_safe",
  "正在对齐多人站位…": "job.scene_aligning",
  "等待对齐多人站位…": "job.scene_waiting",
  "正在加载站位估计模型…": "job.scene_loading",
  "动作已生成，可按原片对齐多人场景。": "job.scene_ready",
  "首次运行，正在加载推理模型…": "job.model_loading",
  "No selection - using default person": "job.default_person",
  "Recovered from an earlier session; detailed progress is unavailable.": "server.job_details_unavailable",
  "Failed": "job.failed",
  "Done": "job.done",
}));
const COUNTERS = [
  [/^SAM: (\d+)\/(\d+) frames$/, "job.sam_frames"],
  [/^正在补提取手框 · (\d+) \/ (\d+) 帧$/, "job.hand_box_frames"],
  [/^HaMeR 手指精修 · (\d+) \/ (\d+) 帧$/, "job.hamer_frames"],
  [/^对齐多人站位 · (\d+)\/(\d+) 个素材帧$/, "job.scene_frames"],
];

function legacyMessage(value) {
  if (!value) return "";
  if (LEGACY.has(value)) return t(LEGACY.get(value));
  for (const [pattern, key] of COUNTERS) {
    const match = value.match(pattern);
    if (match) return t(key, { current: match[1], total: match[2] });
  }
  const camera = value.match(/^(CPU 相机估计|省显存相机估计|相机估计) · 采样 (\d+)\/(\d+) 帧 · 间隔 (\d+)$/);
  if (camera) return t("progress.camera_samples", {
    mode: m({ "CPU 相机估计": "progress.camera_cpu", "省显存相机估计": "progress.camera_low", "相机估计": "stage.camera" }[camera[1]]),
    samples: camera[2], frames: camera[3], interval: camera[4],
  });
  const detected = value.match(/^Found (\d+) person\(s\)$/);
  if (detected) return t("job.detected_count", { count: detected[1] });
  const chosen = value.match(/^已选择(?:(?:人物 (\d+))| (\d+) 人)$/);
  if (chosen) return chosen[1] ? t("job.chosen_one", { number: chosen[1] }) : t("job.chosen_count", { count: chosen[2] });
  return value;
}

export function initProgress() {
  const list = $("stageList");

  function renderStages(stages, now) {
    const rows = [];
    for (const s of stages) {
      const row = el("li", `stage ${s.status}`);
      const key = s.label === "Static camera" ? "camera.static" : `stage.${s.name}`;
      setAttr(row, "title", m(s.label === "Static camera" ? "stage.hint_static" : `stage.hint_${s.name}`, {}, s.detail || ""));
      row.append(el("span", "stage-dot"), el("span", "stage-label", m(key, {}, s.label)));
      const bar = el("div", "stage-bar");
      const fill = el("div", "stage-fill");
      fill.style.width = `${s.status === "completed" ? 100 : s.progress}%`;
      bar.append(fill);
      row.append(bar);
      row.append(el("span", "stage-pct num", `${s.status === "completed" ? 100 : Math.round(s.progress)}%`));
      let time = formatSeconds(s.elapsed_seconds);
      if (s.start) time = formatSeconds(Math.max(0, (s.end || s.paused_at || now) - s.start - (s.paused_seconds || 0)));
      row.append(el("span", "stage-time num", s.status === "waiting_user" ? m("job.waiting") : time));
      rows.push(row);
    }
    list.replaceChildren(...rows);
  }

  function jobMessage(job) {
    if (job.status === "waiting_user") return t("job.choose_in_video");
    if (job.status === "done") {
      return [
        t("progress.finished_frames", { frames: job.num_frames }),
        job.person_count ? t("progress.finished_people", { done: job.person_done, total: job.person_count }) : "",
        job.track_range ? t("timeline.source_span", { start: job.track_range[0] + 1, end: job.track_range[1] + 1 }) : "",
        job.finished && job.started ? formatSeconds(job.finished - job.started) : "",
      ].filter(Boolean).join(" · ");
    }
    const value = job.message_code
      ? t(`job.${job.message_code}`, job.message_params, () => legacyMessage(job.message))
      : legacyMessage(job.message);
    return job.current_person ? [
      t("person.number", { number: job.current_person }),
      t("progress.finished_people", { done: job.person_done, total: job.person_count }), value,
    ].filter(Boolean).join(" · ") : value;
  }

  function renderCamera(camera) {
    const node = $("jobCamera");
    setText(node, !camera ? "" : camera.mode === "static" ? m("camera.static") : m("progress.camera_samples", {
      mode: m(camera.profile === "low_vram" ? "progress.camera_low" : camera.profile === "cpu" ? "progress.camera_cpu"
        : camera.mode === "auto" ? "camera.auto" : "camera.manual"),
      samples: camera.samples, frames: camera.frames, interval: camera.interval,
    }));
    show(node, !!camera);
  }

  function renderBatch(batch, status) {
    const node = $("jobSamBatch");
    if (batch) {
      setText(node, () => [
        "SAM",
        t(batch.reused ? "progress.sam_reused" : batch.requested == null ? "common.auto" : "common.manual"),
        (status === "done" || batch.reused) && batch.min_batch < batch.max_batch
          ? t("progress.sam_range", { min: batch.min_batch, max: batch.max_batch })
          : batch.actual == null ? t("progress.sam_unrecorded") : t("progress.sam_actual", { frames: batch.actual }),
        !batch.reused && batch.total_mib ? t("progress.vram_free", {
          free: (Math.min(batch.free_mib, batch.driver_free_mib ?? Infinity) / 1024).toFixed(1),
          total: (batch.total_mib / 1024).toFixed(1),
        }) : "",
        !batch.reused && batch.ram_free_mib != null
          ? t("progress.ram_free", { free: (batch.ram_free_mib / 1024).toFixed(1) }) : "",
        batch.retries && !batch.reused ? t("progress.sam_retries", { count: batch.retries }) : "",
      ].filter(Boolean).join(" · "));
    }
    show(node, !!batch);
  }

  function renderHamer(hamer) {
    const node = $("jobHamer");
    if (hamer) {
      setText(node, hamer.left || hamer.right ? () => [
        "HaMeR",
        hamer.stride > 1 ? t("progress.hamer_stride", { stride: hamer.stride }) : hamer.stride === 1 ? t("hamer.every_frame") : "",
        t("progress.hamer_hands", { left: hamer.left, right: hamer.right, frames: hamer.frames }),
        formatSeconds(hamer.seconds),
      ].filter(Boolean).join(" · ") : m("progress.hamer_untracked"));
      setAttr(node, "title", hamer.stride > 1
        ? m("progress.hamer_sampled", { left: hamer.sampled_left, right: hamer.sampled_right }) : "");
    }
    show(node, !!hamer);
  }

  function renderCorrection(correction, params) {
    const node = $("jobCorrection");
    const sole = correction?.sole_fit;
    setText(node, correction ? () => [
      t(correction.preset === "strong" ? "correction.lock_strong" : "correction.lock_normal"),
      `${t(correction.automatic ? "correction.floor_auto" : "correction.floor_manual")} ${correction.floor_m.toFixed(3)} m`,
      correction.contacts ? formatSeconds(correction.seconds) : t("progress.correction_no_contact"),
      sole ? t(sole.adjusted ? "progress.correction_sole" : "progress.correction_sole_unchanged",
        { strength: sole.strength ?? params?.sole_fit_strength ?? 0.6 }) : "",
    ].filter(Boolean).join(" · ") : "");
    setAttr(node, "title", correction?.warning || "");
    show(node, !!correction);
  }

  function setBadge(status) {
    const b = $("jobBadge");
    setText(b, () => statusLabel(status));
    b.className = `badge ${status}`;
  }

  return {
    showUploading(name, frac) {
      show("jobCard");
      for (const id of ["btnRerun", "btnAlignScene", "btnSafeRerun", "btnStopRun", "queueBox",
        "jobLongMotion", "jobCamera", "jobSamBatch", "jobHamer", "jobCorrection", "jobError"]) show(id, false);
      setText($("jobName"), name);
      setBadge("uploading");
      setText($("jobMsg"), m("job.importing", { percent: Math.round(frac * 100) }));
    },
    render(job) {
      show("jobCard");
      show("btnRerun", ["done", "failed", "cancelled", "needs_alignment"].includes(job.status));
      show("btnAlignScene", !!job.scene_needs_build);
      show("btnSafeRerun", ["failed", "cancelled"].includes(job.status) && !job.params.static_camera);
      const stop = $("btnStopRun");
      show(stop, ["queued", "running", "waiting_user", "cancel_requested"].includes(job.status));
      stop.disabled = job.status === "cancel_requested";
      setText(stop, m(stop.disabled ? "job.stopping" : "common.stop"));
      setText($("jobName"), () => jobLabel(job));
      $("jobId").textContent = job.id;
      setBadge(job.status);
      const queued = job.status === "queued";
      show("queueBox", queued);
      if (queued) setText($("queueText"), job.queue_position > 0
        ? m("progress.ahead", { count: job.queue_position }) : m("progress.starting_soon"));
      renderStages(job.stages, job.now);
      setText($("jobMsg"), () => jobMessage(job));
      setText($("jobLongMotion"), job.long_motion_frames
        ? m("progress.long_motion", { frames: job.long_motion_frames }) : "");
      show("jobLongMotion", !!job.long_motion_frames);
      renderCamera(job.camera);
      renderBatch(job.sam_batch, job.status);
      renderHamer(job.hamer_report);
      renderCorrection(job.correction, job.params);
      setText($("workspaceState"), () => job.status === "running" ? `${job.progress}% · ${jobMessage(job)}`
        : job.status === "done" ? `${jobMessage(job)} · FlowHMR` : statusLabel(job.status));
      const error = serverMessage(job.error_code, job.error_params, job.error);
      setText($("jobError"), error);
      setAttr($("jobError"), "title", job.error || "");
      show("jobError", job.status === "failed" && !!localize(error));
    },
    showError(msg) {
      show("jobCard");
      for (const id of ["btnRerun", "btnAlignScene", "btnSafeRerun", "btnStopRun",
        "jobLongMotion", "jobCamera", "jobSamBatch", "jobHamer", "jobCorrection"]) show(id, false);
      setBadge("failed");
      setText($("jobError"), msg);
      setAttr($("jobError"), "title", "");
      show("jobError");
    },
    showActionError(msg) {
      show("jobCard");
      setText($("jobError"), msg);
      setAttr($("jobError"), "title", "");
      show("jobError");
    },
  };
}
