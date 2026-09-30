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

import type { Code, PageWarning, ReportField } from "./contract.ts";

/** One cue, as the runtime alone can describe it, in seconds on the narration clock. */
export type CueEvent = {
  /** The wire id of the cue that fired. */
  readonly id: string;
  /** The second the cue was due. */
  readonly due: number;
  /** The second the cue actually ran. */
  readonly ran: number;
  /** The second the animation frame that ran it began, or null before the clock has started. */
  readonly frame: number | null;
  /** What this cue's reveals describe themselves as, joined into one sentence, or null. */
  readonly describe: string | null;
};

/** One line shown word by word, reported once its first word is on screen. */
export type WordEvent = {
  /** The opening of the line, which is enough to find it in the script. */
  readonly text: string;
  /** The second the cue that started the line ran. */
  readonly cueAt: number;
  /** The second the voice reaches the line's first word. */
  readonly runAt: number;
  /** How many words the line holds. */
  readonly count: number;
  /** The second the first word was drawn, which the debug log prints beside `runAt`. */
  readonly firstOn: number;
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
 * The four report fields a recorder fills, named by the contract so the two cannot drift apart.
 *
 * `cues` and `words` are the rows this seam carries, and `frameGaps` and `longFrames` are what the
 * probe observes for itself, because neither needs the DOM or anything the runtime knows.
 */
export type Telemetry = Record<Extract<ReportField, "cues" | "words" | "frameGaps" | "longFrames">, readonly unknown[]>;

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
export type Mode = "index" | "preview" | "cue" | "freeze";

/** A box in stage pixels, which is the coordinate system every static check reasons in. */
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

/** One scene as the catalog publishes it, which is what every static check reads the deck from. */
export type CatalogEntry = {
  scene: string;
  name: string;
  slides: string[];
  cues: Record<string, string[]>;
  spans: Record<string, number>;
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
export type StageLoan = {
  readonly pan: HTMLElement;
  readonly origin: HTMLElement;
  readonly scale: number;
  readonly scenes: ReadonlyMap<string, LoanedScene>;
  build(scene: LoanedScene, slide: LoanedSlide): HTMLElement;
};

/** How the probe reports a freeze stop it could not find, which is the runtime's own warn in its positional shape. */
export type WarnAt = (code: Code, slide: string, cue: string) => void;

/** Everything the recorder injects as `window.__dtprobe`, which the recorder and the runtime both call. */
export type Probe = {
  cover(): void;
  lift(): Promise<number>;
  ready(): Promise<boolean>;
  report(): Record<ReportField, unknown>;
  readonly recorder: Recorder;
  freezeCues(order: readonly string[], slide: string, warn: WarnAt): readonly string[];
  measure(catalog: CatalogEntry[], stage: StageLoan): CatalogEntry[];
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
  readonly frozen: boolean;
  readonly signalled: boolean;
  now(): number;
  started(): boolean;
};

declare global {
  interface Window {
    /** The author-facing runtime, of which the probe calls one method. */
    DeckTalk?: { startClock?(): void };
    __decktalk?: RuntimeView;
    __dtprobe?: Probe;
  }
}
