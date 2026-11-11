// "Video preview" card: plays the local file before upload / the job video afterwards and
// overlays person boxes on a canvas (all candidates while choosing, the tracked person after).
import { $, show, personColor } from "./utils.js";
import { message as m, setText } from "./i18n.js";

export function initPreview(bus) {
  const video = $("previewVideo");
  const canvas = $("previewCanvas");
  const stage = $("previewBox");
  const ctx = canvas.getContext("2d");
  const noHover = window.matchMedia("(any-hover: none)");
  let localUrl = null;
  let candidates = [];      // [{frames, boxes, ...}] while waiting for a pick
  let selected = new Set(), previewIndex = null;
  let tracks = [];         // {start, end, boxes, person} for each reconstructed track
  let locked = null;        // {candidate, index} once the person is confirmed
  let pickable = false;
  let muteBeforePicking = null, candidateLoop = null, previewPending = false;

  let baseSrc = null;       // source without the retry cache-buster
  let retries = 0;
  let retryTimer = null;
  let drawRequest = 0;
  let mediaRequest = null;
  let originRequest = null;
  let origin = null;
  const clock = {
    get ready() { return origin !== null; },
    frameAt: (time, fps = 30) => Math.floor((time - (origin ?? 0)) * fps + 1e-4),
    timeOf: (frame, fps = 30) => (origin ?? 0) + frame / fps,
  };

  function syncControls() {
    const visible = !!baseSrc && (stage.matches(":hover") || video.matches(":focus-visible") ||
      noHover.matches || document.fullscreenElement === video);
    if (video.controls !== visible) video.controls = visible;
  }

  for (const event of ["pointerenter", "pointerleave", "focusin", "focusout"]) {
    stage.addEventListener(event, () => requestAnimationFrame(syncControls));
  }
  noHover.addEventListener("change", syncControls);
  document.addEventListener("fullscreenchange", syncControls);

  function resetClock() {
    if (originRequest !== null) video.cancelVideoFrameCallback(originRequest);
    if (mediaRequest !== null) video.cancelVideoFrameCallback(mediaRequest);
    originRequest = mediaRequest = null;
    origin = null;
  }

  function loadSource(src) {
    resetClock();
    video.src = src;
    video.load();
    if (video.requestVideoFrameCallback) {
      // MP4 audio can start before the first video frame. The first presented frame's
      // timestamp is the origin of the 30 fps track used by OpenCV and the motion model.
      originRequest = video.requestVideoFrameCallback((_, first) => {
        originRequest = null;
        origin = first.mediaTime;
        refreshOverlay();
        video.dispatchEvent(new Event("flowhmr:clockready"));
        if (previewPending) playCandidate(true);
      });
    } else origin = 0;
  }

  function setSource(src, { local = false } = {}) {
    if (localUrl && src !== localUrl) { URL.revokeObjectURL(localUrl); localUrl = null; }
    if (local) localUrl = src;
    clearTimeout(retryTimer);
    baseSrc = src;
    retries = 0;
    stage.style.removeProperty("--video-ratio");
    showPreviewIssue("");
    loadSource(src);
    show("previewBox", true);
    syncControls();
  }

  // A job video can fail to load if it was requested while the server was still writing
  // it; retry a few times with a cache-buster so the browser does not reuse the bad copy.
  video.addEventListener("error", () => {
    if (!baseSrc) return;
    if (baseSrc === localUrl || retries >= 10) {
      showPreviewIssue(m(baseSrc === localUrl ? "source.preview_convert" : "source.preview_retry"));
      return;
    }
    retries += 1;
    clearTimeout(retryTimer);
    retryTimer = setTimeout(() => {
      loadSource(`${baseSrc}${baseSrc.includes("?") ? "&" : "?"}r=${Date.now()}`);
    }, 1500);
  });
  video.addEventListener("loadedmetadata", () => {
    if (video.videoWidth && video.videoHeight) stage.style.setProperty("--video-ratio", video.videoWidth / video.videoHeight);
  });
  video.addEventListener("loadeddata", () => {
    retries = 0; showPreviewIssue("");
    if (previewPending) playCandidate(true);
  });

  function showPreviewIssue(message) {
    setText($("previewIssue"), message);
    show("previewIssue", !!message);
  }

  function clear() {
    if (localUrl) { URL.revokeObjectURL(localUrl); localUrl = null; }
    clearTimeout(retryTimer);
    baseSrc = null;
    stopCandidatePreview();
    resetClock();
    video.removeAttribute("src");
    video.load();
    candidates = []; tracks = []; selected.clear(); previewIndex = null; locked = null;
    show("previewBox", false);
    syncControls();
    showPreviewIssue("");
    setText($("previewTitle"), "");
    refreshOverlay();
  }

  // Box of a candidate at a (30 fps) frame, linearly interpolated between its detections.
  function boxAt(c, f) {
    const fr = c.frames;
    if (!fr || !fr.length || f < fr[0] - 2 || f > fr[fr.length - 1] + 2) return null;
    if (f <= fr[0]) return c.boxes[0];
    if (f >= fr[fr.length - 1]) return c.boxes[fr.length - 1];
    let lo = 0, hi = fr.length - 1;
    while (lo <= hi) {
      const mid = (lo + hi) >> 1;
      if (fr[mid] === f) return c.boxes[mid];
      if (fr[mid] < f) lo = mid + 1; else hi = mid - 1;
    }
    const a = Math.max(0, hi), b = Math.min(fr.length - 1, lo);
    if (a === b) return c.boxes[a];
    const t = (f - fr[a]) / (fr[b] - fr[a]);
    return c.boxes[a].map((v, k) => v + (c.boxes[b][k] - v) * t);
  }

  // Map video pixels to CSS coordinates, accounting for object-fit: contain.
  function layout() {
    const cw = video.clientWidth, ch = video.clientHeight;
    const vw = video.videoWidth, vh = video.videoHeight;
    if (!vw || !vh || !cw || !ch) return null;
    const s = Math.min(cw / vw, ch / vh);
    return { s, ox: (cw - vw * s) / 2, oy: (ch - vh * s) / 2, cw, ch };
  }

  function drawBox(L, box, color, label, strong) {
    const [x1, y1, x2, y2] = box;
    const rx = x1 * L.s + L.ox, ry = y1 * L.s + L.oy, rw = (x2 - x1) * L.s, rh = (y2 - y1) * L.s;
    ctx.strokeStyle = color;
    ctx.lineWidth = strong ? 3 : 2;
    ctx.setLineDash(strong ? [] : [6, 4]);
    ctx.strokeRect(rx, ry, rw, rh);
    ctx.setLineDash([]);
    if (label) {
      ctx.font = "600 12px Inter, -apple-system, sans-serif";
      const tw = ctx.measureText(label).width + 10;
      ctx.fillStyle = color;
      ctx.fillRect(rx - (strong ? 1.5 : 1), Math.max(0, ry - 20), tw, 20);
      ctx.fillStyle = "#fff";
      ctx.fillText(label, rx + 4, Math.max(14, ry - 6));
    }
  }

  function draw() {
    drawRequest = 0;
    const dpr = window.devicePixelRatio || 1;
    const w = Math.round(canvas.clientWidth * dpr), h = Math.round(canvas.clientHeight * dpr);
    if (canvas.width !== w || canvas.height !== h) { canvas.width = w; canvas.height = h; }
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, canvas.clientWidth, canvas.clientHeight);
    const L = layout();
    if (!L) return;
    const f = Math.max(0, clock.frameAt(video.currentTime));
    if (candidates.length) {
      candidates.forEach((c, i) => {
        const b = boxAt(c, f);
        if (b) {
          const checked = selected.has(i);
          drawBox(L, b, checked ? "#10b981" : personColor(i), `#${i + 1}${checked ? " ✓" : ""}`, checked);
        }
      });
    } else if (tracks.length) {
      for (const track of tracks) {
        if (f < track.start || f > track.end) continue;
        const b = track.boxes[f - track.start];
        if (b) drawBox(L, b, track.person ? personColor(track.person - 1) : "#10b981",
          track.person ? `#${track.person}` : locked ? `#${locked.index + 1} ✓` : null, true);
      }
    } else if (locked && locked.candidate) {
      const b = boxAt(locked.candidate, f);
      if (b) drawBox(L, b, "#10b981", `#${locked.index + 1} ✓`, true);
    }
  }

  function refreshOverlay() {
    if (!drawRequest) drawRequest = requestAnimationFrame(draw);
    const tracking = candidates.length > 0 || tracks.length > 0 || locked;
    if (tracking && !video.paused && video.requestVideoFrameCallback) {
      if (mediaRequest === null) mediaRequest = video.requestVideoFrameCallback(() => {
        mediaRequest = null;
        loopCandidate();
        refreshOverlay();
      });
    } else if (mediaRequest !== null) {
      video.cancelVideoFrameCallback(mediaRequest);
      mediaRequest = null;
    }
  }

  function playCandidate(seekStart = false) {
    const candidate = candidates[previewIndex];
    if (!pickable || !candidate) return;
    previewPending = !clock.ready || video.readyState < 2;
    if (previewPending) return;
    candidateLoop = [candidate.preview_start ?? candidate.start, candidate.preview_end ?? candidate.end];
    const frame = clock.frameAt(video.currentTime);
    if (seekStart || frame < candidateLoop[0] || frame > candidateLoop[1]) {
      video.currentTime = clock.timeOf(candidateLoop[0] + 0.5);
    }
    if (muteBeforePicking === null) {
      muteBeforePicking = video.muted;
      video.muted = true;
    }
    video.play().catch(() => {});
  }

  function loopCandidate() {
    if (!candidateLoop || !clock.ready || video.seeking) return;
    if (video.ended || (!video.paused && clock.frameAt(video.currentTime) > candidateLoop[1])) {
      video.currentTime = clock.timeOf(candidateLoop[0] + 0.5);
      if (video.paused) video.play().catch(() => {});
    }
  }

  function stopCandidatePreview() {
    previewPending = false;
    candidateLoop = null;
    if (muteBeforePicking !== null) {
      video.pause();
      video.muted = muteBeforePicking;
      muteBeforePicking = null;
    }
  }
  video.addEventListener("timeupdate", loopCandidate);
  video.addEventListener("ended", loopCandidate);
  for (const event of ["loadeddata", "emptied", "seeked", "timeupdate", "play", "pause"]) {
    video.addEventListener(event, refreshOverlay);
  }
  new ResizeObserver(refreshOverlay).observe(video);

  // Click on a box to pick that person.
  canvas.addEventListener("click", (e) => {
    if (!pickable || !candidates.length) return;
    const L = layout();
    if (!L) return;
    const r = canvas.getBoundingClientRect();
    const x = (e.clientX - r.left - L.ox) / L.s, y = (e.clientY - r.top - L.oy) / L.s;
    const f = Math.max(0, clock.frameAt(video.currentTime));
    let best = null, bestArea = Infinity;
    candidates.forEach((c, i) => {
      const b = boxAt(c, f);
      if (!b || x < b[0] || x > b[2] || y < b[1] || y > b[3]) return;
      const area = (b[2] - b[0]) * (b[3] - b[1]);
      if (area < bestArea) { best = i; bestArea = area; }
    });
    if (best !== null) bus.emit("preview:pick", best);
  });

  bus.on("person:candidates", ({ candidates: c, selected: s, previewIndex: i }) => {
    candidates = c || [];
    selected = new Set(s);
    previewIndex = i;
    locked = null;
    pickable = candidates.length > 0;
    canvas.classList.toggle("pickable", pickable);
    if (pickable) playCandidate(true); else stopCandidatePreview();
    refreshOverlay();
  });
  bus.on("person:selection", ({ selected: s, previewIndex: i }) => {
    selected = new Set(s);
    previewIndex = i;
    playCandidate();
    refreshOverlay();
  });
  bus.on("person:locked", (l) => {
    locked = l;
    candidates = [];
    selected.clear();
    previewIndex = null;
    pickable = false;
    stopCandidatePreview();
    canvas.classList.remove("pickable");
    refreshOverlay();
  });

  return {
    showLocal(file) { tracks = []; setSource(URL.createObjectURL(file), { local: true }); setText($("previewTitle"), file.name); },
    showJob(url, name, { refresh = false } = {}) {
      if (baseSrc !== url || refresh) setSource(url);
      setText($("previewTitle"), name);
    },
    setTrack(t) { tracks = t ? [t] : []; refreshOverlay(); },
    setTracks(items) { tracks = items; refreshOverlay(); },
    clear,
    video,
    clock,
    get temporaryMute() { return muteBeforePicking !== null; },
  };
}
