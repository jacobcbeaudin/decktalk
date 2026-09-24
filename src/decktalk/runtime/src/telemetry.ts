/*! The seam between the page and the recorder, and nothing else.
 *
 * A deck in a browser tab should pay nothing for being recordable. The runtime therefore reports
 * what it alone knows, which is when a cue ran and which line the voice reached, through one small
 * interface, and the arrays, the retention caps and the follow-up frame stamps live in the probe the
 * recorder injects. A page opened without the probe keeps the no-op default and allocates nothing.
 *
 * This module holds the interface, the default and the one setter. It has no DOM and no state a
 * reader can see, so a runtime module that wants to record something imports `recorder()` and calls
 * it without ever knowing whether anyone is listening.
 */

import type { ReportField } from "./contract.ts";

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
  /** The second the first word was drawn, which is what `PAGE_WORD_LATE` compares with `runAt`. */
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
