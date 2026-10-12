/*! Declared motion: a thing that moves from one cue to another, a loop, and a region a frame hook draws in.
 *
 * A moment is a reveal of half a second or less, measured as a step in the picture. A motion is the
 * other noun: it can run for any length, its length is the seconds between two cues and never a
 * number in markup, and it is declared on the element it moves, so verify can mask it from every
 * reveal and measure its own departure and arrival instead. A loop turns for as long as its slide is
 * on screen and is masked and never a landing. A frame hook draws inside a region it names.
 *
 * Anything that keeps moving past the half second a cue may still move and declares none of these is
 * undeclared motion, which is exactly what makes verify's control dishonest, so the page reports it.
 */

import { earliest, every, round } from "./clock.ts";
import {
  ATTRS,
  DUE_SLACK_SECONDS,
  type Ease,
  MEASURABLE_SPAN_SECONDS,
  MILLISECONDS,
  MOTION_EASES,
  PLAYABLE_SPAN_SECONDS,
  SPRING_SETTLE_RADIANS,
} from "./contract.ts";
import { ATTR, easeOf, loopSeconds, MOVING_SELECTOR, type Slide, written } from "./scene.ts";
import type { Undeclared } from "./telemetry.ts";
import { warn } from "./warn.ts";

/** Calibration: how many points the spring's CSS `linear()` curve is drawn through, smooth at every frame rate. */
const SPRING_POINTS = 32;

/** Calibration: the decimal places each of those points is written to, far under a pixel on any motion. */
const SPRING_DIGITS = 4;

/** Truth: the weight a cubic Bezier gives each of its two inner control points, which is three. */
const BERNSTEIN = 3;

/** Calibration: how many halvings solve a Bezier for one moment, which is far under a pixel on any motion. */
const HALVINGS = 50;

/** Where a declared region's box is read from, which is an element or the selector that finds one. */
export type Region = Element | string;

/** What the timeline asks about an animation it has not seen before, so undeclared motion is caught where it starts. */
type Born = { readonly target: Element | null; readonly endSeconds: number };

// ---- the eases --------------------------------------------------------------------------------------

/** A cubic Bezier through (0, 0), its two control points and (1, 1), as progress over time. */
function bezier(x1: number, y1: number, x2: number, y2: number): (u: number) => number {
  const at = (a: number, b: number, s: number) =>
    BERNSTEIN * (1 - s) ** 2 * s * a + BERNSTEIN * (1 - s) * s * s * b + s ** BERNSTEIN;
  return (u) => {
    if (u <= 0) return 0;
    if (u >= 1) return 1;
    let lo = 0;
    let hi = 1;
    let s = u;
    // Bisection is slower than Newton and never diverges, and a frame asks for one value.
    for (let step = 0; step < HALVINGS; step += 1) {
      s = (lo + hi) / 2;
      if (at(x1, x2, s) < u) lo = s;
      else hi = s;
    }
    return at(y1, y2, s);
  };
}

/** A critically damped spring stretched over the motion, normalised so it arrives exactly at the end. */
function spring(u: number): number {
  const reach = (x: number) => 1 - (1 + x) * Math.exp(-x);
  return u <= 0 ? 0 : u >= 1 ? 1 : reach(SPRING_SETTLE_RADIANS * u) / reach(SPRING_SETTLE_RADIANS);
}

/** Progress under one ease after a share `u` of the motion's length. */
export function eased(ease: Ease, u: number): number {
  const curve = MOTION_EASES[ease].curve;
  return curve ? bezier(...(curve as unknown as [number, number, number, number]))(u) : spring(u);
}

/** The CSS timing function an ease is played with, so a stylesheet motion and a script motion agree. */
function cssTiming(ease: Ease): string {
  const curve = MOTION_EASES[ease].curve;
  if (curve) return `cubic-bezier(${curve.join(", ")})`;
  const points = Array.from({ length: SPRING_POINTS + 1 }, (_, k) => spring(k / SPRING_POINTS).toFixed(SPRING_DIGITS));
  return `linear(${points.join(", ")})`;
}

// ---- the lanes --------------------------------------------------------------------------------------

/** One thing the page declared as moving, which is what verify masks and what a landing is measured on. */
type Lane = {
  readonly id: string;
  readonly kind: "motion" | "loop" | "region";
  readonly region: Region;
  readonly from: number | null;
  readonly to: number | null;
  readonly cues: { readonly from: string | null; readonly to: string | null };
};

/** Every lane the page has declared since its clock started. */
const lanes: Lane[] = [];

/** Where one lane was seen while the recorder stepped: the box it swept and the seconds it was alive. */
type Swept = {
  left: number;
  top: number;
  right: number;
  bottom: number;
  width: number;
  height: number;
  first: number;
  last: number;
};

/** What the recorder's steps saw of each lane, keyed by the lane's id. */
const swept = new Map<string, Swept>();

/** The element a region names now, or null when it is not on the page. */
function resolve(region: Region): Element | null {
  const found = typeof region === "string" ? document.querySelector(region) : region;
  return found?.isConnected ? found : null;
}

/** The element a lane's region names now, or null when it is not on the page. */
function regionOf(one: Lane): Element | null {
  return resolve(one.region);
}

/** Whether a lane is moving at one second: a motion between its two cues, anything else while it is on the page. */
function alive(one: Lane, seconds: number): boolean {
  if (one.from !== null && one.to !== null)
    return seconds >= one.from - DUE_SLACK_SECONDS && seconds <= one.to + DUE_SLACK_SECONDS;
  return true;
}

/**
 * Note where every live lane is at one stepped second, which is what verify masks and measures on.
 *
 * The recorder calls this once a frame after the timeline has been sought, so a box read here is
 * where the frame shows the element, and the union over every frame is the whole lane it swept.
 */
export function sample(seconds: number): void {
  for (const one of lanes) {
    if (!alive(one, seconds)) continue;
    const el = regionOf(one);
    if (el) note(one.id, el, seconds);
  }
}

/** Widen what one lane swept by where its element is drawn at one stepped second. */
function note(id: string, el: Element, seconds: number): void {
  const box = el.getBoundingClientRect();
  if (!box.width || !box.height) return;
  const seen = swept.get(id);
  if (!seen) {
    swept.set(id, {
      left: box.left,
      top: box.top,
      right: box.right,
      bottom: box.bottom,
      width: box.width,
      height: box.height,
      first: seconds,
      last: seconds,
    });
    return;
  }
  seen.left = Math.min(seen.left, box.left);
  seen.top = Math.min(seen.top, box.top);
  seen.right = Math.max(seen.right, box.right);
  seen.bottom = Math.max(seen.bottom, box.bottom);
  seen.last = seconds;
}

/**
 * Whether a declared motion or loop is moving at one stepped second, with its element on the page.
 *
 * The recorder writes a frame again only when nothing on the page moved, and a declared motion is
 * moving by declaration, whatever the animation that draws it reports. A frame hook's region is not
 * asked here, because the clock already counts every hook it calls.
 */
export function moving(seconds: number): boolean {
  return lanes.some((one) => one.kind !== "region" && alive(one, seconds) && regionOf(one) !== null);
}

/**
 * The earliest second at or after `seconds` a declared motion or loop can next move, or null when none will.
 *
 * One moving now moves now, and one still to depart moves from its first cue. One whose element is
 * not on the page cannot move until something puts it there, which is a task or a timer the step
 * already answers for.
 */
export function stillUntil(seconds: number): number | null {
  let next: number | null = null;
  for (const one of lanes) {
    if (one.kind === "region") continue;
    if (alive(one, seconds) && regionOf(one) !== null) return seconds;
    if (one.from !== null && one.from - DUE_SLACK_SECONDS > seconds)
      next = earliest(next, one.from - DUE_SLACK_SECONDS);
  }
  return next;
}

/** One lane as the recording log keeps it: what moved, the box it swept, when it moved and where it lands. */
type LaneRow = {
  id: string;
  kind: Lane["kind"];
  box: { x: number; y: number; w: number; h: number } | null;
  size: { w: number; h: number } | null;
  departs: number | null;
  arrives: number | null;
  landings: { cue: string; kind: "depart" | "arrive"; at: number }[];
};

/** Every lane the recorder saw, in the order the page declared them, which the probe's report carries. */
export function report(): LaneRow[] {
  return lanes.map((one) => {
    const seen = swept.get(one.id);
    const landings: LaneRow["landings"] = [];
    if (one.cues.from && one.from !== null) landings.push({ cue: one.cues.from, kind: "depart", at: round(one.from) });
    if (one.cues.to && one.to !== null) landings.push({ cue: one.cues.to, kind: "arrive", at: round(one.to) });
    const box = seen
      ? {
          x: Math.round(seen.left),
          y: Math.round(seen.top),
          w: Math.round(seen.right) - Math.round(seen.left),
          h: Math.round(seen.bottom) - Math.round(seen.top),
        }
      : null;
    return {
      id: one.id,
      kind: one.kind,
      box,
      size: seen ? { w: Math.round(seen.width), h: Math.round(seen.height) } : null,
      departs: seen ? round(seen.first) : null,
      arrives: seen ? round(seen.last) : null,
      landings,
    };
  });
}

/** Every lane declared so far, which the probe samples while the recorder steps. */
export function declared(): readonly Lane[] {
  return lanes;
}

function lane(next: Omit<Lane, "id">): void {
  lanes.push({ ...next, id: `${next.kind}-${lanes.length + 1}` });
}

/** Whether an element is, or sits inside, something the page declared as moving. */
function covered(target: Element | null): boolean {
  if (!target) return false;
  if (target.closest(MOVING_SELECTOR)) return true;
  return lanes.some((one) => {
    const region = typeof one.region === "string" ? document.querySelector(one.region) : one.region;
    return region?.contains(target) ?? false;
  });
}

/** A short name for an element in a warning, which is enough to find it in the markup. */
function describe(el: Element | null): string {
  if (!el) return "An animation";
  const id = el.id ? `#${el.id}` : "";
  const classes = [...el.classList].map((name) => `.${name}`).join("");
  return `<${el.localName}${id}${classes}>`;
}

/** Every box an element and what it holds are laid out in, in viewport pixels, of which a change paints inside. */
function laidOut(el: Element): DOMRect[] {
  return [el, ...el.querySelectorAll("*")]
    .flatMap((one) => [...one.getClientRects()])
    .filter((rect) => rect.width > 0 && rect.height > 0);
}

/** The smallest box in the viewport that holds every rect, or null when none of them is in it. */
function enclosing(rects: readonly DOMRect[]): Undeclared["box"] {
  const left = Math.max(0, Math.min(...rects.map((rect) => rect.left)));
  const top = Math.max(0, Math.min(...rects.map((rect) => rect.top)));
  const right = Math.min(window.innerWidth, Math.max(...rects.map((rect) => rect.right)));
  const bottom = Math.min(window.innerHeight, Math.max(...rects.map((rect) => rect.bottom)));
  if (!rects.length || right <= left || bottom <= top) return null;
  return { x: left, y: top, w: right - left, h: bottom - top };
}

/**
 * Those of some changed nodes on the page that nothing declared as moving, or null when every one is.
 *
 * The probe hands over every node the page changed between two steps, which is a change no cue, no
 * frame hook and no animation the runtime holds made. A text node stands for the element holding it,
 * and a node no longer on the page shows nothing. The answer names the first and places them all, so
 * the recorder blames them for a frame that differs only where they are, and a frame that differs
 * only where a cue drew is not theirs. An element laid out nowhere is placed nowhere, and the recorder
 * then reads the whole frame.
 */
export function undeclaredChange(nodes: readonly Node[]): Undeclared | null {
  const changed = [
    ...new Set(
      nodes
        .map((node) => (node instanceof Element ? node : node.parentElement))
        .filter((el): el is Element => !!el?.isConnected && !covered(el)),
    ),
  ];
  if (!changed.length) return null;
  const rects = changed.map(laidOut);
  return { what: describe(changed[0] ?? null), box: rects.some((one) => !one.length) ? null : enclosing(rects.flat()) };
}

/**
 * Report an animation the timeline has just met when it outlives a cue's half second undeclared.
 *
 * This is the recorder's half of the rule. The index page's half reads every slide's stylesheet
 * before anything plays, so `check` names a loop the markup forgot without recording a frame.
 */
export function born(animation: Born, slideId: string | null): void {
  if (animation.endSeconds <= PLAYABLE_SPAN_SECONDS || covered(animation.target)) return;
  warn("PAGE_MOTION_UNDECLARED", slideId, null, { value: describe(animation.target) });
}

/** Report every element of a laid-out slide whose own stylesheet keeps it moving and that declares nothing. */
export function undeclaredIn(slideEl: Element, slideId: string): void {
  for (const el of slideEl.querySelectorAll("*")) {
    if (el.closest(MOVING_SELECTOR)) continue;
    const style = getComputedStyle(el);
    const turns = style.animationIterationCount.split(",").map((part) => part.trim());
    const lengths = style.animationDuration.split(",").map((part) => Number.parseFloat(part) || 0);
    const longest = Math.max(
      ...lengths.map((length, at) => (turns[at % turns.length] === "infinite" && length > 0 ? Infinity : length)),
    );
    if (longest > PLAYABLE_SPAN_SECONDS) born({ target: el, endSeconds: longest }, slideId);
  }
}

// ---- the stylesheet's own motions -----------------------------------------------------------------

/** What a mounted slide tells the motions about the clock, which is when each of its cues is due. */
type Timing = {
  readonly frozen: boolean;
  /** The second a cue id is due, or null when the page does not know it. */
  at(cue: string): number | null;
};

/**
 * Wire every motion and loop a mounted slide declares, and answer the action each motion's departure runs.
 *
 * A loop starts with the slide, so it is born on the slide's mount. A motion starts at its departure
 * cue with the two cues' seconds apart as its length, so a re-voiced line re-times it the way every cue
 * already re-times its reveal. A still never loops, and a motion on a still shows where it ends once
 * its departure has fired.
 */
export function wire(el: HTMLElement, slide: Slide, timing: Timing, on: (cue: string, run: () => void) => void): void {
  for (const target of el.querySelectorAll(MOVING_SELECTOR)) {
    const moving = target as HTMLElement;
    const loop = written(moving, ATTR.loop);
    if (timing.frozen) {
      // A still has no clock, so a loop rests and a motion that has departed shows where it ends.
      if (loop) continue;
      const departs = written(moving, ATTR.motionFrom);
      const name = written(moving, ATTR.motion) as string;
      if (departs) on(`${slide.id}:${departs}`, () => (moving.style.animation = `${name} 1s linear 0s 1 normal both`));
      continue;
    }
    if (loop) {
      moving.style.animation = `${loop} ${loopSeconds(moving)}s linear 0s infinite`;
      lane({ kind: "loop", region: moving, from: null, to: null, cues: { from: null, to: null } });
      continue;
    }
    const name = written(moving, ATTR.motion) as string;
    const fromLocal = written(moving, ATTR.motionFrom);
    const toLocal = written(moving, ATTR.motionTo);
    const from = fromLocal ? `${slide.id}:${fromLocal}` : null;
    const to = toLocal ? `${slide.id}:${toLocal}` : null;
    const start = from === null ? null : timing.at(from);
    const end = to === null ? null : timing.at(to);
    if (from === null || to === null || start === null || end === null || end <= start) {
      warn("PAGE_MOTION_CUE_UNKNOWN", slide.id, to ?? from, { attr: ATTR.motion, value: name });
      continue;
    }
    lane({ kind: "motion", region: moving, from: start, to: end, cues: { from, to } });
    const ease = easeOf(moving);
    on(from, () => {
      moving.style.animation = `${name} ${end - start}s ${cssTiming(ease)} 0s 1 normal both`;
    });
  }
}

// ---- the script's own motions ---------------------------------------------------------------------

/** What `DeckTalk.motion` takes: two cue ids, an ease, and the function that draws one moment of it. */
export type Scripted = {
  readonly from: string;
  readonly to: string;
  readonly ease?: Ease;
  readonly draw: (progress: number, seconds: number) => void;
};

/**
 * What a region shows, as far as script can read it: its markup, its inline styles and every canvas in it.
 *
 * A canvas the page may not read back, such as one drawn from another origin, reads as empty.
 */
function picture(el: Element): string {
  const canvases = el instanceof HTMLCanvasElement ? [el] : [...el.querySelectorAll("canvas")];
  const drawn = canvases.map((canvas) => {
    try {
      return canvas.toDataURL();
    } catch {
      return "";
    }
  });
  return [el.outerHTML, ...drawn].join("\n");
}

/**
 * Draw a script motion's arrival, and report it when its draw still changes the picture after it.
 *
 * A motion is drawn no further than its arrival, so a draw that keeps changing once its progress is
 * one, such as a fade it times from the second, would stop on whatever the arrival showed. The draw
 * is first called once at `MEASURABLE_SPAN_SECONDS` past the arrival, the half second verify reads
 * an arrival over, and then at the arrival itself, which is the call the frame keeps. The two
 * pictures differ only when the draw still moves after it lands.
 */
function arrive(region: Region, motion: Scripted, seconds: number): void {
  const el = resolve(region);
  const later = el ? (motion.draw(1, seconds + MEASURABLE_SPAN_SECONDS), picture(el)) : null;
  motion.draw(1, seconds);
  if (el && later !== null && later !== picture(el)) {
    warn("PAGE_MOTION_UNFINISHED", null, motion.to, { value: describe(el), from: motion.from });
  }
}

/**
 * Draw a motion from script between two cues, with its progress eased and its second beside it.
 *
 * Nothing is drawn before the departure, the last call is exactly at the arrival with progress one,
 * and a cue the page was never handed is reported once and draws nothing. A draw that would still
 * change the picture after the arrival is reported at the arrival's cue, because nothing draws it there.
 */
export function scripted(region: Region, motion: Scripted, at: (cue: string) => number | null): void {
  const start = at(motion.from);
  const end = at(motion.to);
  if (start === null || end === null || end <= start) {
    warn("PAGE_MOTION_CUE_UNKNOWN", null, start === null ? motion.from : motion.to);
    return;
  }
  const ease = motion.ease ?? (ATTRS[ATTR.motionEase].default as Ease);
  lane({ kind: "motion", region, from: start, to: end, cues: { from: motion.from, to: motion.to } });
  every((seconds) => {
    if (seconds < start) return true;
    const u = Math.min(1, (seconds - start) / (end - start));
    if (u < 1) {
      motion.draw(eased(ease, u), seconds);
      return true;
    }
    arrive(region, motion, seconds);
    return false;
  });
}

/** Declare the region a frame hook draws in, or report the hook as undeclared motion when it names none. */
export function region(where: Region | undefined): void {
  if (where === undefined) {
    warn("PAGE_MOTION_UNDECLARED", null, null, { value: "A frame hook" });
    return;
  }
  lane({ kind: "region", region: where, from: null, to: null, cues: { from: null, to: null } });
}

/** The seconds an animation's effect ends at, which is infinite for a loop. */
export function ending(animation: Animation): number {
  const end = animation.effect?.getComputedTiming().endTime;
  return typeof end === "number" ? end / MILLISECONDS : Number.POSITIVE_INFINITY;
}
