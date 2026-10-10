/*! The timeline, which holds every animation at the second the stepped clock shows, tested without a browser.
 *
 * An animation here is a plain object with the three members the timeline reads and writes, so the
 * arithmetic of a birth and a seek is held exactly, and the browser tests hold that a real
 * transition, a real CSS animation and a real Web Animation obey it.
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import { observe, schedule, stepTo } from "../../../../src/decktalk/runtime/src/clock.ts";
import { animationsFrom, type Seekable, timeline } from "../../../../src/decktalk/runtime/src/timeline.ts";

/** An animation as the timeline sees one, which records whether it was paused. */
function animation(playbackRate = 1): Seekable & { paused: boolean } {
  return {
    currentTime: 0,
    playbackRate,
    paused: false,
    pause() {
      this.paused = true;
    },
  };
}

const live: Seekable[] = [];
animationsFrom(() => live);
observe(timeline);

const loop = animation();
live.push(loop);

test("an animation present at load is held at the section second on every step", () => {
  for (const seconds of [0, 0.04, 0.5, 1.0]) {
    stepTo(seconds);
    assert.equal(loop.currentTime, seconds * 1000);
  }
  assert.ok((loop as { paused: boolean }).paused);
});

test("a transition a cue starts is born at the cue's second, not at the frame that ran it", () => {
  const transition = animation();
  schedule(1.02, "cue", "a:fade", () => live.push(transition));
  stepTo(1.04);
  assert.equal(Math.round(transition.currentTime as number), 20);
  stepTo(1.08);
  assert.equal(Math.round(transition.currentTime as number), 60);
});

test("a faster animation is held as far along as its own rate takes it", () => {
  const fast = animation(2);
  schedule(2.0, "cue", "a:fast", () => live.push(fast));
  stepTo(2.0);
  stepTo(2.1);
  assert.equal(Math.round(fast.currentTime as number), 200);
});

test("a task sees every animation at its own second, however late the step that runs it", () => {
  const fade = animation();
  live.push(fade);
  stepTo(3.0);
  let seen = Number.NaN;
  schedule(3.5, "cue", "a:back", () => {
    seen = fade.currentTime as number;
  });
  stepTo(3.8);
  assert.equal(Math.round(seen), 500, "the fade stands where second 3.5 puts it when the cue turns it");
  assert.equal(Math.round(fade.currentTime as number), 800);
});

/** An animation of `duration` milliseconds that holds its end once over, as `fill: both` does. */
function filled(duration: number): Seekable {
  const one = animation();
  return Object.assign(one, {
    effect: {
      getComputedTiming: () => ({
        progress: Math.min(1, Math.max(0, (one.currentTime as number) / duration)),
        currentIteration: 0,
      }),
    },
  });
}

test("a step says the page changed while an animation's progress moves, and not once it rests at its end", () => {
  live.splice(0, live.length);
  stepTo(4.0);
  assert.equal(stepTo(4.04).changed, false, "nothing on the page moves");
  schedule(4.06, "cue", "a:fade", () => live.push(filled(100)));
  assert.equal(stepTo(4.08).changed, true);
  assert.equal(stepTo(4.12).changed, true);
  assert.equal(stepTo(4.16).changed, true, "the fade reaches its end on this step");
  assert.equal(stepTo(4.2).changed, false, "the fade rests where it ended");
});

test("a step says the page changed when an animation leaves the page, even one that had come to rest", () => {
  live.splice(0, live.length);
  const rested = filled(40);
  live.push(rested);
  stepTo(5.0);
  stepTo(5.04);
  assert.equal(stepTo(5.08).changed, false);
  live.splice(0, live.length);
  assert.equal(stepTo(5.12).changed, true);
  assert.equal(stepTo(5.16).changed, false);
});

/** An animation of `duration` milliseconds after `delay`, read the way a browser reads a paused one. */
function delayed(delay: number, duration: number, iterations = 1): Seekable {
  const one = animation();
  return Object.assign(one, {
    effect: {
      getComputedTiming: () => {
        const local = one.currentTime as number;
        const progress = local < delay ? 0 : Math.min(1, (local - delay) / duration);
        return { progress, currentIteration: 0, delay, activeDuration: duration * iterations };
      },
    },
  });
}

test("a step answers when an animation waiting out its delay starts, and none while one plays", () => {
  live.splice(0, live.length);
  stepTo(6.0);
  schedule(6.02, "cue", "a:late", () => live.push(delayed(500, 200)));
  const waiting = stepTo(6.04);
  assert.ok(Math.abs((waiting.stillUntil as number) - 6.52) < 1e-9, `${waiting.stillUntil}`);
  assert.equal(stepTo(6.6).stillUntil, 6.6, "the animation plays");
  assert.equal(stepTo(6.8).stillUntil, null, "the animation rests where it ended");
});

test("a step answers no still stretch while an animation repeats for ever", () => {
  live.splice(0, live.length);
  live.push(delayed(0, 1000, Number.POSITIVE_INFINITY));
  assert.equal(stepTo(7.0).stillUntil, 7.0);
  assert.equal(stepTo(9.0).stillUntil, 9.0);
});

test("a step answers no still stretch for an animation it cannot read the timing of", () => {
  live.splice(0, live.length);
  live.push(animation());
  assert.equal(stepTo(10.0).stillUntil, 10.0);
});
