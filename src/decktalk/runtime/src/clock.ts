/*! The narration clock and the queue of everything the page has still to do.
 *
 * Every second a DeckTalk page reasons in is a second after narration t=0, which is the frame the
 * recorder's cover came off or the moment a preview started. This module owns that origin, the
 * queue of work sorted by the second it is due, and the one animation frame loop that drains it.
 *
 * It holds no DOM and reads no markup, so a test can drive a whole recording's worth of cues
 * through it without a browser. The queue is drained inside an animation frame on purpose: a cue
 * that fires between two frames is a cue whose reveal is painted at the same time as one that fired
 * inside the frame, and the recorder cannot tell the two apart afterwards.
 */

/** What a queued task is, which decides the order two tasks due at the same second run in. */
export type Kind = "mount" | "cue" | "reveal";

/** One piece of work the page owes the clock, with the second on the narration clock it is due at. */
export type Task = {
  readonly at: number;
  readonly kind: Kind;
  readonly id: string;
  readonly run: () => void;
};

/**
 * A mount comes before anything else due at the same second.
 *
 * A cue and a timed reveal both need the slide that carries them to be on the stage already, so the
 * two ranks below are the whole of the tie-breaking rule.
 */
const MOUNT_FIRST = 0;
const AFTER_THE_MOUNT = 1;

/** The narration origin in `performance.now()` milliseconds, or null while the clock has not started. */
let origin: number | null = null;

/** The `performance.now()` stamp of the animation frame the queue last ran in, which stamps every cue. */
let frame = 0;

/** The tasks still owed, sorted by the second they are due and then by whether they are a mount. */
let queue: Task[] = [];

/** Whether the loop is already running, so a second call to `run` cannot start a second loop. */
let looping = false;

/**
 * Start the narration clock, which fixes the second every later call reads time against.
 *
 * The recorder calls this through `DeckTalk.startClock` on the frame its cover came off, and a page
 * nobody is recording calls it for itself once the document has loaded. A second call is ignored,
 * because an origin that moved would move every cue that had already fired.
 */
export function start(): void {
  if (origin === null) origin = performance.now();
}

/** Whether the clock has started, which is false only between the page load and the recorder's signal. */
export function started(): boolean {
  return origin !== null;
}

/** The second on the narration clock, or minus infinity before the clock has started. */
export function now(): number {
  return origin === null ? Number.NEGATIVE_INFINITY : (performance.now() - origin) / 1000;
}

/** The second the animation frame that ran the queue began, which is when a cue was really drawn. */
export function frameAt(): number | null {
  return origin === null ? null : round((frame - origin) / 1000);
}

/** A second on the narration clock at the precision every report and every log row carries. */
export function round(seconds: number): number {
  return Number(seconds.toFixed(3));
}

/** Owe the clock one piece of work at one second, which the next frame past that second runs. */
export function schedule(at: number, kind: Kind, id: string, run: () => void): void {
  queue.push({ at, kind, id, run });
  queue.sort((a, b) => a.at - b.at || rank(a.kind) - rank(b.kind));
}

function rank(kind: Kind): number {
  return kind === "mount" ? MOUNT_FIRST : AFTER_THE_MOUNT;
}

/** Everything still owed, which the heads-up display reads and a test reads to see what is due. */
export function pending(): readonly Task[] {
  return queue;
}

/** Forget every task, which is what a mode does when it takes the page over from another one. */
export function clear(): void {
  queue = [];
}

/**
 * Drain the queue once an animation frame, for as long as the page is open.
 *
 * `onFrame` is the one hook the loop offers, which the heads-up display uses. Nothing else may run
 * every frame, because a recording pays for every frame the page spends on work a viewer cannot see.
 */
export function run(onFrame?: (seconds: number) => void): void {
  if (looping) return;
  looping = true;
  const tick = (at: number) => {
    frame = at;
    const seconds = now();
    while (queue.length && (queue[0] as Task).at <= seconds) (queue.shift() as Task).run();
    onFrame?.(seconds);
    requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);
}
