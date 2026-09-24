/*! The four things a DeckTalk page can be doing, and the one URL that decides which.
 *
 * A page with no query lists its scenes. `?scene=` plays one at the speed a person reads it.
 * `?cues=` plays one against the narration clock, which is the mode a recording is made in.
 * `?slide=` freezes one slide with its cues already fired, which is what a screenshot opens.
 *
 * Every key this module reads is one the contract publishes, and each is read once here so no other
 * module ever touches the URL. A preview goes one step further and asks the project for the cue
 * times the last run resolved, so reviewing a scene in a browser shows the film's own timing rather
 * than an invented one, and nothing on that path may fail a page that has no such file.
 */

import { frameAt, now, round, run, schedule, start } from "./clock.ts";
import { FRAME_STEP_MS, type Q, SLIDE_ENTRANCES, wireId } from "./contract.ts";
import { typeset, ready as typesetterReady } from "./katex.ts";
import { fire, type Mounted, prepare } from "./reveal.ts";
import {
  all,
  type CatalogEntry,
  type Context,
  catalog as catalogOf,
  cueOrder,
  findSlide,
  handled,
  handlersFor,
  measureClassSpans,
  ownerOf,
  readMarkup,
  type Scene,
  type Slide,
} from "./scene.ts";
import {
  build,
  CLASS,
  frame,
  hide,
  pan,
  reduced,
  say,
  scale,
  slideSeconds,
  span,
  freeze as stopEverything,
  style,
  styleClass,
} from "./stage.ts";
import { recorder } from "./telemetry.ts";
import { key, type Spoken } from "./text.ts";
import { warn } from "./warn.ts";

/** Every query key this page reads, typed against the contract so a misspelling will not compile. */
const SCENE: Q = "scene";
const SLIDE: Q = "slide";
const CUES: Q = "cues";
const WORDS: Q = "words";
const T0: Q = "t0";
const SPEED: Q = "speed";
const HUD: Q = "hud";

/** What `?t0=` says when the recorder means to start the clock itself rather than name a second. */
const SIGNAL = "signal";

/** What `?hud=` says when a page is asked to draw its own clock, which a recording never is. */
const ON = "1";

/** The slowest a preview may be played, which is slow enough to read a cue and fast enough to end. */
const SLOWEST = 0.05;

/** The router alias a project answers with the cue times its last run resolved. */
const CUE_TIMES = "/__decktalk/cue-times.json";

/** How long a preview without cue times rests before its first cue, and between two of them. */
const PREVIEW_STEP_SECONDS = 1;

/** How long after the deck is done a page says so, which is what a screenshot and a test wait for. */
const DONE = "1";

/** What the page is doing, which the probe reports and a person reads in the heads-up display. */
export type Mode = "index" | "preview" | "cue" | "freeze";

/** Everything the page knows about itself, which is what `window.__decktalk` is a view onto. */
export const state = {
  mode: "index" as Mode,
  scene: null as Scene | null,
  slide: null as Slide | null,
  mounted: null as Mounted | null,
  cues: [] as { id: string; at: number }[],
  fired: [] as string[],
  catalog: [] as CatalogEntry[],
  words: null as Spoken[] | null,
};

const params = new URLSearchParams(location.search);

/** Whether the page is a still, which every effect asks before it starts moving anything. */
const frozen = params.has(SLIDE);

/** The second on the narration clock the page starts at, which the recorder names or sends. */
const signalled = params.get(T0) === SIGNAL;
const origin = signalled ? 0 : Number.parseFloat(params.get(T0) ?? "") || 0;

/** How much faster than life a preview runs, which a recording ignores. */
const speed = Math.max(SLOWEST, Number.parseFloat(params.get(SPEED) ?? "") || 1);

/** Whether the recorder starts the clock rather than the page, which is what `?t0=signal` asks for. */
export function waitsForSignal(): boolean {
  return signalled;
}

/** Whether the page is a still, which the handlers are told so a page can draw a still differently. */
export function isFrozen(): boolean {
  return frozen;
}

/** The page's own query, which a deck reads to find the previous section's words or its own scene. */
export function query(): URLSearchParams {
  return params;
}

// ---- building and mounting ---------------------------------------------------------------------------

/**
 * A slide with its markup in place, before any moment is wired and before any handler has run.
 *
 * Mounting starts here and so does measuring, which is what makes a measured box the box a mount
 * will really draw the element at.
 */
export function buildSlide(scene: Scene, slide: Slide): HTMLElement {
  const el = document.createElement("div");
  el.className = CLASS.slide;
  if (slide.markup) el.appendChild(slide.markup.content.cloneNode(true));
  else if (slide.render) {
    // Every other page callback is wrapped, and this one is reached from the readiness chain, so a
    // render that throws has to become a warning rather than a rejection nobody expects.
    try {
      el.innerHTML = slide.render({ scene, slide, frozen });
    } catch (err) {
      warn("PAGE_RENDER_THREW", slide.id, null, { value: String(err) });
    }
  }
  return el;
}

/**
 * Put one slide on the stage, taking the one before it off once the incoming slide has arrived.
 *
 * The outgoing slide keeps full opacity underneath the incoming one for the whole crossfade, so the
 * composite of the two is opaque throughout and never shows the page's own background through the
 * pair of them, and it is removed on the animation's own end rather than on a length written twice.
 */
function mount(scene: Scene, slide: Slide, held: ReadonlySet<string> | null, at: number | null): HTMLElement {
  const old = pan().querySelector(`.${CLASS.slide}:not(.${CLASS.leaving})`);
  const el = buildSlide(scene, slide);
  const mounted = prepare(el, slide, { frozen, held, words: state.words });
  typeset(el, slide.id);
  state.slide = slide;
  state.mounted = mounted;
  const entrance = old ? slide.enter : "cut";
  if (old) {
    el.classList.add(CLASS.arriving, styleClass("enter", entrance));
    span(el, SLIDE_ENTRANCES[entrance].seconds);
  }
  // A script inside a cloned template runs on this line, so by now exactly one slide is live and a
  // reveal measures the box its author laid out.
  pan().appendChild(el);
  if (old) retire(el, old as HTMLElement, entrance);
  const mountAt = at ?? now();
  if (slide.entered) {
    try {
      slide.entered(el, context(slide.id, slide.id, mountAt));
    } catch (err) {
      warn("PAGE_ENTER_THREW", slide.id, null, { value: String(err) });
    }
  }
  return el;
}

/** Take the outgoing slide off once the incoming one has finished arriving, and never before. */
function retire(incoming: HTMLElement, outgoing: HTMLElement, entrance: keyof typeof SLIDE_ENTRANCES): void {
  const leaving = state.mounted;
  outgoing.classList.add(CLASS.leaving);
  let gone = false;
  const go = () => {
    if (gone) return;
    gone = true;
    if (leaving && leaving.el === outgoing) leaving.stop();
    outgoing.remove();
  };
  incoming.addEventListener("animationend", go, { once: true });
  // The fallback is the crossfade the stylesheet is really playing plus one captured frame, so a
  // page that never fires the event still loses its outgoing slide at the right moment.
  setTimeout(go, (slideSeconds(entrance) + FRAME_STEP_MS / 1000) * 1000);
}

/** What a handler is told about where it is running, which is the same for every kind of handler. */
function context(cue: string, slideId: string, at: number): Context {
  return { id: cue, at: round(at), frozen, slideId };
}

// ---- firing one cue -------------------------------------------------------------------------------

/**
 * Fire one cue: every moment it declares, then every handler registered for it, then the log row.
 *
 * A cue that draws nothing and runs nothing is almost always a name that drifted between the page
 * and the project file, so it is reported rather than passed over in silence.
 */
function fireCue(cue: string, due: number): void {
  state.fired.push(cue);
  const sentence = fire(state.mounted, cue);
  const slide = state.slide;
  const slideEl = state.mounted?.el ?? null;
  const ctx = context(cue, slide ? slide.id : "", now());
  const local = slide ? cue.slice(wireId(slide.id, "").length) : cue;
  const own = slide?.on[local];
  if (own) {
    try {
      own(slideEl, ctx);
    } catch (err) {
      warn("PAGE_SLIDE_HANDLER_THREW", slide?.id ?? null, cue, { value: String(err) });
    }
  }
  for (const handler of handlersFor(cue)) {
    try {
      handler(slideEl, ctx);
    } catch (err) {
      warn("PAGE_HANDLER_THREW", slide?.id ?? null, cue, { value: String(err) });
    }
  }
  if (sentence === null && !own && !handled(cue) && !findSlide(cue)) {
    warn("PAGE_CUE_UNKNOWN", slide?.id ?? null, cue);
  }
  recorder().cue({ id: cue, due: round(due), ran: round(now()), frame: frameAt(), describe: sentence });
}

// ---- the modes ---------------------------------------------------------------------------------------

/** `?cues=` as the recorder writes it, which is a wire id and a second, sorted by the second. */
function parseCues(raw: string): { id: string; at: number }[] {
  return raw
    .split(",")
    .map((token) => token.trim())
    .filter(Boolean)
    .map((token) => {
      const mark = token.lastIndexOf("@");
      return { id: token.slice(0, mark), at: Number.parseFloat(token.slice(mark + 1)) };
    })
    .filter((cue) => cue.id && !Number.isNaN(cue.at))
    .sort((a, b) => a.at - b.at);
}

/** `?words=` as `narrate` wrote it, which is every spoken word and the second the voice reaches it. */
function parseWords(raw: string): Spoken[] {
  return raw
    .split(",")
    .map((item) => {
      const mark = item.lastIndexOf("@");
      return { key: key(item.slice(0, mark)), at: Number.parseFloat(item.slice(mark + 1)) };
    })
    .filter((word) => word.key && !Number.isNaN(word.at));
}

/**
 * Play one scene against the narration clock, which is the mode every recording is made in.
 *
 * The first cued slide is mounted before the clock starts and before the recorder's cover comes off,
 * so no frame of a recording is ever drawn on an empty stage, and its own reveals still wait for
 * their own cues.
 */
function play(scene: Scene, cues: { id: string; at: number }[], mode: Mode): void {
  state.mode = mode;
  state.scene = scene;
  state.cues = cues;
  const mountAt = new Map<Slide, number>();
  for (const cue of cues) {
    const owner = ownerOf(cue.id, scene);
    if (!owner) {
      if (!handled(cue.id)) warn("PAGE_NO_OWNER", scene.id, cue.id);
      continue;
    }
    mountAt.set(owner.slide, Math.min(mountAt.get(owner.slide) ?? Number.POSITIVE_INFINITY, cue.at));
  }
  const ordered = [...mountAt.entries()].sort((a, b) => a[1] - b[1]);
  if (!ordered.length) {
    warn("PAGE_NO_OWNER", scene.id, cues.length ? (cues[0] as { id: string }).id : null);
    return;
  }
  // A preview plays whatever times it found, so a slide left out of them is not a slide left out of
  // a recording, and only a recording can leave a slide out of the film.
  if (mode === "cue") {
    for (const slide of scene.slides) {
      if (!mountAt.has(slide)) warn("PAGE_SLIDE_UNUSED", slide.id);
    }
  }
  const last = (ordered[ordered.length - 1] as [Slide, number])[0];
  mount(scene, (ordered[0] as [Slide, number])[0], null, origin);
  if ((ordered[0] as [Slide, number])[0] === last) document.body.dataset.done = DONE;
  for (const [slide] of ordered.slice(1)) {
    schedule(origin + (mountAt.get(slide) as number), "mount", slide.id, () => {
      mount(scene, slide, null, null);
      if (slide === last) document.body.dataset.done = DONE;
    });
  }
  for (const cue of cues) {
    schedule(origin + cue.at, "cue", cue.id, () => fireCue(cue.id, origin + cue.at));
  }
}

/**
 * Preview one scene on its own clock, with the project's own cue times when there are any.
 *
 * A preview that finds the times the last run resolved plays the film's timing, which is the only
 * way to review a scene without recording it. A preview that finds nothing fires every cue a slide
 * declares, one step apart, so even a deck whose cues have never been resolved shows its whole self.
 */
function preview(scene: Scene): void {
  state.mode = "preview";
  state.scene = scene;
  let at = origin;
  const last = scene.slides[scene.slides.length - 1];
  scene.slides.forEach((slide, index) => {
    const mountAt = at;
    const cues = cueOrder(slide);
    const start = () => {
      mount(scene, slide, null, index === 0 ? origin : null);
      cues.forEach((cue, order) => {
        schedule(mountAt + ((order + 1) * PREVIEW_STEP_SECONDS) / speed, "cue", cue, () =>
          fireCue(cue, mountAt + ((order + 1) * PREVIEW_STEP_SECONDS) / speed),
        );
      });
      if (slide === last) document.body.dataset.done = DONE;
    };
    if (index === 0) start();
    else schedule(mountAt, "mount", slide.id, start);
    at += Math.max(slide.hold, (cues.length + 1) * PREVIEW_STEP_SECONDS) / speed;
  });
}

/** One section of the cue times a project publishes, as the router alias answers with them. */
type TimedSection = { scene?: string; cues?: { cue?: string; at?: number }[] };

/**
 * The cue times the last run resolved for one scene, or null when the project has published none.
 *
 * Nothing here may fail a page. A project that has never run `cue`, a page opened from the file
 * system and a server that answers with something else all take the same path, which is the one that
 * falls back to a preview of the slide's own declared order.
 */
async function resolvedCues(sceneId: string): Promise<{ id: string; at: number }[] | null> {
  let document_: { sections?: TimedSection[] };
  try {
    const answer = await fetch(CUE_TIMES);
    if (!answer.ok) return null;
    document_ = await answer.json();
  } catch {
    return null;
  }
  const rows = (document_.sections ?? []).filter((section) => String(section.scene ?? "") === sceneId);
  if (!rows.length) return null;
  if (rows.length > 1) {
    warn("PAGE_PREVIEW_AMBIGUOUS", sceneId);
    return null;
  }
  const cues = ((rows[0] as TimedSection).cues ?? [])
    .filter((row) => typeof row.cue === "string" && typeof row.at === "number")
    .map((row) => ({ id: row.cue as string, at: (row.at as number) / speed }));
  return cues.length ? cues.sort((a, b) => a.at - b.at) : null;
}

/**
 * Freeze one slide with its cues fired, which is the still the index links and a screenshot records.
 *
 * Every cue fires unless the probe hands back a shorter list, and an element waiting on a cue that
 * never fires stays hidden, which is what makes a still of one moment of a slide possible at all.
 */
function stop(slideId: string, held: (order: readonly string[], slide: string) => readonly string[]): void {
  const found = findSlide(slideId);
  if (!found) {
    index(`unknown slide ${slideId}`);
    return;
  }
  state.mode = "freeze";
  state.scene = found.scene;
  stopEverything();
  const order = cueOrder(found.slide);
  const firing = held(order, found.slide.id);
  mount(found.scene, found.slide, new Set(order.slice(firing.length)), 0);
  state.fired = [...firing];
  document.body.dataset.done = DONE;
}

/** The index page, which lists every scene and every slide and links a preview and a still of each. */
function index(note?: string): void {
  state.mode = "index";
  hide();
  const div = document.createElement("div");
  div.id = "dt-index";
  const title = document.title || "decktalk scenes";
  div.appendChild(heading(title, note));
  for (const scene of all().values()) {
    const head = document.createElement("h2");
    head.textContent = `Scene ${scene.id} — ${scene.name} `;
    head.appendChild(link(`?${SCENE}=${encodeURIComponent(scene.id)}`, "▶ play"));
    div.appendChild(head);
    const row = document.createElement("div");
    row.className = "dt-slides";
    for (const slide of scene.slides) {
      const cues = cueOrder(slide);
      const anchor = link(`?${SLIDE}=${encodeURIComponent(slide.id)}`, `slide ${slide.id} `);
      const note_ = document.createElement("code");
      note_.textContent = `(${slide.hold}s${cues.length ? `, cues ${cues.join(" ")}` : ""})`;
      anchor.appendChild(note_);
      row.appendChild(anchor);
    }
    div.appendChild(row);
  }
  document.body.appendChild(div);
}

/** The index page's own heading, built as elements so no page title is ever parsed as markup. */
function heading(title: string, note?: string): DocumentFragment {
  const out = document.createDocumentFragment();
  const h1 = document.createElement("h1");
  h1.textContent = title;
  out.appendChild(h1);
  if (note) {
    const p = document.createElement("p");
    const bold = document.createElement("b");
    bold.textContent = note;
    p.appendChild(bold);
    out.appendChild(p);
  }
  return out;
}

function link(href: string, text: string): HTMLAnchorElement {
  const anchor = document.createElement("a");
  anchor.href = href;
  anchor.textContent = text;
  return anchor;
}

// ---- starting ------------------------------------------------------------------------------------

/** What the probe lends the page, which is the shorter freeze list and the measured boxes. */
export type Probe = {
  freezeCues?(order: readonly string[], slide: string, report: typeof warn): readonly string[];
  measure?(catalog: CatalogEntry[], stage: unknown): CatalogEntry[];
};

/**
 * Read the page, pick the mode its URL asks for, and start the clock loop.
 *
 * The catalog is built before anything is mounted, because the index page hides the stage the boxes
 * would be measured in and because a static check reads the catalog rather than the markup.
 */
export function begin(probe: Probe | null): Promise<void> {
  style();
  readMarkup();
  state.catalog = catalogOf();
  if (!signalled) start();
  build(params.get(HUD) === ON);
  const wordsRaw = params.get(WORDS);
  state.words = wordsRaw ? parseWords(wordsRaw) : null;
  const cuesRaw = params.get(CUES);
  const cues = cuesRaw ? parseCues(cuesRaw) : [];
  const chosen = params.get(SCENE);
  let listing = false;
  let waiting: Promise<void> = Promise.resolve();
  if (frozen) {
    stop(params.get(SLIDE) ?? "", (order, slide) => probe?.freezeCues?.(order, slide, warn) ?? order);
  } else if (chosen !== null || cues.length) {
    const scene =
      chosen !== null ? (all().get(chosen) ?? null) : (ownerOf((cues[0] as { id: string }).id)?.scene ?? null);
    if (!scene) index(`unknown scene ${chosen ?? ""}`);
    else if (cues.length) play(scene, cues, "cue");
    else
      waiting = resolvedCues(scene.id).then((resolved) =>
        resolved ? play(scene, resolved, "preview") : preview(scene),
      );
  } else {
    listing = true;
  }
  // The loop is handed a per-frame hook only when the heads-up display is on, because a recording
  // pays for every frame the page spends on work a viewer cannot see.
  run(params.get(HUD) === ON ? report : undefined);
  if (!listing) return waiting.then(() => typesetterReady(pan()));
  return waiting
    .then(() => typesetterReady(pan()))
    .then(() => {
      measure(probe);
      index();
    });
}

/** One line of the heads-up display, which is for an author reviewing a scene and never for a recording. */
function report(seconds: number): void {
  const where = `scene ${state.scene?.id ?? "-"} · slide ${state.slide?.id ?? "-"}`;
  say(`${state.mode} · ${where} · t ${Math.max(0, seconds).toFixed(2)}s`);
}

/**
 * Lay every slide out once to read the length of each class change, then let the probe measure boxes.
 *
 * Both passes happen on the index page alone, which is the page a static check opens, so no preview
 * and no recording ever lays a slide out twice.
 */
function measure(probe: Probe | null): void {
  const layer = document.createElement("div");
  layer.id = "dt-spans";
  layer.style.cssText = "position:absolute;inset:0;visibility:hidden";
  pan().appendChild(layer);
  const dim = reduced();
  for (const entry of state.catalog) {
    const scene = all().get(entry.scene);
    if (!scene) continue;
    for (const slide of scene.slides) {
      const el = buildSlide(scene, slide);
      layer.appendChild(el);
      measureClassSpans(el, slide, entry.spans, dim);
      layer.removeChild(el);
    }
  }
  layer.remove();
  probe?.measure?.(state.catalog, {
    scenes: all(),
    pan: pan(),
    origin: frame(),
    scale: scale(),
    build: buildSlide,
  });
}
