/*! decktalk-runtime.js: the page contract, as one script a deck includes and nothing else.
 *
 * Include it and declare a scene in markup. Nothing here needs JavaScript:
 *
 *   <script src="decktalk-runtime.js"></script>
 *   <div data-scene="pitch" data-name="How often">
 *     <template data-slide="pitch.listing">
 *       <h1>Value still listed</h1>
 *       <p data-in="by-hour" data-describe="the hourly figure">by hour, through first pitch</p>
 *       <p data-in="share" data-describe="the share of games">one game in ten</p>
 *     </template>
 *   </div>
 *
 * An element has four moments, each the local name of a cue that `cues.json` gives a second to: it
 * arrives, it steps back, it comes to the front and it leaves. A page that wants behaviour of its
 * own adds it beside the markup, and a page that would rather build a slide in script gives that
 * slide a render function instead of a template.
 *
 * This module is the facade and nothing else. It reads the probe's telemetry sink once, hands it to
 * the seam, and publishes `window.DeckTalk` for an author and `window.__decktalk` for whoever is
 * reading the page back. Nothing imports it, which is what keeps every other module testable.
 */

import { now, start, started } from "./clock.ts";
import { MILLISECONDS } from "./contract.ts";
import { begin, buildSlide, isFrozen, type Probe, query, state, waitsForSignal } from "./modes.ts";
import { all, declare, findSlide, type Handler, on, type SceneInput, setMotionScale } from "./scene.ts";
import { motionScale } from "./stage.ts";
import { type Recorder, setRecorder } from "./telemetry.ts";
import { warn, warnings } from "./warn.ts";

/** The version this page carries, which is the one version literal in the whole runtime. */
const VERSION = "0.5.0-rc1"; // x-release-please-version

/** How long the page waits for a promise its author handed it before it is drawn without it. */
const GATE_SECONDS = 5;

/** Every promise the page asked the runtime to wait for before it calls itself ready. */
const gates: Promise<unknown>[] = [];

/**
 * The probe the recorder injected, or null on every page a person opens for themselves.
 *
 * `probe.ts` declares the property on `window` as unknown, because it owns that declaration and
 * knows nothing of this module, so this is the one place the page says what it expects to find.
 */
function injected(): (Probe & { recorder?: Recorder }) | null {
  return (window.__dtprobe as (Probe & { recorder?: Recorder }) | undefined) ?? null;
}

/**
 * Wait for everything the page asked the runtime to wait for, and never reject.
 *
 * A page's own readiness condition is the page's own business, so a promise that rejects and a
 * promise that never settles are both warnings and the page is drawn either way.
 */
function gatesSettled(): Promise<void> {
  if (!gates.length) return Promise.resolve();
  let done = false;
  const all_ = Promise.all(
    gates.map((gate) =>
      Promise.resolve(gate).then(null, (err) => warn("PAGE_WAIT_REJECTED", null, null, { value: String(err) })),
    ),
  ).then(() => {
    done = true;
  });
  const limit = new Promise<void>((resolve) => {
    setTimeout(() => {
      if (!done) warn("PAGE_WAIT_UNSETTLED");
      resolve();
    }, GATE_SECONDS * MILLISECONDS);
  });
  return Promise.race([all_, limit]);
}

/** Whether the page has already been started, so a second call from an author changes nothing. */
let running = false;

/**
 * Read the page and start it, once.
 *
 * The readiness chain is fonts, then the page's own promises, then the mode's own work, because a
 * reader that awaits `ready` expects a page that is laid out, and every link of it treats a failure
 * as readiness so the chain never rejects.
 */
function boot(): void {
  if (running) return;
  running = true;
  setRecorder(injected()?.recorder);
  setMotionScale(motionScale());
  const fonts = Promise.resolve(document.fonts ? document.fonts.ready : null).catch(() => null);
  const ready = fonts
    .then(gatesSettled)
    .then(() => begin(injected()))
    .then(() => true);
  view.ready = ready;
}

/** What an author writes against, which is the whole page-facing surface of the runtime. */
const DeckTalk = {
  version: VERSION,
  /** Declare one scene from script, for a deck that would rather build its slides than write them. */
  scene(id: string | number, input: SceneInput) {
    declare(id, input);
    return DeckTalk;
  },
  /** Run a function at one cue, wherever in the deck that cue belongs. */
  on(cue: string, fn: Handler) {
    on(cue, fn);
    return DeckTalk;
  },
  /** Read the page and start it, which a page that loads the runtime late may need to call itself. */
  start: boot,
  /** Start the narration clock, which is what the recorder sends once its cover has come off. */
  startClock: start,
  /** Hold readiness until a promise of the page's own has settled. */
  waitFor(promise: unknown) {
    gates.push(Promise.resolve(promise));
    return DeckTalk;
  },
  /** Every scene the page declared, which a deck reads to walk its own slides. */
  get scenes() {
    return all();
  },
  /** The page's own query, which a deck reads for anything the contract does not answer for it. */
  get params() {
    return query();
  },
  findSlide,
  buildSlide,
};

/**
 * What the recorder and every check read the page back through.
 *
 * Every field is a getter, so a reader sees the page as it stands rather than as it was when the
 * runtime started, and the probe's one report call is written from exactly these names.
 */
const view: Record<string, unknown> = {
  version: VERSION,
  ready: null as Promise<boolean> | null,
  get mode() {
    return state.mode;
  },
  get scene() {
    return state.scene?.id ?? null;
  },
  get slide() {
    return state.slide?.id ?? null;
  },
  get cues() {
    return state.cues;
  },
  get fired() {
    return state.fired;
  },
  get warnings() {
    return warnings();
  },
  get catalog() {
    return state.catalog;
  },
  get frozen() {
    return isFrozen();
  },
  get signalled() {
    return waitsForSignal();
  },
  now,
  started,
};

window.DeckTalk = DeckTalk as unknown as Window["DeckTalk"];
window.__decktalk = view as unknown as Window["__decktalk"];

if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
else queueMicrotask(boot);
