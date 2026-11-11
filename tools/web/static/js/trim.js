import { $ } from "./utils.js";
import { appError, message as m, setAttr, setText, t } from "./i18n.js";

// The trim lane is in source frames. The playback lane is in generated motion frames.
export function initTrim({ video, clock, seek }) {
  const start = $("trimStart"), end = $("trimEnd");
  const startValue = $("trimStartValue"), endValue = $("trimEndValue");
  const full = $("trimFull"), lane = $("trimRange");
  let active = false, editable = true, frames = 0, exact = false, capacity = 0;

  function sync() {
    const first = startValue.valueAsNumber || 1;
    const last = endValue.valueAsNumber || frames || first;
    const max = Math.max(1, frames), denominator = Math.max(1, max - 1);
    start.max = end.max = max;
    start.value = Math.min(max, first);
    end.value = Math.min(max, Math.max(first, last));
    lane.style.setProperty("--trim-start", (Number(start.value) - 1) / denominator);
    lane.style.setProperty("--trim-rest", (max - Number(end.value)) / denominator);
    start.disabled = end.disabled = !active || !editable || frames < 2;
    startValue.disabled = endValue.disabled = full.disabled = !active || !editable;
    const hasEnd = !!endValue.value;
    setAttr(start, "aria-valuetext", m("timeline.frame", { frame: first }));
    setAttr(end, "aria-valuetext", hasEnd ? m("timeline.frame", { frame: last }) : m("timeline.auto_end"));
    setText($("rangeSummary"), () => [
      t("timeline.range"),
      first === 1 && !hasEnd ? t("timeline.entire") : t("timeline.from_to", {
        first, end: hasEnd ? m("timeline.frame", { frame: last }) : m("timeline.auto_end_short"),
      }),
      frames ? t(exact ? "timeline.source_count" : "timeline.estimated_count", { frames }) : "",
      capacity && frames && last - first + 1 > capacity ? t("timeline.capacity", { frames: capacity }) : "",
    ].filter(Boolean).join(" · "));
  }

  function estimate() {
    if (active && !exact && Number.isFinite(video.duration) && video.duration > 0) {
      frames = Math.max(1, Math.round((video.duration - clock.timeOf(0)) * 30));
      sync();
    }
  }
  for (const event of ["loadedmetadata", "durationchange", "flowhmr:clockready"]) video.addEventListener(event, estimate);

  start.addEventListener("input", () => {
    const first = Math.min(Number(start.value), endValue.valueAsNumber || frames);
    startValue.value = first;
    sync();
    seek(first - 1);
  });
  end.addEventListener("input", () => {
    const last = Math.max(Number(end.value), startValue.valueAsNumber || 1);
    endValue.value = last >= frames ? "" : last;
    sync();
    seek(last - 1);
  });
  for (const field of [startValue, endValue]) {
    field.addEventListener("input", sync);
    field.addEventListener("change", () => {
      const frame = field.valueAsNumber;
      if (Number.isSafeInteger(frame) && frame >= 1 && (!frames || frame <= frames)) seek(frame - 1);
    });
  }
  full.onclick = () => { startValue.value = 1; endValue.value = ""; sync(); };
  sync();

  return {
    begin() { active = editable = true; frames = 0; exact = false; sync(); },
    clear() { active = false; editable = true; frames = 0; exact = false; full.onclick(); },
    setCapacity(max) { capacity = max || 0; sync(); },
    setEditable(value) { if (editable !== value) { editable = value; sync(); } },
    setSource(count) {
      if (Number.isSafeInteger(count) && count > 0 && (frames !== count || !exact)) { frames = count; exact = true; sync(); }
    },
    apply(params) {
      startValue.value = (params.start_frame ?? 0) + 1;
      const oldLimit = params.auto_frames === false ? params.max_frames : null;
      const requested = params.end_frame == null ? null : params.end_frame + 1;
      endValue.value = (oldLimit && requested ? Math.min(oldLimit, requested) : oldLimit ?? requested) ?? "";
      sync();
    },
    read() {
      const first = startValue.valueAsNumber, last = endValue.value === "" && !endValue.validity.badInput ? null : endValue.valueAsNumber;
      if (!Number.isSafeInteger(first) || first < 1 || (last !== null && (!Number.isSafeInteger(last) || last < first))) {
        throw appError("timeline.invalid");
      }
      if (exact && (first > frames || (last !== null && last > frames))) throw appError("timeline.out_of_range", { frames });
      return { auto_frames: true, start_frame: first - 1, end_frame: last === null ? null : last - 1 };
    },
  };
}
