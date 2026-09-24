/*! Scenes, slides, the markup reader and the cue order every check is written against.
 *
 * A deck is scenes, a scene is slides, and a slide declares its own cues. Nothing else in the
 * runtime reads markup, so every rule about what an author may write is in one place: the attribute
 * names are derived from the registry rather than spelled here, an unknown `data-` word and a value
 * outside its published set are reported before a single pixel is drawn, and a moment is qualified
 * with the id of the template it was written in so the author writes `expand` and the wire carries
 * `4.1:expand`.
 *
 * Ownership is declared and never inferred. A slide owns exactly the cues its moment attributes
 * name plus the local names it lists, which is what lets a cue id carry any characters an author
 * likes and what replaced the longest-prefix rule that used to guess.
 */

import {
  ATTENTION,
  ATTRS,
  type Attr,
  COUNTS,
  type Count,
  ENTRANCES,
  type Entrance,
  EXITS,
  type Exit,
  known,
  MOMENT_SELECTOR,
  MOMENTS,
  pairs,
  refuse,
  SLIDE_ENTRANCES,
  type SlideEntrance,
  WORD_STYLES,
  type WordStyle,
  wireId,
} from "./contract.ts";
import { warn } from "./warn.ts";

/**
 * Every attribute name the contract defines, and the prefix all of them share.
 *
 * A runtime module may not spell an attribute, because the registry is the one home of the
 * vocabulary, so the prefix is read off the registry rather than written here and each name below is
 * found by the word that follows it. A node test holds every one of them against the registry's own
 * `known`, so a word that names no row cannot survive a run of the suite.
 */
const NAMES = Object.keys(ATTRS) as Attr[];
const PREFIX = (NAMES[0] as string).slice(0, (NAMES[0] as string).indexOf("-") + 1);

function named(word: string): Attr {
  return NAMES.find((name) => name.slice(PREFIX.length) === word) ?? (word as Attr);
}

/** Every attribute this runtime reads, named once so no module below spells one for itself. */
export const ATTR = {
  scene: named("scene"),
  name: named("name"),
  slide: named("slide"),
  hold: named("hold"),
  owns: named("owns"),
  enter: named("enter"),
  in: named("in"),
  back: named("back"),
  front: named("front"),
  out: named("out"),
  inStyle: named("in-style"),
  inSeconds: named("in-seconds"),
  outStyle: named("out-style"),
  steps: named("steps"),
  stagger: named("stagger"),
  swaps: named("swaps"),
  words: named("words"),
  count: named("count"),
  class: named("class"),
  describe: named("describe"),
  describeClass: named("describe-class"),
  describeOut: named("describe-out"),
  tex: named("tex"),
  texDisplay: named("tex-display"),
} as const satisfies Record<string, Attr>;

/** What a slide handler receives beside the mounted element, which says where in the deck it ran. */
export type Context = {
  readonly id: string;
  readonly at: number;
  readonly frozen: boolean;
  readonly slideId: string;
};

/** A handler the page registered for one cue, on a slide or on the deck. */
export type Handler = (slide: HTMLElement | null, ctx: Context) => void;

/** A slide that builds its own markup in script rather than declaring a template. */
export type Render = (ctx: { scene: Scene; slide: Slide; frozen: boolean }) => string;

/** One slide, as the markup declared it or as a script registered it. */
export type Slide = {
  readonly id: string;
  hold: number;
  enter: SlideEntrance;
  owns: string[];
  markup: HTMLTemplateElement | null;
  render: Render | null;
  entered: Handler | null;
  on: Record<string, Handler>;
};

/** One scene, which is what a section of the project names and what a preview plays. */
export type Scene = {
  readonly id: string;
  name: string;
  slides: Slide[];
};

/** One scene as the catalog publishes it, which is what every static check reads the deck from. */
export type CatalogEntry = {
  scene: string;
  name: string;
  slides: string[];
  cues: Record<string, string[]>;
  spans: Record<string, number>;
  elements?: Record<string, unknown[]>;
};

/** How a scene with no name of its own is called, which is the word the index page prints. */
const SCENE_WORD = "Scene";

/** Every scene of the deck, keyed by the id a section names, in the order they were declared. */
const scenes = new Map<string, Scene>();

/** Every handler the deck registered outside a slide, keyed by the wire id of its cue. */
const handlers = new Map<string, Handler[]>();

/** The scale every declared span is multiplied by, which a reduced-motion render lowers. */
let motionScale = 1;

/** Tell the reader how far a reduced render slows the page down, before it reads any markup. */
export function setMotionScale(scale: number): void {
  motionScale = scale;
}

/** Every scene, which the facade publishes and the index page lists. */
export function all(): Map<string, Scene> {
  return scenes;
}

/** Every handler registered for one cue, which a mode runs after the cue's own reveals. */
export function handlersFor(cue: string): readonly Handler[] {
  return handlers.get(cue) ?? [];
}

/** Whether the deck registered a handler for a cue, which decides whether a cue that drew nothing is wrong. */
export function handled(cue: string): boolean {
  return handlers.has(cue);
}

/** Register a handler for one wire id, which is what `DeckTalk.on` does. */
export function on(cue: string, fn: Handler): void {
  const list = handlers.get(cue) ?? [];
  list.push(fn);
  handlers.set(cue, list);
}

// ---- reading one attribute ---------------------------------------------------------------------

/** What an element wrote for an attribute, trimmed, or null when it wrote nothing. */
export function written(el: Element, name: Attr): string | null {
  const value = el.getAttribute(name);
  return value === null ? null : value.trim();
}

/** Whether an element carries a flag, which is an attribute whose presence is its whole value. */
export function flagged(el: Element, name: Attr): boolean {
  return el.hasAttribute(name);
}

/**
 * The closed word an element chose, or the default the registry publishes for that attribute.
 *
 * A value outside the set has already been reported by the markup reader, so the fallback here is
 * what keeps a misspelled style from leaving an element with no rendering at all.
 */
function word<T extends string>(el: Element, name: Attr, set: Readonly<Record<string, unknown>>): T {
  const value = written(el, name);
  if (value !== null && Object.hasOwn(set, value)) return value as T;
  return ATTRS[name].default as T;
}

/** The number an element wrote for a ranged attribute, or null when it wrote nothing usable. */
function seconds(el: Element, name: Attr): number | null {
  const value = written(el, name);
  if (value === null || refuse(name, value) !== null) return null;
  return Number(value);
}

// ---- the timings one element declares -----------------------------------------------------------

/** How the element arrives, which is the closed word `data-in-style` names. */
export function entranceOf(el: Element): Entrance {
  return word<Entrance>(el, ATTR.inStyle, ENTRANCES);
}

/** How the element leaves, which is the closed word `data-out-style` names. */
export function exitOf(el: Element): Exit {
  return word<Exit>(el, ATTR.outStyle, EXITS);
}

/** How the line is shown on the voice, which is the closed word `data-words` names. */
export function wordStyleOf(el: Element): WordStyle {
  return word<WordStyle>(el, ATTR.words, WORD_STYLES);
}

/** Which number in the text counts up, or null when the element declares no count. */
export function countOf(el: Element): Count | null {
  const value = written(el, ATTR.count);
  return value !== null && Object.hasOwn(COUNTS, value) ? (value as Count) : null;
}

/** How this slide replaces the one before it, which is the closed word `data-enter` names. */
function slideEntranceOf(el: Element): SlideEntrance {
  return word<SlideEntrance>(el, ATTR.enter, SLIDE_ENTRANCES);
}

/** How long the entrance plays, which is the author's own length or the style's own. */
export function entranceSeconds(el: Element): number {
  return seconds(el, ATTR.inSeconds) ?? ENTRANCES[entranceOf(el)].seconds;
}

/** How far apart a container's children arrive, or null when the container staggers nothing. */
export function staggerSeconds(el: Element): number | null {
  return seconds(el, ATTR.stagger);
}

/** How long the preview rests on a slide, which is the author's own length or the published default. */
function holdSeconds(el: Element): number {
  return seconds(el, ATTR.hold) ?? Number(ATTRS[ATTR.hold].default);
}

// ---- moments -------------------------------------------------------------------------------------

/** One moment an element declares, as the author wrote it and as the wire carries it. */
export type Moment = { readonly attr: Attr; readonly local: string; readonly cue: string };

/**
 * Every moment one element declares, in the order the registry lists the moment attributes.
 *
 * The four moment rows are walked from the registry rather than named here, so an attribute that
 * joins the family later reaches the cue order, the catalog and the transcript without a second edit.
 */
export function momentsOf(el: Element, slideId: string): Moment[] {
  const out: Moment[] = [];
  for (const attr of MOMENTS) {
    const local = written(el, attr);
    if (local) out.push({ attr, local, cue: wireId(slideId, local) });
  }
  return out;
}

/** Every class an element changes, as a moment and the class name it adds at that moment. */
export function classMomentsOf(el: Element, slideId: string): { cue: string; local: string; name: string }[] {
  const value = written(el, ATTR.class);
  if (!value) return [];
  return pairs(value).map((pair) => ({ cue: wireId(slideId, pair.moment), local: pair.moment, name: pair.value }));
}

/** The phrase the author wrote for one class change, or null when they wrote none for it. */
export function classPhraseOf(el: Element, local: string): string | null {
  const value = written(el, ATTR.describeClass);
  if (value === null) return null;
  const found = pairs(value).find((pair) => pair.moment === local);
  return found ? found.value : null;
}

// ---- the declared cue order ------------------------------------------------------------------------

/** Every element of a slide that declares a moment, in document order, from markup or from a mount. */
export function momentElements(root: ParentNode): Element[] {
  return [...root.querySelectorAll(MOMENT_SELECTOR), ...root.querySelectorAll(`[${ATTR.class}]`)].filter(
    (el, at, list) => list.indexOf(el) === at,
  );
}

/**
 * The cues a slide declares, in the order its markup names them, then the ones only a handler serves.
 *
 * A slide built by a render function is never rendered here, because a render may touch the live
 * page, so its declared order is the list it owns and the cues it registered handlers for.
 */
export function cueOrder(slide: Slide): string[] {
  const order: string[] = [];
  const add = (cue: string) => {
    if (!order.includes(cue)) order.push(cue);
  };
  if (slide.markup) {
    for (const el of momentElements(slide.markup.content)) {
      for (const moment of momentsOf(el, slide.id)) add(moment.cue);
      for (const change of classMomentsOf(el, slide.id)) add(change.cue);
    }
  }
  for (const local of Object.keys(slide.on)) add(wireId(slide.id, local));
  for (const local of slide.owns) add(wireId(slide.id, local));
  return order;
}

/** The slide that declares a cue, across every scene or within one, or null when none does. */
export function ownerOf(cue: string, within: Scene | null = null): { scene: Scene; slide: Slide } | null {
  for (const scene of within ? [within] : scenes.values()) {
    for (const slide of scene.slides) {
      if (cueOrder(slide).includes(cue)) return { scene, slide };
    }
  }
  return null;
}

/** The scene and slide one slide id names, or null when the deck declares no such slide. */
export function findSlide(id: string): { scene: Scene; slide: Slide } | null {
  for (const scene of scenes.values()) {
    const slide = scene.slides.find((one) => one.id === id);
    if (slide) return { scene, slide };
  }
  return null;
}

// ---- the declared spans -------------------------------------------------------------------------

/**
 * The seconds of motion each of a slide's cues puts between itself and the next one.
 *
 * Every span but one is arithmetic over the registry, so the catalog carries it before a browser has
 * drawn anything. The exception is a class change, whose length belongs to the page's own stylesheet
 * and is read off a laid-out element by `measureClassSpans`.
 */
export function spansOf(slide: Slide): Record<string, number> {
  const spans: Record<string, number> = {};
  const widen = (cue: string, span: number) => {
    spans[cue] = Math.max(spans[cue] ?? 0, declared(span));
  };
  if (!slide.markup) return spans;
  for (const el of momentElements(slide.markup.content)) {
    for (const moment of momentsOf(el, slide.id)) widen(moment.cue, spanOf(el, moment.attr));
    for (const change of classMomentsOf(el, slide.id)) widen(change.cue, 0);
  }
  return spans;
}

/**
 * The span a moment declares under the render in hand, which is what the catalog publishes.
 *
 * The scale multiplies it and nothing clamps it, because the clamp belongs to the length the page
 * plays and a published span that had been clamped would hide the very overrun a finding names.
 */
function declared(span: number): number {
  return Number((span * motionScale).toFixed(3));
}

/** The span one moment of one element declares, which is the length of the motion that moment starts. */
function spanOf(el: Element, attr: Attr): number {
  if (attr === ATTR.back) return ATTENTION.back.seconds;
  if (attr === ATTR.front) return ATTENTION.front.seconds;
  if (attr === ATTR.out) return EXITS[exitOf(el)].seconds;
  return arrivalSpan(el);
}

/**
 * The span an arrival declares, which is the longest of the entrance, the stagger, the count and the line.
 *
 * A staggered container's own span is exact arithmetic, the last child starting one step per earlier
 * child after the cue and then playing its own entrance, which is why its overrun is a certain finding.
 */
function arrivalSpan(el: Element): number {
  const entrance = entranceSeconds(el);
  const step = staggerSeconds(el);
  const children = step === null ? 0 : el.children.length;
  const spread = step === null ? entrance : step * Math.max(0, children - 1) + entrance;
  const count = countOf(el) === null ? 0 : COUNTS[countOf(el) as Count].seconds;
  const line = flagged(el, ATTR.words) ? WORD_STYLES[wordStyleOf(el)].seconds : 0;
  return Math.max(spread, count, line);
}

/**
 * Read the length of every class change off a laid-out slide, and say so when a reduced render still moves.
 *
 * A class hands the page's own stylesheet an unbounded animation at a cue, which is the one thing
 * this contract cannot bound by a published range, so the span is measured rather than declared and
 * a reduced render that still animates is reported against the stylesheet that owes the reduction.
 */
export function measureClassSpans(
  slideEl: Element,
  slide: Slide,
  into: Record<string, number>,
  reduced: boolean,
): void {
  for (const el of momentElements(slideEl)) {
    for (const change of classMomentsOf(el, slide.id)) {
      el.classList.add(change.name);
      const span = longestMotion(el);
      el.classList.remove(change.name);
      into[change.cue] = Math.max(into[change.cue] ?? 0, declared(span));
      if (reduced && span > 0) {
        warn("PAGE_CLASS_NOT_REDUCED", slide.id, change.cue, { value: change.name, attr: ATTR.class });
      }
    }
  }
}

/** The longest animation or transition an element's own stylesheet gives it, in seconds. */
function longestMotion(el: Element): number {
  const style = getComputedStyle(el);
  const lengths = [style.animationDuration, style.animationDelay, style.transitionDuration, style.transitionDelay]
    .join(",")
    .split(",")
    .map((part) => Number.parseFloat(part) * (part.trim().endsWith("ms") ? 0.001 : 1))
    .filter((value) => Number.isFinite(value));
  const animation = lengths.length ? Math.max(...lengths) : 0;
  return animation > 0 ? animation : 0;
}

// ---- the catalog ----------------------------------------------------------------------------------

/** Every scene as the catalog publishes it, which is what a static check reads instead of the markup. */
export function catalog(): CatalogEntry[] {
  return [...scenes.values()].map((scene) => ({
    scene: scene.id,
    name: scene.name,
    slides: scene.slides.map((slide) => slide.id),
    cues: Object.fromEntries(scene.slides.map((slide) => [slide.id, cueOrder(slide)])),
    spans: Object.assign({}, ...scene.slides.map(spansOf)) as Record<string, number>,
  }));
}

// ---- declaring a scene in script --------------------------------------------------------------------

/** One slide of a scene a script declares, in the shape a page writes it. */
export type SlideInput = {
  id?: string | number;
  hold?: number;
  enter?: SlideEntrance;
  owns?: readonly (string | number)[];
  render?: Render;
  on?: Record<string, Handler>;
  entered?: Handler;
};

/** One scene a script declares, in the shape a page writes it. */
export type SceneInput = { name?: string; slides?: readonly SlideInput[] };

function slideFrom(sceneId: string, input: SlideInput, at: number): Slide {
  return {
    id: String(input.id ?? `${sceneId}.${at + 1}`),
    hold: Number(input.hold ?? ATTRS[ATTR.hold].default),
    enter: input.enter ?? (ATTRS[ATTR.enter].default as SlideEntrance),
    owns: (input.owns ?? []).map(String),
    markup: null,
    render: input.render ?? null,
    entered: input.entered ?? null,
    on: input.on ?? {},
  };
}

/** Declare one scene from script, which is the whole of the page-facing `DeckTalk.scene`. */
export function declare(id: string | number, input: SceneInput): void {
  const sceneId = String(id);
  scenes.set(sceneId, {
    id: sceneId,
    name: input.name || `${SCENE_WORD} ${sceneId}`,
    slides: (input.slides ?? []).map((slide, at) => slideFrom(sceneId, slide, at)),
  });
}

// ---- reading the markup ------------------------------------------------------------------------------

/**
 * Every scene the document declares, with every rule about what an author may write checked once.
 *
 * A scene a script already registered keeps its definition and each of its slides that defines no
 * render takes the markup of the template with its id, so a deck may be half markup and half script
 * without either half knowing about the other.
 */
export function readMarkup(): void {
  // Every slide id the document has claimed so far, because two templates claiming one id give every
  // moment written inside either of them two owners and no wire id can tell those apart.
  const claimed = new Set<string>();
  for (const wrap of document.querySelectorAll(`[${ATTR.scene}]`)) {
    const sceneId = written(wrap, ATTR.scene) ?? "";
    const templates = [...wrap.querySelectorAll("template")].filter((tpl) => {
      if (written(tpl, ATTR.slide)) return true;
      warn("PAGE_SLIDE_NO_ID", sceneId);
      return false;
    });
    if (!templates.length) {
      warn("PAGE_SCENE_EMPTY", sceneId);
      continue;
    }
    check(wrap, sceneId, false);
    const existing = scenes.get(sceneId);
    if (!existing) {
      declare(sceneId, {
        name: written(wrap, ATTR.name) || undefined,
        slides: templates.map((tpl) => ({ id: written(tpl, ATTR.slide) as string })),
      });
    }
    const scene = scenes.get(sceneId) as Scene;
    for (const tpl of templates) {
      const slideId = written(tpl, ATTR.slide) as string;
      if (claimed.has(slideId)) {
        warn("PAGE_SLIDE_DOUBLED", slideId);
        continue;
      }
      claimed.add(slideId);
      let slide = scene.slides.find((one) => one.id === slideId);
      if (!slide) {
        slide = slideFrom(sceneId, { id: slideId }, scene.slides.length);
        scene.slides.push(slide);
      }
      // A slide that builds itself in script keeps its render, and the template beside it is the
      // markup of nothing, so only a slide with no render of its own takes one.
      if (!slide.render) slide.markup = tpl;
      slide.hold = holdSeconds(tpl);
      slide.enter = slideEntranceOf(tpl);
      slide.owns = (written(tpl, ATTR.owns) ?? "").split(/\s+/).filter(Boolean);
      check(tpl.content, slideId, true);
      for (const nested of tpl.content.querySelectorAll("template")) {
        if (!written(nested, ATTR.slide)) warn("PAGE_TEMPLATE_IGNORED", slideId);
      }
    }
    for (const slide of scene.slides) order(slide);
  }
}

/**
 * Every rule about what an author may write, checked over one subtree before anything is drawn.
 *
 * `place` is the template the subtree belongs to, or the scene wrapper when the subtree is the
 * wrapper's own light DOM, where a moment has nothing to qualify it and is therefore a cue no slide
 * can own.
 */
function check(root: ParentNode, place: string, inSlide: boolean): void {
  // A scene wrapper carries attributes of its own, so the root is checked beside its descendants
  // whenever the root is an element rather than a template's content.
  const elements = root instanceof Element ? [root, ...root.querySelectorAll("*")] : [...root.querySelectorAll("*")];
  for (const el of elements) {
    for (const attribute of el.attributes) {
      if (!attribute.name.startsWith(PREFIX)) continue;
      if (!known(attribute.name)) {
        warn("PAGE_UNKNOWN_ATTR", place, null, { attr: attribute.name });
        continue;
      }
      const row = ATTRS[attribute.name];
      if (!row.values.length && !row.range) continue;
      // An attribute written bare is the author asking for its own default, which every row that
      // publishes one admits, so an empty value is refused only where there is no default to take.
      const value = attribute.value.trim();
      if (!value && row.default !== null) continue;
      const why = refuse(attribute.name, value);
      if (why) {
        warn("PAGE_BAD_VALUE", place, null, { attr: attribute.name, value: attribute.value, allowed: why });
      }
    }
    if (!inSlide) {
      for (const attr of MOMENTS) {
        const local = written(el, attr);
        if (local) warn("PAGE_MOMENT_UNKNOWN", place, null, { attr, value: local });
      }
      continue;
    }
    describedClasses(el, place);
    staggered(el, place);
    swapped(el, root, place);
  }
}

/** A class change with no phrase for it loses its line in the transcript, which is the whole of the rule. */
function describedClasses(el: Element, slideId: string): void {
  for (const change of classMomentsOf(el, slideId)) {
    if (classPhraseOf(el, change.local) === null) {
      warn("PAGE_CLASS_UNDESCRIBED", slideId, change.cue, { value: change.name, attr: ATTR.class });
    }
  }
}

/** A container that staggers no children spreads one entrance over nothing, which is always a mistake. */
function staggered(el: Element, slideId: string): void {
  if (staggerSeconds(el) === null || el.children.length) return;
  const moment = momentsOf(el, slideId).find((one) => one.attr === ATTR.in);
  warn("PAGE_STAGGER_EMPTY", slideId, moment ? moment.cue : null, { attr: ATTR.stagger });
}

/**
 * A swap is between one thing and one other thing, and zero or two of them is a guess.
 *
 * The markup reader sees this before a slide is ever mounted, which is what lets `check` name it
 * without opening a recording, and the mount reads the same markup and finds the same one partner.
 */
function swapped(el: Element, root: ParentNode, slideId: string): void {
  if (!flagged(el, ATTR.swaps)) return;
  const local = written(el, ATTR.in);
  if (!local) return;
  const leaving = [...root.querySelectorAll(`[${ATTR.out}]`)].filter((other) => written(other, ATTR.out) === local);
  if (leaving.length === 1) return;
  warn("PAGE_SWAP_AMBIGUOUS", slideId, wireId(slideId, local), { value: leaving.length, attr: ATTR.swaps });
}

/** An exit at or before its own entrance never plays, which the declared order makes exact. */
function order(slide: Slide): void {
  if (!slide.markup) return;
  const declared = cueOrder(slide);
  for (const el of momentElements(slide.markup.content)) {
    const moments = momentsOf(el, slide.id);
    const arrival = moments.find((one) => one.attr === ATTR.in);
    const departure = moments.find((one) => one.attr === ATTR.out);
    if (!arrival || !departure) continue;
    if (declared.indexOf(departure.cue) <= declared.indexOf(arrival.cue)) {
      warn("PAGE_MOMENT_ORDER", slide.id, departure.cue, { attr: ATTR.out, value: departure.local });
    }
  }
}
