/*! The section clock and the queue of everything the page has still to do.
 *
 * Every second a DeckTalk page reasons in is a second on the section clock. This module owns that
 * clock, the queue of work sorted by the second it is due, and the frame hooks that redraw something
 * every frame, and it moves all three through one of two drivers.
 *
 * The realtime driver is a person's browser: the clock reads `performance.now()` from the moment the
 * page started, and one animation frame loop drains the queue and calls the hooks. The stepped driver
 * is the recorder's: nothing moves until the recorder calls `stepTo(t)`, which puts the clock at that
 * second, runs what is due in the order it was due, each at its own second, calls every hook with that
 * second and then tells the timeline to show it. A recording is therefore a list of exact states rather than a race against
 * a compositor, and two recordings of the same page are the same frames. The step also answers whether
 * anything it knows of moved: a task ran, a hook drew, or what hears the clock says what it holds moved.
 * The recorder writes the frame before again for a step where nothing did and the page agrees. The
 * step also answers the earliest second anything can next change, which is the next task's second,
 * when no hook draws, unless what hears the clock moves sooner, so the recorder can carry the frame
 * across every second before it without a step.
 *
 * It holds no DOM and reads no markup, so a test can drive a whole recording's worth of cues through
 * it without a browser. The timeline that seeks the page's animations listens through `observe`, so
 * this module never touches an animation itself.
 */

import { DUE_SLACK_SECONDS, MILLISECONDS, SECOND_DIGITS } from "./contract.ts";
import type { Step } from "./telemetry.ts";

/** What a queued task is, which decides the order two tasks due at the same second run in. */
export type Kind = "mount" | "cue" | "reveal" | "retire";

/** One piece of work the page owes the clock, with the second on the section clock it is due at. */
type Task = {
  readonly at: number;
  readonly kind: Kind;
  readonly id: string;
  readonly run: () => void;
};

/** Who moves the clock: a person's browser in real time, or the recorder one frame at a time. */
type Driver = "realtime" | "stepped";

/**
 * Something redrawn every frame from the section second, which returns false once it has finished.
 *
 * A hook is how a count, a word-by-word line and an author's own `DeckTalk.frame` are drawn, so the
 * stepped driver draws them at exactly the second a frame shows and never at the wall clock's.
 */
type Hook = (seconds: number) => boolean | undefined;

/**
 * What hears the clock move, which is the timeline that seeks every animation on the page.
 *
 * `running` is told the second each task is due just before it runs, which is when the timeline is
 * sought to that second, so a transition the task interrupts turns from where it stands at the task's
 * own second and not at whichever second the page last showed. `ran` is told the same second as soon
 * as the task has run, so whatever that task started is born at its own cue rather than at the frame
 * that happened to run it. `stepped` is told the second a step ends on, after every task and every
 * hook, which is when the timeline is sought to the frame. The page at a second is therefore a
 * function of that second, however few or many seconds were stepped before it. `stepped` answers
 * whether anything it holds now draws differently from the step before, such as an animation's progress.
 * `still` is asked right after, and answers the earliest second anything it holds can next draw
 * differently: `seconds` itself while something moves, or null when nothing it holds ever will.
 */
export type Observer = {
  running(at: number): void;
  ran(at: number): void;
  stepped(seconds: number): boolean;
  still(seconds: number): number | null;
};

/**
 * A mount comes before anything else due at the same second.
 *
 * A cue and a timed reveal both need the slide that carries them to be on the canvas already, so the
 * two ranks below are the whole of the tie-breaking rule.
 */
const MOUNT_FIRST = 0;
const AFTER_THE_MOUNT = 1;

/** The driver in force, which the first `start` or the first `stepTo` decides for the life of the page. */
let driver: Driver = "realtime";

/** The section clock's origin in `performance.now()` milliseconds, or null while the clock has not started. */
let origin: number | null = null;

/** The second the stepped driver last moved to, which is `now()` under that driver. */
let current = 0;

/** The `performance.now()` stamp of the animation frame the queue last ran in, which stamps every cue. */
let frame = 0;

/** The tasks still owed, sorted by the second they are due and then by whether they are a mount. */
let queue: Task[] = [];

/** Every hook still drawing, in the order it was added. */
let hooks: Hook[] = [];

/** What hears the clock move, or null on a page with nothing to seek. */
let observer: Observer | null = null;

/** Whether the loop is already running, so a second call to `run` cannot start a second loop. */
let looping = false;

/** The animation frame the loop waits on next, or null when it waits on none. */
let waiting: number | null = null;

/**
 * Start the section clock under the realtime driver, which fixes the second every later call reads time against.
 *
 * A page nobody is recording calls this for itself once the document has loaded. A second call is
 * ignored, and so is a call once the recorder has started stepping, because an origin that moved
 * would move every cue that had already fired.
 */
export function start(): void {
  if (origin === null) origin = performance.now();
}

/** Whether the clock has started, which is false only between the page load and the first step or start. */
export function started(): boolean {
  return origin !== null;
}

/** Which driver moves the clock, which is the stepped one only once the recorder has stepped it. */
export function driven(): Driver {
  return driver;
}

/** The second on the section clock, or minus infinity before the clock has started. */
export function now(): number {
  if (origin === null) return Number.NEGATIVE_INFINITY;
  return driver === "stepped" ? current : (performance.now() - origin) / MILLISECONDS;
}

/** The second the frame that ran the queue began, which is when a cue was really drawn. */
export function frameAt(): number | null {
  if (origin === null) return null;
  return driver === "stepped" ? round(current) : round((frame - origin) / MILLISECONDS);
}

/** A second on the section clock at the precision every report and every log row carries. */
export function round(seconds: number): number {
  return Number(seconds.toFixed(SECOND_DIGITS));
}

/** Owe the clock one piece of work at one second, which the next frame at or past that second runs. */
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

/** Forget every task and every hook, which is what a mode does when it takes the page over from another one. */
export function clear(): void {
  queue = [];
  hooks = [];
}

/** Redraw something every frame from the section second until it returns false. */
export function every(hook: Hook): void {
  hooks.push(hook);
}

/** Hand the clock what hears it move, which the timeline does once at startup. */
export function observe(next: Observer | null): void {
  observer = next;
}

/**
 * Run every task due by `seconds`, each told to the observer at its own due second before and after
 * it runs, and answer whether any ran.
 */
function drain(seconds: number): boolean {
  let ran = false;
  while (queue.length && (queue[0] as Task).at <= seconds + DUE_SLACK_SECONDS) {
    const task = queue.shift() as Task;
    observer?.running(task.at);
    task.run();
    observer?.ran(task.at);
    ran = true;
  }
  return ran;
}

/**
 * Call every hook with `seconds`, dropping each one that says it has finished, and answer whether any drew.
 *
 * A hook draws whatever it likes, a canvas included, so a hook called at all counts as a change,
 * the call that says it has finished too.
 */
function draw(seconds: number): boolean {
  const drew = hooks.length > 0;
  hooks = hooks.filter((hook) => hook(seconds) !== false);
  return drew;
}

/** The earliest of some seconds, where null is a second that never comes. */
export function earliest(...seconds: (number | null)[]): number | null {
  let first: number | null = null;
  for (const one of seconds) if (one !== null && (first === null || one < first)) first = one;
  return first;
}

/**
 * Move the clock to `seconds` under the stepped driver, which is the whole of one recorded frame,
 * and answer the second the next task is due, or null when nothing is owed, and whether anything the
 * clock knows of changed: a task ran, a hook drew, or the observer says what it holds moved. It also
 * answers the earliest second anything can next change: `seconds` itself while a hook is still drawing,
 * and otherwise the next task's second or the observer's own, whichever comes first.
 *
 * The first call starts the clock at zero under that driver, so the section clock is the recorder's
 * frame count and nothing else, and it always counts as a change. A page whose clock a person
 * already started keeps the realtime driver, and the call does nothing, because one page cannot be on
 * two clocks. It answers a change and no still stretch then, because nothing holds the page still.
 */
export function stepTo(seconds: number): Step {
  const first = origin === null;
  if (first) {
    driver = "stepped";
    origin = 0;
    // The loop would only see the stepped driver and stop, so its frame is not left on the recorder's clock.
    if (waiting !== null) cancelAnimationFrame(waiting);
    waiting = null;
  }
  if (driver !== "stepped") return { due: null, changed: true, stillUntil: seconds };
  current = seconds;
  const ran = drain(seconds);
  const drew = draw(seconds);
  const moved = observer?.stepped(seconds) ?? false;
  const stillUntil = hooks.length > 0 ? seconds : earliest(due(), observer?.still(seconds) ?? null);
  return { due: due(), changed: first || ran || drew || moved, stillUntil };
}

/**
 * The second the next task is due, or null when nothing is owed.
 *
 * The recorder steps to this second before it moves the page's timers past it, so a timer a cue sets
 * starts from the cue's own second and not from the frame after it.
 */
export function due(): number | null {
  return queue.length ? (queue[0] as Task).at : null;
}

/**
 * Drain the queue and draw the hooks once an animation frame, for as long as the realtime driver moves the clock.
 *
 * `onFrame` is the one extra the loop offers, which the heads-up display uses. The loop stops when
 * the recorder starts stepping, whose first step cancels the frame it waits on, because from then on
 * the recorder is the only thing that moves the clock and a frame left waiting on its clock is a timer
 * that ends a still stretch for nothing.
 */
export function run(onFrame?: (seconds: number) => void): void {
  if (looping) return;
  looping = true;
  const tick = (at: number) => {
    waiting = null;
    if (driver === "stepped") return;
    frame = at;
    const seconds = now();
    drain(seconds);
    if (origin !== null) draw(seconds);
    onFrame?.(seconds);
    waiting = requestAnimationFrame(tick);
  };
  waiting = requestAnimationFrame(tick);
}
