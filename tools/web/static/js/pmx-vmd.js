// Encode the PMX solver's authored (pre-append) pose, in the model's original bone names.
const UINT32_MAX = 0xffffffff;
const HEADER_BYTES = 54;
const BONE_BYTES = 111;
const BONE_NAME_BYTES = 15;
const PROPERTY_BYTES = 9;
const IK_BYTES = 21;
const VMD_SIGNATURE = new TextEncoder().encode("Vocaloid Motion Data 0002");
const LINEAR = new Uint8Array(64);
const channels = Uint8Array.of(...Array(8).fill(20), ...Array(8).fill(107));
for (let i = 0; i < 4; i++) LINEAR.set(channels.subarray(i), 16 * i);

export class PmxVmdExportError extends Error {
  constructor(code, message) {
    super(message);
    this.name = "PmxVmdExportError";
    this.code = code;
  }
}

function fail(code, message) {
  throw new PmxVmdExportError(`pmx_vmd_${code}`, message);
}

let cp932;

// Encoding API supports decoding Windows-31J, but TextEncoder is UTF-8 only.
// Build the reverse table once from bytes that decode to one exact code point.
function windows31j() {
  if (cp932) return cp932;
  const decoder = new TextDecoder("shift_jis", { fatal: true });
  const table = new Map();
  const one = new Uint8Array(1), two = new Uint8Array(2);
  const remember = (bytes, packed) => {
    try {
      const decoded = decoder.decode(bytes);
      if (decoded.length === 1 && !table.has(decoded)) table.set(decoded, packed);
    } catch {
      // This byte or pair is unassigned.
    }
  };
  for (let i = 0x20; i <= 0x7e; i++) {
    one[0] = i;
    remember(one, i);
  }
  for (let i = 0xa1; i <= 0xdf; i++) {
    one[0] = i;
    remember(one, i);
  }
  for (let lead = 0x81; lead <= 0xfc; lead++) {
    if (lead > 0x9f && lead < 0xe0) continue;
    two[0] = lead;
    for (let trail = 0x40; trail <= 0xfc; trail++) {
      if (trail === 0x7f) continue;
      two[1] = trail;
      remember(two, (lead << 8) | trail);
    }
  }
  cp932 = table;
  return cp932;
}

function nameBytes(name, maxBytes, kind) {
  if (typeof name !== "string" || !name) {
    if (kind) fail("name", `Missing PMX ${kind} name.`);
    return new Uint8Array(0);
  }
  const table = windows31j(), bytes = [];
  for (const ch of name) {
    const n = table.get(ch);
    if (n === undefined) {
      if (kind) fail("name", `PMX ${kind} "${name}" cannot be written exactly in Windows-31J (CP932).`);
      return new Uint8Array(0);
    }
    if (bytes.length + (n > 0xff ? 2 : 1) > maxBytes) {
      if (kind) fail("name", `PMX ${kind} "${name}" exceeds the VMD limit of ${maxBytes} bytes.`);
      break;
    }
    if (n > 0xff) bytes.push(n >>> 8);
    bytes.push(n & 0xff);
  }
  return Uint8Array.from(bytes);
}

export function canWriteVmdBoneName(name) {
  try {
    return nameBytes(name, BONE_NAME_BYTES, "bone").length > 0;
  } catch (error) {
    if (!(error instanceof PmxVmdExportError)) throw error;
    return false;
  }
}

function validateBinding(vmd, rest) {
  if (!vmd || typeof vmd.sample !== "function" || !Array.isArray(vmd.tracks) || !vmd.tracks.length ||
      !Array.isArray(vmd.ikNames) || !Array.isArray(rest?.bones)) {
    fail("binding", "The PMX solver has no exportable bone tracks.");
  }
  const byIndex = new Map();
  const counts = new Map();
  for (const bone of rest.bones) {
    if (!bone || !Number.isSafeInteger(bone.index) || bone.index < 0 || byIndex.has(bone.index)) {
      fail("binding", "The PMX has invalid or duplicate bone indices.");
    }
    byIndex.set(bone.index, bone);
    counts.set(bone.name, (counts.get(bone.name) ?? 0) + 1);
  }
  const used = new Set();
  const tracks = Array.from(vmd.tracks, (track) => {
    const bone = byIndex.get(track?.index);
    if (!bone || used.has(track.index) || bone.name !== track.name) {
      fail("binding", "An export track does not match a unique PMX bone index.");
    }
    used.add(track.index);
    if (counts.get(track.name) !== 1) fail("name", `PMX bone name "${track.name}" is ambiguous.`);
    return nameBytes(track.name, BONE_NAME_BYTES, "bone");
  });
  const seenIk = new Set();
  const enabledIk = new Set(vmd.ikEnabledNames ?? []);
  const iks = Array.from(vmd.ikNames, (name) => {
    if (seenIk.has(name) || counts.get(name) !== 1) {
      fail("name", `PMX IK name "${name}" is missing or ambiguous.`);
    }
    seenIk.add(name);
    // The field is 20 bytes, but MikuMikuDance and MMD Tools read only 15.
    return { bytes: nameBytes(name, BONE_NAME_BYTES, "IK bone"), enabled: enabledIk.has(name) };
  });
  return { tracks, iks, hasRoot: used.has(vmd.rootBoneIndex) };
}

function timeline(frameCount, fps, startFrame, sceneEndFrame) {
  const valid = (frame) => Number.isSafeInteger(frame) && frame >= 0 && frame <= UINT32_MAX;
  if (fps !== 30 || !valid(startFrame) || !Number.isSafeInteger(frameCount) || frameCount < 1) {
    fail("timeline", "PMX VMD requires a nonempty motion on a 30 FPS timeline.");
  }
  const end = startFrame + frameCount - 1;
  if (!valid(end) || (sceneEndFrame != null && (!valid(sceneEndFrame) || sceneEndFrame < end))) {
    fail("timeline", "The PMX motion's frame range is outside the VMD timeline.");
  }
  const display = new Map();
  if (startFrame > 0) {
    display.set(0, false);
    display.set(startFrame - 1, false);
  }
  display.set(startFrame, true);
  if (sceneEndFrame != null && end < sceneEndFrame) {
    display.set(end, true);
    display.set(end + 1, false);
  }
  // Adjacent boundary keys avoid one long transition in importers that interpolate display.
  return display;
}

function actorPlacement(placement, hasRoot) {
  if (placement == null) return undefined;
  if (typeof placement !== "object" || Array.isArray(placement)) {
    fail("placement", "The actor placement is not a finite yaw and world offset.");
  }
  const { yaw = 0, offset = [0, 0, 0] } = placement;
  if (!Number.isFinite(yaw) || !Array.isArray(offset) || offset.length !== 3 ||
      !Number.isFinite(offset[0]) || !Number.isFinite(offset[1]) || !Number.isFinite(offset[2])) {
    fail("placement", "The actor placement is not a finite yaw and world offset.");
  }
  if (yaw === 0 && offset.every((axis) => axis === 0)) return undefined;
  if (!hasRoot) fail("placement", "The PMX has no exportable root for its scene placement.");
  return { yaw, offset: offset.slice() };
}

function abortIfNeeded(signal) {
  if (signal?.aborted) throw new DOMException("PMX VMD export aborted.", "AbortError");
}

// frameCount is local to the actor; startFrame and sceneEndFrame are source frames,
// zero based, with an inclusive end. The solver composes the optional scene placement.
export async function encodePmxVmd({
  solver, rest, modelName = "", frameCount, fps = 30, startFrame = 0,
  sceneEndFrame = null, placement = null, onProgress, signal,
}) {
  abortIfNeeded(signal);
  const display = timeline(frameCount, fps, startFrame, sceneEndFrame);
  const vmd = solver?.vmd;
  const { tracks, iks, hasRoot } = validateBinding(vmd, rest);
  const transform = actorPlacement(placement, hasRoot);
  const keyCount = frameCount * tracks.length;
  const length = HEADER_BYTES + keyCount * BONE_BYTES + 20 +
    display.size * (PROPERTY_BYTES + iks.length * IK_BYTES);
  if (!Number.isSafeInteger(length) || length > UINT32_MAX) fail("size", "The VMD exceeds the export size limit.");
  let bytes;
  try {
    bytes = new Uint8Array(length);
  } catch {
    fail("size", "There is not enough memory for this VMD export.");
  }
  const view = new DataView(bytes.buffer);
  bytes.set(VMD_SIGNATURE);
  const title = nameBytes(modelName, 20);
  bytes.set(title.length ? title : new TextEncoder().encode("FlowHMR"), 30);
  view.setUint32(50, keyCount, true);

  const qs = new Float32Array(tracks.length * 4);
  const ts = new Float32Array(tracks.length * 3);
  const previous = new Float32Array(qs.length);
  let at = HEADER_BYTES;
  let since = performance.now();
  onProgress?.(0);
  for (let f = 0; f < frameCount; f++) {
    abortIfNeeded(signal);
    qs.fill(NaN);
    ts.fill(NaN);
    if (!vmd.sample(f, qs, ts, transform)) {
      fail("pose", `The PMX solver could not produce frame ${f + 1}.`);
    }
    for (let b = 0; b < tracks.length; b++) {
      const p = b * 3, q = b * 4;
      const x = ts[p], y = ts[p + 1], z = ts[p + 2];
      const qx = qs[q], qy = qs[q + 1], qz = qs[q + 2], qw = qs[q + 3];
      const norm = Math.hypot(qx, qy, qz, qw);
      if (!Number.isFinite(x) || !Number.isFinite(y) || !Number.isFinite(z) || !Number.isFinite(norm) || norm < 1e-7) {
        fail("pose", `Invalid PMX bone pose for "${vmd.tracks[b].name}" at frame ${f + 1}.`);
      }
      const dot = qx * previous[q] + qy * previous[q + 1] + qz * previous[q + 2] + qw * previous[q + 3];
      const factor = (dot < 0 ? -1 : 1) / norm;
      previous[q] = qx * factor;
      previous[q + 1] = qy * factor;
      previous[q + 2] = qz * factor;
      previous[q + 3] = qw * factor;
      bytes.set(tracks[b], at);
      view.setUint32(at + 15, startFrame + f, true);
      // Three's right-handed Y-up axes to the original PMX/VMD left-handed axes.
      view.setFloat32(at + 19, x, true);
      view.setFloat32(at + 23, y, true);
      view.setFloat32(at + 27, -z, true);
      view.setFloat32(at + 31, -previous[q], true);
      view.setFloat32(at + 35, -previous[q + 1], true);
      view.setFloat32(at + 39, previous[q + 2], true);
      view.setFloat32(at + 43, previous[q + 3], true);
      bytes.set(LINEAR, at + 47);
      at += BONE_BYTES;
    }
    if (f + 1 < frameCount && performance.now() - since >= 24) {
      onProgress?.((f + 1) / frameCount);
      await new Promise((resume) => setTimeout(resume, 0));
      since = performance.now();
    }
  }
  abortIfNeeded(signal);
  at += 16; // Empty morph, camera, light and self-shadow tracks.
  view.setUint32(at, display.size, true);
  at += 4;
  for (const [frame, visible] of display) {
    view.setUint32(at, frame, true);
    view.setUint8(at + 4, visible ? 1 : 0);
    view.setUint32(at + 5, iks.length, true);
    at += PROPERTY_BYTES;
    for (const ik of iks) {
      bytes.set(ik.bytes, at);
      view.setUint8(at + 20, ik.enabled ? 1 : 0);
      at += IK_BYTES;
    }
  }
  onProgress?.(1);
  return bytes;
}

const CRC_TABLE = Uint32Array.from({ length: 256 }, (_, entry) => {
  let n = entry;
  for (let i = 0; i < 8; i++) n = (n >>> 1) ^ (n & 1 ? 0xedb88320 : 0);
  return n;
});

function crc32(bytes) {
  let crc = UINT32_MAX;
  for (let i = 0; i < bytes.length; i++) crc = (crc >>> 8) ^ CRC_TABLE[(crc ^ bytes[i]) & 0xff];
  return (crc ^ UINT32_MAX) >>> 0;
}

// ZIP32, store method, UTF-8 filenames. Each member is an independent VMD on
// the same source timeline; MMD has no multi-character VMD container.
export function packPmxVmdZip(entries) {
  if (!Array.isArray(entries) || !entries.length || entries.length >= 0xffff) {
    fail("zip", "A VMD ZIP needs between 1 and 65534 motion files.");
  }
  const chunks = [], directory = [], used = new Set();
  const utf8 = new TextEncoder();
  let offset = 0, dirBytes = 0;
  for (const entry of entries) {
    const { name, bytes } = entry ?? {};
    if (typeof name !== "string" || name.trim() !== name || !/^.+\.vmd$/i.test(name) ||
        /[<>:"/\\|?*\x00-\x1f\x7f]/.test(name) || /^(con|prn|aux|nul|com[1-9]|lpt[1-9])\./i.test(name) ||
        !(bytes instanceof Uint8Array) || bytes.length < HEADER_BYTES + 20 ||
        !VMD_SIGNATURE.every((value, i) => value === bytes[i])) {
      fail("zip", "A ZIP entry needs a plain .vmd filename and binary motion data.");
    }
    const key = name.normalize("NFC").toLowerCase();
    if (used.has(key)) fail("zip", `Duplicate VMD filename "${name}".`);
    used.add(key);
    const fileName = utf8.encode(name);
    if (fileName.length > 0xffff || offset + 30 + fileName.length + bytes.length >= UINT32_MAX) {
      fail("size", "The VMD ZIP exceeds the ZIP32 size limit.");
    }
    const crc = crc32(bytes);
    const local = new Uint8Array(30 + fileName.length);
    const lh = new DataView(local.buffer);
    lh.setUint32(0, 0x04034b50, true);
    lh.setUint16(4, 20, true);
    lh.setUint16(6, 0x0800, true);
    lh.setUint16(12, 0x0021, true); // 1980-01-01, valid DOS date.
    lh.setUint32(14, crc, true);
    lh.setUint32(18, bytes.length, true);
    lh.setUint32(22, bytes.length, true);
    lh.setUint16(26, fileName.length, true);
    local.set(fileName, 30);

    const central = new Uint8Array(46 + fileName.length);
    const ch = new DataView(central.buffer);
    ch.setUint32(0, 0x02014b50, true);
    ch.setUint16(4, 20, true);
    ch.setUint16(6, 20, true);
    central.set(local.subarray(6, 28), 8); // Flags through filename length.
    ch.setUint32(42, offset, true);
    central.set(fileName, 46);
    chunks.push(local, bytes);
    directory.push(central);
    offset += local.length + bytes.length;
    dirBytes += central.length;
  }
  if (offset + dirBytes + 22 >= UINT32_MAX) fail("size", "The VMD ZIP exceeds the ZIP32 size limit.");
  const end = new Uint8Array(22);
  const e = new DataView(end.buffer);
  e.setUint32(0, 0x06054b50, true);
  e.setUint16(8, entries.length, true);
  e.setUint16(10, entries.length, true);
  e.setUint32(12, dirBytes, true);
  e.setUint32(16, offset, true);
  return new Blob([...chunks, ...directory, end], { type: "application/zip" });
}
