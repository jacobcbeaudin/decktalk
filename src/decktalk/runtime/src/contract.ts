/*! The page contract: every attribute an author writes, every code the page reports, and the words a
 * URL and a report are spelled with.
 *
 * An element on a slide has four moments and one value type. It arrives, it steps back, it comes to
 * the front, and it leaves, and each of those is the local name of a cue. Everything else is either
 * how a moment looks, which is a closed word this file owns, or what a moment means, which is a
 * sentence for the transcript. Seconds are never written on the page, because cues.json owns them.
 *
 * This module is the one home of that grammar. Every other runtime module reads its attribute names
 * from here rather than spelling a "data-" literal of its own, `scripts/build_runtime.py` prints
 * `CONTRACT` through node and writes `contract.json` and `src/decktalk/page.py` from it, and the
 * documentation table and the skills' JSON are written from those. Nothing here touches the DOM, so
 * a test can read the whole contract without a browser.
 *
 * Two rules decide what may live here. An attribute survives only when its value can change the
 * verdict of some check or be named by some finding, and the five rows that do neither are named in
 * `EXEMPT` with the sentence that says why. No span a page can declare may reach
 * `MEASURABLE_SPAN_SECONDS`, because an effect still moving half a second after its cue marks its
 * own cue unmeasurable.
 */

// ---- the two units every clock here converts between -------------------------------------------

/**
 * How many milliseconds one second holds.
 *
 * Truth: the browser reports every time in milliseconds and every number DeckTalk publishes is in
 * seconds, so the conversion happens at each boundary and is named there rather than spelled out.
 */
export const MILLISECONDS = 1000;

/**
 * How many decimal places a second is written to, which is a millisecond.
 *
 * Truth: a millisecond is finer than any frame a recording holds, so a reader comparing two seconds
 * against each other never has to read precision the measurement never had.
 */
export const SECOND_DIGITS = 3;

// ---- what the recording can see ---------------------------------------------------------------

/** The rate the recorder captures at, which is the rate Chromium's screencast paints a deck at. */
export const CAPTURE_FPS = 25;

/**
 * One captured frame in milliseconds, which is the finest interval any range on this page may name.
 *
 * It is written out rather than divided, because every range below is written in whole frames and a
 * reader should be able to see the step the ranges are built on. A test holds it equal to
 * `1000 / CAPTURE_FPS`, so the two can never drift.
 */
export const FRAME_STEP_MS = 40;

/**
 * The longest motion a cue may still be playing, in seconds.
 *
 * This one number is three things the design keeps together on purpose. It is the point at which an
 * effect marks its own cue unmeasurable, it is the ceiling `motion.scale` is clamped against, and it
 * is the total a staggered container may not pass. A page range that admitted a value above it would
 * let an author delete a check by turning a knob.
 */
export const MEASURABLE_SPAN_SECONDS = 0.5;

/** The share of an entrance that must be drawn in its first captured frame, so verify reads an onset. */
export const ONSET_FIRST_FRAME_PERCENT = 25;

/** The words `appear` may reveal one at a time before the line is longer than a cue can carry. */
export const APPEAR_WORDS_MAX = 8;

/** The opacity a stepped-back element holds, which is dim enough to read as secondary and light enough to read. */
export const BACK_OPACITY = 0.45;

// ---- the closed word sets ---------------------------------------------------------------------

/**
 * How long each entrance plays, and how far outside its resting box it travels while it plays.
 *
 * A static check inflates an element's measured box by its entrance's envelope, because the box the
 * probe measures is where the element comes to rest and not where it passes through.
 */
export const ENTRANCES = {
  rise: { seconds: 0.32, liftPixels: 10, overshootPercent: 0 },
  settle: { seconds: 0.28, liftPixels: 4, overshootPercent: 0 },
  fade: { seconds: 0.24, liftPixels: 0, overshootPercent: 0 },
  pop: { seconds: 0.2, liftPixels: 0, overshootPercent: 4 },
  draw: { seconds: 0.48, liftPixels: 0, overshootPercent: 0 },
  cut: { seconds: 0, liftPixels: 0, overshootPercent: 0 },
} as const;

/** How long each exit plays. Both exits are the same length, because an exit is never the subject. */
export const EXITS = {
  fade: { seconds: 0.2 },
  fall: { seconds: 0.2 },
} as const;

/** How a slide replaces the one before it, and how long that takes. */
export const SLIDE_ENTRANCES = {
  crossfade: { seconds: 0.24 },
  cut: { seconds: 0 },
} as const;

/** How a line is shown on the voice, and how long one word takes to arrive. */
export const WORD_STYLES = {
  highlight: { seconds: 0.12 },
  appear: { seconds: 0.12 },
} as const;

/** Which number in the text counts up from zero, and how long the count runs. */
export const COUNTS = {
  last: { seconds: 0.48 },
  first: { seconds: 0.48 },
} as const;

/** How long a step back and a return to the front play. */
export const ATTENTION = {
  back: { seconds: 0.28 },
  front: { seconds: 0.2 },
} as const;

export type Entrance = keyof typeof ENTRANCES;
export type Exit = keyof typeof EXITS;
export type SlideEntrance = keyof typeof SLIDE_ENTRANCES;
export type WordStyle = keyof typeof WORD_STYLES;
export type Count = keyof typeof COUNTS;

// ---- the codes the page reports ---------------------------------------------------------------

/** How sure a code is. A certain code names something that is wrong, and an uncertain one names a risk. */
export type Certainty = "certain" | "uncertain";

/** Who raises a code. The runtime reports it from the page, and Python measures it from what was recorded. */
export type RaisedBy = "runtime" | "python";

/** One code the page contract owns, with the sentence a person reads and the fields that fill it. */
export type CodeRow = {
  readonly message: string;
  readonly certainty: Certainty;
  readonly raisedBy: RaisedBy;
};

/**
 * Every code whose subject is the page, keyed by the name Python's `Code` enum carries.
 *
 * The message is a whole sentence that carries its own fix, written for the console and for a
 * person. Nothing parses it, and the documentation URL is derived from the code by whoever prints
 * it, because shipping a URL would bake the documentation host into every deck.
 */
export const CODES = {
  PAGE_UNKNOWN_ATTR: {
    message: "{attr} is not an attribute this contract defines, so check its spelling against the attribute table.",
    certainty: "certain",
    raisedBy: "runtime",
  },
  PAGE_BAD_VALUE: {
    message: "{attr}={value} is not one of {allowed}, so write one of those instead.",
    certainty: "certain",
    raisedBy: "runtime",
  },
  PAGE_MOMENT_UNKNOWN: {
    message: "{attr}={value} names a cue slide {slide} does not own, so declare it on the slide or correct the name.",
    certainty: "certain",
    raisedBy: "runtime",
  },
  PAGE_MOMENT_ORDER: {
    message: "The exit {value} is at or before the entrance on the same element, so give the exit a later cue.",
    certainty: "certain",
    raisedBy: "runtime",
  },
  PAGE_CUE_UNKNOWN: {
    message: "The cue {cue} is not one this deck declares, so remove it or name it in a moment attribute.",
    certainty: "certain",
    raisedBy: "runtime",
  },
  PAGE_NO_OWNER: {
    message: "No slide owns the cue {cue}, so name it in a moment attribute or list it in data-owns.",
    certainty: "certain",
    raisedBy: "runtime",
  },
  PAGE_SCENE_EMPTY: {
    message: "The scene {slide} declares no slide, so add a template to it or remove the scene.",
    certainty: "certain",
    raisedBy: "runtime",
  },
  PAGE_SLIDE_NO_ID: {
    message: "A template carries no data-slide, so give it the id a section names.",
    certainty: "certain",
    raisedBy: "runtime",
  },
  PAGE_SLIDE_DOUBLED: {
    message: "Two templates claim the slide id {slide}, so every moment local to it has two owners.",
    certainty: "certain",
    raisedBy: "runtime",
  },
  PAGE_SLIDE_UNUSED: {
    message: "Slide {slide} is never shown, so nothing it declares reaches a recording.",
    certainty: "certain",
    raisedBy: "runtime",
  },
  PAGE_TEMPLATE_IGNORED: {
    message: "A template inside slide {slide} declares no slide of its own, so nothing ever mounts it.",
    certainty: "certain",
    raisedBy: "runtime",
  },
  PAGE_WORDS_NOT_FOUND: {
    message: "The line {value} is not among the spoken words, so it cannot be shown word by word.",
    certainty: "certain",
    raisedBy: "runtime",
  },
  PAGE_KATEX_MISSING: {
    message: "KaTeX is not loaded, so {attr} is left as the author wrote it.",
    certainty: "certain",
    raisedBy: "runtime",
  },
  PAGE_KATEX_ERROR: {
    message: "KaTeX refused {value}, so the element shows its readable fallback text.",
    certainty: "certain",
    raisedBy: "runtime",
  },
  PAGE_FREEZE_CUE_UNKNOWN: {
    message: "The cue {cue} is not one of slide {slide}'s cues, so the freeze stopped at nothing.",
    certainty: "certain",
    raisedBy: "runtime",
  },
  PAGE_RENDER_THREW: {
    message: "The render handler for slide {slide} threw {value}, so the slide is drawn as its markup stands.",
    certainty: "certain",
    raisedBy: "runtime",
  },
  PAGE_ENTER_THREW: {
    message: "The enter handler for slide {slide} threw {value}, so the slide arrived without it.",
    certainty: "certain",
    raisedBy: "runtime",
  },
  PAGE_SLIDE_HANDLER_THREW: {
    message: "The handler slide {slide} registered for cue {cue} threw {value}, so that moment did nothing.",
    certainty: "certain",
    raisedBy: "runtime",
  },
  PAGE_HANDLER_THREW: {
    message: "The handler the deck registered for cue {cue} threw {value}, so that moment did nothing.",
    certainty: "certain",
    raisedBy: "runtime",
  },
  PAGE_WAIT_REJECTED: {
    message: "A promise the page waited for rejected with {value}, so the page was drawn without it.",
    certainty: "certain",
    raisedBy: "runtime",
  },
  PAGE_WAIT_UNSETTLED: {
    message: "A promise the page waited for never settled, so the page was drawn without it.",
    certainty: "certain",
    raisedBy: "runtime",
  },
  PAGE_CLASS_UNDESCRIBED: {
    message: "data-class names {value} with no phrase for it in data-describe-class, so the transcript loses it.",
    certainty: "certain",
    raisedBy: "runtime",
  },
  PAGE_CLASS_NOT_REDUCED: {
    message: "The class {value} still animates under reduced motion, so the page's own stylesheet must honour it.",
    certainty: "certain",
    raisedBy: "runtime",
  },
  PAGE_SWAP_AMBIGUOUS: {
    message: "data-swaps at cue {cue} found {value} elements leaving, so name the one it replaces with a shared cue.",
    certainty: "certain",
    raisedBy: "runtime",
  },
  PAGE_PREVIEW_AMBIGUOUS: {
    message: "Two sections name the scene {slide}, so a preview cannot tell which one's cue times to play.",
    certainty: "certain",
    raisedBy: "runtime",
  },
  PAGE_APPEAR_TOO_LONG: {
    message: "data-words=appear is on a line of {value} words, so shorten it or use highlight instead.",
    certainty: "certain",
    raisedBy: "runtime",
  },
  PAGE_STAGGER_EMPTY: {
    message: "The container at cue {cue} staggers no children, so give it children or remove the attribute.",
    certainty: "certain",
    raisedBy: "runtime",
  },
  PAGE_MOTION_OVERRUN: {
    message: "The motion at cue {cue} is still playing {value} s later, at the frame the next cue is read from.",
    certainty: "certain",
    raisedBy: "python",
  },
  PAGE_STAGGER_OVERRUN: {
    message: "The stagger at cue {cue} runs {value} s in all, which passes the half second a cue may still move.",
    certainty: "certain",
    raisedBy: "python",
  },
  PAGE_WORD_LATE: {
    message: "The first synced word of {value} was shown after the voice reached it, so the line trails the speech.",
    certainty: "uncertain",
    raisedBy: "python",
  },
  PAGE_THIN_DRAW: {
    message: "The stroke at cue {cue} sweeps {value} percent of the frame, which is under the change floor.",
    certainty: "uncertain",
    raisedBy: "python",
  },
  PAGE_NO_DESCRIPTION: {
    message: "The element at cue {cue} has no text and no data-tex, so give it data-describe or an empty one.",
    certainty: "certain",
    raisedBy: "python",
  },
  PAGE_SWAP_APART: {
    message: "The swap at cue {cue} lands {value} percent over the box it replaces, so the two may read as unrelated.",
    certainty: "uncertain",
    raisedBy: "python",
  },
  PAGE_STALLED: {
    message: "The page stopped drawing {value} s into the recording, so every later cue was captured on a dead frame.",
    certainty: "certain",
    raisedBy: "python",
  },
  PAGE_BLACK: {
    message: "The frame at cue {cue} is black, so nothing the slide declares was on screen when the voice arrived.",
    certainty: "certain",
    raisedBy: "python",
  },
  PAGE_TRUNCATED: {
    message: "The recording ends {value} s before the narration does, so the last cues are not in it.",
    certainty: "certain",
    raisedBy: "python",
  },
  PAGE_CDN_ASSET: {
    message: "The page loaded {value} from another origin, so the film depends on a host it does not own.",
    certainty: "certain",
    raisedBy: "python",
  },
} as const satisfies Record<string, CodeRow>;

export type Code = keyof typeof CODES;

/** The five fields a page reports a warning as, which is the whole wire shape of a page warning. */
export type PageWarning = {
  readonly code: Code;
  readonly message: string;
  readonly slide: string | null;
  readonly cue: string | null;
  readonly attr: string | null;
};

// ---- the attribute registry -------------------------------------------------------------------

/** What an attribute is written on. A container row is written on an element that has cued children. */
export type Subject = "element" | "container" | "scene" | "slide";

/** What kind of value an attribute takes, which is what a reader parses it as. */
export type Kind = "moment" | "word" | "flag" | "seconds" | "phrase" | "pairs" | "id" | "names" | "tex";

/** What turning an attribute changes, published so an agent can tell a knob from a label. */
export type Affects = "cue-order" | "motion" | "transcript" | "catalog" | "preview" | "style";

/** The values a number attribute admits, in the unit the tool can see. */
export type Range = {
  readonly min: number;
  readonly max: number;
  readonly step: number;
  readonly unit: "seconds";
};

/** One row of the attribute table, which is everything published about one knob. */
export type AttrRow = {
  readonly name: string;
  readonly on: readonly Subject[];
  readonly kind: Kind;
  readonly values: readonly string[];
  readonly default: string | null;
  readonly range: Range | null;
  readonly code: Code | null;
  readonly span: number | null;
  readonly affects: readonly Affects[];
  readonly summary: string;
};

/**
 * The seconds of motion a row puts between its cue and the next one.
 *
 * A number is the span the row declares on its own. Zero is a row that never moves a pixel. `null`
 * is a row whose span is read from the page or from a closed word set, and every one of those is
 * bounded by `MEASURABLE_SPAN_SECONDS` as well.
 */
const READ_FROM_THE_PAGE = null;

/** How long an entrance may play, which is three to twelve captured frames. */
const IN_SECONDS_RANGE: Range = { min: 0.12, max: 0.48, step: 0.04, unit: "seconds" };

/** How far apart a container's children arrive, which is one to five captured frames. */
const STAGGER_RANGE: Range = { min: 0.04, max: 0.2, step: 0.04, unit: "seconds" };

/** How long a slide rests in the preview, which no recording reads and no check measures. */
const HOLD_RANGE: Range = { min: 1, max: 60, step: 1, unit: "seconds" };

/**
 * Every attribute of the contract, in the order an author reaches for them.
 *
 * The generated table keeps this order, so the first ten rows are the ten a person holds in their
 * head and the rows written once a deck sit after the rows written forty times a deck.
 */
export const ATTRS = {
  "data-in": {
    name: "data-in",
    on: ["element", "container"],
    kind: "moment",
    values: [],
    default: null,
    range: null,
    code: "PAGE_MOMENT_UNKNOWN",
    span: READ_FROM_THE_PAGE,
    affects: ["cue-order", "motion", "transcript", "catalog"],
    summary: "The element arrives at this cue.",
  },
  "data-describe": {
    name: "data-describe",
    on: ["element", "slide"],
    kind: "phrase",
    values: [],
    default: null,
    range: null,
    code: null,
    span: 0,
    affects: ["transcript", "catalog"],
    summary: "The subject of the reveal, which the runtime gives a verb per moment. An empty phrase means decorative.",
  },
  "data-tex": {
    name: "data-tex",
    on: ["element"],
    kind: "tex",
    values: [],
    default: null,
    range: null,
    code: "PAGE_KATEX_ERROR",
    span: 0,
    affects: ["catalog", "transcript"],
    summary: "Typeset with KaTeX, with the element's own text as the readable fallback.",
  },
  "data-tex-display": {
    name: "data-tex-display",
    on: ["element"],
    kind: "flag",
    values: [],
    default: null,
    range: null,
    code: "PAGE_KATEX_ERROR",
    span: 0,
    affects: ["catalog", "style"],
    summary: "Typeset the TeX as a display equation on its own line rather than inline.",
  },
  "data-in-style": {
    name: "data-in-style",
    on: ["element", "container"],
    kind: "word",
    values: ["rise", "settle", "fade", "pop", "draw", "cut"],
    default: "rise",
    range: null,
    code: "PAGE_BAD_VALUE",
    span: READ_FROM_THE_PAGE,
    affects: ["motion", "style"],
    summary: "How the element arrives. Each word declares its own span.",
  },
  "data-back": {
    name: "data-back",
    on: ["element", "container"],
    kind: "moment",
    values: [],
    default: null,
    range: null,
    code: "PAGE_MOMENT_UNKNOWN",
    span: ATTENTION.back.seconds,
    affects: ["cue-order", "motion", "transcript", "catalog"],
    summary: "The element steps back at this cue, dimmed but still readable.",
  },
  "data-front": {
    name: "data-front",
    on: ["element", "container"],
    kind: "moment",
    values: [],
    default: null,
    range: null,
    code: "PAGE_MOMENT_UNKNOWN",
    span: ATTENTION.front.seconds,
    affects: ["cue-order", "motion", "transcript", "catalog"],
    summary: "The element returns to full strength at this cue.",
  },
  "data-out": {
    name: "data-out",
    on: ["element", "container"],
    kind: "moment",
    values: [],
    default: null,
    range: null,
    code: "PAGE_MOMENT_ORDER",
    span: READ_FROM_THE_PAGE,
    affects: ["cue-order", "motion", "transcript", "catalog"],
    summary: "The element leaves at this cue.",
  },
  "data-words": {
    name: "data-words",
    on: ["element"],
    kind: "word",
    values: ["highlight", "appear"],
    default: "highlight",
    range: null,
    code: "PAGE_WORDS_NOT_FOUND",
    span: WORD_STYLES.highlight.seconds,
    affects: ["motion", "style"],
    summary: "The line is shown word by word on the voice, matched against the section's spoken words.",
  },
  "data-count": {
    name: "data-count",
    on: ["element"],
    kind: "word",
    values: ["last", "first"],
    default: null,
    range: null,
    code: "PAGE_BAD_VALUE",
    span: COUNTS.last.seconds,
    affects: ["motion", "style"],
    summary: "A number in the text counts up from zero when the element arrives.",
  },
  "data-class": {
    name: "data-class",
    on: ["element", "container"],
    kind: "pairs",
    values: [],
    default: null,
    range: null,
    code: "PAGE_CLASS_UNDESCRIBED",
    span: READ_FROM_THE_PAGE,
    affects: ["cue-order", "motion", "transcript", "style"],
    summary: "Adds a class at a moment, written as moment:name pairs, for the page's own stylesheet.",
  },
  "data-describe-class": {
    name: "data-describe-class",
    on: ["element", "container"],
    kind: "pairs",
    values: [],
    default: null,
    range: null,
    code: null,
    span: 0,
    affects: ["transcript"],
    summary: "What each class change means, as moment:phrase pairs separated by a vertical bar.",
  },
  "data-stagger": {
    name: "data-stagger",
    on: ["container"],
    kind: "seconds",
    values: [],
    default: null,
    range: STAGGER_RANGE,
    code: "PAGE_STAGGER_OVERRUN",
    span: READ_FROM_THE_PAGE,
    affects: ["motion"],
    summary: "The container's children arrive this far apart from one cue.",
  },
  "data-steps": {
    name: "data-steps",
    on: ["container"],
    kind: "flag",
    values: [],
    default: null,
    range: null,
    code: "PAGE_STAGGER_EMPTY",
    span: READ_FROM_THE_PAGE,
    affects: ["cue-order", "motion", "transcript", "catalog"],
    summary: "Each cued child comes to the front as it arrives and the ones before it step back.",
  },
  "data-in-seconds": {
    name: "data-in-seconds",
    on: ["element", "container"],
    kind: "seconds",
    values: [],
    default: null,
    range: IN_SECONDS_RANGE,
    code: "PAGE_BAD_VALUE",
    span: READ_FROM_THE_PAGE,
    affects: ["motion"],
    summary: "How long the entrance plays, which governs the entrance alone and never the onset.",
  },
  "data-out-style": {
    name: "data-out-style",
    on: ["element", "container"],
    kind: "word",
    values: ["fade", "fall"],
    default: "fade",
    range: null,
    code: "PAGE_BAD_VALUE",
    span: EXITS.fade.seconds,
    affects: ["motion", "style"],
    summary: "How the element leaves.",
  },
  "data-swaps": {
    name: "data-swaps",
    on: ["element"],
    kind: "flag",
    values: [],
    default: null,
    range: null,
    code: "PAGE_SWAP_AMBIGUOUS",
    span: READ_FROM_THE_PAGE,
    affects: ["motion", "catalog"],
    summary: "The element takes the place of whatever leaves on its own arrival cue, in a held swap.",
  },
  "data-describe-out": {
    name: "data-describe-out",
    on: ["element"],
    kind: "phrase",
    values: [],
    default: null,
    range: null,
    code: null,
    span: 0,
    affects: ["transcript"],
    summary: "Overrides the departure sentence when leaving means something of its own.",
  },
  "data-scene": {
    name: "data-scene",
    on: ["scene"],
    kind: "id",
    values: [],
    default: null,
    range: null,
    code: "PAGE_SCENE_EMPTY",
    span: 0,
    affects: ["catalog"],
    summary: "Declares the scene a section names, on the wrapper that holds its slides.",
  },
  "data-name": {
    name: "data-name",
    on: ["scene"],
    kind: "phrase",
    values: [],
    default: null,
    range: null,
    code: null,
    span: 0,
    affects: ["catalog"],
    summary: "The scene's name in the index and in the catalog.",
  },
  "data-slide": {
    name: "data-slide",
    on: ["slide"],
    kind: "id",
    values: [],
    default: null,
    range: null,
    code: "PAGE_SLIDE_NO_ID",
    span: 0,
    affects: ["cue-order", "catalog"],
    summary: "Declares a slide on a template. Its id qualifies every moment written inside it.",
  },
  "data-hold": {
    name: "data-hold",
    on: ["slide"],
    kind: "seconds",
    values: [],
    default: "8",
    range: HOLD_RANGE,
    code: null,
    span: 0,
    affects: ["preview"],
    summary: "How long the preview rests on this slide. No recording reads it.",
  },
  "data-owns": {
    name: "data-owns",
    on: ["slide"],
    kind: "names",
    values: [],
    default: null,
    range: null,
    code: "PAGE_CUE_UNKNOWN",
    span: 0,
    affects: ["cue-order", "catalog"],
    summary: "Local names of cues only a handler serves, which no moment attribute mentions.",
  },
  "data-enter": {
    name: "data-enter",
    on: ["slide"],
    kind: "word",
    values: ["crossfade", "cut"],
    default: "crossfade",
    range: null,
    code: "PAGE_BAD_VALUE",
    span: SLIDE_ENTRANCES.crossfade.seconds,
    affects: ["motion", "style"],
    summary: "How this slide replaces the one before it.",
  },
} as const satisfies Record<string, AttrRow>;

export type Attr = keyof typeof ATTRS;

/**
 * The rows that survive without a code, and the sentence that says why each one is allowed to.
 *
 * An attribute survives when its value can change the verdict of some check or be named by some
 * finding. These five carry a phrase a person wrote, or a preview length nothing records, so no
 * value of them can change a verdict. The list is closed and a test counts it, because an open field
 * saying what a row affects would readmit every knob the panel cut.
 */
export const EXEMPT = {
  "data-describe": "Any phrase clears the finding, so no value of it can change a verdict.",
  "data-describe-class": "The transcript prints the phrase verbatim, so no value of it can change a verdict.",
  "data-describe-out": "It replaces one transcript sentence with another, and neither is measured.",
  "data-hold": "The preview alone reads it, and a preview is never recorded.",
  "data-name": "It names a scene in the index, which no check and no finding reads.",
} as const satisfies Record<string, string>;

// ---- the query vocabulary ---------------------------------------------------------------------

/** Every query key a DeckTalk page reads, with what it asks the page for. */
export const QUERY = {
  scene: "Play this scene from its first slide.",
  slide: "Freeze this slide with its cues already fired, which is what a screenshot opens.",
  cues: "The cue times to fire at, as id@seconds pairs separated by a comma.",
  words: "The spoken words to sync a line against, as word@seconds pairs separated by a comma.",
  t0: "The narration second the page starts at, or the word signal when the recorder starts the clock.",
  speed: "Multiply the preview clock, so a long section is reviewed quickly.",
  hud: "Draw the on-page clock and cue list, which is for an author and never for a recording.",
  after: "Freeze after this cue, so the cues up to and including it fire and later ones stay hidden.",
  before: "Freeze just before this cue, so it and every later cue stay hidden.",
} as const;

export type Q = keyof typeof QUERY;

// ---- what a page reports back ------------------------------------------------------------------

/** Every field the probe's one `report()` call answers with, and what a reader does with it. */
export const REPORT = {
  version: "The runtime version the page carries.",
  mode: "Which of index, preview, cue and freeze the page is in.",
  scene: "The scene the page is playing, or null on the index page.",
  slide: "The slide on screen, or null when none is mounted.",
  warnings: "Every distinct warning the page reported, as code, message, slide, cue and attr.",
  catalog: "One entry per scene, with its slides, its cues and the measured box of every element.",
  cues: "When each cue was due, when it ran, and when the frames around it began.",
  words: "Each synced line, when its cue ran, when the voice reaches it and when its first word showed.",
  frameGaps: "Every gap between two animation frames longer than the recorder can absorb.",
  longFrames: "Every animation frame that took longer than a captured frame, with when it was presented.",
} as const;

export type ReportField = keyof typeof REPORT;

// ---- the derived views every other module reads -------------------------------------------------

/** Every attribute whose value is the local name of a cue, which is what puts a moment in the cue order. */
export const MOMENTS: readonly Attr[] = Object.keys(ATTRS).filter(
  (name) => ATTRS[name as Attr].kind === "moment",
) as Attr[];

/** The CSS selector that finds every element declaring a moment, built from the registry and never typed twice. */
export const MOMENT_SELECTOR = MOMENTS.map((name) => `[${name}]`).join(",");

/** The separator between two moment:value pairs, and the one between a pair's moment and its value. */
export const PAIR_SEPARATOR = "|";
export const PAIR_MARK = ":";

/** The separator between a slide id and a local moment name in the wire id that cues.json carries. */
export const WIRE_MARK = ":";

// ---- the pure readers -----------------------------------------------------------------------

/** The wire id of a local moment, which is the slide it was written in and the name the author wrote. */
export function wireId(slide: string, local: string): string {
  return `${slide}${WIRE_MARK}${local}`;
}

/**
 * The moment and the value of each pair in a `data-class` or a `data-describe-class` value.
 *
 * A pair with no mark, an empty moment or an empty value is dropped rather than guessed at, because
 * a half-written pair is a spelling mistake and `PAGE_CLASS_UNDESCRIBED` is the code that names it.
 */
export function pairs(value: string): { moment: string; value: string }[] {
  const out: { moment: string; value: string }[] = [];
  for (const part of value.split(PAIR_SEPARATOR)) {
    const at = part.indexOf(PAIR_MARK);
    if (at < 0) continue;
    const moment = part.slice(0, at).trim();
    const rest = part.slice(at + 1).trim();
    if (!moment || !rest) continue;
    out.push({ moment, value: rest });
  }
  return out;
}

/** The message a code prints, with each `{field}` replaced by what the caller knows about the page. */
export function message(code: Code, fields: Readonly<Record<string, string | number>> = {}): string {
  return CODES[code].message.replace(/\{(\w+)\}/g, (whole, key: string) =>
    key in fields ? String(fields[key]) : whole,
  );
}

/** Whether a `data-` attribute name is one the contract defines, which is what `PAGE_UNKNOWN_ATTR` asks. */
export function known(name: string): name is Attr {
  return Object.hasOwn(ATTRS, name);
}

/**
 * The reason a value is not one its attribute admits, or null when the value is fine.
 *
 * A closed word set is checked against its own list, a number against its published range and its
 * step, and every other kind takes any non-empty string, because a phrase and a cue name are the
 * author's words.
 */
export function refuse(name: Attr, value: string): string | null {
  const row = ATTRS[name];
  if (row.values.length) {
    return row.values.includes(value as never) ? null : `one of ${row.values.join(", ")}`;
  }
  if (row.range) {
    const seconds = Number(value);
    if (!Number.isFinite(seconds)) return `a number of seconds`;
    const { min, max, step } = row.range;
    if (seconds < min || seconds > max) return `${min} to ${max} seconds`;
    return Math.abs(Math.round(seconds / step) * step - seconds) < 1e-9 ? null : `a multiple of ${step} seconds`;
  }
  return value.trim() ? null : "a value";
}

/**
 * The whole span a staggered container puts between its cue and the next one.
 *
 * The arithmetic is exact, which is why `PAGE_STAGGER_OVERRUN` is a certain finding: the last child
 * starts one step per earlier child after the cue and then plays its own entrance.
 */
export function staggerSpan(step: number, children: number, entrance: number): number {
  return children > 0 ? step * (children - 1) + entrance : 0;
}

/** Whether a declared span is short enough for the cue it belongs to still to be measurable. */
export function measurable(span: number): boolean {
  return span < MEASURABLE_SPAN_SECONDS;
}

/**
 * A declared span under a reduced-motion render, clamped so no scaled span crosses the ceiling.
 *
 * The scale multiplies the declared span as well as the duration, so a project that slows its motion
 * down cannot slow it past the point where its own cues stop being measurable.
 */
export function scaled(span: number, scale: number): number {
  return Math.min(span * scale, MEASURABLE_SPAN_SECONDS - FRAME_STEP_MS / 1000);
}

// ---- the document the Python side reads ---------------------------------------------------------

/**
 * The whole contract as one JSON document, which `scripts/build_runtime.py` prints through node.
 *
 * Nothing else may read the TypeScript, so this object is the only boundary between the runtime and
 * everything written from it: `contract.json`, `src/decktalk/page.py`, the documentation table and
 * the JSON the skills quote.
 */
export const CONTRACT = {
  captureFps: CAPTURE_FPS,
  pairSeparator: PAIR_SEPARATOR,
  pairMark: PAIR_MARK,
  wireMark: WIRE_MARK,
  frameStepMs: FRAME_STEP_MS,
  measurableSpanSeconds: MEASURABLE_SPAN_SECONDS,
  onsetFirstFramePercent: ONSET_FIRST_FRAME_PERCENT,
  appearWordsMax: APPEAR_WORDS_MAX,
  backOpacity: BACK_OPACITY,
  entrances: ENTRANCES,
  exits: EXITS,
  slideEntrances: SLIDE_ENTRANCES,
  wordStyles: WORD_STYLES,
  counts: COUNTS,
  attention: ATTENTION,
  attrs: ATTRS,
  exempt: EXEMPT,
  codes: CODES,
  query: QUERY,
  report: REPORT,
} as const;
