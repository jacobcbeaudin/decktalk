/*! The stage a deck is drawn on, and the one stylesheet that renders every closed word.
 *
 * A DeckTalk page draws into one stage of a fixed size, named below and scaled to whatever window
 * it is opened in, so an element measured on a laptop is at the pixel a recording will put it at.
 * This module owns that stage, the fit, the heads-up display, and the stylesheet.
 *
 * Every rule below is generated from the registry, so a style word's length lives once. Each
 * selector sits inside `:where()`, which gives it no specificity at all, and the sheet is prepended
 * to the head, so a page rule of equal weight wins on document order. An author's stylesheet is
 * therefore always able to override the runtime without reaching for `!important`.
 */

import {
  ATTENTION,
  ATTRS,
  BACK_OPACITY,
  COUNTS,
  ENTRANCES,
  type Entrance,
  EXITS,
  type Exit,
  MEASURABLE_SPAN_SECONDS,
  SLIDE_ENTRANCES,
  type SlideEntrance,
  scaled,
  WORD_STYLES,
  type WordStyle,
} from "./contract.ts";

/** The stage every deck is laid out on, which is the frame a recording captures. */
const STAGE_WIDTH = 1920;
const STAGE_HEIGHT = 1080;

/** The one easing every entrance shares, which draws a quarter of the element inside the first frame. */
const EASE = "cubic-bezier(.2,.7,.2,1)";

/** How small `pop` starts, which reads as a pop without ever being too small to recognise. */
const POP_ENTRY_SCALE = 0.7;

/** The share of `pop` spent growing to its overshoot, the rest being the settle back to its own size. */
const POP_OVERSHOOT_AT = 60;

/** How far `fall` travels as it leaves, which is the short distance `rise` already arrives across. */
const FALL_PIXELS = ENTRANCES.rise.liftPixels;

/** The custom property a reduced render writes the page's motion scale into, on the root element. */
const SCALE_PROPERTY = "--dt-motion-scale";

/** The custom property each element's own motion length is set through, which the sheet reads. */
const SPAN_PROPERTY = "--dt-span";

/** Every class the runtime adds to an element it is drawing, named once so no module spells one twice. */
export const CLASS = {
  slide: "dt-slide",
  leaving: "dt-leaving",
  arriving: "dt-arriving",
  hidden: "dt-hidden",
  shown: "dt-shown",
  back: "dt-back",
  front: "dt-front",
  word: "dt-word",
  frozen: "dt-frozen",
  /** The class a reduced render puts on the root, which the page's own stylesheet must honour. */
  reduced: "dt-reduced",
} as const;

/** The name of the keyframes one closed word animates through, which the sheet and the runtime share. */
function frames(family: string, word: string): string {
  return `dt-${family}-${word}`;
}

/** The class one closed word of a family is drawn under, built the same way its keyframes are. */
export function styleClass(family: string, word: string): string {
  return frames(family, word);
}

/** What each entrance animates through, from the travel its own registry row declares. */
const ENTRANCE_BODY: Record<Entrance, string> = {
  rise: `from{opacity:0;transform:translateY(${ENTRANCES.rise.liftPixels}px)}to{opacity:1;transform:none}`,
  settle: `from{opacity:0;transform:translateY(${ENTRANCES.settle.liftPixels}px)}to{opacity:1;transform:none}`,
  fade: "from{opacity:0}to{opacity:1}",
  pop:
    `0%{opacity:0;transform:scale(${POP_ENTRY_SCALE})}` +
    `${POP_OVERSHOOT_AT}%{opacity:1;transform:scale(${1 + ENTRANCES.pop.overshootPercent / 100})}` +
    "100%{opacity:1;transform:none}",
  draw: "from{opacity:1;stroke-dashoffset:1}to{opacity:1;stroke-dashoffset:0}",
  cut: "from{opacity:1}to{opacity:1}",
};

/** What each exit animates through, both of them over the one length an exit is allowed. */
const EXIT_BODY: Record<Exit, string> = {
  fade: "from{opacity:1}to{opacity:0}",
  fall: `from{opacity:1;transform:none}to{opacity:0;transform:translateY(${FALL_PIXELS}px)}`,
};

/** What each word style animates through, which is the whole difference between the two of them. */
const WORD_BODY: Record<WordStyle, string> = {
  highlight: `from{opacity:${BACK_OPACITY}}to{opacity:1}`,
  appear: "from{opacity:0}to{opacity:1}",
};

/** What each slide entrance animates through, the incoming slide alone, over the outgoing one. */
const SLIDE_BODY: Record<SlideEntrance, string> = {
  crossfade: "from{opacity:0}to{opacity:1}",
  cut: "from{opacity:1}to{opacity:1}",
};

/** One keyframes block and the class that plays it, for one closed word of one family. */
function rule(family: string, word: string, seconds: number, body: string, extra = ""): string {
  const name = frames(family, word);
  return (
    `@keyframes ${name}{${body}}\n` +
    `:where(.${name}){animation-name:${name};` +
    `animation-duration:var(${SPAN_PROPERTY},${seconds}s);` +
    `animation-timing-function:${EASE};animation-fill-mode:both;${extra}}\n`
  );
}

/**
 * The rules for the two things DeckTalk draws for an author rather than for a film.
 *
 * The heads-up display sits over the stage and the index page lists a deck's scenes, and a
 * recording holds neither of them. Every length here is therefore typography that no check reads
 * and no finding names, which is why the block is one binding rather than a number a reader has to
 * weigh one at a time.
 */
const CHROME_CSS = [
  ":where(#dt-hud){position:fixed;left:12px;top:12px;z-index:2147483000;font:14px/1.4 ui-monospace,Menlo,monospace;" +
    "color:#fff;background:rgba(0,0,0,.6);padding:6px 10px;border-radius:6px;pointer-events:none;white-space:pre}\n",
  ":where(#dt-index){font:16px/1.5 system-ui,sans-serif;max-width:900px;margin:40px auto;padding:0 24px;color:inherit}\n",
  ":where(#dt-index h1){font-size:28px}:where(#dt-index h2){font-size:20px;margin-top:28px}\n",
  ":where(#dt-index a){color:inherit;font-weight:600;text-decoration:underline;text-underline-offset:3px;margin-right:16px}\n",
  ":where(#dt-index code){color:inherit;opacity:.7}\n",
  ":where(#dt-index .dt-slides){display:flex;flex-wrap:wrap;gap:8px 4px}\n",
];

/**
 * The whole stylesheet, generated from the registry so no length is written twice.
 *
 * The attribute selectors below are derived from the registry as well: every row whose value is an
 * id declares markup the runtime mounts rather than markup the page draws, so the two of them are
 * what the sheet hides before the first paint.
 */
function sheet(): string {
  const declared = Object.values(ATTRS)
    .filter((row) => row.kind === "id")
    .map((row) => `[${row.name}]`)
    .join(",");
  const parts = [
    `:where(${declared}){display:none}\n`,
    `:where(#dt-stage){position:absolute;left:0;top:0;width:${STAGE_WIDTH}px;height:${STAGE_HEIGHT}px;` +
      "overflow:hidden;transform-origin:0 0}\n",
    ":where(#dt-camera,#dt-pan){position:absolute;inset:0}\n",
    `:where(.${CLASS.slide}){position:absolute;inset:0}\n`,
    // The outgoing slide keeps full opacity underneath the incoming one, so the composite of the two
    // is opaque at every moment of a crossfade and never dips towards the page's own background.
    `:where(.${CLASS.leaving}){opacity:1;z-index:0;pointer-events:none}\n`,
    `:where(.${CLASS.arriving}){z-index:1}\n`,
    `:where(.${CLASS.hidden}){opacity:0}\n`,
    `:where(.${CLASS.shown}){opacity:1}\n`,
  ];
  for (const [word, effect] of Object.entries(ENTRANCES)) {
    const extra = word === "draw" ? "stroke-dasharray:1;animation-timing-function:linear;" : "";
    parts.push(rule("in", word, effect.seconds, ENTRANCE_BODY[word as Entrance], extra));
  }
  for (const [word, effect] of Object.entries(EXITS))
    parts.push(rule("out", word, effect.seconds, EXIT_BODY[word as Exit]));
  for (const [word, effect] of Object.entries(WORD_STYLES)) {
    parts.push(rule("word", word, effect.seconds, WORD_BODY[word as WordStyle]));
  }
  for (const [word, effect] of Object.entries(SLIDE_ENTRANCES)) {
    parts.push(rule("enter", word, effect.seconds, SLIDE_BODY[word as SlideEntrance]));
  }
  // Attention comes after every entrance, because each selector carries no specificity at all and a
  // tie between two of them is therefore settled by the order they are written in.
  parts.push(
    `:where(.${CLASS.back}){opacity:${BACK_OPACITY};transition:opacity var(${SPAN_PROPERTY},${ATTENTION.back.seconds}s) ${EASE}}\n`,
    `:where(.${CLASS.front}){opacity:1;transition:opacity var(${SPAN_PROPERTY},${ATTENTION.front.seconds}s) ${EASE}}\n`,
  );
  // A reduced render keeps every length and drops every travel, so the film is the same duration
  // with the same captions and nothing on it slides, scales or overshoots.
  const fade = frames("in", "fade");
  parts.push(
    `:where(.${CLASS.reduced}) :where(${Object.keys(ENTRANCES)
      .map((word) => `.${frames("in", word)}`)
      .join(",")}){animation-name:${fade}}\n`,
  );
  // A frozen page is a still, so every length collapses and the element is drawn where it lands.
  parts.push(
    `:where(.${CLASS.frozen}) *{animation-duration:0s!important;animation-delay:0s!important;transition-duration:0s!important}\n`,
  );
  parts.push(...CHROME_CSS);
  return parts.join("");
}

/** The elements the stage is made of, created once and kept for as long as the page is open. */
let stageEl: HTMLElement | null = null;
let cameraEl: HTMLElement | null = null;
let panEl: HTMLElement | null = null;
let hudEl: HTMLElement | null = null;
let fitScale = 1;

/** Put the stylesheet in the document, before anything the page itself declares. */
export function style(): void {
  if (document.getElementById("dt-style")) return;
  const el = document.createElement("style");
  el.id = "dt-style";
  el.textContent = sheet();
  const head = document.head || document.documentElement;
  head.insertBefore(el, head.firstChild);
}

/**
 * Whether this render is the reduced one, which is what the operating system and the recorder agree on.
 *
 * The recorder asks Chromium for a page that prefers reduced motion when `motion.reduce` is set, and
 * a person who has asked their own machine for it gets the same render in a preview, which is the
 * whole point of making it a render rather than an attribute.
 */
export function reduced(): boolean {
  return window.matchMedia?.("(prefers-reduced-motion: reduce)").matches === true;
}

/**
 * How far this render slows every length down, read from the root element's own custom property.
 *
 * `motion.scale` reaches the page as one declaration on `:root`, which the recorder adds and an
 * author may write for themselves, and it is clamped here so a slowed page cannot slow itself past
 * the point where its own cues stop being measurable.
 */
export function motionScale(): number {
  const written = getComputedStyle(document.documentElement).getPropertyValue(SCALE_PROPERTY).trim();
  const scale = Number.parseFloat(written);
  return Number.isFinite(scale) && scale > 0 ? scale : 1;
}

/** One element's own motion length, scaled and clamped, written where the stylesheet reads it. */
export function span(el: HTMLElement, seconds: number): void {
  el.style.setProperty(SPAN_PROPERTY, `${Math.min(scaled(seconds, motionScale()), MEASURABLE_SPAN_SECONDS)}s`);
}

/** Build the stage, fit it to the window, and keep it fitted for as long as the page is open. */
export function build(hud: boolean): void {
  if (stageEl) return;
  stageEl = document.getElementById("dt-stage") ?? document.createElement("div");
  stageEl.id = "dt-stage";
  if (!stageEl.isConnected) document.body.appendChild(stageEl);
  cameraEl = document.createElement("div");
  cameraEl.id = "dt-camera";
  panEl = document.createElement("div");
  panEl.id = "dt-pan";
  cameraEl.appendChild(panEl);
  stageEl.appendChild(cameraEl);
  if (hud) {
    hudEl = document.createElement("div");
    hudEl.id = "dt-hud";
    document.body.appendChild(hudEl);
  }
  if (reduced()) document.documentElement.classList.add(CLASS.reduced);
  fit();
  window.addEventListener("resize", fit);
}

/** Scale the stage into the window and centre it, which is what turns a client rect into stage pixels. */
function fit(): void {
  if (!stageEl) return;
  fitScale = Math.min(window.innerWidth / STAGE_WIDTH, window.innerHeight / STAGE_HEIGHT);
  const x = (window.innerWidth - STAGE_WIDTH * fitScale) / 2;
  const y = (window.innerHeight - STAGE_HEIGHT * fitScale) / 2;
  stageEl.style.transform = `translate(${x}px, ${y}px) scale(${fitScale})`;
}

/** The layer slides are mounted into, which is where every reveal is drawn. */
export function pan(): HTMLElement {
  return panEl as HTMLElement;
}

/** The stage itself, which is the frame every measured box is taken against. */
export function frame(): HTMLElement {
  return stageEl as HTMLElement;
}

/** The stage's current fit, which turns a client rect into the stage pixels a check reasons in. */
export function scale(): number {
  return fitScale;
}

/** Stop every length on the page, which is what a still is. */
export function freeze(): void {
  document.documentElement.classList.add(CLASS.frozen);
}

/** Hide the stage, which the index page does because it lists slides rather than drawing one. */
export function hide(): void {
  if (stageEl) stageEl.style.display = "none";
  document.body.style.overflow = "auto";
}

/** Draw one line of the heads-up display, which is for an author and never for a recording. */
export function say(line: string): void {
  if (hudEl) hudEl.textContent = line;
}

/** How long the crossfade into a slide takes, which is what the outgoing slide waits before it goes. */
export function slideSeconds(word: SlideEntrance): number {
  return Math.min(scaled(SLIDE_ENTRANCES[word].seconds, motionScale()), MEASURABLE_SPAN_SECONDS);
}

/** How long a count runs, which the registry owns and a reduced render scales like any other length. */
export function countSeconds(word: keyof typeof COUNTS): number {
  return Math.min(scaled(COUNTS[word].seconds, motionScale()), MEASURABLE_SPAN_SECONDS);
}
