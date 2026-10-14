/*! The two effects that rewrite an element's own text: the count up and the line on the voice.
 *
 * Both are here rather than in `reveal.ts` because both take the author's text apart and put it back
 * together, and neither is a thing a frozen frame can see: a number growing towards its own value is
 * a slow change `verify` reads as an onset, and one word lighting up is far under the change floor,
 * so the line reports itself through the telemetry seam instead of being measured in pixels.
 */

import { CLASS, motionScale, styleClass } from "./canvas.ts";
import { every, now, round, schedule } from "./clock.ts";
import {
  APPEAR_WORDS_MAX,
  BACK_OPACITY,
  LIST_SEPARATOR,
  scaled,
  TIME_MARK,
  WORD_STYLES,
  type WordStyle,
} from "./contract.ts";
import { recorder } from "./telemetry.ts";
import { warn } from "./warn.ts";

/**
 * One word of the section's narration, reduced to what a match compares, the second it is said, and who says it.
 *
 * `voice` is the voice that says the word and `turn` the turn of the section it is said in, counted
 * from one, so a page can place a word by its voice without ever showing a name. Both are null on
 * a page that was handed no turns.
 */
export type Spoken = {
  readonly key: string;
  readonly at: number;
  readonly voice: string | null;
  readonly turn: number | null;
};

/** The characters a match ignores, because punctuation and case are the script's and not the voice's. */
export function key(word: string): string {
  return word.toLowerCase().replace(/[^a-z\d]/g, "");
}

/** One `?words=` or `?turns=` list as its name@seconds entries, each split at its last time mark. */
function entries(raw: string): { name: string; at: number }[] {
  return raw
    .split(LIST_SEPARATOR)
    .map((item) => {
      const mark = item.lastIndexOf(TIME_MARK);
      return { name: item.slice(0, mark), at: Number.parseFloat(item.slice(mark + 1)) };
    })
    .filter((entry) => entry.name && !Number.isNaN(entry.at));
}

/**
 * `?words=` and `?turns=` as the recorder writes them: every spoken word, the second the voice reaches it, and who says it.
 *
 * The turns are numbered by their place in their list, and a word belongs to the last turn that
 * began at or before its own second, so a turn that says no word still keeps the numbers after it.
 */
export function parseWords(raw: string, turnsRaw: string | null): Spoken[] {
  const turns = turnsRaw ? entries(turnsRaw) : [];
  return entries(raw)
    .map((entry) => {
      let turn = 0;
      while (turn < turns.length && (turns[turn] as { at: number }).at <= entry.at) turn += 1;
      const voice = turn ? (turns[turn - 1] as { name: string }).name : null;
      return { key: key(entry.name), at: entry.at, voice, turn: turn || null };
    })
    .filter((word) => word.key);
}

/** Calibration: the opening of a line, which is enough to find it again in the script without carrying the whole of it. */
const TEXT_MAX = 24;

/**
 * How near the cue a matched run has to be before the search stops looking for a later one.
 *
 * A line whose words the narration says more than once matches every run of them, and the run the
 * author meant is the one about to be spoken, so the search keeps going until it reaches one that
 * has not already gone by.
 *
 * Calibration: a second and a half is a few words of speech, which is as early as a line is ever cued.
 */
const NEAR_THE_CUE_SECONDS = 1.5;

/** What a count and a line need from whoever fires the cue, which is the state neither may read itself. */
export type Scene = {
  readonly frozen: boolean;
  readonly words: readonly Spoken[] | null;
  /** The slide and the cue a warning from here belongs to, which neither effect can find for itself. */
  readonly slide: string | null;
  readonly cue: string | null;
  /** Run `stop` when the slide leaves, so nothing keeps painting into a detached element. */
  readonly onLeave: (stop: () => void) => void;
};

// ---- the count -----------------------------------------------------------------------------------

/** Where the number the count runs up to sits in the text, which is the last one or the first one. */
const LAST_NUMBER = /(\d[\d,]*(?:\.\d+)?)(?!.*\d)/;
const FIRST_NUMBER = /(\d[\d,]*(?:\.\d+)?)/;

/**
 * The curve a count runs on, as the power the remaining share is raised to.
 *
 * Calibration: a cube is the shallowest ease that still lands the last digits slowly enough for a
 * viewer to read them, and a viewer reads a number that arrives rather than one that stops.
 */
const EASE_POWER = 3;

/** Truth: every group of three digits a thousands separator goes in front of, which is how the author wrote it. */
const THOUSANDS = /\B(?=(\d{3})+(?!\d))/g;

/** The share of the count already run after a given share of its length, which is a cubic ease out. */
function eased(part: number): number {
  return 1 - (1 - part) ** EASE_POWER;
}

/**
 * Grow a number in the text from zero to what the author wrote, over the length its word declares.
 *
 * The rest of the line is left exactly as it was written, and the number keeps its own thousands
 * separators and its own decimals, so a count is the author's text with one span of it moving.
 */
export function count(el: HTMLElement, text: string, first: boolean, seconds: number, scene: Scene): void {
  const found = text.match(first ? FIRST_NUMBER : LAST_NUMBER);
  if (!found || scene.frozen) {
    el.textContent = text;
    return;
  }
  const written = found[1] as string;
  const target = Number.parseFloat(written.replace(/,/g, ""));
  const decimals = (written.split(".")[1] ?? "").length;
  const grouped = written.includes(",");
  const at = found.index as number;
  const draw = (value: number) => {
    let body = value.toFixed(decimals);
    if (grouped) body = body.replace(THOUSANDS, ",");
    el.textContent = text.slice(0, at) + body + text.slice(at + written.length);
  };
  const started = now();
  let live = true;
  scene.onLeave(() => {
    live = false;
  });
  // The count is drawn from the section second and not the wall clock, so a recorder stepping the
  // clock one frame at a time sees the number each frame's own second holds.
  every((at) => {
    if (!live) return false;
    const part = Math.min(1, (at - started) / seconds);
    draw(target * eased(part));
    return part < 1;
  });
}

// ---- the line on the voice -------------------------------------------------------------------------

/**
 * Show a line one word at a time, each word whole by the second the voice reaches it.
 *
 * The lead is the length of one word's own fade, so a word finishes arriving exactly as it is said
 * rather than starting to arrive then. The text is matched against the section's spoken words, which
 * is the only way a page can know when a word is said, and a line that is not in the narration says
 * so rather than showing nothing.
 */
export function line(el: HTMLElement, text: string, style: WordStyle, scene: Scene): void {
  const spoken = scene.words;
  if (scene.frozen || !spoken || !spoken.length) return;
  const parts = text.split(/(\s+)/);
  const keys = parts.map(key).filter(Boolean);
  if (!keys.length) return;
  if (style === "appear" && keys.length > APPEAR_WORDS_MAX) {
    warn("PAGE_APPEAR_TOO_LONG", scene.slide, scene.cue, { value: keys.length });
  }
  const cueAt = now();
  const start = firstRun(keys, spoken, cueAt);
  if (start < 0) {
    warn("PAGE_WORDS_NOT_FOUND", scene.slide, scene.cue, { value: text.slice(0, TEXT_MAX) });
    return;
  }
  const lead = scaled(WORD_STYLES[style].seconds, motionScale());
  const due: number[] = [];
  el.textContent = "";
  let taken = 0;
  for (const part of parts) {
    if (!key(part)) {
      el.appendChild(document.createTextNode(part));
      continue;
    }
    const word = document.createElement("span");
    word.className = CLASS.word;
    word.textContent = part;
    word.style.opacity = String(resting(style));
    el.appendChild(word);
    due.push((spoken[start + taken] as Spoken).at - lead);
    taken += 1;
  }
  let live = true;
  let told = false;
  scene.onLeave(() => {
    live = false;
  });
  // Each word is a task due at its own second, so the stepped clock steps to that second whichever
  // seconds it showed before, and the word's fade is born there and not on the frame that drew it.
  const show = (word: Element) => {
    if (!live) return;
    word.classList.add(CLASS.shown, styleClass("word", style));
    if (told) return;
    told = true;
    recorder().words({
      text: text.slice(0, TEXT_MAX),
      cueAt: round(cueAt),
      spokenAt: (spoken[start] as Spoken).at,
      count: keys.length,
      firstShown: round(now()),
    });
  };
  el.querySelectorAll(`.${CLASS.word}`).forEach((word, index) => {
    const at = due[index] as number;
    if (at <= cueAt) show(word);
    else schedule(at, "reveal", scene.cue ?? text.slice(0, TEXT_MAX), () => show(word));
  });
}

/** What a word looks like before it is said, which is dim for a highlight and absent for an appear. */
function resting(style: WordStyle): number {
  return style === "highlight" ? BACK_OPACITY : 0;
}

/**
 * Where in the narration a line's words are said, preferring the run the cue is about to reach.
 *
 * A line whose words are said more than once matches every run, so the search takes the first run
 * that has not already gone by and falls back to the last match when every run is behind the cue.
 */
function firstRun(keys: readonly string[], spoken: readonly Spoken[], cueAt: number): number {
  let found = -1;
  for (let at = 0; at + keys.length <= spoken.length; at += 1) {
    if (keys.some((one, offset) => (spoken[at + offset] as Spoken).key !== one)) continue;
    found = at;
    if ((spoken[at] as Spoken).at >= cueAt - NEAR_THE_CUE_SECONDS) break;
  }
  return found;
}
