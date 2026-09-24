/*! The four moments, the class moment, the swap, and the sentence each of them writes.
 *
 * An element arrives, steps back, comes to the front and leaves, and every one of those is a cue.
 * This module wires a mounted slide so that each of its cues knows exactly what to do and what to
 * say, and nothing else in the runtime knows what a moment is.
 *
 * The transcript is composed here as well, because the author writes one noun phrase and the verb
 * belongs to the moment rather than to the author. The lines of one cue are joined in document
 * order, which is what stops two lines at one second from being ordered by their first letter
 * anywhere downstream.
 */

import { now, schedule } from "./clock.ts";
import { ATTENTION, type Attr, type Count, EXITS, wireId } from "./contract.ts";
import { fallback } from "./katex.ts";
import {
  ATTR,
  classMomentsOf,
  classPhraseOf,
  countOf,
  entranceOf,
  entranceSeconds,
  exitOf,
  flagged,
  momentElements,
  momentsOf,
  type Slide,
  staggerSeconds,
  wordStyleOf,
  written,
} from "./scene.ts";
import { CLASS, countSeconds, span, styleClass } from "./stage.ts";
import { count as countUp, type Spoken, line as spokenLine } from "./text.ts";

/**
 * The verb each moment gives the noun phrase its author wrote, which is the whole of the composition.
 *
 * These five sentences are the transcript's own words. They live here once, because the registry
 * publishes what an attribute means to an agent and this is what a moment sounds like read aloud.
 */
const SENTENCE: Record<string, (subject: string) => string> = {
  [ATTR.in]: (subject) => `${subject} appears.`,
  [ATTR.back]: (subject) => `${subject} steps back.`,
  [ATTR.front]: (subject) => `${subject} comes to the front.`,
  [ATTR.out]: (subject) => `${subject} leaves.`,
};

/** What one element does at one cue, and what that moment says in the transcript. */
type Action = {
  readonly attr: Attr;
  readonly run: () => void;
  readonly line: string | null;
};

/** What a mounted slide needs from the mode that mounted it, which is the state no element can read. */
export type Playing = {
  readonly frozen: boolean;
  readonly held: ReadonlySet<string> | null;
  readonly words: readonly Spoken[] | null;
};

/** A slide on the stage, with every cue of it wired and every stop it owes when it leaves. */
export type Mounted = {
  readonly el: HTMLElement;
  readonly slide: Slide;
  readonly actions: Map<string, Action[]>;
  readonly stop: () => void;
};

/**
 * Wire every moment of a mounted slide, and hide whatever has still to arrive.
 *
 * Nothing is drawn here and no cue is fired. A frozen slide is the one exception, because a still
 * has no clock to fire anything on, so every cue it is not asked to hold is run as it is wired.
 */
export function prepare(el: HTMLElement, slide: Slide, playing: Playing): Mounted {
  const actions = new Map<string, Action[]>();
  const stops: (() => void)[] = [];
  const held = heldSwaps(el);
  const mounted: Mounted = {
    el,
    slide,
    actions,
    stop: () => {
      for (const one of stops) one();
      stops.length = 0;
    },
  };
  const onLeave = (fn: () => void) => stops.push(fn);
  for (const target of momentElements(el)) {
    const element = target as HTMLElement;
    const moments = momentsOf(element, slide.id);
    const arrival = moments.find((one) => one.attr === ATTR.in);
    if (arrival && !playing.frozen) hide(element);
    for (const moment of moments) {
      const action = actionFor(element, moment.attr, moment.cue, slide, playing, held, onLeave);
      push(actions, moment.cue, action);
    }
    for (const change of classMomentsOf(element, slide.id)) {
      push(actions, change.cue, {
        attr: ATTR.class,
        run: () => element.classList.add(change.name),
        line: classPhraseOf(element, change.local) || null,
      });
    }
  }
  for (const container of el.querySelectorAll(`[${ATTR.steps}]`)) steps(container as HTMLElement, slide, actions);
  if (playing.frozen) {
    for (const [cue, list] of actions) {
      if (playing.held?.has(cue)) continue;
      for (const action of list) action.run();
    }
  }
  return mounted;
}

/** Add one action to the list of a cue, keeping the document order the elements were walked in. */
function push(actions: Map<string, Action[]>, cue: string, action: Action): void {
  const list = actions.get(cue) ?? [];
  list.push(action);
  actions.set(cue, list);
}

/** Hide an element that has still to arrive, which is the state every entrance animates out of. */
function hide(el: HTMLElement): void {
  const step = staggerSeconds(el);
  if (step === null) {
    el.classList.add(CLASS.hidden);
    return;
  }
  for (const child of el.children) child.classList.add(CLASS.hidden);
}

/**
 * Which element each swap holds on the stage until its replacement has arrived.
 *
 * A swap is only ever between one thing and one other thing, so zero candidates and two candidates
 * are both reported and neither is guessed at: an author who meant a swap names the cue on both
 * elements, and an author who did not gets their own exit at its own length.
 */
function heldSwaps(root: HTMLElement): Map<Element, number> {
  const held = new Map<Element, number>();
  for (const el of root.querySelectorAll(`[${ATTR.swaps}]`)) {
    const local = written(el, ATTR.in);
    if (!local) continue;
    // A swap between one thing and no other thing, or two, is reported by the markup reader, which
    // sees the same markup before a slide is ever mounted.
    const leaving = [...momentElements(root)].filter((other) => written(other, ATTR.out) === local);
    if (leaving.length !== 1) continue;
    held.set(leaving[0] as Element, entranceSeconds(el));
  }
  return held;
}

/** What one moment of one element does, and what it says, which is the whole of the grammar. */
function actionFor(
  el: HTMLElement,
  attr: Attr,
  cue: string,
  slide: Slide,
  playing: Playing,
  held: Map<Element, number>,
  onLeave: (stop: () => void) => void,
): Action {
  if (attr === ATTR.back) {
    return {
      attr,
      run: () => {
        span(el, ATTENTION.back.seconds);
        el.classList.remove(CLASS.front);
        el.classList.add(CLASS.back);
      },
      line: subject(el, attr),
    };
  }
  if (attr === ATTR.front) {
    return {
      attr,
      run: () => {
        span(el, ATTENTION.front.seconds);
        el.classList.remove(CLASS.back);
        el.classList.add(CLASS.front);
      },
      line: subject(el, attr),
    };
  }
  if (attr === ATTR.out) {
    const style = exitOf(el);
    const wait = held.get(el) ?? 0;
    return {
      attr,
      run: () => {
        const go = () => {
          span(el, EXITS[style].seconds);
          el.classList.add(styleClass("out", style));
        };
        if (wait > 0 && !playing.frozen) schedule(now() + wait, "reveal", cue, go);
        else go();
      },
      line: written(el, ATTR.describeOut) || subject(el, attr),
    };
  }
  return arrival(el, cue, slide, playing, onLeave);
}

/** What an arrival does, which is the entrance, the stagger it may spread, and the text effects. */
function arrival(
  el: HTMLElement,
  cue: string,
  slide: Slide,
  playing: Playing,
  onLeave: (stop: () => void) => void,
): Action {
  const style = entranceOf(el);
  const seconds = entranceSeconds(el);
  const step = staggerSeconds(el);
  const text = fallback(el);
  return {
    attr: ATTR.in,
    run: () => {
      if (step === null) show(el, style, seconds);
      else {
        [...el.children].forEach((child, at) => {
          const one = child as HTMLElement;
          one.style.animationDelay = `${step * at}s`;
          show(one, style, seconds);
        });
      }
      const counted = countOf(el);
      const scene = { frozen: playing.frozen, words: playing.words, slide: slide.id, cue, onLeave };
      if (counted !== null) countUp(el, text, counted === "first", countSeconds(counted as Count), scene);
      if (flagged(el, ATTR.words)) spokenLine(el, text, wordStyleOf(el), scene);
    },
    line: arrivalLine(el, text),
  };
}

/**
 * Draw one element with its entrance, which is the one place a style word becomes a class.
 *
 * The entrance class is taken off once it has played, so the element rests in the plain shown state
 * and a later step back is a change of class rather than an argument with a finished animation.
 */
function show(el: HTMLElement, style: string, seconds: number): void {
  const entrance = styleClass("in", style);
  el.classList.remove(CLASS.hidden);
  span(el, seconds);
  el.classList.add(CLASS.shown, entrance);
  el.addEventListener("animationend", (event) => {
    if (event.target === el) el.classList.remove(entrance);
  });
}

/**
 * A stepped container's children come forward as they arrive and the ones before them step back.
 *
 * The moments are generated at the cues the children already declare, so they land in the catalog as
 * though the author had written them and no check downstream can tell the two spellings apart.
 */
function steps(container: HTMLElement, slide: Slide, actions: Map<string, Action[]>): void {
  const cued = [...container.children].filter((child) => written(child, ATTR.in));
  cued.forEach((child, at) => {
    const cue = wireId(slide.id, written(child, ATTR.in) as string);
    push(actions, cue, {
      attr: ATTR.front,
      run: () => {
        for (const earlier of cued.slice(0, at)) {
          span(earlier as HTMLElement, ATTENTION.back.seconds);
          earlier.classList.remove(CLASS.front);
          earlier.classList.add(CLASS.back);
        }
        child.classList.remove(CLASS.back);
      },
      line: null,
    });
  });
}

/** The line an arrival writes, which is the subject and then the text the element reads as. */
function arrivalLine(el: Element, text: string): string | null {
  const describe = written(el, ATTR.describe);
  if (describe === "") return null;
  const parts = [describe === null ? null : SENTENCE[ATTR.in]?.(describe), text || null].filter(Boolean);
  return parts.length ? parts.join(" ") : null;
}

/** The line a moment other than an arrival writes, which is its own verb on the author's noun phrase. */
function subject(el: Element, attr: Attr): string | null {
  const describe = written(el, ATTR.describe);
  if (!describe) return null;
  return SENTENCE[attr]?.(describe) ?? null;
}

// ---- firing one cue ---------------------------------------------------------------------------------

/**
 * Run every moment of one cue, in document order, and answer with the sentence they wrote together.
 *
 * A step back that shares its cue with an arrival writes nothing, because it is the shadow of that
 * arrival rather than an event of its own, and a return to the front always writes a line, because
 * it is an attention event that a viewer is meant to notice.
 */
export function fire(mounted: Mounted | null, cue: string): string | null {
  const list = mounted?.actions.get(cue);
  if (!list?.length) return null;
  for (const action of list) action.run();
  const arrived = list.some((action) => action.attr === ATTR.in && action.line !== null);
  const lines: string[] = [];
  for (const action of list) {
    if (action.line === null) continue;
    if (arrived && action.attr === ATTR.back) continue;
    if (!lines.includes(action.line)) lines.push(action.line);
  }
  return lines.length ? lines.join(" ") : null;
}
