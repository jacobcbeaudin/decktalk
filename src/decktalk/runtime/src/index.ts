/*! decktalk-runtime.js: the page contract, as one script a deck includes and nothing else.
 *
 * Include it and declare a scene in markup. Nothing here needs JavaScript:
 *
 *   <script src="/__decktalk/decktalk-runtime.js"></script>
 *   <div data-scene="pitch" data-name="How often">
 *     <template data-slide="pitch.listing">
 *       <h1>Value still listed</h1>
 *       <p data-in="by-hour" data-describe="the hourly figure">by hour, through first pitch</p>
 *       <p data-in="share" data-describe="the share of games">one game in ten</p>
 *     </template>
 *   </div>
 *
 * An element has four moments, each the cue name of a cue whose phrase `cues.json` names: it
 * arrives, it steps back, it comes to the front and it leaves. A page that wants behaviour of its
 * own adds it beside the markup, and a page that would rather build a slide in script gives that
 * slide a render function instead of a template.
 *
 * This module is the facade and nothing else. It reads the probe's telemetry sink once, hands it to
 * the seam, and publishes `window.DeckTalk` for an author and `window.__decktalk` for whoever is
 * reading the page back. Nothing imports it, which is what keeps every other module testable.
 */

import { type Caption, motionScale, showCaption } from "./canvas.ts";
import { earliest, every, now, observe, started, stepTo } from "./clock.ts";
import { MILLISECONDS } from "./contract.ts";
import { begin, buildSlide, cueAt, isFrozen, query, state, waitsForSignal } from "./modes.ts";
import {
  born,
  ending,
  report as lanes,
  moving,
  type Region,
  region,
  type Scripted,
  sample,
  scripted,
  stillUntil,
  undeclaredChange,
} from "./motion.ts";
import { all, declare, findSlide, type Handler, on, type SceneInput, setMotionScale } from "./scene.ts";
import { type Probe, type RuntimeView, setRecorder } from "./telemetry.ts";
import { timeline, whenMet } from "./timeline.ts";
import { warn, warnings } from "./warn.ts";

/**
 * The version this page carries, which is the one version literal in the whole runtime.
 *
 * The bundle's first line names the engine version from the project file, and release-please writes
 * both, so the version a page reports and the version its file names are the same.
 */
const VERSION = "0.5.0"; // x-release-please-version

/** How long the page waits for a promise its author handed it before it is drawn without it. */
const GATE_SECONDS = 5;

/** Every promise the page asked the runtime to wait for before it calls itself ready. */
const gates: Promise<unknown>[] = [];

/** The probe the recorder injected, or null on every page a person opens for themselves. */
function injected(): Probe | null {
  return window.__decktalkProbe ?? null;
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
  // The timeline holds every animation at the stepped second first, so a lane is sampled where the frame shows it.
  // A step changed the picture when an animation moved or a declared motion or loop is under way, and the page
  // is still until the first animation, motion or loop next moves.
  observe({
    running: (at) => timeline.running(at),
    ran: (at) => timeline.ran(at),
    stepped: (seconds) => {
      const moved = timeline.stepped(seconds);
      sample(seconds);
      return moved || moving(seconds);
    },
    still: (seconds) => earliest(timeline.still(seconds), stillUntil(seconds)),
  });
  whenMet((animation) => {
    const seen = animation as Animation;
    const target = seen.effect instanceof KeyframeEffect ? seen.effect.target : null;
    born({ target, endSeconds: ending(seen) }, state.slide?.id ?? null);
  });
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
  /**
   * Move the section clock to one second and show that second, which is how the recorder draws a frame.
   *
   * The first call puts the page on the stepped clock for good: every task due by then runs, every
   * frame hook draws, and every animation is held at that second. It answers the second the next task
   * is due and whether anything the runtime knows of changed the picture since the step before.
   */
  stepTo,
  /**
   * Draw something every frame from the section second, such as a canvas, a typed line or a scene.
   *
   * The hook is handed the second the frame shows, so a recorded frame and a preview frame draw the
   * same picture for the same second. `region` is the element or selector it draws inside, which
   * verify masks, and a hook that names none is undeclared motion. A hook that throws is reported
   * once and stops drawing.
   */
  frame(fn: (seconds: number) => void, options: { region?: Region } = {}) {
    region(options.region);
    let told = false;
    every((seconds) => {
      try {
        fn(seconds);
        return true;
      } catch (err) {
        if (!told) warn("PAGE_FRAME_THREW", null, null, { value: String(err) });
        told = true;
        return false;
      }
    });
    return DeckTalk;
  },
  /**
   * Move one element from one cue to another in script, drawing `draw(progress, seconds)` every frame between them.
   *
   * The cues are cue ids, the length is their seconds apart, and progress is eased by one of the
   * contract's eases, so a script motion lands on its cue exactly as a declared one does.
   */
  motion(el: Region, motion: Scripted) {
    scripted(el, motion, cueAt);
    return DeckTalk;
  },
  /** Hold readiness until a promise of the page's own has settled. */
  waitFor(promise: unknown) {
    gates.push(Promise.resolve(promise));
    return DeckTalk;
  },
  /**
   * Every cue this page plays and the second on the section clock it is due, keyed by cue id.
   *
   * A composition page keys its own drawing on these, so a re-voiced line re-times it as it does a cue.
   */
  get cues(): Record<string, number> {
    return Object.fromEntries(state.cues.map((cue) => [cue.id, cueAt(cue.id) ?? cue.at]));
  },
  /**
   * Every word the section speaks, as the runtime matches it, the second the voice reaches it, and who says it.
   *
   * `voice` names the voice and `turn` counts the section's turns from one, so a page can place what
   * it draws by its voice without showing the name.
   */
  get words(): { word: string; at: number; voice: string | null; turn: number | null }[] {
    return (state.words ?? []).map((spoken) => ({
      word: spoken.key,
      at: spoken.at,
      voice: spoken.voice,
      turn: spoken.turn,
    }));
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
const view: RuntimeView = {
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
  get motions() {
    return lanes();
  },
  get frozen() {
    return isFrozen();
  },
  get signalled() {
    return waitsForSignal();
  },
  now,
  started,
  caption: (caption: Caption | null) => showCaption(caption),
  undeclared: undeclaredChange,
};

window.DeckTalk = DeckTalk;
window.__decktalk = view;

if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
else queueMicrotask(boot);
