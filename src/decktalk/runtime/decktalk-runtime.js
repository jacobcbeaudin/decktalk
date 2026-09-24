"use strict";
(() => {
  // src/decktalk/runtime/src/contract.ts
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
  var MILLISECONDS = 1e3;
  var SECOND_DIGITS = 3;
  var FRAME_STEP_MS = 40;
  var MEASURABLE_SPAN_SECONDS = 0.5;
  var APPEAR_WORDS_MAX = 8;
  var BACK_OPACITY = 0.45;
  var ENTRANCES = {
    rise: { seconds: 0.32, liftPixels: 10, overshootPercent: 0 },
    settle: { seconds: 0.28, liftPixels: 4, overshootPercent: 0 },
    fade: { seconds: 0.24, liftPixels: 0, overshootPercent: 0 },
    pop: { seconds: 0.2, liftPixels: 0, overshootPercent: 4 },
    draw: { seconds: 0.48, liftPixels: 0, overshootPercent: 0 },
    cut: { seconds: 0, liftPixels: 0, overshootPercent: 0 },
  };
  var EXITS = {
    fade: { seconds: 0.2 },
    fall: { seconds: 0.2 },
  };
  var SLIDE_ENTRANCES = {
    crossfade: { seconds: 0.24 },
    cut: { seconds: 0 },
  };
  var WORD_STYLES = {
    highlight: { seconds: 0.12 },
    appear: { seconds: 0.12 },
  };
  var COUNTS = {
    last: { seconds: 0.48 },
    first: { seconds: 0.48 },
  };
  var ATTENTION = {
    back: { seconds: 0.28 },
    front: { seconds: 0.2 },
  };
  var CODES = {
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
      certainty: "certain",
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
      message:
        "The swap at cue {cue} lands {value} percent over the box it replaces, so the two may read as unrelated.",
      certainty: "uncertain",
      raisedBy: "python",
    },
    PAGE_STALLED: {
      message:
        "The page stopped drawing {value} s into the recording, so every later cue was captured on a dead frame.",
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
  };
  var READ_FROM_THE_PAGE = null;
  var IN_SECONDS_RANGE = { min: 0.12, max: 0.48, step: 0.04, unit: "seconds" };
  var STAGGER_RANGE = { min: 0.04, max: 0.2, step: 0.04, unit: "seconds" };
  var HOLD_RANGE = { min: 1, max: 60, step: 1, unit: "seconds" };
  var ATTRS = {
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
      summary:
        "The subject of the reveal, which the runtime gives a verb per moment. An empty phrase means decorative.",
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
  };
  var MOMENTS = Object.keys(ATTRS).filter((name) => ATTRS[name].kind === "moment");
  var MOMENT_SELECTOR = MOMENTS.map((name) => `[${name}]`).join(",");
  var PAIR_SEPARATOR = "|";
  var PAIR_MARK = ":";
  var WIRE_MARK = ":";
  function wireId(slide, local) {
    return `${slide}${WIRE_MARK}${local}`;
  }
  function pairs(value) {
    const out = [];
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
  function message(code, fields = {}) {
    return CODES[code].message.replace(/\{(\w+)\}/g, (whole, key2) => (key2 in fields ? String(fields[key2]) : whole));
  }
  function known(name) {
    return Object.hasOwn(ATTRS, name);
  }
  function refuse(name, value) {
    const row = ATTRS[name];
    if (row.values.length) {
      return row.values.includes(value) ? null : `one of ${row.values.join(", ")}`;
    }
    if (row.range) {
      const seconds2 = Number(value);
      if (!Number.isFinite(seconds2)) return `a number of seconds`;
      const { min, max, step } = row.range;
      if (seconds2 < min || seconds2 > max) return `${min} to ${max} seconds`;
      return Math.abs(Math.round(seconds2 / step) * step - seconds2) < 1e-9 ? null : `a multiple of ${step} seconds`;
    }
    return value.trim() ? null : "a value";
  }
  function scaled(span2, scale2) {
    return Math.min(span2 * scale2, MEASURABLE_SPAN_SECONDS - FRAME_STEP_MS / 1e3);
  }

  // src/decktalk/runtime/src/clock.ts
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
  var MOUNT_FIRST = 0;
  var AFTER_THE_MOUNT = 1;
  var origin = null;
  var frame = 0;
  var queue = [];
  var looping = false;
  function start() {
    if (origin === null) origin = performance.now();
  }
  function started() {
    return origin !== null;
  }
  function now() {
    return origin === null ? Number.NEGATIVE_INFINITY : (performance.now() - origin) / MILLISECONDS;
  }
  function frameAt() {
    return origin === null ? null : round((frame - origin) / MILLISECONDS);
  }
  function round(seconds2) {
    return Number(seconds2.toFixed(SECOND_DIGITS));
  }
  function schedule(at, kind, id, run2) {
    queue.push({ at, kind, id, run: run2 });
    queue.sort((a, b) => a.at - b.at || rank(a.kind) - rank(b.kind));
  }
  function rank(kind) {
    return kind === "mount" ? MOUNT_FIRST : AFTER_THE_MOUNT;
  }
  function run(onFrame) {
    if (looping) return;
    looping = true;
    const tick = (at) => {
      frame = at;
      const seconds2 = now();
      while (queue.length && queue[0].at <= seconds2) queue.shift().run();
      onFrame?.(seconds2);
      requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
  }

  // src/decktalk/runtime/src/warn.ts
  /*! Everything the page could not honour, in the five fields the contract publishes.
   *
   * A page never throws at its author. It records what it could not do, echoes it to the console for
   * whoever has the page open, and carries on drawing, because a deck that stopped at the first
   * misspelled attribute would cost a recording rather than save one. The recorder reads the list back
   * through the probe's one report call and turns each row into a finding.
   *
   * The sentence a row prints belongs to the contract and never to this module, so a code's wording
   * changes in one file and reaches the console, the finding and the documentation at once.
   */
  var CONSOLE_PREFIX = "decktalk";
  var recorded = [];
  function warn(code, slide = null, cue = null, fields = {}) {
    const filled = { ...(slide === null ? {} : { slide }), ...(cue === null ? {} : { cue }), ...fields };
    const row = {
      code,
      message: message(code, filled),
      slide,
      cue,
      attr: fields.attr === void 0 ? null : String(fields.attr),
    };
    if (recorded.some((seen) => same(seen, row))) return;
    recorded.push(row);
    console.warn(`${CONSOLE_PREFIX}: ${row.message}`);
  }
  function same(a, b) {
    return a.code === b.code && a.slide === b.slide && a.cue === b.cue && a.attr === b.attr;
  }
  function warnings() {
    return recorded;
  }

  // src/decktalk/runtime/src/scene.ts
  /*! Scenes, slides, the markup reader and the cue order every check is written against.
   *
   * A deck is scenes, a scene is slides, and a slide declares its own cues. Nothing else in the
   * runtime reads markup, so every rule about what an author may write is in one place: the attribute
   * names are derived from the registry rather than spelled here, an unknown `data-` word and a value
   * outside its published set are reported before a single pixel is drawn, and a moment is qualified
   * with the id of the template it was written in, so the author writes `expand` inside the slide
   * `pitch.listing` and the wire carries `pitch.listing:expand`.
   *
   * Ownership is declared and never inferred. A slide owns exactly the cues its moment attributes
   * name plus the local names it lists, which is what lets a cue id carry any characters an author
   * likes and what replaced the longest-prefix rule that used to guess.
   */
  var NAMES = Object.keys(ATTRS);
  var PREFIX = NAMES[0].slice(0, NAMES[0].indexOf("-") + 1);
  function named(word2) {
    return NAMES.find((name) => name.slice(PREFIX.length) === word2) ?? word2;
  }
  var ATTR = {
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
  };
  var SCENE_WORD = "Scene";
  var scenes = /* @__PURE__ */ new Map();
  var handlers = /* @__PURE__ */ new Map();
  var motionScale = 1;
  function setMotionScale(scale2) {
    motionScale = scale2;
  }
  function all() {
    return scenes;
  }
  function handlersFor(cue) {
    return handlers.get(cue) ?? [];
  }
  function handled(cue) {
    return handlers.has(cue);
  }
  function on(cue, fn) {
    const list = handlers.get(cue) ?? [];
    list.push(fn);
    handlers.set(cue, list);
  }
  function written(el, name) {
    const value = el.getAttribute(name);
    return value === null ? null : value.trim();
  }
  function flagged(el, name) {
    return el.hasAttribute(name);
  }
  function word(el, name, set) {
    const value = written(el, name);
    if (value !== null && Object.hasOwn(set, value)) return value;
    return ATTRS[name].default;
  }
  function seconds(el, name) {
    const value = written(el, name);
    if (value === null || refuse(name, value) !== null) return null;
    return Number(value);
  }
  function entranceOf(el) {
    return word(el, ATTR.inStyle, ENTRANCES);
  }
  function exitOf(el) {
    return word(el, ATTR.outStyle, EXITS);
  }
  function wordStyleOf(el) {
    return word(el, ATTR.words, WORD_STYLES);
  }
  function countOf(el) {
    const value = written(el, ATTR.count);
    return value !== null && Object.hasOwn(COUNTS, value) ? value : null;
  }
  function slideEntranceOf(el) {
    return word(el, ATTR.enter, SLIDE_ENTRANCES);
  }
  function entranceSeconds(el) {
    return seconds(el, ATTR.inSeconds) ?? ENTRANCES[entranceOf(el)].seconds;
  }
  function staggerSeconds(el) {
    return seconds(el, ATTR.stagger);
  }
  function holdSeconds(el) {
    return seconds(el, ATTR.hold) ?? Number(ATTRS[ATTR.hold].default);
  }
  function momentsOf(el, slideId) {
    const out = [];
    for (const attr of MOMENTS) {
      const local = written(el, attr);
      if (local) out.push({ attr, local, cue: wireId(slideId, local) });
    }
    return out;
  }
  function classMomentsOf(el, slideId) {
    const value = written(el, ATTR.class);
    if (!value) return [];
    return pairs(value).map((pair) => ({ cue: wireId(slideId, pair.moment), local: pair.moment, name: pair.value }));
  }
  function classPhraseOf(el, local) {
    const value = written(el, ATTR.describeClass);
    if (value === null) return null;
    const found = pairs(value).find((pair) => pair.moment === local);
    return found ? found.value : null;
  }
  function momentElements(root) {
    return [...root.querySelectorAll(MOMENT_SELECTOR), ...root.querySelectorAll(`[${ATTR.class}]`)].filter(
      (el, at, list) => list.indexOf(el) === at,
    );
  }
  function cueOrder(slide) {
    const order2 = [];
    const add = (cue) => {
      if (!order2.includes(cue)) order2.push(cue);
    };
    if (slide.markup) {
      for (const el of momentElements(slide.markup.content)) {
        for (const moment of momentsOf(el, slide.id)) add(moment.cue);
        for (const change of classMomentsOf(el, slide.id)) add(change.cue);
      }
    }
    for (const local of Object.keys(slide.on)) add(wireId(slide.id, local));
    for (const local of slide.owns) add(wireId(slide.id, local));
    return order2;
  }
  function ownerOf(cue, within = null) {
    for (const scene of within ? [within] : scenes.values()) {
      for (const slide of scene.slides) {
        if (cueOrder(slide).includes(cue)) return { scene, slide };
      }
    }
    return null;
  }
  function findSlide(id) {
    for (const scene of scenes.values()) {
      const slide = scene.slides.find((one) => one.id === id);
      if (slide) return { scene, slide };
    }
    return null;
  }
  function spansOf(slide) {
    const spans = {};
    const widen = (cue, span2) => {
      spans[cue] = Math.max(spans[cue] ?? 0, declared(span2));
    };
    if (!slide.markup) return spans;
    for (const el of momentElements(slide.markup.content)) {
      for (const moment of momentsOf(el, slide.id)) widen(moment.cue, spanOf(el, moment.attr));
      for (const change of classMomentsOf(el, slide.id)) widen(change.cue, 0);
    }
    return spans;
  }
  function declared(span2) {
    return Number((span2 * motionScale).toFixed(SECOND_DIGITS));
  }
  function spanOf(el, attr) {
    if (attr === ATTR.back) return ATTENTION.back.seconds;
    if (attr === ATTR.front) return ATTENTION.front.seconds;
    if (attr === ATTR.out) return EXITS[exitOf(el)].seconds;
    return arrivalSpan(el);
  }
  function arrivalSpan(el) {
    const entrance = entranceSeconds(el);
    const step = staggerSeconds(el);
    const children = step === null ? 0 : el.children.length;
    const spread = step === null ? entrance : step * Math.max(0, children - 1) + entrance;
    const count2 = countOf(el) === null ? 0 : COUNTS[countOf(el)].seconds;
    const line2 = flagged(el, ATTR.words) ? WORD_STYLES[wordStyleOf(el)].seconds : 0;
    return Math.max(spread, count2, line2);
  }
  function measureClassSpans(slideEl, slide, into, reduced2) {
    for (const el of momentElements(slideEl)) {
      for (const change of classMomentsOf(el, slide.id)) {
        el.classList.add(change.name);
        const span2 = longestMotion(el);
        el.classList.remove(change.name);
        into[change.cue] = Math.max(into[change.cue] ?? 0, declared(span2));
        if (reduced2 && span2 > 0) {
          warn("PAGE_CLASS_NOT_REDUCED", slide.id, change.cue, { value: change.name, attr: ATTR.class });
        }
      }
    }
  }
  function longestMotion(el) {
    const style2 = getComputedStyle(el);
    const lengths = [style2.animationDuration, style2.animationDelay, style2.transitionDuration, style2.transitionDelay]
      .join(",")
      .split(",")
      .map((part) => Number.parseFloat(part) * (part.trim().endsWith("ms") ? 1 / MILLISECONDS : 1))
      .filter((value) => Number.isFinite(value));
    const animation = lengths.length ? Math.max(...lengths) : 0;
    return animation > 0 ? animation : 0;
  }
  function catalog() {
    return [...scenes.values()].map((scene) => ({
      scene: scene.id,
      name: scene.name,
      slides: scene.slides.map((slide) => slide.id),
      cues: Object.fromEntries(scene.slides.map((slide) => [slide.id, cueOrder(slide)])),
      spans: Object.assign({}, ...scene.slides.map(spansOf)),
    }));
  }
  function slideFrom(sceneId, input, at) {
    return {
      id: String(input.id ?? `${sceneId}.${at + 1}`),
      hold: Number(input.hold ?? ATTRS[ATTR.hold].default),
      enter: input.enter ?? ATTRS[ATTR.enter].default,
      owns: (input.owns ?? []).map(String),
      markup: null,
      render: input.render ?? null,
      entered: input.entered ?? null,
      on: input.on ?? {},
    };
  }
  function declare(id, input) {
    const sceneId = String(id);
    scenes.set(sceneId, {
      id: sceneId,
      name: input.name || `${SCENE_WORD} ${sceneId}`,
      slides: (input.slides ?? []).map((slide, at) => slideFrom(sceneId, slide, at)),
    });
  }
  function readMarkup() {
    const claimed = /* @__PURE__ */ new Set();
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
          name: written(wrap, ATTR.name) || void 0,
          slides: templates.map((tpl) => ({ id: written(tpl, ATTR.slide) })),
        });
      }
      const scene = scenes.get(sceneId);
      for (const tpl of templates) {
        const slideId = written(tpl, ATTR.slide);
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
  function check(root, place, inSlide) {
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
  function describedClasses(el, slideId) {
    for (const change of classMomentsOf(el, slideId)) {
      if (classPhraseOf(el, change.local) === null) {
        warn("PAGE_CLASS_UNDESCRIBED", slideId, change.cue, { value: change.name, attr: ATTR.class });
      }
    }
  }
  function staggered(el, slideId) {
    if (staggerSeconds(el) === null || el.children.length) return;
    const moment = momentsOf(el, slideId).find((one) => one.attr === ATTR.in);
    warn("PAGE_STAGGER_EMPTY", slideId, moment ? moment.cue : null, { attr: ATTR.stagger });
  }
  function swapped(el, root, slideId) {
    if (!flagged(el, ATTR.swaps)) return;
    const local = written(el, ATTR.in);
    if (!local) return;
    const leaving = [...root.querySelectorAll(`[${ATTR.out}]`)].filter((other) => written(other, ATTR.out) === local);
    if (leaving.length === 1) return;
    warn("PAGE_SWAP_AMBIGUOUS", slideId, wireId(slideId, local), { value: leaving.length, attr: ATTR.swaps });
  }
  function order(slide) {
    if (!slide.markup) return;
    const declared2 = cueOrder(slide);
    for (const el of momentElements(slide.markup.content)) {
      const moments = momentsOf(el, slide.id);
      const arrival2 = moments.find((one) => one.attr === ATTR.in);
      const departure = moments.find((one) => one.attr === ATTR.out);
      if (!arrival2 || !departure) continue;
      if (declared2.indexOf(departure.cue) <= declared2.indexOf(arrival2.cue)) {
        warn("PAGE_MOMENT_ORDER", slide.id, departure.cue, { attr: ATTR.out, value: departure.local });
      }
    }
  }

  // src/decktalk/runtime/src/katex.ts
  /*! Typesetting, the wait for the typesetter, and the readable text an equation falls back to.
   *
   * KaTeX is the page's own dependency and never the runtime's, so everything here is written for a
   * page that loads it late, loads it from somewhere slow, or never loads it at all. An element that
   * cannot be typeset keeps the text its author wrote inside it, which is why the contract asks for
   * that text in the first place: it is the equation's readable fallback, it is what the transcript
   * prints, and it is what a screen reader reads.
   */
  var KATEX_SECONDS = 5;
  var LOOK_EVERY_MS = 100;
  var ERROR_CLASS = "katex-error";
  var readable = /* @__PURE__ */ new WeakMap();
  var waiting = false;
  function fallback(el) {
    return (readable.get(el) ?? el.textContent ?? "").trim().replace(/\s+/g, " ");
  }
  function wants() {
    if (document.querySelector(`[${ATTR.tex}]`) || document.querySelector('script[src*="katex"]')) return true;
    return [...document.querySelectorAll("template")].some(
      (tpl) => tpl.content.querySelector(`[${ATTR.tex}]`) !== null,
    );
  }
  function typeset(root, slideId = null) {
    const equations = [...root.querySelectorAll(`[${ATTR.tex}]`)];
    if (!equations.length) return;
    if (!window.katex) {
      for (const el of equations) readable.set(el, el.textContent ?? "");
      watch(slideId);
      return;
    }
    for (const el of equations) {
      if (readable.has(el) && el.querySelector(".katex")) continue;
      const tex = written(el, ATTR.tex) ?? "";
      readable.set(el, readable.get(el) ?? el.textContent ?? "");
      window.katex.render(tex, el, { throwOnError: false, displayMode: flagged(el, ATTR.texDisplay) });
      if (el.querySelector(`.${ERROR_CLASS}`)) {
        el.textContent = readable.get(el) ?? "";
        warn("PAGE_KATEX_ERROR", slideId, null, { value: tex, attr: ATTR.tex });
      }
    }
  }
  function watch(slideId) {
    if (waiting) return;
    waiting = true;
    setTimeout(() => {
      if (!window.katex) warn("PAGE_KATEX_MISSING", slideId, null, { attr: ATTR.tex });
    }, KATEX_SECONDS * MILLISECONDS);
  }
  function ready(root) {
    if (!wants() || window.katex) return Promise.resolve();
    return new Promise((resolve) => {
      const started2 = performance.now();
      const look = () => {
        if (window.katex) {
          if (root) typeset(root);
          resolve();
          return;
        }
        if (performance.now() - started2 > KATEX_SECONDS * MILLISECONDS) {
          warn("PAGE_KATEX_MISSING", null, null, { attr: ATTR.tex });
          resolve();
          return;
        }
        setTimeout(look, LOOK_EVERY_MS);
      };
      look();
    });
  }

  // src/decktalk/runtime/src/stage.ts
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
  var STAGE_WIDTH = 1920;
  var STAGE_HEIGHT = 1080;
  var EASE = "cubic-bezier(.2,.7,.2,1)";
  var POP_ENTRY_SCALE = 0.7;
  var POP_OVERSHOOT_AT = 60;
  var FALL_PIXELS = ENTRANCES.rise.liftPixels;
  var SCALE_PROPERTY = "--dt-motion-scale";
  var SPAN_PROPERTY = "--dt-span";
  var CLASS = {
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
  };
  function frames(family, word2) {
    return `dt-${family}-${word2}`;
  }
  function styleClass(family, word2) {
    return frames(family, word2);
  }
  var ENTRANCE_BODY = {
    rise: `from{opacity:0;transform:translateY(${ENTRANCES.rise.liftPixels}px)}to{opacity:1;transform:none}`,
    settle: `from{opacity:0;transform:translateY(${ENTRANCES.settle.liftPixels}px)}to{opacity:1;transform:none}`,
    fade: "from{opacity:0}to{opacity:1}",
    pop: `0%{opacity:0;transform:scale(${POP_ENTRY_SCALE})}${POP_OVERSHOOT_AT}%{opacity:1;transform:scale(${1 + ENTRANCES.pop.overshootPercent / 100})}100%{opacity:1;transform:none}`,
    draw: "from{opacity:1;stroke-dashoffset:1}to{opacity:1;stroke-dashoffset:0}",
    cut: "from{opacity:1}to{opacity:1}",
  };
  var EXIT_BODY = {
    fade: "from{opacity:1}to{opacity:0}",
    fall: `from{opacity:1;transform:none}to{opacity:0;transform:translateY(${FALL_PIXELS}px)}`,
  };
  var WORD_BODY = {
    highlight: `from{opacity:${BACK_OPACITY}}to{opacity:1}`,
    appear: "from{opacity:0}to{opacity:1}",
  };
  var SLIDE_BODY = {
    crossfade: "from{opacity:0}to{opacity:1}",
    cut: "from{opacity:1}to{opacity:1}",
  };
  function rule(family, word2, seconds2, body, extra = "") {
    const name = frames(family, word2);
    return `@keyframes ${name}{${body}}
:where(.${name}){animation-name:${name};animation-duration:var(${SPAN_PROPERTY},${seconds2}s);animation-timing-function:${EASE};animation-fill-mode:both;${extra}}
`;
  }
  var CHROME_CSS = [
    ":where(#dt-hud){position:fixed;left:12px;top:12px;z-index:2147483000;font:14px/1.4 ui-monospace,Menlo,monospace;color:#fff;background:rgba(0,0,0,.6);padding:6px 10px;border-radius:6px;pointer-events:none;white-space:pre}\n",
    ":where(#dt-index){font:16px/1.5 system-ui,sans-serif;max-width:900px;margin:40px auto;padding:0 24px;color:inherit}\n",
    ":where(#dt-index h1){font-size:28px}:where(#dt-index h2){font-size:20px;margin-top:28px}\n",
    ":where(#dt-index a){color:inherit;font-weight:600;text-decoration:underline;text-underline-offset:3px;margin-right:16px}\n",
    ":where(#dt-index code){color:inherit;opacity:.7}\n",
    ":where(#dt-index .dt-slides){display:flex;flex-wrap:wrap;gap:8px 4px}\n",
  ];
  function sheet() {
    const declared2 = Object.values(ATTRS)
      .filter((row) => row.kind === "id")
      .map((row) => `[${row.name}]`)
      .join(",");
    const parts = [
      `:where(${declared2}){display:none}
`,
      `:where(#dt-stage){position:absolute;left:0;top:0;width:${STAGE_WIDTH}px;height:${STAGE_HEIGHT}px;overflow:hidden;transform-origin:0 0}
`,
      ":where(#dt-camera,#dt-pan){position:absolute;inset:0}\n",
      `:where(.${CLASS.slide}){position:absolute;inset:0}
`,
      // The outgoing slide keeps full opacity underneath the incoming one, so the composite of the two
      // is opaque at every moment of a crossfade and never dips towards the page's own background.
      `:where(.${CLASS.leaving}){opacity:1;z-index:0;pointer-events:none}
`,
      `:where(.${CLASS.arriving}){z-index:1}
`,
      `:where(.${CLASS.hidden}){opacity:0}
`,
      `:where(.${CLASS.shown}){opacity:1}
`,
    ];
    for (const [word2, effect] of Object.entries(ENTRANCES)) {
      const extra = word2 === "draw" ? "stroke-dasharray:1;animation-timing-function:linear;" : "";
      parts.push(rule("in", word2, effect.seconds, ENTRANCE_BODY[word2], extra));
    }
    for (const [word2, effect] of Object.entries(EXITS))
      parts.push(rule("out", word2, effect.seconds, EXIT_BODY[word2]));
    for (const [word2, effect] of Object.entries(WORD_STYLES)) {
      parts.push(rule("word", word2, effect.seconds, WORD_BODY[word2]));
    }
    for (const [word2, effect] of Object.entries(SLIDE_ENTRANCES)) {
      parts.push(rule("enter", word2, effect.seconds, SLIDE_BODY[word2]));
    }
    parts.push(
      `:where(.${CLASS.back}){opacity:${BACK_OPACITY};transition:opacity var(${SPAN_PROPERTY},${ATTENTION.back.seconds}s) ${EASE}}
`,
      `:where(.${CLASS.front}){opacity:1;transition:opacity var(${SPAN_PROPERTY},${ATTENTION.front.seconds}s) ${EASE}}
`,
    );
    const fade = frames("in", "fade");
    parts.push(
      `:where(.${CLASS.reduced}) :where(${Object.keys(ENTRANCES)
        .map((word2) => `.${frames("in", word2)}`)
        .join(",")}){animation-name:${fade}}
`,
    );
    parts.push(
      `:where(.${CLASS.frozen}) *{animation-duration:0s!important;animation-delay:0s!important;transition-duration:0s!important}
`,
    );
    parts.push(...CHROME_CSS);
    return parts.join("");
  }
  var stageEl = null;
  var cameraEl = null;
  var panEl = null;
  var hudEl = null;
  var fitScale = 1;
  function style() {
    if (document.getElementById("dt-style")) return;
    const el = document.createElement("style");
    el.id = "dt-style";
    el.textContent = sheet();
    const head = document.head || document.documentElement;
    head.insertBefore(el, head.firstChild);
  }
  function reduced() {
    return window.matchMedia?.("(prefers-reduced-motion: reduce)").matches === true;
  }
  function motionScale2() {
    const written2 = getComputedStyle(document.documentElement).getPropertyValue(SCALE_PROPERTY).trim();
    const scale2 = Number.parseFloat(written2);
    return Number.isFinite(scale2) && scale2 > 0 ? scale2 : 1;
  }
  function span(el, seconds2) {
    el.style.setProperty(SPAN_PROPERTY, `${Math.min(scaled(seconds2, motionScale2()), MEASURABLE_SPAN_SECONDS)}s`);
  }
  function build(hud) {
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
  function fit() {
    if (!stageEl) return;
    fitScale = Math.min(window.innerWidth / STAGE_WIDTH, window.innerHeight / STAGE_HEIGHT);
    const x = (window.innerWidth - STAGE_WIDTH * fitScale) / 2;
    const y = (window.innerHeight - STAGE_HEIGHT * fitScale) / 2;
    stageEl.style.transform = `translate(${x}px, ${y}px) scale(${fitScale})`;
  }
  function pan() {
    return panEl;
  }
  function frame2() {
    return stageEl;
  }
  function scale() {
    return fitScale;
  }
  function freeze() {
    document.documentElement.classList.add(CLASS.frozen);
  }
  function hide() {
    if (stageEl) stageEl.style.display = "none";
    document.body.style.overflow = "auto";
  }
  function say(line2) {
    if (hudEl) hudEl.textContent = line2;
  }
  function slideSeconds(word2) {
    return Math.min(scaled(SLIDE_ENTRANCES[word2].seconds, motionScale2()), MEASURABLE_SPAN_SECONDS);
  }
  function countSeconds(word2) {
    return Math.min(scaled(COUNTS[word2].seconds, motionScale2()), MEASURABLE_SPAN_SECONDS);
  }

  // src/decktalk/runtime/src/telemetry.ts
  /*! The seam between the page and the recorder, and nothing else.
   *
   * A deck in a browser tab should pay nothing for being recordable. The runtime therefore reports
   * what it alone knows, which is when a cue ran and which line the voice reached, through one small
   * interface, and the arrays, the retention caps and the follow-up frame stamps live in the probe the
   * recorder injects. A page opened without the probe keeps the no-op default and allocates nothing.
   *
   * This module holds the interface, the default and the one setter. It has no DOM and no state a
   * reader can see, so a runtime module that wants to record something imports `recorder()` and calls
   * it without ever knowing whether anyone is listening.
   */
  var NOBODY = {
    cue() {},
    words() {},
  };
  var current = NOBODY;
  function recorder() {
    return current;
  }
  function setRecorder(next) {
    current = next ?? NOBODY;
  }

  // src/decktalk/runtime/src/text.ts
  /*! The two effects that rewrite an element's own text: the count up and the line on the voice.
   *
   * Both are here rather than in `reveal.ts` because both take the author's text apart and put it back
   * together, and neither is a thing a frozen frame can see: a number growing towards its own value is
   * a slow change `verify` reads as an onset, and one word lighting up is far under the change floor,
   * so the line reports itself through the telemetry seam instead of being measured in pixels.
   */
  function key(word2) {
    return word2.toLowerCase().replace(/[^a-z\d]/g, "");
  }
  var TEXT_MAX = 24;
  var NEAR_THE_CUE_SECONDS = 1.5;
  var LAST_NUMBER = /(\d[\d,]*(?:\.\d+)?)(?!.*\d)/;
  var FIRST_NUMBER = /(\d[\d,]*(?:\.\d+)?)/;
  var EASE_POWER = 3;
  var THOUSANDS = /\B(?=(\d{3})+(?!\d))/g;
  function eased(part) {
    return 1 - (1 - part) ** EASE_POWER;
  }
  function count(el, text, first, seconds2, scene) {
    const found = text.match(first ? FIRST_NUMBER : LAST_NUMBER);
    if (!found || scene.frozen) {
      el.textContent = text;
      return;
    }
    const written2 = found[1];
    const target = Number.parseFloat(written2.replace(/,/g, ""));
    const decimals = (written2.split(".")[1] ?? "").length;
    const grouped = written2.includes(",");
    const at = found.index;
    const draw = (value) => {
      let body = value.toFixed(decimals);
      if (grouped) body = body.replace(THOUSANDS, ",");
      el.textContent = text.slice(0, at) + body + text.slice(at + written2.length);
    };
    const started2 = performance.now();
    const length = seconds2 * MILLISECONDS;
    let live = true;
    scene.onLeave(() => {
      live = false;
    });
    const tick = (stamp) => {
      if (!live) return;
      const part = Math.min(1, (stamp - started2) / length);
      draw(target * eased(part));
      if (part < 1) requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
  }
  function line(el, text, style2, scene) {
    const spoken = scene.words;
    if (scene.frozen || !spoken || !spoken.length) return;
    const parts = text.split(/(\s+)/);
    const keys = parts.map(key).filter(Boolean);
    if (!keys.length) return;
    if (style2 === "appear" && keys.length > APPEAR_WORDS_MAX) {
      warn("PAGE_APPEAR_TOO_LONG", scene.slide, scene.cue, { value: keys.length });
    }
    const cueAt = now();
    const start2 = firstRun(keys, spoken, cueAt);
    if (start2 < 0) {
      warn("PAGE_WORDS_NOT_FOUND", scene.slide, scene.cue, { value: text.slice(0, TEXT_MAX) });
      return;
    }
    const lead = scaled(WORD_STYLES[style2].seconds, motionScale2());
    const due = [];
    el.textContent = "";
    let taken = 0;
    for (const part of parts) {
      if (!key(part)) {
        el.appendChild(document.createTextNode(part));
        continue;
      }
      const word2 = document.createElement("span");
      word2.className = CLASS.word;
      word2.textContent = part;
      word2.style.opacity = String(resting(style2));
      el.appendChild(word2);
      due.push(spoken[start2 + taken].at - lead);
      taken += 1;
    }
    let live = true;
    let told = false;
    scene.onLeave(() => {
      live = false;
    });
    const tick = () => {
      if (!live) return;
      const at = now();
      let waiting2 = false;
      const words = el.querySelectorAll(`.${CLASS.word}`);
      words.forEach((word2, index2) => {
        if (word2.classList.contains(CLASS.shown)) return;
        if (at >= due[index2]) {
          word2.classList.add(CLASS.shown, styleClass("word", style2));
          if (!told) {
            told = true;
            recorder().words({
              text: text.slice(0, TEXT_MAX),
              cueAt: round(cueAt),
              runAt: spoken[start2].at,
              count: keys.length,
              firstOn: round(at),
            });
          }
        } else waiting2 = true;
      });
      if (waiting2) requestAnimationFrame(tick);
    };
    tick();
  }
  function resting(style2) {
    return style2 === "highlight" ? BACK_OPACITY : 0;
  }
  function firstRun(keys, spoken, cueAt) {
    let found = -1;
    for (let at = 0; at + keys.length <= spoken.length; at += 1) {
      if (keys.some((one, offset) => spoken[at + offset].key !== one)) continue;
      found = at;
      if (spoken[at].at >= cueAt - NEAR_THE_CUE_SECONDS) break;
    }
    return found;
  }

  // src/decktalk/runtime/src/reveal.ts
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
  var SENTENCE = {
    [ATTR.in]: (subject2) => `${subject2} appears.`,
    [ATTR.back]: (subject2) => `${subject2} steps back.`,
    [ATTR.front]: (subject2) => `${subject2} comes to the front.`,
    [ATTR.out]: (subject2) => `${subject2} leaves.`,
  };
  function prepare(el, slide, playing) {
    const actions = /* @__PURE__ */ new Map();
    const stops = [];
    const held = heldSwaps(el);
    const mounted = {
      el,
      slide,
      actions,
      stop: () => {
        for (const one of stops) one();
        stops.length = 0;
      },
    };
    const onLeave = (fn) => stops.push(fn);
    for (const target of momentElements(el)) {
      const element = target;
      const moments = momentsOf(element, slide.id);
      const arrival2 = moments.find((one) => one.attr === ATTR.in);
      if (arrival2 && !playing.frozen) hide2(element);
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
    for (const container of el.querySelectorAll(`[${ATTR.steps}]`)) steps(container, slide, actions);
    if (playing.frozen) {
      for (const [cue, list] of actions) {
        if (playing.held?.has(cue)) continue;
        for (const action of list) action.run();
      }
    }
    return mounted;
  }
  function push(actions, cue, action) {
    const list = actions.get(cue) ?? [];
    list.push(action);
    actions.set(cue, list);
  }
  function hide2(el) {
    const step = staggerSeconds(el);
    if (step === null) {
      el.classList.add(CLASS.hidden);
      return;
    }
    for (const child of el.children) child.classList.add(CLASS.hidden);
  }
  function heldSwaps(root) {
    const held = /* @__PURE__ */ new Map();
    for (const el of root.querySelectorAll(`[${ATTR.swaps}]`)) {
      const local = written(el, ATTR.in);
      if (!local) continue;
      const leaving = [...momentElements(root)].filter((other) => written(other, ATTR.out) === local);
      if (leaving.length !== 1) continue;
      held.set(leaving[0], entranceSeconds(el));
    }
    return held;
  }
  function actionFor(el, attr, cue, slide, playing, held, onLeave) {
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
      const style2 = exitOf(el);
      const wait = held.get(el) ?? 0;
      return {
        attr,
        run: () => {
          const go = () => {
            span(el, EXITS[style2].seconds);
            el.classList.add(styleClass("out", style2));
          };
          if (wait > 0 && !playing.frozen) schedule(now() + wait, "reveal", cue, go);
          else go();
        },
        line: written(el, ATTR.describeOut) || subject(el, attr),
      };
    }
    return arrival(el, cue, slide, playing, onLeave);
  }
  function arrival(el, cue, slide, playing, onLeave) {
    const style2 = entranceOf(el);
    const seconds2 = entranceSeconds(el);
    const step = staggerSeconds(el);
    const text = fallback(el);
    return {
      attr: ATTR.in,
      run: () => {
        if (step === null) show(el, style2, seconds2);
        else {
          [...el.children].forEach((child, at) => {
            const one = child;
            one.style.animationDelay = `${step * at}s`;
            show(one, style2, seconds2);
          });
        }
        const counted = countOf(el);
        const scene = { frozen: playing.frozen, words: playing.words, slide: slide.id, cue, onLeave };
        if (counted !== null) count(el, text, counted === "first", countSeconds(counted), scene);
        if (flagged(el, ATTR.words)) line(el, text, wordStyleOf(el), scene);
      },
      line: arrivalLine(el, text),
    };
  }
  function show(el, style2, seconds2) {
    const entrance = styleClass("in", style2);
    el.classList.remove(CLASS.hidden);
    span(el, seconds2);
    el.classList.add(CLASS.shown, entrance);
    el.addEventListener("animationend", (event) => {
      if (event.target === el) el.classList.remove(entrance);
    });
  }
  function steps(container, slide, actions) {
    const cued = [...container.children].filter((child) => written(child, ATTR.in));
    cued.forEach((child, at) => {
      const cue = wireId(slide.id, written(child, ATTR.in));
      push(actions, cue, {
        attr: ATTR.front,
        run: () => {
          for (const earlier of cued.slice(0, at)) {
            span(earlier, ATTENTION.back.seconds);
            earlier.classList.remove(CLASS.front);
            earlier.classList.add(CLASS.back);
          }
          child.classList.remove(CLASS.back);
        },
        line: null,
      });
    });
  }
  function arrivalLine(el, text) {
    const describe = written(el, ATTR.describe);
    if (describe === "") return null;
    const parts = [describe === null ? null : SENTENCE[ATTR.in]?.(describe), text || null].filter(Boolean);
    return parts.length ? parts.join(" ") : null;
  }
  function subject(el, attr) {
    const describe = written(el, ATTR.describe);
    if (!describe) return null;
    return SENTENCE[attr]?.(describe) ?? null;
  }
  function fire(mounted, cue) {
    const list = mounted?.actions.get(cue);
    if (!list?.length) return null;
    for (const action of list) action.run();
    const arrived = list.some((action) => action.attr === ATTR.in && action.line !== null);
    const lines = [];
    for (const action of list) {
      if (action.line === null) continue;
      if (arrived && action.attr === ATTR.back) continue;
      if (!lines.includes(action.line)) lines.push(action.line);
    }
    return lines.length ? lines.join(" ") : null;
  }

  // src/decktalk/runtime/src/modes.ts
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
  var SCENE = "scene";
  var SLIDE = "slide";
  var CUES = "cues";
  var WORDS = "words";
  var T0 = "t0";
  var SPEED = "speed";
  var HUD = "hud";
  var SIGNAL = "signal";
  var ON = "1";
  var SLOWEST = 0.05;
  var CUE_TIMES = "/__decktalk/cue-times.json";
  var PREVIEW_STEP_SECONDS = 1;
  var DONE = "1";
  var state = {
    mode: "index",
    scene: null,
    slide: null,
    mounted: null,
    cues: [],
    fired: [],
    catalog: [],
    words: null,
  };
  var params = new URLSearchParams(location.search);
  var frozen = params.has(SLIDE);
  var signalled = params.get(T0) === SIGNAL;
  var origin2 = signalled ? 0 : Number.parseFloat(params.get(T0) ?? "") || 0;
  var speed = Math.max(SLOWEST, Number.parseFloat(params.get(SPEED) ?? "") || 1);
  function waitsForSignal() {
    return signalled;
  }
  function isFrozen() {
    return frozen;
  }
  function query() {
    return params;
  }
  function buildSlide(scene, slide) {
    const el = document.createElement("div");
    el.className = CLASS.slide;
    if (slide.markup) el.appendChild(slide.markup.content.cloneNode(true));
    else if (slide.render) {
      try {
        el.innerHTML = slide.render({ scene, slide, frozen });
      } catch (err) {
        warn("PAGE_RENDER_THREW", slide.id, null, { value: String(err) });
      }
    }
    return el;
  }
  function mount(scene, slide, held, at) {
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
    pan().appendChild(el);
    if (old) retire(el, old, entrance);
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
  function retire(incoming, outgoing, entrance) {
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
    setTimeout(go, (slideSeconds(entrance) + FRAME_STEP_MS / MILLISECONDS) * MILLISECONDS);
  }
  function context(cue, slideId, at) {
    return { id: cue, at: round(at), frozen, slideId };
  }
  function fireCue(cue, due) {
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
  function parseCues(raw) {
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
  function parseWords(raw) {
    return raw
      .split(",")
      .map((item) => {
        const mark = item.lastIndexOf("@");
        return { key: key(item.slice(0, mark)), at: Number.parseFloat(item.slice(mark + 1)) };
      })
      .filter((word2) => word2.key && !Number.isNaN(word2.at));
  }
  function play(scene, cues, mode) {
    state.mode = mode;
    state.scene = scene;
    state.cues = cues;
    const mountAt = /* @__PURE__ */ new Map();
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
      warn("PAGE_NO_OWNER", scene.id, cues.length ? cues[0].id : null);
      return;
    }
    if (mode === "cue") {
      for (const slide of scene.slides) {
        if (!mountAt.has(slide)) warn("PAGE_SLIDE_UNUSED", slide.id);
      }
    }
    const last = ordered[ordered.length - 1][0];
    mount(scene, ordered[0][0], null, origin2);
    if (ordered[0][0] === last) document.body.dataset.done = DONE;
    for (const [slide] of ordered.slice(1)) {
      schedule(origin2 + mountAt.get(slide), "mount", slide.id, () => {
        mount(scene, slide, null, null);
        if (slide === last) document.body.dataset.done = DONE;
      });
    }
    for (const cue of cues) {
      schedule(origin2 + cue.at, "cue", cue.id, () => fireCue(cue.id, origin2 + cue.at));
    }
  }
  function preview(scene) {
    state.mode = "preview";
    state.scene = scene;
    let at = origin2;
    const last = scene.slides[scene.slides.length - 1];
    scene.slides.forEach((slide, index2) => {
      const mountAt = at;
      const cues = cueOrder(slide);
      const start2 = () => {
        mount(scene, slide, null, index2 === 0 ? origin2 : null);
        cues.forEach((cue, order2) => {
          schedule(mountAt + ((order2 + 1) * PREVIEW_STEP_SECONDS) / speed, "cue", cue, () =>
            fireCue(cue, mountAt + ((order2 + 1) * PREVIEW_STEP_SECONDS) / speed),
          );
        });
        if (slide === last) document.body.dataset.done = DONE;
      };
      if (index2 === 0) start2();
      else schedule(mountAt, "mount", slide.id, start2);
      at += Math.max(slide.hold, (cues.length + 1) * PREVIEW_STEP_SECONDS) / speed;
    });
  }
  async function resolvedCues(sceneId) {
    let document_;
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
    const cues = (rows[0].cues ?? [])
      .filter((row) => typeof row.cue === "string" && typeof row.at === "number")
      .map((row) => ({ id: row.cue, at: row.at / speed }));
    return cues.length ? cues.sort((a, b) => a.at - b.at) : null;
  }
  function stop(slideId, held) {
    const found = findSlide(slideId);
    if (!found) {
      index(`unknown slide ${slideId}`);
      return;
    }
    state.mode = "freeze";
    state.scene = found.scene;
    freeze();
    const order2 = cueOrder(found.slide);
    const firing = held(order2, found.slide.id);
    mount(found.scene, found.slide, new Set(order2.slice(firing.length)), 0);
    state.fired = [...firing];
    document.body.dataset.done = DONE;
  }
  function index(note) {
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
  function heading(title, note) {
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
  function link(href, text) {
    const anchor = document.createElement("a");
    anchor.href = href;
    anchor.textContent = text;
    return anchor;
  }
  function begin(probe) {
    style();
    readMarkup();
    state.catalog = catalog();
    if (!signalled) start();
    build(params.get(HUD) === ON);
    const wordsRaw = params.get(WORDS);
    state.words = wordsRaw ? parseWords(wordsRaw) : null;
    const cuesRaw = params.get(CUES);
    const cues = cuesRaw ? parseCues(cuesRaw) : [];
    const chosen = params.get(SCENE);
    let listing = false;
    let waiting2 = Promise.resolve();
    if (frozen) {
      stop(params.get(SLIDE) ?? "", (order2, slide) => probe?.freezeCues?.(order2, slide, warn) ?? order2);
    } else if (chosen !== null || cues.length) {
      const scene = chosen !== null ? (all().get(chosen) ?? null) : (ownerOf(cues[0].id)?.scene ?? null);
      if (!scene) index(`unknown scene ${chosen ?? ""}`);
      else if (cues.length) play(scene, cues, "cue");
      else
        waiting2 = resolvedCues(scene.id).then((resolved) =>
          resolved ? play(scene, resolved, "preview") : preview(scene),
        );
    } else {
      listing = true;
    }
    run(params.get(HUD) === ON ? report : void 0);
    if (!listing) return waiting2.then(() => ready(pan()));
    return waiting2
      .then(() => ready(pan()))
      .then(() => {
        measure(probe);
        index();
      });
  }
  function report(seconds2) {
    const where = `scene ${state.scene?.id ?? "-"} · slide ${state.slide?.id ?? "-"}`;
    say(`${state.mode} · ${where} · t ${Math.max(0, seconds2).toFixed(2)}s`);
  }
  function measure(probe) {
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
      origin: frame2(),
      scale: scale(),
      build: buildSlide,
    });
  }

  // src/decktalk/runtime/src/index.ts
  /*! decktalk-runtime.js: the page contract, as one script a deck includes and nothing else.
   *
   * Include it and declare a scene in markup. Nothing here needs JavaScript:
   *
   *   <script src="decktalk-runtime.js"><\/script>
   *   <div data-scene="pitch" data-name="How often">
   *     <template data-slide="pitch.listing">
   *       <h1>Value still listed</h1>
   *       <p data-in="by-hour" data-describe="the hourly figure">by hour, through first pitch</p>
   *       <p data-in="share" data-describe="the share of games">one game in ten</p>
   *     </template>
   *   </div>
   *
   * An element has four moments, each the local name of a cue that `cues.json` gives a second to: it
   * arrives, it steps back, it comes to the front and it leaves. A page that wants behaviour of its
   * own adds it beside the markup, and a page that would rather build a slide in script gives that
   * slide a render function instead of a template.
   *
   * This module is the facade and nothing else. It reads the probe's telemetry sink once, hands it to
   * the seam, and publishes `window.DeckTalk` for an author and `window.__decktalk` for whoever is
   * reading the page back. Nothing imports it, which is what keeps every other module testable.
   */
  var VERSION = "0.4.1";
  var GATE_SECONDS = 5;
  var gates = [];
  function injected() {
    return window.__dtprobe ?? null;
  }
  function gatesSettled() {
    if (!gates.length) return Promise.resolve();
    let done = false;
    const all_ = Promise.all(
      gates.map((gate) =>
        Promise.resolve(gate).then(null, (err) => warn("PAGE_WAIT_REJECTED", null, null, { value: String(err) })),
      ),
    ).then(() => {
      done = true;
    });
    const limit = new Promise((resolve) => {
      setTimeout(() => {
        if (!done) warn("PAGE_WAIT_UNSETTLED");
        resolve();
      }, GATE_SECONDS * MILLISECONDS);
    });
    return Promise.race([all_, limit]);
  }
  var running = false;
  function boot() {
    if (running) return;
    running = true;
    setRecorder(injected()?.recorder);
    setMotionScale(motionScale2());
    const fonts = Promise.resolve(document.fonts ? document.fonts.ready : null).catch(() => null);
    const ready2 = fonts
      .then(gatesSettled)
      .then(() => begin(injected()))
      .then(() => true);
    view.ready = ready2;
  }
  var DeckTalk = {
    version: VERSION,
    /** Declare one scene from script, for a deck that would rather build its slides than write them. */
    scene(id, input) {
      declare(id, input);
      return DeckTalk;
    },
    /** Run a function at one cue, wherever in the deck that cue belongs. */
    on(cue, fn) {
      on(cue, fn);
      return DeckTalk;
    },
    /** Read the page and start it, which a page that loads the runtime late may need to call itself. */
    start: boot,
    /** Start the narration clock, which is what the recorder sends once its cover has come off. */
    startClock: start,
    /** Hold readiness until a promise of the page's own has settled. */
    waitFor(promise) {
      gates.push(Promise.resolve(promise));
      return DeckTalk;
    },
    /** Every scene the page declared, which a deck reads to walk its own slides. */
    get scenes() {
      return all();
    },
    /** The page's own query, which a deck reads for anything the contract does not answer for it. */
    get params() {
      return query();
    },
    findSlide,
    buildSlide,
  };
  var view = {
    version: VERSION,
    ready: null,
    get mode() {
      return state.mode;
    },
    get scene() {
      return state.scene?.id ?? null;
    },
    get slide() {
      return state.slide?.id ?? null;
    },
    get cues() {
      return state.cues;
    },
    get fired() {
      return state.fired;
    },
    get warnings() {
      return warnings();
    },
    get catalog() {
      return state.catalog;
    },
    get frozen() {
      return isFrozen();
    },
    get signalled() {
      return waitsForSignal();
    },
    now,
    started,
  };
  window.DeckTalk = DeckTalk;
  window.__decktalk = view;
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
  else queueMicrotask(boot);
})();
