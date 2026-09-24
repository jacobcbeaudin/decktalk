/*! The recorder's instrumentation, injected into a page before it loads.
 *
 * Every DeckTalk command that opens a page drives a headless Chromium, and what that command needs
 * from the page is not what a reader needs: a cover over the first paint so the recording starts on
 * a known frame, a helper that says when the page has settled, a measured catalog that says where
 * every reveal sits, a freeze that stops at one cue, and a record of how the page behaved while it
 * was captured. None of that belongs to a deck, so none of it is in the runtime a deck loads.
 *
 * Playwright adds this file with `add_init_script`, so it runs before the page's own scripts and no
 * page ever references it. A page opened without it plays, previews, freezes and lists its scenes
 * the same, and keeps nothing.
 *
 * What the recorder calls
 *   window.__dtprobe.cover()   draw the magenta cover and the keep-alive from the first paint
 *   window.__dtprobe.lift()    remove the cover and start the page clock on the next animation
 *                              frame, resolving to the performance.now() of that frame, which is
 *                              the recording's narration t=0
 *   window.__dtprobe.ready()   the page's fonts and its window.__decktalk.ready, whichever exist
 *   window.__dtprobe.report()  everything the recorder reads back off the page, in one call
 *
 * What the runtime reads, and only when this file is there
 *   window.__dtprobe.recorder                          the telemetry sink, read once at startup
 *   window.__dtprobe.freezeCues(order, slideId, warn)  which of a frozen slide's cues fire
 *   window.__dtprobe.measure(catalog, stage)           one box row per element, onto the catalog
 *
 * This module imports the contract and the telemetry seam and nothing else, which is what keeps the
 * split honest: the probe knows the vocabulary and it knows the sink, and it knows no DOM the
 * runtime owns.
 */

import { ATTRS, type Attr, type Code, MOMENT_SELECTOR, type Q, wireId } from "../contract.ts";
import type { CueEvent, Recorder, WordEvent } from "../telemetry.ts";

/** The cover over the first paint, and the square that keeps the compositor painting under it. */
const COVER_ID = "__t0cover";
const KEEPALIVE_ID = "__dtkeepalive";

/** A gap between two animation frames longer than this many milliseconds was long enough to move a cue. */
const GAP_MS = 100;

/** The two query keys only the probe reads, typed against the contract so a misspelling will not compile. */
const AFTER: Q = "after";
const BEFORE: Q = "before";

/** The most rows of any one kind the probe keeps, so a long recording cannot grow without a bound. */
const KEEP = 5000;

/** The longest text a measured row carries, which is enough to find the element in the script. */
const TEXT_MAX = 80;

/** A box in stage pixels, which is the coordinate system every static check reasons in. */
type Box = { x: number; y: number; w: number; h: number };

/** One measured element of a slide, as the author wrote it and as the layout placed it. */
type ElementRow = {
  attrs: Record<string, string>;
  moments: Record<string, string>;
  text: string;
  box: Box;
};

/** What the runtime hands `measure`, which is the layer to build into and the frame to measure against. */
type Stage = {
  pan: HTMLElement;
  origin: HTMLElement;
  scale: number;
  scenes: Map<string, { slides: { id: string }[] }>;
  build(scene: unknown, slide: unknown): HTMLElement;
};

/** The catalog the runtime keeps, one entry per scene, which `measure` adds the boxes to. */
type CatalogEntry = { scene: string; elements?: Record<string, ElementRow[]> };

/** The live view of the page the runtime publishes, which the probe reads and never writes. */
type RuntimeView = {
  version?: string;
  ready?: unknown;
  mode?: string;
  scene?: string | null;
  slide?: string | null;
  warnings?: unknown[];
  catalog?: CatalogEntry[];
};

declare global {
  interface Window {
    DeckTalk?: { startClock?(): void };
    __decktalk?: RuntimeView;
    __dtprobe?: unknown;
  }
}

// ---- the cover ---------------------------------------------------------------------------------

/**
 * Cover the page in magenta from its first paint until the narration clock starts.
 *
 * The first clean frame in the recording is then t=0, no matter when the recorder began capturing.
 * The cover comes with a keep-alive, a two pixel square in the corner that turns for the whole
 * recording, because Chromium's screencast emits a frame only when the compositor paints one and a
 * static cover paints once. The keep-alive outlives the cover on purpose. Playwright stamps each
 * frame by when it was swapped, rounded down to the capture grid, and a busy compositor swaps later
 * in the frame than an idle one, so if the motion stopped with the cover then the cover-off frame
 * would be stamped busy and every later reveal on a still page stamped idle, one or two frames
 * earlier, and reveals would record ahead of their words. It is mid-gray at three percent opacity,
 * so it moves a pixel's luma by four steps at most, which is under every diff level verify reads.
 */
export function cover(): void {
  const add = () => {
    if (document.getElementById(COVER_ID)) return;
    const parent = document.body || document.documentElement;
    const sheet = document.createElement("div");
    sheet.id = COVER_ID;
    sheet.style.cssText = "position:fixed;inset:0;background:#ff00ff;z-index:2147483647;pointer-events:none";
    parent.appendChild(sheet);
    const alive = document.createElement("div");
    alive.id = KEEPALIVE_ID;
    alive.setAttribute("aria-hidden", "true");
    alive.style.cssText =
      "position:fixed;right:1px;bottom:1px;width:2px;height:2px;background:#808080;opacity:.03;" +
      `z-index:2147483647;pointer-events:none;animation:${KEEPALIVE_ID} .5s linear infinite`;
    const style = document.createElement("style");
    style.textContent = `@keyframes ${KEEPALIVE_ID}{to{transform:rotate(360deg)}}`;
    alive.appendChild(style);
    parent.appendChild(alive);
  };
  if (document.documentElement) add();
  else document.addEventListener("DOMContentLoaded", add, { once: true });
}

/** The narration origin in `performance.now()` milliseconds, or null while the clock has not started. */
let origin: number | null = null;

/** The second on the narration clock a `performance.now()` millisecond stands for, rounded to a millisecond. */
function clockAt(ms: number): number | null {
  return origin === null ? null : Number(((ms - origin) / 1000).toFixed(3));
}

/**
 * Remove the cover and start the page clock.
 *
 * The frame that shows the cover gone is composited on the next animation frame, and that frame is
 * the recording's t=0, so the clock starts there rather than at the moment of this call.
 */
export function lift(): Promise<number> {
  return new Promise((resolve) => {
    document.getElementById(COVER_ID)?.remove();
    requestAnimationFrame((at) => {
      origin = at;
      window.DeckTalk?.startClock?.();
      resolve(performance.now());
    });
  });
}

// ---- the wait helper ---------------------------------------------------------------------------

/**
 * Whether the page has settled, which is its fonts and the runtime's own readiness promise.
 *
 * Either may be missing, on a page that is not a deck or in a browser without the fonts API, and a
 * page missing both is ready as it stands.
 */
export function ready(): Promise<boolean> {
  const fonts = document.fonts ? document.fonts.ready : null;
  const runtime = window.__decktalk?.ready;
  return Promise.all([fonts, runtime].map((p) => Promise.resolve(p).catch(() => null))).then(() => true);
}

// ---- the telemetry the recorder keeps -----------------------------------------------------------

const cues: (CueEvent & { next: number | null; after: number | null })[] = [];
const words: WordEvent[] = [];
const frameGaps: { at: number | null; ms: number }[] = [];
const longFrames: { start: number | null; ms: number; render: number | null; presented: number | null }[] = [];

/**
 * The sink the runtime picks up at startup, which is the only thing a recorded page does extra.
 *
 * A cue row grows two more stamps after it is recorded, the frame after the one that ran the cue and
 * the one after that, because a cue that ran on time but whose next frame began late was held up by
 * the frame that drew it rather than before it.
 */
export const recorder: Recorder = {
  cue(event: CueEvent): void {
    if (cues.length >= KEEP) return;
    const row = { ...event, next: null as number | null, after: null as number | null };
    cues.push(row);
    requestAnimationFrame((next) => {
      row.next = clockAt(next);
      requestAnimationFrame((after) => {
        row.after = clockAt(after);
      });
    });
  },
  words(event: WordEvent): void {
    if (words.length < KEEP) words.push(event);
  },
};

/**
 * Watch how well the compositor kept up, which needs no DOM and nothing the runtime knows.
 *
 * A recording is only as good as the frames the compositor produced. A gap between two animation
 * frames longer than a few captured frames means a reveal was captured late, and a long animation
 * frame says which work held it up, so both are recorded for the recorder to judge.
 */
function watchFrames(): void {
  let last: number | null = null;
  const tick = (at: number) => {
    if (last !== null && at - last > GAP_MS && frameGaps.length < KEEP) {
      frameGaps.push({ at: clockAt(at), ms: Math.round(at - last) });
    }
    last = at;
    requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);
  const kinds = window.PerformanceObserver ? PerformanceObserver.supportedEntryTypes || [] : [];
  if (!kinds.includes("long-animation-frame")) return;
  new PerformanceObserver((list) => {
    for (const entry of list.getEntries() as (PerformanceEntry & {
      renderStart?: number;
      presentationTime?: number;
    })[]) {
      if (origin === null || entry.startTime < origin || longFrames.length >= KEEP) continue;
      longFrames.push({
        start: clockAt(entry.startTime),
        ms: Math.round(entry.duration),
        render: entry.renderStart === undefined ? null : clockAt(entry.renderStart),
        presented: entry.presentationTime === undefined ? null : clockAt(entry.presentationTime),
      });
    }
  }).observe({ type: "long-animation-frame" } as PerformanceObserverInit);
}

/** Everything the recorder reads back off the page, in the one call the contract names. */
export function report(): Record<string, unknown> {
  const view = window.__decktalk ?? {};
  return {
    version: view.version ?? null,
    mode: view.mode ?? null,
    scene: view.scene ?? null,
    slide: view.slide ?? null,
    warnings: view.warnings ?? [],
    catalog: view.catalog ?? [],
    cues,
    words,
    frameGaps,
    longFrames,
  };
}

// ---- freezing at one cue -------------------------------------------------------------------------

/**
 * Which of a frozen slide's cues fire, which is every one of them unless the URL names a stop.
 *
 * The runtime freezes a slide with every cue fired, which is the frozen slide the index page links
 * and the storyboard opens. A check needs the slide one cue earlier, so it asks for a stop, and this
 * is the only reader of the two query keys that name one.
 */
export function freezeCues(
  order: readonly string[],
  slideId: string,
  warn: (code: Code, slide: string, cue: string) => void,
): readonly string[] {
  const params = new URLSearchParams(location.search);
  const after = params.get(AFTER);
  const before = params.get(BEFORE);
  const stop = after || before;
  if (!stop) return order;
  const at = order.indexOf(stop);
  if (at < 0) {
    warn("PAGE_FREEZE_CUE_UNKNOWN", slideId, stop);
    return order;
  }
  return order.slice(0, after ? at + 1 : at);
}

// ---- the measured catalog --------------------------------------------------------------------------

/** Every attribute of the contract an element may carry, which is what a measured row records verbatim. */
const ELEMENT_ATTRS: Attr[] = (Object.keys(ATTRS) as Attr[]).filter((name) =>
  ATTRS[name].on.some((subject) => subject === "element" || subject === "container"),
);

/** The moment attributes alone, which are the rows that qualify into a wire id. */
const MOMENT_ATTRS: Attr[] = ELEMENT_ATTRS.filter((name) => ATTRS[name].kind === "moment");

function boxOf(el: Element, frame: DOMRect, scale: number): Box {
  const rect = el.getBoundingClientRect();
  return {
    x: Math.round((rect.left - frame.left) / scale),
    y: Math.round((rect.top - frame.top) / scale),
    w: Math.round(rect.width / scale),
    h: Math.round(rect.height / scale),
  };
}

function rowFor(el: Element, slideId: string, frame: DOMRect, scale: number): ElementRow {
  const attrs: Record<string, string> = {};
  for (const name of ELEMENT_ATTRS) {
    const written = el.getAttribute(name);
    if (written !== null) attrs[name] = written;
  }
  const moments: Record<string, string> = {};
  for (const name of MOMENT_ATTRS) {
    const local = attrs[name];
    if (local) moments[name] = wireId(slideId, local);
  }
  return {
    attrs,
    moments,
    text: (el.textContent ?? "").trim().replace(/\s+/g, " ").slice(0, TEXT_MAX),
    box: boxOf(el, frame, scale),
  };
}

/**
 * One row per element a slide declares a moment for, and one per direct child it never reveals.
 *
 * The caption band and the off-stage rules read these rows, and both key on whether an element is
 * ever drawn rather than on whether it carries a cue, so an element on screen from the mount is
 * measured exactly like one that arrives.
 */
function rowsFor(slideEl: Element, slideId: string, frame: DOMRect, scale: number): ElementRow[] {
  const rows = [...slideEl.querySelectorAll(MOMENT_SELECTOR)].map((el) => rowFor(el, slideId, frame, scale));
  for (const el of slideEl.children) {
    if (el.matches(MOMENT_SELECTOR) || el.querySelector(MOMENT_SELECTOR)) continue;
    rows.push(rowFor(el, slideId, frame, scale));
  }
  return rows;
}

/**
 * Lay every slide out once in a hidden layer of the stage, measure it and throw it away.
 *
 * The runtime asks for this on the index page alone, which is the page a static check opens, so no
 * recording and no preview ever lays a slide out twice.
 */
export function measure(catalog: CatalogEntry[], stage: Stage): CatalogEntry[] {
  const layer = document.createElement("div");
  layer.id = "dt-measure";
  layer.style.cssText = "position:absolute;inset:0;visibility:hidden";
  stage.pan.appendChild(layer);
  const frame = stage.origin.getBoundingClientRect();
  const scale = stage.scale || 1;
  for (const entry of catalog) {
    const scene = stage.scenes.get(entry.scene);
    if (!scene) continue;
    entry.elements = {};
    for (const slide of scene.slides) {
      const slideEl = stage.build(scene, slide);
      layer.appendChild(slideEl);
      entry.elements[slide.id] = rowsFor(slideEl, slide.id, frame, scale);
      layer.removeChild(slideEl);
    }
  }
  layer.remove();
  return catalog;
}

watchFrames();

window.__dtprobe = { cover, lift, ready, report, recorder, freezeCues, measure };
