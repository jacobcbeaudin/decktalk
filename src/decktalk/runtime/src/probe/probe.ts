/*! The recorder's instrumentation, injected into a page before it loads.
 *
 * Every DeckTalk command that opens a page drives a headless Chromium, and what that command needs
 * from the page is not what a reader needs: a step that moves the section clock to one recorded
 * frame, a helper that says when the page has settled, a measured catalog that says where every
 * reveal sits, a freeze that stops at one cue, and a record of what the page did while it was
 * recorded. None of that belongs to a deck, so none of it is in the runtime a deck loads.
 *
 * Playwright adds this file with `add_init_script`, so it runs before the page's own scripts and no
 * page ever references it. A page opened without it plays, previews, freezes and lists its scenes
 * the same, and keeps nothing.
 *
 * What the recorder calls
 *   window.__decktalkProbe.step(t, s) move the section clock to second t and answer the next due second,
 *                                    whether the picture may have changed, the earliest second it next
 *                                    can and what the page changed undeclared since the step before,
 *                                    once the page has drawn it, or refuse once s seconds pass without that
 *   window.__decktalkProbe.started(s) answer once every animation the page set playing has started, or
 *                                    refuse once s seconds pass without that
 *   window.__decktalkProbe.ready()   the page's fonts and its window.__decktalk.ready, whichever exist
 *   window.__decktalkProbe.report()  everything the recorder reads back off the page, in one call
 *
 * What the runtime reads, and only when this file is there
 *   window.__decktalkProbe.recorder                          the telemetry sink, read once at startup
 *   window.__decktalkProbe.freezeCues(order, slideId, warn)  which of a frozen slide's cues fire
 *   window.__decktalkProbe.measure(catalog, canvas)          one box row per element, onto the catalog
 *
 * This module imports the contract and the telemetry seam and nothing else, which is what keeps the
 * split honest: the probe knows the vocabulary and it knows the sink, and it knows no DOM the
 * runtime owns.
 */

import { ATTRS, type Attr, cueId, MILLISECONDS, MOMENT_SELECTOR, type Q, type ReportField } from "../contract.ts";
import type * as Seam from "../telemetry.ts";
import type { Stepped } from "../telemetry.ts";

/** The most rows of any one kind the probe keeps, so a long recording cannot grow without a bound. */
const KEEP = 5000;

/** The longest text a measured row carries, which is enough to find the element in the script. */
const TEXT_MAX = 80;

/** The two query keys only the probe reads, typed against the contract so a misspelling will not compile. */
const AFTER: Q = "after";
const BEFORE: Q = "before";

// ---- the stepped clock ---------------------------------------------------------------------------

/**
 * The browser's own animation frame and timer, taken before Playwright's clock replaces them.
 *
 * The recorder adds this file before it installs the clock, so these are the browser's. The clock
 * holds the page's timers and animation frames still between steps, and a step has to see the
 * page's real rendering run, so it waits on these and on nothing the page itself can hold.
 */
const nativeFrame = window.requestAnimationFrame.bind(window);
const nativeTimer = window.setTimeout.bind(window);
const nativeCancel = window.clearTimeout.bind(window);

/** Every image a step has already seen decoded, so each is waited on once and not on every frame. */
const decoded = new WeakSet<HTMLImageElement>();

/**
 * What draws on its own, out of the DOM's sight, so a page that holds one is never reported still.
 *
 * A canvas is drawn by script without a mutation, a video plays at its own rate, and a framed
 * document, an embed or an object changes inside a document this page's observer cannot see.
 */
const SELF_DRAWN = "canvas, video, iframe, embed, object";

/**
 * Whether anything changed the DOM or scrolled since the last step answered, which starts true for the first.
 *
 * The observer's own callback runs at a microtask checkpoint and empties the record queue, so it
 * sets this flag rather than leaving records for the step to take. A page timer that the recorder's
 * clock fires between two steps, or an image's load handler, changes the page with nothing the
 * runtime ran, and this is how the step hears of it.
 */
let touched = true;

const touch = () => {
  touched = true;
};

/**
 * Every node a mutation touched since the last step answered, which is what the page changed with no step running.
 *
 * A step runs the cues, the frame hooks and the animations, and everything they change is handed to
 * the observer while the step draws. What the observer holds when the next step begins was therefore
 * changed by something else: a timer or an animation frame of the page's own that the recorder's
 * clock ran between the two steps.
 */
let between: Node[] = [];

/** Keep the nodes some mutation records touched, up to the probe's bound, and count them as a change. */
function keep(records: readonly MutationRecord[]): void {
  if (!records.length) return;
  touched = true;
  for (const record of records) {
    if (between.length >= KEEP) return;
    between.push(record.target);
  }
}

const watcher = new MutationObserver(keep);
watcher.observe(document, { subtree: true, childList: true, attributes: true, characterData: true });
document.addEventListener("scroll", touch, { capture: true, passive: true });

/**
 * Where Playwright's clock keeps every timer and animation frame the page has set, read off the page's global object.
 *
 * Truth: the recorder installs Playwright's clock, which holds each timer with the tick it is due at
 * and answers the earliest through `_firstTimer`, on its own ticks, which `performanceNow` reads.
 * Both are the clock's own and not a promise Playwright makes, so a clock without them makes every
 * step answer its own second, which steps every frame and skips nothing it should not.
 */
const PAGE_CLOCK = "__pwClock";

/** The part of Playwright's clock the probe reads, each member optional because none of it is promised. */
type PageClock = {
  readonly controller?: {
    _firstTimer?(): { readonly callAt: number } | null;
    performanceNow?(): number;
  };
};

/**
 * The second on the section clock the page's own next timer runs at, measured from the step at `seconds`.
 *
 * A timer the page set itself, a plain `setTimeout` in a deck's script, changes the page with nothing
 * the runtime ran, so a still stretch ends where the next one is due. It is null when no timer is
 * set, and `seconds` itself when the clock cannot be read.
 */
function nextTimer(seconds: number): number | null {
  const clock = (window as unknown as Record<string, PageClock | undefined>)[PAGE_CLOCK]?.controller;
  if (typeof clock?._firstTimer !== "function" || typeof clock.performanceNow !== "function") return seconds;
  const timer = clock._firstTimer();
  if (!timer) return null;
  return seconds + Math.max(0, timer.callAt - clock.performanceNow()) / MILLISECONDS;
}

/** The earlier of two seconds, where null is a second that never comes. */
function sooner(one: number | null, other: number | null): number | null {
  if (one === null) return other;
  if (other === null) return one;
  return Math.min(one, other);
}

/** Whether the DOM changed or scrolled since the last call, counting the records not yet handed to the observer. */
function mutated(): boolean {
  const seen = touched || watcher.takeRecords().length > 0;
  touched = false;
  between = [];
  return seen;
}

/** Whether a step has answered yet, because what changed before the first one is the page loading. */
let answered = false;

/**
 * The first element the page changed since the last step answered that nothing declared as moving, named, or null.
 *
 * It is asked before the runtime moves, so nothing the step runs is counted. The runtime decides what
 * is declared, because it alone holds the motions, the loops and the regions.
 */
function undeclared(): string | null {
  keep(watcher.takeRecords());
  const changed = between;
  between = [];
  if (!answered || !changed.length) return null;
  return window.__decktalk?.undeclared(changed) ?? null;
}

/**
 * Resolve once the page has run one whole rendering update that began after this call.
 *
 * An animation frame callback runs at the start of an update, before its style, layout, paint and
 * commit, and a message posted from it is a task of its own, which runs once that update has
 * finished. So when this resolves, the compositor holds everything the page drew before it was called.
 */
function rendered(): Promise<void> {
  return new Promise((done) => {
    nativeFrame(() => {
      const channel = new MessageChannel();
      channel.port1.onmessage = () => done();
      channel.port2.postMessage(null);
    });
  });
}

/**
 * What the page is still fetching or decoding for the picture it has just laid out, as one promise each.
 *
 * A font starts loading when the first text set in it is laid out, and an image is decoded off the
 * main thread, so both can finish after a rendering update and change the picture in a later one.
 */
function outstanding(): Promise<unknown>[] {
  const waits: Promise<unknown>[] = [];
  if (document.fonts && document.fonts.status !== "loaded") waits.push(document.fonts.ready);
  for (const image of Array.from(document.images)) {
    if (decoded.has(image)) continue;
    waits.push(
      image
        .decode()
        .catch(() => undefined)
        .then(() => decoded.add(image)),
    );
  }
  return waits;
}

/**
 * Resolve once the page has drawn everything it shows now: every font and image it needs is in, and
 * a whole rendering update has run since the last of them arrived. It resolves true when it waited for
 * a font or an image, which changes the picture once it arrives.
 *
 * Each pass renders first, because rendering is what starts a font loading, and then waits for what
 * that rendering asked for, until a rendering update asks for nothing more.
 */
async function drawn(): Promise<boolean> {
  let waited = false;
  for (;;) {
    await rendered();
    const waits = outstanding();
    if (!waits.length) return waited;
    waited = true;
    await Promise.all(waits);
  }
}

/**
 * Move the page to one second on the section clock, which is everything one recorded frame is, and
 * answer once the page has drawn it.
 *
 * The runtime owns the clock and the timeline, so this hands the second over. The recorder calls it
 * once a frame, after it has moved the page's timers on by one frame, and captures what the page
 * shows once it answers. The answer carries the second the next task is due, which the recorder steps
 * to on its own before it moves the timers past it, and whether the picture may have changed since the
 * step before. The runtime says what it knows: a task ran, a hook drew, an animation or a declared
 * motion moved. This adds what only the browser knows: the DOM changed or scrolled, a font or an image
 * arrived, or the page holds something that draws on its own. A page with no runtime always changed.
 * It also names the first element the page changed since the step before outside everything the
 * runtime ran and inside nothing it declared as moving, which the recorder reports when the frame
 * shows it.
 *
 * The answer also carries the earliest second the picture can next change, which is the runtime's
 * own unless one of the page's timers is due sooner. It is the step's own second when the page holds
 * something that draws on its own, has no runtime, or heard of anything while it drew: a font, an
 * image, or a change to the DOM made after the runtime had answered, by an event or an observer the
 * runtime never ran, which may have started something the runtime has not seen.
 *
 * A capture taken the moment the runtime returns would race the page's own rendering: the style,
 * layout and paint of the step, the compositor's copy of every animation the step sought, a font the
 * step's text has only just asked for and an image still decoding. So the answer waits for all of
 * them, and a page that has not drawn the step within `waitSeconds` is refused rather than captured.
 */
export function step(seconds: number, waitSeconds: number): Promise<Stepped> {
  // What the page changed between the two steps is read before the runtime moves and before `mutated` empties it.
  const outside = undeclared();
  const stepped = window.DeckTalk?.stepTo?.(seconds) ?? { due: null, changed: true, stillUntil: seconds };
  const touchedBefore = mutated();
  return new Promise((answer, refuse) => {
    const late = nativeTimer(
      () => refuse(new Error(`the page had not drawn second ${seconds} within ${waitSeconds} seconds`)),
      waitSeconds * MILLISECONDS,
    );
    drawn().then((waited) => {
      nativeCancel(late);
      const touchedWhileDrawn = mutated();
      const selfDrawn = document.querySelector(SELF_DRAWN) !== null;
      answered = true;
      const heard = waited || touchedWhileDrawn || selfDrawn;
      answer({
        due: stepped.due,
        changed: stepped.changed || touchedBefore || heard,
        stillUntil: heard ? seconds : sooner(stepped.stillUntil, nextTimer(seconds)),
        undeclared: outside,
      });
    });
  });
}

/**
 * Answer once no animation on the page is still waiting for the browser to start it, which is when the
 * recorder may hold the clock.
 *
 * A loop the first slide starts plays from the mount, before the recorder's first step, and the
 * browser starts it in a rendering update of its own, often on the compositor. Paused and sought to
 * the section's second zero while it is still waiting to start, it keeps whatever the compositor had
 * begun, so frame 0 showed it a frame further on from some pages and not from others. Started, it
 * pauses where it is sought. Each pass waits for what is pending now, until nothing is.
 */
export function started(waitSeconds: number): Promise<boolean> {
  const pending = () => document.getAnimations().filter((animation) => animation.pending);
  const settle = async (): Promise<void> => {
    for (let waiting = pending(); waiting.length; waiting = pending()) {
      await Promise.all(waiting.map((animation) => animation.ready.catch(() => undefined)));
    }
  };
  return new Promise((answer, refuse) => {
    const late = nativeTimer(
      () => refuse(new Error(`the page's animations had not started within ${waitSeconds} seconds`)),
      waitSeconds * MILLISECONDS,
    );
    settle().then(() => {
      nativeCancel(late);
      answer(true);
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

const cues: Seam.CueEvent[] = [];
const words: Seam.WordEvent[] = [];

/** The sink the runtime picks up at startup, which is the only thing a recorded page does extra. */
export const recorder: Seam.Recorder = {
  cue(event: Seam.CueEvent): void {
    if (cues.length < KEEP) cues.push(event);
  },
  words(event: Seam.WordEvent): void {
    if (words.length < KEEP) words.push(event);
  },
};

/** Everything the recorder reads back off the page, in the one call the contract names. */
export function report(): Record<ReportField, unknown> {
  const view = window.__decktalk;
  return {
    version: view?.version ?? null,
    mode: view?.mode ?? null,
    scene: view?.scene ?? null,
    slide: view?.slide ?? null,
    warnings: view?.warnings ?? [],
    catalog: view?.catalog ?? [],
    cues,
    words,
    motions: view?.motions ?? [],
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
export function freezeCues(order: readonly string[], slideId: string, warn: Seam.WarnAt): readonly string[] {
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

/** The attribute that makes a container stagger its children, typed as a contract row so a rename fails to compile. */
const STAGGER: Attr = "data-stagger";

/** The moment attributes alone, which are the rows that qualify into a cue id. */
const MOMENT_ATTRS: Attr[] = ELEMENT_ATTRS.filter((name) => ATTRS[name].kind === "moment");

function boxOf(el: Element, frame: DOMRect, scale: number): Seam.Box {
  const rect = el.getBoundingClientRect();
  return {
    x: Math.round((rect.left - frame.left) / scale),
    y: Math.round((rect.top - frame.top) / scale),
    w: Math.round(rect.width / scale),
    h: Math.round(rect.height / scale),
  };
}

function rowFor(el: Element, slideId: string, frame: DOMRect, scale: number): Seam.ElementRow {
  const attrs: Record<string, string> = {};
  for (const name of ELEMENT_ATTRS) {
    const written = el.getAttribute(name);
    if (written !== null) attrs[name] = written;
  }
  const moments: Record<string, string> = {};
  for (const name of MOMENT_ATTRS) {
    const local = attrs[name];
    if (local) moments[name] = cueId(slideId, local);
  }
  return {
    attrs,
    moments,
    text: (el.textContent ?? "").trim().replace(/\s+/g, " ").slice(0, TEXT_MAX),
    box: boxOf(el, frame, scale),
    // The count a stagger's span is worked out from, which no attribute carries, because `data-stagger`
    // gives the step between children and says nothing about how many of them there are.
    children: el.hasAttribute(STAGGER) ? el.children.length : 0,
  };
}

/**
 * One row per element a slide declares a moment for, and one per direct child it never reveals.
 *
 * The caption band and the off-canvas rules read these rows, and both key on whether an element is
 * ever drawn rather than on whether it carries a cue, so an element on screen from the mount is
 * measured exactly like one that arrives.
 */
function rowsFor(slideEl: Element, slideId: string, frame: DOMRect, scale: number): Seam.ElementRow[] {
  const rows = [...slideEl.querySelectorAll(MOMENT_SELECTOR)].map((el) => rowFor(el, slideId, frame, scale));
  for (const el of slideEl.children) {
    if (el.matches(MOMENT_SELECTOR) || el.querySelector(MOMENT_SELECTOR)) continue;
    rows.push(rowFor(el, slideId, frame, scale));
  }
  return rows;
}

/**
 * Lay every slide out once in a hidden layer of the canvas, measure it and throw it away.
 *
 * The runtime asks for this on the index page alone, which is the page a static check opens, so no
 * recording and no preview ever lays a slide out twice.
 */
export function measure(catalog: Seam.CatalogEntry[], canvas: Seam.CanvasLoan): Seam.CatalogEntry[] {
  const layer = document.createElement("div");
  layer.id = "dt-measure";
  layer.style.cssText = "position:absolute;inset:0;visibility:hidden";
  canvas.pan.appendChild(layer);
  const frame = canvas.origin.getBoundingClientRect();
  const scale = canvas.scale || 1;
  for (const entry of catalog) {
    const scene = canvas.scenes.get(entry.scene);
    if (!scene) continue;
    entry.elements = {};
    for (const slide of scene.slides) {
      const slideEl = canvas.build(scene, slide);
      layer.appendChild(slideEl);
      entry.elements[slide.id] = rowsFor(slideEl, slide.id, frame, scale);
      layer.removeChild(slideEl);
    }
  }
  layer.remove();
  return catalog;
}

const probe: Seam.Probe = { step, started, ready, report, recorder, freezeCues, measure };
window.__decktalkProbe = probe;
