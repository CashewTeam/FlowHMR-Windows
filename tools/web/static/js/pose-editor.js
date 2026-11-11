import { $, el, show } from "./utils.js";
import { message as m, setText, setAttr, t } from "./i18n.js";
import { normalizePoseOffsets, ROTATION_LIMIT, TRANSLATION_LIMIT } from "./pose-offsets.js";

// The diagram faces the viewer: the character's left is on screen right.
const BODY = [
  [15,90,24,"head"], [12,90,51,"neck"], [9,90,74,"chest"],
  [6,90,98,"spine"], [3,90,122,"waist"], [0,90,147,"pelvis"],
  [13,111,60,"clavicle","left"], [14,69,60,"clavicle","right"],
  [16,130,75,"upper_arm","left"], [17,50,75,"upper_arm","right"],
  [18,149,109,"elbow","left"], [19,31,109,"elbow","right"],
  [20,161,145,"wrist","left"], [21,19,145,"wrist","right"],
  [1,111,155,"hip","left"], [2,69,155,"hip","right"],
  [4,114,200,"knee","left"], [5,66,200,"knee","right"],
  [7,115,241,"ankle","left"], [8,65,241,"ankle","right"],
  [10,127,263,"toes","left"], [11,53,263,"toes","right"],
  ["root",90,266,"root"],
];
const BODY_LINKS = [[15,12,9,6,3,0], [9,13,16,18,20], [9,14,17,19,21], [0,1,4,7,10], [0,2,5,8,11]];
const HAND = [
  [22,56,128,"index",1], [23,50,90,"index",2], [24,47,53,"index",3],
  [25,82,119,"middle",1], [26,81,77,"middle",2], [27,80,34,"middle",3],
  [28,132,142,"pinky",1], [29,143,117,"pinky",2], [30,151,89,"pinky",3],
  [31,108,123,"ring",1], [32,111,83,"ring",2], [33,114,46,"ring",3],
  [34,47,182,"thumb",1], [35,29,161,"thumb",2], [36,17,135,"thumb",3],
];
const HAND_LINKS = [[22,23,24],[25,26,27],[28,29,30],[31,32,33],[34,35,36],[34,22,25,31,28]];
const SHAPES = {
  body: "M90 7a17 17 0 0 1 17 17q0 15-10 22l3 7 25 9 17 17 18 40 12 27-12 9-16-21-18-32-7 40 5 39 2 56 10 23-18 9-14-24-9-57-5-29-5 29-9 57-14 24-18-9 10-23 2-56 5-39-7-40-18 32-16 21-12-9 12-27 18-40 17-17 25-9 3-7q-10-7-10-22A17 17 0 0 1 90 7z",
  fingers: "M58 255l-3-34q-14-14-23-33L8 147q-6-12 0-17 7-5 15 1l27 29-13-103q-2-14 7-17 9-3 13 12l13 65-1-83q0-13 11-13 9 0 10 13l4 82 8-72q1-12 11-12 10 2 9 14l-4 87 23-47q4-11 13-8 8 4 5 15l-17 67q-8 33-24 51l-2 44z",
};
const byId = new Map([...BODY, ...HAND].map((node) => [node[0], node]));
const oppositeBone = new Map(BODY.filter(([, , , , side]) => side).map(([id, , , part, side]) =>
  [id, BODY.find((node) => node[3] === part && node[4] && node[4] !== side)[0]]));
const SVG = "http://www.w3.org/2000/svg";

function jointLabel(id) {
  const [, , , part, detail] = byId.get(id);
  const joint = m(`pose.${part}`);
  if (typeof detail === "number") return m("pose.digit_joint", { joint, index: detail });
  if (detail) return m("pose.side_joint", { joint, side: m(`pose.${detail}`) });
  return joint;
}

export function initPoseEditor(onChange, onActor) {
  const menu = $("characterPoseMenu"), diagram = $("poseDiagram");
  const bodyTab = $("poseBodyTab"), fingersTab = $("poseFingersTab");
  const popup = menu.querySelector(".character-pose-popup"), header = menu.closest(".viewport-header");
  const anchor = menu.querySelector("summary"), grip = $("posePanelGrip");
  const actorSelect = $("poseActor"), mirror = $("poseMirror");
  const fkMode = $("poseFkMode"), ikMode = $("poseIkMode");
  let character = null, tab = "body", joint = "root";
  let values = normalizePoseOffsets(null), buttons = new Map();
  const keyName = () => joint === "root" ? "rootRotation" : "rotations";
  const current = () => joint === "root" ? values.rootRotation : values.rotations[joint] ?? [0, 0, 0];
  const changed = (a) => a.some((value) => value !== 0);
  const rangeOf = (type) => type === "translation" ? TRANSLATION_LIMIT : ROTATION_LIMIT;

  function boneName(id) {
    const { asset, mesh, rig } = character ?? {};
    const i = rig?.sourceToTarget[id];
    if (!(i >= 0)) return "";
    return asset.kind === "fbx" ? asset.getRig(mesh).bones[i]?.name : asset.rest.bones[i]?.name;
  }

  function canSelect(id) {
    return id === "root" || !!boneName(id) || (id >= 22 && !!boneName(id + 15));
  }

  function mirrorTarget() {
    const target = oppositeBone.get(joint), map = character?.rig.sourceToTarget;
    return map && target !== undefined && map[joint] >= 0 && map[target] >= 0 && map[joint] !== map[target]
      ? target : undefined;
  }

  function mirrorCurrent() {
    const target = mirrorTarget();
    if (!values.mirror || target === undefined) return;
    const angles = character.rig.mirrorRotation(joint, target, current());
    if (!angles) return;
    if (changed(angles)) values.rotations[target] = angles;
    else delete values.rotations[target];
  }

  function syncButtons() {
    for (const [id, button] of buttons) {
      button.setAttribute("aria-pressed", String(id === joint));
      button.classList.toggle("edited", id === "root"
        ? changed(values.rootRotation) || changed(values.rootTranslation) : !!values.rotations[id]);
    }
  }

  function syncAxes(skip) {
    for (const { type, axis, slider, number } of axes) {
      const a = type === "translation" ? values.rootTranslation : current();
      if (slider !== skip) slider.value = a[axis];
      if (number !== skip) number.value = a[axis];
    }
    $("poseResetJoint").disabled = !changed(current()) && (joint !== "root" || !changed(values.rootTranslation));
    $("poseResetAll").disabled = !Object.keys(values.rotations).length &&
      !changed(values.rootRotation) && !changed(values.rootTranslation);
    show(mirror, tab === "body");
    mirror.setAttribute("aria-pressed", String(values.mirror));
    show("poseIkRow", !!character?.rig.nativeIkAvailable);
    fkMode.setAttribute("aria-pressed", String(values.ikMode === "fk"));
    ikMode.setAttribute("aria-pressed", String(values.ikMode === "ik"));
    syncButtons();
  }

  function save(skip) {
    syncAxes(skip);
    if (character) onChange(character, normalizePoseOffsets(values));
  }

  function setAxis(type, axis, input) {
    const limit = rangeOf(type), x = input.valueAsNumber;
    if (!Number.isFinite(x) || x < -limit || x > limit) return;
    if (type === "translation") values.rootTranslation[axis] = x;
    else if (keyName() === "rootRotation") values.rootRotation[axis] = x;
    else {
      const angles = [...current()];
      angles[axis] = x;
      if (changed(angles)) values.rotations[joint] = angles;
      else delete values.rotations[joint];
      mirrorCurrent();
    }
    save(input);
  }

  const axes = [];
  for (const [type, target] of [["rotation", $("poseRotationAxes")], ["translation", $("poseTranslationAxes")]]) {
    const group = document.createElement("div");
    group.className = "pose-axis-group";
    const label = document.createElement("p");
    label.className = "pose-axis-title";
    setText(label, m(`pose.${type}`));
    group.append(label);
    for (const [axis, name] of [..."XYZ"].entries()) {
      const row = document.createElement("div"), marker = document.createElement("span");
      row.className = "pose-axis";
      marker.textContent = name;
      const slider = document.createElement("input"), number = document.createElement("input");
      slider.type = "range";
      number.type = "number";
      number.className = "input";
      for (const input of [slider, number]) {
        input.min = -rangeOf(type);
        input.max = rangeOf(type);
        input.step = type === "translation" || input === number ? "0.01" : "1";
        input.dataset.poseAxis = type + name;
        setAttr(input, "aria-label", m("pose.axis", { kind: m(`pose.${type}`), name }));
        input.addEventListener("input", () => setAxis(type, axis, input));
      }
      number.addEventListener("change", () => {
        if (Number.isFinite(number.valueAsNumber)) {
          number.value = Math.max(-rangeOf(type), Math.min(rangeOf(type), number.valueAsNumber));
          setAxis(type, axis, number);
        }
        syncAxes();
      });
      row.append(marker, slider, number);
      group.append(row);
      axes.push({ type, axis, slider, number });
    }
    target.append(group);
  }

  function select(id) {
    if (!canSelect(id)) return;
    joint = id;
    setText($("poseJointName"), jointLabel(id));
    setText($("poseMappedName"), id === "root" ? m("pose.root_hint")
      : [boneName(id), id >= 22 ? boneName(id + 15) : ""].filter(Boolean).join(" / "));
    show("poseTranslationAxes", id === "root");
    syncAxes();
  }

  function draw(next) {
    tab = next;
    const isHand = tab === "fingers";
    const nodes = isHand ? HAND : BODY;
    bodyTab.setAttribute("aria-pressed", String(!isHand));
    fingersTab.setAttribute("aria-pressed", String(isHand));
    setText($("poseDiagramHint"), m(isHand ? "pose.mirror" : "pose.pick"));
    const svg = document.createElementNS(SVG, "svg");
    svg.setAttribute("viewBox", "0 0 180 288");
    svg.setAttribute("aria-hidden", "true");
    const shape = document.createElementNS(SVG, "path");
    shape.setAttribute("class", "pose-shape");
    shape.setAttribute("d", SHAPES[tab]);
    svg.append(shape);
    for (const link of isHand ? HAND_LINKS : BODY_LINKS) {
      const line = document.createElementNS(SVG, "polyline");
      line.setAttribute("class", "pose-links");
      line.setAttribute("points", link.map((id) => byId.get(id).slice(1, 3).join(",")).join(" "));
      svg.append(line);
    }
    diagram.replaceChildren(svg);
    buttons = new Map();
    for (const [id, x, y] of nodes) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "pose-node";
      button.dataset.bone = id;
      button.style.left = `${100 * x / 180}%`;
      button.style.top = `${100 * y / 288}%`;
      button.disabled = !canSelect(id);
      setAttr(button, "title", jointLabel(id));
      setAttr(button, "aria-label", jointLabel(id));
      button.addEventListener("click", () => select(id));
      buttons.set(id, button);
      diagram.append(button);
    }
    const selected = buttons.has(joint) && canSelect(joint) ? joint : nodes.find(([id]) => canSelect(id))?.[0];
    if (selected !== undefined) select(selected);
    else draw("body");
  }

  bodyTab.addEventListener("click", () => draw("body"));
  fingersTab.addEventListener("click", () => draw("fingers"));
  mirror.addEventListener("click", () => {
    if (!character) return;
    values.mirror = !values.mirror;
    if (changed(current())) mirrorCurrent();
    save();
  });
  $("poseResetJoint").addEventListener("click", () => {
    if (joint === "root") {
      values.rootRotation = [0, 0, 0];
      values.rootTranslation = [0, 0, 0];
    }
    else {
      delete values.rotations[joint];
      mirrorCurrent();
    }
    save();
  });
  $("poseResetAll").addEventListener("click", () => {
    values = normalizePoseOffsets({ mirror: values.mirror, ikMode: values.ikMode });
    save();
  });
  for (const [button, mode] of [[fkMode, "fk"], [ikMode, "ik"]]) button.addEventListener("click", () => {
    if (!character?.rig.nativeIkAvailable || values.ikMode === mode) return;
    values.ikMode = mode;
    save();
  });
  const margin = 8;
  const keepInside = (value, farEdge) => Math.max(margin, Math.min(value, farEdge - margin));
  let floating = null, drag = null, suppressDragClick = false;
  function placePopup() {
    if (!menu.open) return;
    const { clientWidth: width, clientHeight: height } = document.documentElement;
    const bounds = header.getBoundingClientRect();
    const anchorTop = keepInside(bounds.bottom + 7, height - grip.offsetHeight - 2);
    popup.style.maxHeight = `${Math.min(440, height - (floating ? margin : anchorTop) - margin)}px`;
    const left = keepInside(floating?.left ?? bounds.left + 6, width - popup.offsetWidth);
    const top = floating ? keepInside(floating.top, height - popup.offsetHeight) : anchorTop;
    popup.style.left = `${left}px`;
    popup.style.top = `${top}px`;
    popup.setAttribute("data-positioned", "");
    if (floating) Object.assign(floating, { left, top });
  }

  function stopDrag(event) {
    if (!drag || (event && event.pointerId !== drag.id)) return;
    const { id } = drag;
    drag = null;
    popup.classList.remove("dragging");
    if (!event && grip.hasPointerCapture(id)) grip.releasePointerCapture(id);
  }

  grip.addEventListener("pointerdown", (event) => {
    if (!menu.open || drag || !event.isPrimary || event.button !== 0) return;
    event.preventDefault();
    const bounds = popup.getBoundingClientRect();
    drag = { id: event.pointerId, x: event.clientX - bounds.left, y: event.clientY - bounds.top,
      startX: event.clientX, startY: event.clientY, moved: false };
    grip.setPointerCapture(drag.id);
    popup.classList.add("dragging");
  });
  window.addEventListener("pointermove", (event) => {
    if (event.pointerId !== drag?.id) return;
    if (!drag.moved && Math.hypot(event.clientX - drag.startX, event.clientY - drag.startY) < 3) return;
    drag.moved = true;
    floating = { left: event.clientX - drag.x, top: event.clientY - drag.y };
    placePopup();
  }, true);
  window.addEventListener("pointerup", (event) => {
    if (event.pointerId !== drag?.id) return;
    suppressDragClick = drag.moved;
    stopDrag(event);
  }, true);
  window.addEventListener("pointercancel", stopDrag, true);
  window.addEventListener("blur", () => stopDrag());
  // A drag release can produce a click outside the panel when the window
  // edge limits its motion. Suppress that click, never a new pointer gesture.
  window.addEventListener("pointerdown", () => { suppressDragClick = false; }, true);
  window.addEventListener("click", (event) => {
    if (!suppressDragClick || !event.detail) return;
    suppressDragClick = false;
    event.preventDefault();
    event.stopImmediatePropagation();
  }, true);
  menu.addEventListener("toggle", () => {
    if (menu.open) placePopup();
    else {
      stopDrag();
      floating = null;
      popup.removeAttribute("data-positioned");
    }
  });
  const resize = new ResizeObserver(placePopup);
  for (const node of [header, popup]) resize.observe(node);
  window.addEventListener("resize", placePopup);
  menu.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    menu.open = false;
    anchor.focus();
    event.stopPropagation();
  });
  actorSelect.onchange = () => onActor(actorSelect.value);
  return {
    setActors(actors, focused) {
      const custom = actors.filter((actor) => actor.character);
      const active = custom.find((actor) => actor.id === focused) ?? custom[0];
      actorSelect.replaceChildren(...custom.map((actor) => {
        const name = actor.character.asset.name;
        const option = el("option", "", () => `${t("person.number", { number: actor.number })} · ${name}`);
        option.value = actor.id;
        return option;
      }));
      if (active) actorSelect.value = active.id;
      show("poseActorRow", actors.length > 1 && !!active);
      const next = active?.character ?? null;
      if (character === next) return;
      character = next;
      show(menu, !!next);
      if (!next) { menu.open = false; return; }
      values = normalizePoseOffsets(next.poseOffsets);
      fingersTab.disabled = !HAND.some(([id]) => canSelect(id));
      draw(tab === "fingers" && fingersTab.disabled ? "body" : tab);
    },
  };
}
