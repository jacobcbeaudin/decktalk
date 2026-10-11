/*! The seam between the page and the recorder, and nothing else.
 *
 * A deck in a browser tab should pay nothing for being recordable. The runtime therefore reports
 * what it alone knows, which is when a cue ran and which line the voice reached, through one small
 * interface, and the arrays, the retention caps and the follow-up frame stamps live in the probe the
 * recorder injects. A page opened without the probe keeps the no-op default and allocates nothing.
 *
 * This module holds the interface, the default and the one setter, and the types of everything that
 * crosses the seam: the view the runtime publishes, the probe the recorder injects and the catalog
 * the two of them fill. The runtime and the probe are two bundles that meet only on `window`, so these
 * types are the one declaration both compile against. It has no DOM and no state a reader can see,
 * so a runtime module that wants to record something imports `recorder()` and calls it without ever
 * knowing whether anyone is listening.
 */

import type { Caption } from "./canvas.ts";
import type { Code, Ease, PageWarning, ReportField } from "./contract.ts";

/** One cue, as the runtime alone can describe it, in seconds on the section clock. */
export type CueEvent = {
  /** The cue id of the cue that fired. */
  readonly id: string;
  /** The second the cue was due. */
  readonly due: number;
  /** The second the cue actually ran. */
  readonly ran: number;
  /** The second the frame that ran it showed, or null before the clock has started. */
  readonly frame: number | null;
  /** What this cue's reveals describe themselves as that the slide has not already said, as one sentence, or null. */
  readonly describe: string | null;
};

/** One line shown word by word, reported once its first word is on screen. */
export type WordEvent = {
  /** The opening of the line, which is enough to find it in the script. */
  readonly text: string;
  /** The second the cue that started the line ran. */
  readonly cueAt: number;
  /** The second the voice reaches the line's first word. */
  readonly spokenAt: number;
  /** How many words the line holds. */
  readonly count: number;
  /** The second the first word was drawn, which the debug log prints beside `spokenAt`. */
  readonly firstShown: number;
};

/**
 * What the recorder wants from a page that does not know it is being recorded.
 *
 * Every method returns nothing and may be called from inside an animation frame, so an
 * implementation records the row and does no work the frame cannot afford.
 */
export type Recorder = {
  cue(event: CueEvent): void;
  words(event: WordEvent): void;
};

/**
 * The two report fields a recorder fills, named by the contract so the two cannot drift apart.
 */
export type Telemetry = Record<Extract<ReportField, "cues" | "words">, readonly unknown[]>;

/** The recorder a page has when nobody is recording it, which allocates nothing and keeps nothing. */
const NOBODY: Recorder = {
  cue() {},
  words() {},
};

let current: Recorder = NOBODY;

/** The recorder in force, which every runtime call site reads afresh so the seam can be set once at startup. */
export function recorder(): Recorder {
  return current;
}

/** Hand the page a recorder, or `null` to give it back the one that keeps nothing. */
export function setRecorder(next: Recorder | null | undefined): void {
  current = next ?? NOBODY;
}

// ---- what crosses the seam on window ------------------------------------------------------------

/** What the page is doing, which the probe reports and a person reads in the heads-up display. */
export type Mode = "index" | "preview" | "record" | "freeze" | "caption";

/** A box in canvas pixels, which is the coordinate system every static check reasons in. */
export type Box = { x: number; y: number; w: number; h: number };

/** One measured element of a slide, as the author wrote it and as the layout placed it. */
export type ElementRow = {
  attrs: Record<string, string>;
  moments: Record<string, string>;
  text: string;
  box: Box;
  /** How many children a staggered element reveals one after another, which is zero for any other element. */
  children: number;
};

/** One motion or loop a slide declares, with the cue ids it departs and arrives on. */
export type MotionRow = {
  kind: "motion" | "loop";
  name: string;
  from: string | null;
  to: string | null;
  ease: Ease | null;
  seconds: number | null;
};

/** One scene as the catalog publishes it, which is what every static check reads the deck from. */
export type CatalogEntry = {
  scene: string;
  name: string;
  slides: string[];
  cues: Record<string, string[]>;
  spans: Record<string, number>;
  /** Every motion and loop each slide declares in its markup, keyed by the slide id. */
  motions: Record<string, MotionRow[]>;
  /** The measured rows of each slide, which the probe adds on the index page and nothing else writes. */
  elements?: Record<string, ElementRow[]>;
};

/** A slide as the probe sees it while it measures, which is its id and nothing the probe could change. */
export type LoanedSlide = { readonly id: string };

/** A scene as the probe sees it while it measures, which is its slides in order. */
export type LoanedScene = { readonly slides: readonly LoanedSlide[] };

/**
 * What the runtime lends the probe to measure in, which is the layer to build into and the frame to measure against.
 *
 * `build` is written as a method so the runtime's own builder, which takes its full scene and slide
 * types, is the function it lends, and the probe only ever hands back what `scenes` gave it.
 */
export type CanvasLoan = {
  readonly pan: HTMLElement;
  readonly origin: HTMLElement;
  readonly scale: number;
  readonly scenes: ReadonlyMap<string, LoanedScene>;
  build(scene: LoanedScene, slide: LoanedSlide): HTMLElement;
};

/** How the probe reports a freeze stop it could not find, which is the runtime's own warn in its positional shape. */
export type WarnAt = (code: Code, slide: string, cue: string) => void;

/**
 * What one step of the section clock answers, which is everything the recorder learns from a frame.
 *
 * `due` is the second the next task is due, or null when nothing is owed, which the recorder steps to
 * first. `changed` is whether anything the page draws may have moved since the step before. The
 * recorder writes the frame before again rather than capture a frame that answered false, so a step
 * answers true whenever it cannot vouch that the picture stood still. `stillUntil` is the earliest
 * second anything the page draws can next change: the step's own second while anything moves, or
 * null when nothing owed or moving ever will. The recorder writes every frame before that second
 * again without stepping the page to it, so a step answers its own second whenever it cannot vouch
 * for the seconds after it.
 */
export type Step = {
  readonly due: number | null;
  readonly changed: boolean;
  readonly stillUntil: number | null;
};

/**
 * What the probe answers for one step, which is the runtime's step and what only the browser saw.
 *
 * `undeclared` names the first element the page changed between the step before and this one, outside
 * every cue, frame hook and animation the runtime ran and inside nothing the page declared as moving,
 * or is null when there is none.
 */
export type Stepped = Step & { readonly undeclared: string | null };

/** Everything the recorder injects as `window.__decktalkProbe`, which the recorder and the runtime both call. */
export type Probe = {
  step(seconds: number, waitSeconds: number): Promise<Stepped>;
  started(waitSeconds: number): Promise<boolean>;
  ready(): Promise<boolean>;
  report(): Record<ReportField, unknown>;
  readonly recorder: Recorder;
  freezeCues(order: readonly string[], slide: string, warn: WarnAt): readonly string[];
  measure(catalog: CatalogEntry[], canvas: CanvasLoan): CatalogEntry[];
};

/**
 * The live view the runtime publishes as `window.__decktalk`, which the probe reads and never writes.
 *
 * `version` is the runtime's own, so a reader holding a page can tell which runtime drew it without
 * reading the file the page loaded.
 */
export type RuntimeView = {
  readonly version: string;
  ready: Promise<boolean> | null;
  readonly mode: Mode;
  readonly scene: string | null;
  readonly slide: string | null;
  readonly cues: readonly { id: string; at: number }[];
  readonly fired: readonly string[];
  readonly warnings: readonly PageWarning[];
  readonly catalog: readonly CatalogEntry[];
  /** Every declared motion, loop and region, with the box it swept and the cues it lands on. */
  readonly motions: readonly unknown[];
  readonly frozen: boolean;
  readonly signalled: boolean;
  now(): number;
  started(): boolean;
  /** Draw one caption in the band of a page opened with `?caption=1`, or none, and answer once it has painted. */
  caption(caption: Caption | null): Promise<boolean>;
  /** The first of some nodes the page changed that nothing declared as moving, named, or null when there is none. */
  undeclared(nodes: readonly Node[]): string | null;
};

declare global {
  interface Window {
    /** The author-facing runtime, of which the probe calls the one method that moves the clock. */
    DeckTalk?: { stepTo?(seconds: number): Step };
    __decktalk?: RuntimeView;
    __decktalkProbe?: Probe;
  }
}
