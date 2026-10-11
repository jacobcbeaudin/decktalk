/*! The timeline: every animation on the page, held at the second the stepped clock shows.
 *
 * Playwright's clock moves timers and animation frames, and it does not move the document timeline,
 * so a CSS transition, a CSS animation or a Web Animation keeps playing at the compositor's own pace
 * unless something holds it. Under the stepped driver this module is that something. It hears the
 * clock run each task and end each step, gives every animation a birth second the first time it sees
 * it, and on every step pauses each one at the second since its birth.
 *
 * The birth is the second the task that started it was due, never the frame that ran it, so a
 * transition a cue starts between two frames is already as far along as the cue's own second says on
 * the first frame that shows it. Every animation is also sought to a task's second just before the
 * task runs, so a transition the task interrupts turns from its value at that second, which is the
 * same whether the recorder stepped every frame before it or only the seconds work was due. Under the
 * realtime driver this module does nothing, and the browser plays every animation as it always has.
 *
 * Each step it also tells the clock whether any animation now draws differently from the step
 * before. It keeps a signature of every live animation's progress, so an animation that has come to
 * rest at its end, or waits out its delay, is no change, and one that started, moved or left is. And
 * it tells the clock the earliest second any animation can next draw differently: the step's own
 * second while one plays, the end of its delay while one waits, and never once every one has ended.
 */

import { driven, earliest, type Observer } from "./clock.ts";
import { MILLISECONDS } from "./contract.ts";

/**
 * Where an animation stands within its effect, which is the part of `ComputedEffectTiming` the timeline reads.
 *
 * A signature reads the progress and the iteration. The next second an animation moves is read from
 * its delay and its active duration, both in milliseconds of its own time.
 */
type Standing = {
  readonly progress?: number | null;
  readonly currentIteration?: number | null;
  readonly delay?: number;
  readonly activeDuration?: CSSNumberish;
};

/** What the timeline needs of one animation, which is the part of `Animation` it reads and writes. */
export type Seekable = {
  currentTime: CSSNumberish | null;
  readonly playbackRate: number;
  readonly effect?: { getComputedTiming(): Standing } | null;
  pause(): void;
};

/** Where the timeline finds the page's animations, which a test swaps for a list of its own. */
let source: () => readonly Seekable[] = () => document.getAnimations();

/** The second on the section clock each animation began, keyed by the animation itself. */
let born = new WeakMap<Seekable, number>();

/** What hears of each animation the moment the timeline first sees it, or null when nobody asks. */
let meet: ((animation: Seekable) => void) | null = null;

/** A number for each animation the timeline has met, in the order it met them, so a signature names each one. */
let numbered = new WeakMap<Seekable, number>();

/** How many animations the timeline has met, which is the number the next one is given. */
let met = 0;

/** The signature of the step before, which the next step compares its own with. */
let last = "";

/** Give every animation the timeline has not seen yet the birth second `at`, and say it was met. */
function adopt(at: number): void {
  for (const animation of source()) {
    if (born.has(animation)) continue;
    born.set(animation, at);
    numbered.set(animation, met);
    met += 1;
    meet?.(animation);
  }
}

/**
 * Every live animation and where it stands, as one string two steps can compare.
 *
 * An animation's progress through its effect is what it draws, so one at rest past its end with its
 * fill held, or waiting out its delay, reads the same on every step. One with no effect to ask is
 * read by its current time, which moves on every step it plays.
 */
function signature(): string {
  return source()
    .map((animation) => {
      const standing = animation.effect?.getComputedTiming();
      const where = standing ? `${standing.progress}/${standing.currentIteration}` : `${animation.currentTime}`;
      return `${numbered.get(animation)}:${where}`;
    })
    .join(",");
}

/**
 * The earliest second at or after `seconds` one animation, held at `seconds`, can next draw differently.
 *
 * An animation still waiting out its delay draws the same until the delay ends, one past the end of
 * its active duration draws the same for good, and one in between moves now. One that runs backwards,
 * or whose time or timing cannot be read, moves now, so nothing is skipped on a guess.
 */
function nextMove(animation: Seekable, seconds: number): number | null {
  const rate = animation.playbackRate;
  if (rate === 0) return null;
  const standing = animation.effect?.getComputedTiming();
  const local = animation.currentTime;
  if (rate < 0 || typeof local !== "number" || !standing) return seconds;
  const { delay, activeDuration } = standing;
  if (typeof delay !== "number" || typeof activeDuration !== "number") return seconds;
  if (local >= delay + activeDuration) return null;
  if (local < delay) return seconds + (delay - local) / (rate * MILLISECONDS);
  return seconds;
}

/** Hear of every animation the stepped clock meets, once, which is where undeclared motion is caught. */
export function whenMet(next: ((animation: Seekable) => void) | null): void {
  meet = next;
}

/** Hold every animation at the second `seconds` shows, as far along as it is since its own birth. */
function seek(seconds: number): void {
  for (const animation of source()) {
    const birth = born.get(animation) ?? seconds;
    animation.pause();
    animation.currentTime = Math.max(0, (seconds - birth) * MILLISECONDS * animation.playbackRate);
  }
}

/** Whether the stepped clock has run, which is when what the page set moving before it starts is born. */
let begun = false;

/**
 * Give everything the page set moving before the clock started its birth at the section's second zero.
 *
 * The first slide mounts before the recorder's first step, so a loop it starts was moving from the
 * section's start whichever second the recorder asks for first.
 */
function begin(): void {
  if (begun) return;
  begun = true;
  adopt(0);
}

/** What the clock tells as it moves, which is a no-op until the recorder has started stepping. */
export const timeline: Observer = {
  running(at: number) {
    if (driven() !== "stepped") return;
    begin();
    adopt(at);
    seek(at);
  },
  ran(at: number) {
    if (driven() !== "stepped") return;
    begin();
    adopt(at);
  },
  stepped(seconds: number) {
    begin();
    adopt(seconds);
    seek(seconds);
    const now = signature();
    const moved = now !== last;
    last = now;
    return moved;
  },
  still(seconds: number) {
    return earliest(...source().map((animation) => nextMove(animation, seconds)));
  },
};

/** Read the page's animations from `next` instead of the document, which is how a test hands over its own. */
export function animationsFrom(next: () => readonly Seekable[]): void {
  source = next;
  born = new WeakMap();
  numbered = new WeakMap();
  met = 0;
  last = "";
  begun = false;
}
