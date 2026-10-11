/*! decktalk-runtime 0.5.0 */
"use strict";
(() => {
  // src/decktalk/runtime/src/contract.ts
  /*! The page contract: every attribute an author writes, every code the page reports, and the words a
   * URL and a report are spelled with.
   *
   * An element on a slide has four moments and one value type. It arrives, it steps back, it comes to
   * the front, and it leaves, and each of those is the cue name of a cue. Everything else is either
   * how a moment looks, which is a closed word this file owns, or what a moment means, which is a
   * sentence for the transcript. A cue's second is never written on the page, because the cue stage
   * resolves it from the phrase `cues.json` pairs with it.
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
  var DUE_SLACK_SECONDS = 1e-6;
  var FRAME_STEP_MS = 40;
  var MEASURABLE_SPAN_SECONDS = 0.5;
  var PLAYABLE_SPAN_SECONDS = 0.46;
  var APPEAR_WORDS_MAX = 8;
  var BACK_OPACITY = 0.45;
  var ENTRANCES = {
    rise: { seconds: 0.32, liftPixels: 10, overshootPercent: 0 },
    settle: { seconds: 0.28, liftPixels: 4, overshootPercent: 0 },
    fade: { seconds: 0.24, liftPixels: 0, overshootPercent: 0 },
    pop: { seconds: 0.2, liftPixels: 0, overshootPercent: 4 },
    draw: { seconds: 0.44, liftPixels: 0, overshootPercent: 0 },
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
    last: { seconds: 0.44 },
    first: { seconds: 0.44 },
  };
  var ATTENTION = {
    back: { seconds: 0.28 },
    front: { seconds: 0.2 },
  };
  var MOTION_EASES = {
    firm: { curve: [0.25, 0.8, 0.5, 0.95], arrival: "edge" },
    glide: { curve: [0, 0, 1, 1], arrival: "edge" },
    settle: { curve: [0.16, 1, 0.3, 1], arrival: "rest" },
    spring: { curve: null, arrival: "rest" },
  };
  var SPRING_SETTLE_RADIANS = 9.23;
  var CODES = {
    PAGE_ATTR_UNKNOWN:
      "{attr} is not an attribute this contract defines, so check its spelling against the attribute table.",
    PAGE_BAD_VALUE: "{attr}={value} is not one of {allowed}, so write one of those instead.",
    PAGE_MOMENT_UNKNOWN:
      "{attr}={value} names a cue slide {slide} does not own, so declare it on the slide or correct the name.",
    PAGE_MOMENT_ORDER:
      "The exit {value} is at or before the entrance on the same element, so give the exit a later cue.",
    PAGE_CUE_UNKNOWN: "The cue {cue} is not one this deck declares, so remove it or name it in a moment attribute.",
    PAGE_NO_OWNER: "No slide owns the cue {cue}, so name it in a moment attribute or list it in data-owns.",
    PAGE_SCENE_EMPTY: "The scene {slide} declares no slide, so add a template to it or remove the scene.",
    PAGE_SLIDE_NO_ID: "A template carries no data-slide, so give it the id a section names.",
    PAGE_SLIDE_DOUBLED: "Two templates claim the slide id {slide}, so every moment local to it has two owners.",
    PAGE_SLIDE_UNUSED: "Slide {slide} is never shown, so nothing it declares reaches a recording.",
    PAGE_TEMPLATE_IGNORED: "A template inside slide {slide} declares no slide of its own, so nothing ever mounts it.",
    PAGE_WORDS_NOT_FOUND: "The line {value} is not among the spoken words, so it cannot be shown word by word.",
    PAGE_KATEX_MISSING: "KaTeX is not loaded, so {attr} is left as the author wrote it.",
    PAGE_KATEX_ERROR: "KaTeX refused {value}, so the element shows its readable fallback text.",
    PAGE_FREEZE_CUE_UNKNOWN: "The cue {cue} is not one of slide {slide}'s cues, so the freeze stopped at nothing.",
    PAGE_RENDER_THREW:
      "The render handler for slide {slide} threw {value}, so the slide is drawn as its markup stands.",
    PAGE_ENTER_THREW: "The enter handler for slide {slide} threw {value}, so the slide arrived without it.",
    PAGE_SLIDE_HANDLER_THREW:
      "The handler slide {slide} registered for cue {cue} threw {value}, so that moment did nothing.",
    PAGE_HANDLER_THREW: "The handler the deck registered for cue {cue} threw {value}, so that moment did nothing.",
    PAGE_MOTION_UNDECLARED:
      "{value} keeps moving past the half second a cue may still move and declares no motion, loop or region, so verify cannot mask it.",
    PAGE_MOTION_CUE_UNKNOWN:
      "The motion or pane clip names the cue {cue}, which no slide declares, so it never starts or never lands.",
    PAGE_REALTIME_MEDIA:
      "Slide {slide} holds a {value} element, which plays at its own rate and not on the stepped clock, so play it in a pane with data-clip.",
    PAGE_CLIP_UNREADABLE:
      "The pane clip {value} on slide {slide} could not be read as a picture, so the pane plays nothing.",
    PAGE_FRAME_THREW: "A frame hook the deck registered threw {value}, so it stopped drawing from that frame on.",
    PAGE_WAIT_REJECTED: "A promise the page waited for rejected with {value}, so the page was drawn without it.",
    PAGE_WAIT_UNSETTLED: "A promise the page waited for never settled, so the page was drawn without it.",
    PAGE_CLASS_UNDESCRIBED:
      "data-class names {value} with no description for it in data-describe-class, so the transcript loses it.",
    PAGE_CLASS_NOT_REDUCED:
      "The class {value} still animates under reduced motion, so the page's own stylesheet must honour it.",
    PAGE_SWAP_AMBIGUOUS:
      "data-swaps at cue {cue} found {value} elements leaving, so name the one it replaces with a shared cue.",
    PAGE_PREVIEW_AMBIGUOUS:
      "Two sections name the scene {slide}, so a preview cannot tell which one's cue times to play.",
    PAGE_APPEAR_TOO_LONG: "data-words=appear is on a line of {value} words, so shorten it or use highlight instead.",
    PAGE_STAGGER_EMPTY: "The container at cue {cue} staggers no children, so give it children or remove the attribute.",
    PAGE_SPOTLIGHT_EMPTY:
      "The spotlight on slide {slide} has no child with data-in, so give its children cues or remove the attribute.",
    PAGE_MOTION_UNFINISHED:
      "The script motion on {value} from {from} still changes after it arrives at {cue}, so move its arrival later or end the change by it.",
    PAGE_MOTION_OVERRUN:
      "The motion at cue {cue} is still playing {value} s later, at the frame the next cue is read from.",
    PAGE_STAGGER_OVERRUN:
      "The stagger at cue {cue} runs {value} s in all, which passes the half second a cue may still move.",
    PAGE_THIN_DRAW: "The stroke at cue {cue} sweeps {value} percent of the frame, which is under the change floor.",
    PAGE_NO_DESCRIPTION:
      "The element at cue {cue} has no text and no data-tex, so give it data-describe or an empty one.",
    PAGE_SWAP_APART:
      "The swap at cue {cue} lands {value} percent over the box it replaces, so the two may read as unrelated.",
    PAGE_CDN_ASSET: "The page loaded {value} from another origin, so the film depends on a host it does not own.",
    PAGE_CHANGE_UNDECLARED:
      "{value} changed the picture with no cue, motion, loop or frame hook to explain it, so declare it or draw it from a cue.",
    RECORD_BLACK:
      "The frame at cue {cue} is black, so nothing the slide declares was on screen when the voice arrived.",
    RECORD_TRUNCATED: "The recording ends {value} s before the narration does, so the last cues are not in it.",
  };
  var READ_FROM_THE_PAGE = null;
  var IN_SECONDS_RANGE = { min: 0.12, max: 0.44, step: 0.04, unit: "seconds" };
  var STAGGER_RANGE = { min: 0.04, max: 0.2, step: 0.04, unit: "seconds" };
  var LOOP_SECONDS_RANGE = { min: 0.2, max: 60, step: 0.04, unit: "seconds" };
  var PREVIEW_SECONDS_RANGE = { min: 1, max: 60, step: 1, unit: "seconds" };
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
      kind: "description",
      values: [],
      default: null,
      range: null,
      code: null,
      span: 0,
      affects: ["transcript", "catalog"],
      summary:
        "The subject of the reveal, which the runtime gives a verb per moment. An empty description means decorative.",
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
      summary: "Adds a class at a cue, written as cue:class pairs, for the page's own stylesheet.",
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
      summary: "What each class change means, as cue:description pairs separated by a vertical bar.",
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
    "data-spotlight": {
      name: "data-spotlight",
      on: ["container"],
      kind: "flag",
      values: [],
      default: null,
      range: null,
      code: "PAGE_SPOTLIGHT_EMPTY",
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
      kind: "description",
      values: [],
      default: null,
      range: null,
      code: null,
      span: 0,
      affects: ["transcript"],
      summary: "Overrides the departure sentence when leaving means something of its own.",
    },
    "data-motion": {
      name: "data-motion",
      on: ["element"],
      kind: "keyframes",
      values: [],
      default: null,
      range: null,
      code: "PAGE_MOTION_UNDECLARED",
      span: 0,
      affects: ["motion", "catalog"],
      summary:
        "The element moves through these @keyframes from one cue to another, masked from every reveal check and landing on its cues.",
    },
    "data-motion-from": {
      name: "data-motion-from",
      on: ["element"],
      kind: "cue",
      values: [],
      default: null,
      range: null,
      code: "PAGE_MOTION_CUE_UNKNOWN",
      span: 0,
      affects: ["cue-order", "motion", "catalog"],
      summary: "The motion departs at this cue.",
    },
    "data-motion-to": {
      name: "data-motion-to",
      on: ["element"],
      kind: "cue",
      values: [],
      default: null,
      range: null,
      code: "PAGE_MOTION_CUE_UNKNOWN",
      span: 0,
      affects: ["cue-order", "motion", "catalog"],
      summary: "The motion arrives at this cue, so its length is the two cues' seconds apart.",
    },
    "data-motion-ease": {
      name: "data-motion-ease",
      on: ["element"],
      kind: "word",
      values: ["firm", "glide", "settle", "spring"],
      default: "firm",
      range: null,
      code: "PAGE_BAD_VALUE",
      span: 0,
      affects: ["motion", "style"],
      summary: "How the motion moves. Settle and spring ease to rest, so their arrival cue must opt out of verify.",
    },
    "data-loop": {
      name: "data-loop",
      on: ["element"],
      kind: "keyframes",
      values: [],
      default: null,
      range: null,
      code: "PAGE_MOTION_UNDECLARED",
      span: 0,
      affects: ["motion", "catalog"],
      summary:
        "The element turns through these @keyframes for as long as its slide is on screen, masked and never a landing.",
    },
    "data-loop-seconds": {
      name: "data-loop-seconds",
      on: ["element"],
      kind: "seconds",
      values: [],
      default: "1",
      range: LOOP_SECONDS_RANGE,
      code: "PAGE_BAD_VALUE",
      span: 0,
      affects: ["motion"],
      summary: "How long one turn of the loop takes.",
    },
    "data-clip": {
      name: "data-clip",
      on: ["element"],
      kind: "path",
      values: [],
      default: null,
      range: null,
      code: "PAGE_CLIP_UNREADABLE",
      span: 0,
      affects: ["motion", "catalog"],
      summary:
        "The element is a pane that plays this project file, a clip with its own sound, drawn under the page and masked from every reveal check.",
    },
    "data-clip-from": {
      name: "data-clip-from",
      on: ["element"],
      kind: "cue",
      values: [],
      default: null,
      range: null,
      code: "PAGE_MOTION_CUE_UNKNOWN",
      span: 0,
      affects: ["cue-order", "motion", "catalog"],
      summary: "The pane clip starts playing, picture and sound, at this cue, and shows its first frame before it.",
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
      kind: "description",
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
    "data-preview-seconds": {
      name: "data-preview-seconds",
      on: ["slide"],
      kind: "seconds",
      values: [],
      default: "8",
      range: PREVIEW_SECONDS_RANGE,
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
      summary: "Cue names only a handler serves, which no moment attribute mentions.",
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
  var CUE_MARK = ":";
  var TIME_MARK = "@";
  var LIST_SEPARATOR = ",";
  var T0_SIGNAL = "signal";
  var DONE_ATTR = "data-done";
  var ENGINE_PATH = "/__decktalk/";
  var PREVIEW_CUE_TIMES = `${ENGINE_PATH}cue-times.json`;
  var CLIP_FRAMES = `${ENGINE_PATH}clip/`;
  var MOTION_SCALE_PROPERTY = "--dt-motion-scale";
  function cueId(slide, local) {
    return `${slide}${CUE_MARK}${local}`;
  }
  function pairs(value) {
    const out = [];
    for (const part of value.split(PAIR_SEPARATOR)) {
      const at = part.indexOf(PAIR_MARK);
      if (at < 0) continue;
      const cue = part.slice(0, at).trim();
      const rest = part.slice(at + 1).trim();
      if (!cue || !rest) continue;
      out.push({ cue, value: rest });
    }
    return out;
  }
  function message(code, fields = {}) {
    return CODES[code].replace(/\{(\w+)\}/g, (whole, key2) => (key2 in fields ? String(fields[key2]) : whole));
  }
  function known(name) {
    return Object.hasOwn(ATTRS, name);
  }
  function refuse(name, value) {
    const row2 = ATTRS[name];
    if (row2.values.length) {
      return row2.values.includes(value) ? null : `one of ${row2.values.join(", ")}`;
    }
    if (row2.range) {
      const seconds2 = Number(value);
      if (!Number.isFinite(seconds2)) return `a number of seconds`;
      const { min, max, step } = row2.range;
      if (seconds2 < min || seconds2 > max) return `${min} to ${max} seconds`;
      return Math.abs(Math.round(seconds2 / step) * step - seconds2) < 1e-9 ? null : `a multiple of ${step} seconds`;
    }
    return value.trim() ? null : "a value";
  }
  function staggerSpan(step, children, entrance) {
    return children > 0 ? step * (children - 1) + entrance : 0;
  }
  function scaled(span2, scale2) {
    return Math.min(span2 * scale2, PLAYABLE_SPAN_SECONDS);
  }

  // src/decktalk/runtime/src/canvas.ts
  /*! The canvas a deck is drawn on, and the one stylesheet that renders every closed word.
   *
   * A DeckTalk page draws onto one canvas of a fixed size, named below and scaled to whatever window
   * it is opened in, so an element measured on a laptop is at the pixel a recording will put it at.
   * This module owns that canvas, the fit, the heads-up display, and the stylesheet.
   *
   * Every rule below is generated from the registry, so a style word's length lives once. Each
   * selector sits inside `:where()`, which gives it no specificity at all, and the sheet is prepended
   * to the head, so a page rule of equal weight wins on document order. An author's stylesheet is
   * therefore always able to override the runtime without reaching for `!important`.
   */
  var CANVAS_WIDTH = 1920;
  var CANVAS_HEIGHT = 1080;
  var EASE = "cubic-bezier(.2,.7,.2,1)";
  var POP_ENTRY_SCALE = 0.7;
  var POP_OVERSHOOT_AT = 60;
  var FALL_PIXELS = ENTRANCES.rise.liftPixels;
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
    /** The class a page drawing the caption band alone puts on the root, which clears everything else. */
    captioning: "dt-captioning",
    /** One caption in the band, which carries `data-voice` and `data-turn` for a page to place it by. */
    caption: "dt-caption",
    /** One line of a caption, which carries `data-sound` when it says what is heard rather than said. */
    captionLine: "dt-caption-line",
    /** A slide that plays a pane clip, every picture of which the sheet draws on a layer of its own. */
    paned: "dt-paned",
  };
  var CAPTIONS_ID = "dt-captions";
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
  var CAPTION_CSS = [
    `:where(#${CAPTIONS_ID}){position:absolute;left:0;right:0;bottom:0;padding:0 160px 72px;pointer-events:none}
`,
    `:where(.${CLASS.caption}){margin:0;text-align:center;font:600 46px/1.3 system-ui,-apple-system,"Segoe UI",sans-serif;color:#fff;text-shadow:0 2px 6px rgba(0,0,0,.85),0 0 18px rgba(0,0,0,.6)}
`,
    `:where(.${CLASS.captionLine}){display:block}
`,
    `:where(.${CLASS.captioning}),:where(.${CLASS.captioning}) body{background:transparent!important}
`,
    `:where(.${CLASS.captioning}) body{visibility:hidden!important}
`,
    `:where(.${CLASS.captioning} #${CAPTIONS_ID}){visibility:visible!important}
`,
  ];
  function sheet() {
    const declared2 = Object.values(ATTRS)
      .filter((row2) => row2.kind === "id")
      .map((row2) => `[${row2.name}]`)
      .join(",");
    const parts = [
      `:where(${declared2}){display:none}
`,
      `:where(#dt-canvas){position:absolute;left:0;top:0;width:${CANVAS_WIDTH}px;height:${CANVAS_HEIGHT}px;overflow:hidden;transform-origin:0 0}
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
      // A pane clip's picture is a compositor layer of its own, and Chromium lays a picture overlapping
      // it out with it, so a picture that first shows beside it draws the pictures around it again with
      // nothing changed. Every picture of a slide that plays one is a layer of its own, so none is.
      `:where(.${CLASS.paned}) img{will-change:transform}
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
    parts.push(...CHROME_CSS, ...CAPTION_CSS);
    return parts.join("");
  }
  var canvasEl = null;
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
  function motionScale() {
    const written2 = getComputedStyle(document.documentElement).getPropertyValue(MOTION_SCALE_PROPERTY).trim();
    const scale2 = Number.parseFloat(written2);
    return Number.isFinite(scale2) && scale2 > 0 ? scale2 : 1;
  }
  function span(el, seconds2) {
    el.style.setProperty(SPAN_PROPERTY, `${scaled(seconds2, motionScale())}s`);
  }
  function build(hud) {
    if (canvasEl) return;
    canvasEl = document.getElementById("dt-canvas") ?? document.createElement("div");
    canvasEl.id = "dt-canvas";
    if (!canvasEl.isConnected) document.body.appendChild(canvasEl);
    cameraEl = document.createElement("div");
    cameraEl.id = "dt-camera";
    panEl = document.createElement("div");
    panEl.id = "dt-pan";
    cameraEl.appendChild(panEl);
    canvasEl.appendChild(cameraEl);
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
    if (!canvasEl) return;
    fitScale = Math.min(window.innerWidth / CANVAS_WIDTH, window.innerHeight / CANVAS_HEIGHT);
    const x = (window.innerWidth - CANVAS_WIDTH * fitScale) / 2;
    const y = (window.innerHeight - CANVAS_HEIGHT * fitScale) / 2;
    canvasEl.style.transform = `translate(${x}px, ${y}px) scale(${fitScale})`;
  }
  function pan() {
    return panEl;
  }
  function frame() {
    return canvasEl;
  }
  function scale() {
    return fitScale;
  }
  function freeze() {
    document.documentElement.classList.add(CLASS.frozen);
  }
  function hide() {
    if (canvasEl) canvasEl.style.display = "none";
    document.body.style.overflow = "auto";
  }
  function say(line2) {
    if (hudEl) hudEl.textContent = line2;
  }
  function slideSeconds(word2) {
    return scaled(SLIDE_ENTRANCES[word2].seconds, motionScale());
  }
  function countSeconds(word2) {
    return scaled(COUNTS[word2].seconds, motionScale());
  }
  var bandEl = null;
  function captioning() {
    document.documentElement.classList.add(CLASS.captioning);
    if (bandEl || !canvasEl) return;
    bandEl = document.createElement("div");
    bandEl.id = CAPTIONS_ID;
    canvasEl.appendChild(bandEl);
  }
  function showCaption(caption) {
    captioning();
    const band = bandEl;
    band.replaceChildren();
    if (caption) {
      const p = document.createElement("p");
      p.className = CLASS.caption;
      if (caption.voice !== null) p.dataset.voice = caption.voice;
      if (caption.turn !== null) p.dataset.turn = String(caption.turn);
      const [first, after] = caption.spoken;
      caption.lines.forEach((text, index2) => {
        const line2 = document.createElement("span");
        line2.className = CLASS.captionLine;
        line2.textContent = text;
        if (index2 < first || index2 >= after) line2.dataset.sound = "";
        p.appendChild(line2);
      });
      band.appendChild(p);
    }
    const fonts = Promise.resolve(document.fonts ? document.fonts.ready : null).catch(() => null);
    return fonts.then(
      () => new Promise((done2) => requestAnimationFrame(() => requestAnimationFrame(() => done2(true)))),
    );
  }

  // src/decktalk/runtime/src/clock.ts
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
  var MOUNT_FIRST = 0;
  var AFTER_THE_MOUNT = 1;
  var driver = "realtime";
  var origin = null;
  var current = 0;
  var frame2 = 0;
  var queue = [];
  var hooks = [];
  var observer = null;
  var looping = false;
  var waiting = null;
  function start() {
    if (origin === null) origin = performance.now();
  }
  function started() {
    return origin !== null;
  }
  function driven() {
    return driver;
  }
  function now() {
    if (origin === null) return Number.NEGATIVE_INFINITY;
    return driver === "stepped" ? current : (performance.now() - origin) / MILLISECONDS;
  }
  function frameAt() {
    if (origin === null) return null;
    return driver === "stepped" ? round(current) : round((frame2 - origin) / MILLISECONDS);
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
  function every(hook) {
    hooks.push(hook);
  }
  function observe(next) {
    observer = next;
  }
  function drain(seconds2) {
    let ran = false;
    while (queue.length && queue[0].at <= seconds2 + DUE_SLACK_SECONDS) {
      const task = queue.shift();
      observer?.running(task.at);
      task.run();
      observer?.ran(task.at);
      ran = true;
    }
    return ran;
  }
  function draw(seconds2) {
    const drew = hooks.length > 0;
    hooks = hooks.filter((hook) => hook(seconds2) !== false);
    return drew;
  }
  function earliest(...seconds2) {
    let first = null;
    for (const one of seconds2) if (one !== null && (first === null || one < first)) first = one;
    return first;
  }
  function stepTo(seconds2) {
    const first = origin === null;
    if (first) {
      driver = "stepped";
      origin = 0;
      if (waiting !== null) cancelAnimationFrame(waiting);
      waiting = null;
    }
    if (driver !== "stepped") return { due: null, changed: true, stillUntil: seconds2 };
    current = seconds2;
    const ran = drain(seconds2);
    const drew = draw(seconds2);
    const moved = observer?.stepped(seconds2) ?? false;
    const stillUntil2 = hooks.length > 0 ? seconds2 : earliest(due(), observer?.still(seconds2) ?? null);
    return { due: due(), changed: first || ran || drew || moved, stillUntil: stillUntil2 };
  }
  function due() {
    return queue.length ? queue[0].at : null;
  }
  function run(onFrame) {
    if (looping) return;
    looping = true;
    const tick = (at) => {
      waiting = null;
      if (driver === "stepped") return;
      frame2 = at;
      const seconds2 = now();
      drain(seconds2);
      if (origin !== null) draw(seconds2);
      onFrame?.(seconds2);
      waiting = requestAnimationFrame(tick);
    };
    waiting = requestAnimationFrame(tick);
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
    const row2 = {
      code,
      message: message(code, filled),
      slide,
      cue,
      attr: fields.attr === void 0 ? null : String(fields.attr),
    };
    if (recorded.some((seen) => same(seen, row2))) return;
    recorded.push(row2);
    console.warn(`${CONSOLE_PREFIX}: ${row2.message}`);
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
   * `pitch.listing` and its cue id is `pitch.listing:expand`.
   *
   * Ownership is declared and never inferred. A slide owns exactly the cues its moment attributes
   * name plus the cue names it lists, which is what lets a cue id carry any characters an author
   * likes, with no prefix rule guessing which slide a cue belongs to.
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
    previewSeconds: named("preview-seconds"),
    owns: named("owns"),
    enter: named("enter"),
    in: named("in"),
    back: named("back"),
    front: named("front"),
    out: named("out"),
    inStyle: named("in-style"),
    inSeconds: named("in-seconds"),
    outStyle: named("out-style"),
    spotlight: named("spotlight"),
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
    motion: named("motion"),
    motionFrom: named("motion-from"),
    motionTo: named("motion-to"),
    motionEase: named("motion-ease"),
    loop: named("loop"),
    loopSeconds: named("loop-seconds"),
    clip: named("clip"),
    clipFrom: named("clip-from"),
  };
  var MOVING_SELECTOR = `[${ATTR.motion}],[${ATTR.loop}],[${ATTR.clip}]`;
  var REALTIME_MEDIA = ["video", "audio"];
  var SCENE_WORD = "Scene";
  var scenes = /* @__PURE__ */ new Map();
  var handlers = /* @__PURE__ */ new Map();
  var motionScale2 = 1;
  function setMotionScale(scale2) {
    motionScale2 = scale2;
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
  function previewSeconds(el) {
    return seconds(el, ATTR.previewSeconds) ?? Number(ATTRS[ATTR.previewSeconds].default);
  }
  function momentsOf(el, slideId) {
    const out = [];
    for (const attr of MOMENTS) {
      const local = written(el, attr);
      if (local) out.push({ attr, local, cue: cueId(slideId, local) });
    }
    return out;
  }
  function classChangesOf(el, slideId) {
    const value = written(el, ATTR.class);
    if (!value) return [];
    return pairs(value).map((pair) => ({ cue: cueId(slideId, pair.cue), local: pair.cue, name: pair.value }));
  }
  function classDescriptionOf(el, local) {
    const value = written(el, ATTR.describeClass);
    if (value === null) return null;
    const found = pairs(value).find((pair) => pair.cue === local);
    return found ? found.value : null;
  }
  function motionOf(el, slideId) {
    const clip = written(el, ATTR.clip);
    if (clip) {
      const from = written(el, ATTR.clipFrom);
      return {
        kind: "clip",
        name: clip,
        from: from ? cueId(slideId, from) : null,
        to: null,
        ease: null,
        seconds: null,
      };
    }
    const name = written(el, ATTR.motion);
    if (name) {
      const from = written(el, ATTR.motionFrom);
      const to = written(el, ATTR.motionTo);
      return {
        kind: "motion",
        name,
        from: from ? cueId(slideId, from) : null,
        to: to ? cueId(slideId, to) : null,
        ease: easeOf(el),
        seconds: null,
      };
    }
    const loop = written(el, ATTR.loop);
    if (!loop) return null;
    return { kind: "loop", name: loop, from: null, to: null, ease: null, seconds: loopSeconds(el) };
  }
  function motionsOf(slide) {
    if (!slide.markup) return [];
    return [...slide.markup.content.querySelectorAll(MOVING_SELECTOR)]
      .map((el) => motionOf(el, slide.id))
      .filter((row2) => row2 !== null);
  }
  function easeOf(el) {
    return word(el, ATTR.motionEase, MOTION_EASES);
  }
  function loopSeconds(el) {
    return seconds(el, ATTR.loopSeconds) ?? Number(ATTRS[ATTR.loopSeconds].default);
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
        for (const change of classChangesOf(el, slide.id)) add(change.cue);
      }
      for (const row2 of motionsOf(slide)) {
        if (row2.from) add(row2.from);
        if (row2.to) add(row2.to);
      }
    }
    for (const local of Object.keys(slide.on)) add(cueId(slide.id, local));
    for (const local of slide.owns) add(cueId(slide.id, local));
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
      for (const change of classChangesOf(el, slide.id)) widen(change.cue, 0);
    }
    return spans;
  }
  function declared(span2) {
    return Number((span2 * motionScale2).toFixed(SECOND_DIGITS));
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
    const spread = step === null ? entrance : staggerSpan(step, el.children.length, entrance);
    const count2 = countOf(el) === null ? 0 : COUNTS[countOf(el)].seconds;
    const line2 = flagged(el, ATTR.words) ? WORD_STYLES[wordStyleOf(el)].seconds : 0;
    return Math.max(spread, count2, line2);
  }
  function measureClassSpans(slideEl, slide, into, reduced2) {
    for (const el of momentElements(slideEl)) {
      for (const change of classChangesOf(el, slide.id)) {
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
      motions: Object.fromEntries(scene.slides.map((slide) => [slide.id, motionsOf(slide)])),
    }));
  }
  function slideFrom(sceneId, input, at) {
    return {
      id: String(input.id ?? `${sceneId}.${at + 1}`),
      previewSeconds: Number(input.previewSeconds ?? ATTRS[ATTR.previewSeconds].default),
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
        slide.previewSeconds = previewSeconds(tpl);
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
          warn("PAGE_ATTR_UNKNOWN", place, null, { attr: attribute.name });
          continue;
        }
        const row2 = ATTRS[attribute.name];
        if (!row2.values.length && !row2.range) continue;
        const value = attribute.value.trim();
        if (!value && row2.default !== null) continue;
        const why = refuse(attribute.name, value);
        if (why) {
          warn("PAGE_BAD_VALUE", place, null, { attr: attribute.name, value: attribute.value, allowed: why });
        }
      }
      if (REALTIME_MEDIA.includes(el.localName)) warn("PAGE_REALTIME_MEDIA", place, null, { value: el.localName });
      if (!inSlide) {
        for (const attr of MOMENTS) {
          const local = written(el, attr);
          if (local) warn("PAGE_MOMENT_UNKNOWN", place, null, { attr, value: local });
        }
        continue;
      }
      describedClasses(el, place);
      staggered(el, place);
      spotlit(el, place);
      swapped(el, root, place);
    }
  }
  function describedClasses(el, slideId) {
    for (const change of classChangesOf(el, slideId)) {
      if (classDescriptionOf(el, change.local) === null) {
        warn("PAGE_CLASS_UNDESCRIBED", slideId, change.cue, { value: change.name, attr: ATTR.class });
      }
    }
  }
  function staggered(el, slideId) {
    if (staggerSeconds(el) === null || el.children.length) return;
    const moment = momentsOf(el, slideId).find((one) => one.attr === ATTR.in);
    warn("PAGE_STAGGER_EMPTY", slideId, moment ? moment.cue : null, { attr: ATTR.stagger });
  }
  function spotlit(el, slideId) {
    if (!flagged(el, ATTR.spotlight)) return;
    if ([...el.children].some((child) => written(child, ATTR.in))) return;
    warn("PAGE_SPOTLIGHT_EMPTY", slideId, null, { attr: ATTR.spotlight });
  }
  function swapped(el, root, slideId) {
    if (!flagged(el, ATTR.swaps)) return;
    const local = written(el, ATTR.in);
    if (!local) return;
    const leaving = [...root.querySelectorAll(`[${ATTR.out}]`)].filter((other) => written(other, ATTR.out) === local);
    if (leaving.length === 1) return;
    warn("PAGE_SWAP_AMBIGUOUS", slideId, cueId(slideId, local), { value: leaving.length, attr: ATTR.swaps });
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
  var waiting2 = false;
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
    if (waiting2) return;
    waiting2 = true;
    setTimeout(() => {
      if (!window.katex) warn("PAGE_KATEX_MISSING", slideId, null, { attr: ATTR.tex });
    }, KATEX_SECONDS * MILLISECONDS);
  }
  function ready(root) {
    if (!wants() || window.katex) return Promise.resolve();
    return new Promise((resolve2) => {
      const started2 = performance.now();
      const look = () => {
        if (window.katex) {
          if (root) typeset(root);
          resolve2();
          return;
        }
        if (performance.now() - started2 > KATEX_SECONDS * MILLISECONDS) {
          warn("PAGE_KATEX_MISSING", null, null, { attr: ATTR.tex });
          resolve2();
          return;
        }
        setTimeout(look, LOOK_EVERY_MS);
      };
      look();
    });
  }

  // src/decktalk/runtime/src/motion.ts
  /*! Declared motion: a thing that moves from one cue to another, a loop, a region a frame hook draws in, and a pane clip.
   *
   * A moment is a reveal of half a second or less, measured as a step in the picture. A motion is the
   * other noun: it can run for any length, its length is the seconds between two cues and never a
   * number in markup, and it is declared on the element it moves, so verify can mask it from every
   * reveal and measure its own departure and arrival instead. A loop turns for as long as its slide is
   * on screen and is masked and never a landing. A frame hook draws inside a region it names. A pane clip
   * plays a project file inside its element from one cue, frame by frame on the section clock, with the
   * page drawn over it, and is masked from that cue and measured for its departure on it.
   *
   * Anything that keeps moving past the half second a cue may still move and declares none of these is
   * undeclared motion, which is exactly what makes verify's control dishonest, so the page reports it.
   */
  var SPRING_POINTS = 32;
  var SPRING_DIGITS = 4;
  var BERNSTEIN = 3;
  var HALVINGS = 50;
  function bezier(x1, y1, x2, y2) {
    const at = (a, b, s) => BERNSTEIN * (1 - s) ** 2 * s * a + BERNSTEIN * (1 - s) * s * s * b + s ** BERNSTEIN;
    return (u) => {
      if (u <= 0) return 0;
      if (u >= 1) return 1;
      let lo = 0;
      let hi = 1;
      let s = u;
      for (let step = 0; step < HALVINGS; step += 1) {
        s = (lo + hi) / 2;
        if (at(x1, x2, s) < u) lo = s;
        else hi = s;
      }
      return at(y1, y2, s);
    };
  }
  function spring(u) {
    const reach = (x) => 1 - (1 + x) * Math.exp(-x);
    return u <= 0 ? 0 : u >= 1 ? 1 : reach(SPRING_SETTLE_RADIANS * u) / reach(SPRING_SETTLE_RADIANS);
  }
  function eased(ease, u) {
    const curve = MOTION_EASES[ease].curve;
    return curve ? bezier(...curve)(u) : spring(u);
  }
  function cssTiming(ease) {
    const curve = MOTION_EASES[ease].curve;
    if (curve) return `cubic-bezier(${curve.join(", ")})`;
    const points = Array.from({ length: SPRING_POINTS + 1 }, (_, k) =>
      spring(k / SPRING_POINTS).toFixed(SPRING_DIGITS),
    );
    return `linear(${points.join(", ")})`;
  }
  var lanes = [];
  function standsAt(box, rest) {
    return box.left === rest.x && box.top === rest.y && box.width === rest.w && box.height === rest.h;
  }
  var swept = /* @__PURE__ */ new Map();
  function resolve(region2) {
    const found = typeof region2 === "string" ? document.querySelector(region2) : region2;
    return found?.isConnected ? found : null;
  }
  function regionOf(one) {
    return resolve(one.region);
  }
  function alive(one, seconds2) {
    if (one.kind === "clip") return one.from !== null && seconds2 >= one.from - DUE_SLACK_SECONDS;
    if (one.from !== null && one.to !== null)
      return seconds2 >= one.from - DUE_SLACK_SECONDS && seconds2 <= one.to + DUE_SLACK_SECONDS;
    return true;
  }
  function sample(seconds2) {
    for (const one of lanes) {
      if (!alive(one, seconds2)) continue;
      const el = regionOf(one);
      if (el) note(one.id, el, seconds2);
    }
    for (const one of revealed) {
      if (seconds2 < one.at - DUE_SLACK_SECONDS || seconds2 > one.at + MEASURABLE_SPAN_SECONDS + DUE_SLACK_SECONDS)
        continue;
      for (const el of one.els) if (el.isConnected) note(one.id, el, seconds2);
    }
  }
  function note(id, el, seconds2) {
    const box = el.getBoundingClientRect();
    if (!box.width || !box.height) return;
    const seen = swept.get(id);
    if (!seen) {
      swept.set(id, {
        left: box.left,
        top: box.top,
        right: box.right,
        bottom: box.bottom,
        width: box.width,
        height: box.height,
        first: seconds2,
        last: seconds2,
        rest: { x: box.left, y: box.top, w: box.width, h: box.height },
        moved: null,
      });
      return;
    }
    if (seen.moved === null && !standsAt(box, seen.rest)) seen.moved = seconds2;
    seen.left = Math.min(seen.left, box.left);
    seen.top = Math.min(seen.top, box.top);
    seen.right = Math.max(seen.right, box.right);
    seen.bottom = Math.max(seen.bottom, box.bottom);
    seen.last = seconds2;
  }
  function moving(seconds2) {
    return lanes.some((one) => one.kind !== "region" && alive(one, seconds2) && regionOf(one) !== null);
  }
  function stillUntil(seconds2) {
    let next = null;
    for (const one of lanes) {
      if (one.kind === "region") continue;
      if (alive(one, seconds2) && regionOf(one) !== null) return seconds2;
      if (one.from !== null && one.from - DUE_SLACK_SECONDS > seconds2)
        next = earliest(next, one.from - DUE_SLACK_SECONDS);
    }
    return next;
  }
  function report() {
    return lanes.map((one) => {
      const landings = [];
      if (one.cues.from && one.from !== null)
        landings.push({ cue: one.cues.from, kind: "depart", at: round(one.from) });
      if (one.cues.to && one.to !== null) landings.push({ cue: one.cues.to, kind: "arrive", at: round(one.to) });
      const seen = swept.get(one.id);
      return { ...row(one.id, one.kind, seen), landings, clip: one.clip ?? null };
    });
  }
  function reveals() {
    return revealed.map((one) => {
      const seen = swept.get(one.id);
      return {
        ...row(one.id, "reveal", seen),
        departs: seen ? round(one.at) : null,
        arrives: seen ? round(one.at + MEASURABLE_SPAN_SECONDS) : null,
        landings: [{ cue: one.cue, kind: "depart", at: round(one.at) }],
        clip: null,
      };
    });
  }
  function row(id, kind, seen) {
    const box = seen
      ? {
          x: Math.round(seen.left),
          y: Math.round(seen.top),
          w: Math.round(seen.right) - Math.round(seen.left),
          h: Math.round(seen.bottom) - Math.round(seen.top),
        }
      : null;
    return {
      id,
      kind,
      box,
      size: seen ? { w: Math.round(seen.width), h: Math.round(seen.height) } : null,
      departs: seen ? round(seen.first) : null,
      arrives: seen ? round(seen.last) : null,
      rest: seen ? seen.rest : null,
      moved: seen && seen.moved !== null ? round(seen.moved) : null,
    };
  }
  var revealed = [];
  function reveal(el, cue, at) {
    const one = revealed.find((row2) => row2.cue === cue);
    if (one) {
      if (!one.els.includes(el)) one.els.push(el);
      return;
    }
    revealed.push({ id: `reveal-${revealed.length + 1}`, cue, at, els: [el] });
  }
  function lane(next) {
    lanes.push({ ...next, id: `${next.kind}-${lanes.length + 1}` });
  }
  function covered(target) {
    if (!target) return false;
    if (target.closest(MOVING_SELECTOR)) return true;
    return lanes.some((one) => {
      const region2 = typeof one.region === "string" ? document.querySelector(one.region) : one.region;
      return region2?.contains(target) ?? false;
    });
  }
  function describe(el) {
    if (!el) return "An animation";
    const id = el.id ? `#${el.id}` : "";
    const classes = [...el.classList].map((name) => `.${name}`).join("");
    return `<${el.localName}${id}${classes}>`;
  }
  function laidOut(el) {
    return [el, ...el.querySelectorAll("*")]
      .flatMap((one) => [...one.getClientRects()])
      .filter((rect) => rect.width > 0 && rect.height > 0);
  }
  function enclosing(rects) {
    const left = Math.max(0, Math.min(...rects.map((rect) => rect.left)));
    const top = Math.max(0, Math.min(...rects.map((rect) => rect.top)));
    const right = Math.min(window.innerWidth, Math.max(...rects.map((rect) => rect.right)));
    const bottom = Math.min(window.innerHeight, Math.max(...rects.map((rect) => rect.bottom)));
    if (!rects.length || right <= left || bottom <= top) return null;
    return { x: left, y: top, w: right - left, h: bottom - top };
  }
  function undeclaredChange(nodes) {
    const changed = [
      ...new Set(
        nodes
          .map((node) => (node instanceof Element ? node : node.parentElement))
          .filter((el) => !!el?.isConnected && !covered(el)),
      ),
    ];
    if (!changed.length) return null;
    const rects = changed.map(laidOut);
    return {
      what: describe(changed[0] ?? null),
      box: rects.some((one) => !one.length) ? null : enclosing(rects.flat()),
    };
  }
  function born(animation, slideId) {
    if (animation.endSeconds <= PLAYABLE_SPAN_SECONDS || covered(animation.target)) return;
    warn("PAGE_MOTION_UNDECLARED", slideId, null, { value: describe(animation.target) });
  }
  function undeclaredIn(slideEl, slideId) {
    for (const el of slideEl.querySelectorAll("*")) {
      if (el.closest(MOVING_SELECTOR)) continue;
      const style2 = getComputedStyle(el);
      const turns = style2.animationIterationCount.split(",").map((part) => part.trim());
      const lengths = style2.animationDuration.split(",").map((part) => Number.parseFloat(part) || 0);
      const longest = Math.max(
        ...lengths.map((length, at) => (turns[at % turns.length] === "infinite" && length > 0 ? Infinity : length)),
      );
      if (longest > PLAYABLE_SPAN_SECONDS) born({ target: el, endSeconds: longest }, slideId);
    }
  }
  function wire(el, slide, timing, on2) {
    for (const target of el.querySelectorAll(MOVING_SELECTOR)) {
      const moving2 = target;
      const clip = written(moving2, ATTR.clip);
      if (clip) {
        el.classList.add(CLASS.paned);
        paneClip(moving2, clip, slide, timing, on2);
        continue;
      }
      const loop = written(moving2, ATTR.loop);
      if (timing.frozen) {
        if (loop) continue;
        const departs = written(moving2, ATTR.motionFrom);
        const name2 = written(moving2, ATTR.motion);
        if (departs)
          on2(`${slide.id}:${departs}`, () => (moving2.style.animation = `${name2} 1s linear 0s 1 normal both`));
        continue;
      }
      if (loop) {
        moving2.style.animation = `${loop} ${loopSeconds(moving2)}s linear 0s infinite`;
        lane({ kind: "loop", region: moving2, from: null, to: null, cues: { from: null, to: null } });
        continue;
      }
      const name = written(moving2, ATTR.motion);
      const fromLocal = written(moving2, ATTR.motionFrom);
      const toLocal = written(moving2, ATTR.motionTo);
      const from = fromLocal ? `${slide.id}:${fromLocal}` : null;
      const to = toLocal ? `${slide.id}:${toLocal}` : null;
      const start2 = from === null ? null : timing.at(from);
      const end = to === null ? null : timing.at(to);
      if (from === null || to === null || start2 === null || end === null || end <= start2) {
        warn("PAGE_MOTION_CUE_UNKNOWN", slide.id, to ?? from, { attr: ATTR.motion, value: name });
        continue;
      }
      lane({ kind: "motion", region: moving2, from: start2, to: end, cues: { from, to } });
      const ease = easeOf(moving2);
      on2(from, () => {
        moving2.style.animation = `${name} ${end - start2}s ${cssTiming(ease)} 0s 1 normal both`;
      });
    }
  }
  var PANE_FILL = "display:block;width:100%;height:100%;object-fit:fill;pointer-events:none;will-change:transform";
  function clipSecond(seconds2, start2) {
    return round(Math.max(0, seconds2 - start2));
  }
  function clipFrameUrl(clip, second) {
    return `${CLIP_FRAMES}${second}/${clip.split("/").map(encodeURIComponent).join("/")}`;
  }
  function paneClip(el, clip, slide, timing, on2) {
    const local = written(el, ATTR.clipFrom);
    const from = local ? cueId(slide.id, local) : null;
    const start2 = from === null ? null : timing.at(from);
    if (!timing.frozen && (from === null || start2 === null)) {
      warn("PAGE_MOTION_CUE_UNKNOWN", slide.id, from, { attr: ATTR.clip, value: clip });
    }
    if (!timing.frozen && !timing.recorded) {
      const video = document.createElement("video");
      video.src = `/${clip}`;
      video.preload = "auto";
      video.playsInline = true;
      video.style.cssText = PANE_FILL;
      el.prepend(video);
      if (from !== null && start2 !== null) {
        on2(from, () => {
          video.currentTime = Math.max(0, now() - start2);
          video.play().catch(() => {
            video.muted = true;
            return video.play().catch(() => void 0);
          });
        });
      }
      return;
    }
    const img = document.createElement("img");
    img.alt = "";
    img.style.cssText = PANE_FILL;
    if (timing.recorded) {
      img.addEventListener("error", () => warn("PAGE_CLIP_UNREADABLE", slide.id, from, { value: clip }), {
        once: true,
      });
    }
    el.prepend(img);
    const show2 = (second) => {
      const url = clipFrameUrl(clip, second);
      if (img.getAttribute("src") !== url) img.src = url;
    };
    show2(0);
    if (timing.frozen || from === null || start2 === null) return;
    lane({ kind: "clip", region: el, from: start2, to: null, cues: { from, to: null }, clip });
    schedule(start2, "reveal", clip, () =>
      every((seconds2) => {
        if (!img.isConnected) return false;
        show2(clipSecond(seconds2, start2));
        return true;
      }),
    );
  }
  function picture(el) {
    const canvases = el instanceof HTMLCanvasElement ? [el] : [...el.querySelectorAll("canvas")];
    const drawn = canvases.map((canvas) => {
      try {
        return canvas.toDataURL();
      } catch {
        return "";
      }
    });
    return [el.outerHTML, ...drawn].join("\n");
  }
  function arrive(region2, motion, seconds2) {
    const el = resolve(region2);
    const later = el ? (motion.draw(1, seconds2 + MEASURABLE_SPAN_SECONDS), picture(el)) : null;
    motion.draw(1, seconds2);
    if (el && later !== null && later !== picture(el)) {
      warn("PAGE_MOTION_UNFINISHED", null, motion.to, { value: describe(el), from: motion.from });
    }
  }
  function scripted(region2, motion, at) {
    const start2 = at(motion.from);
    const end = at(motion.to);
    if (start2 === null || end === null || end <= start2) {
      warn("PAGE_MOTION_CUE_UNKNOWN", null, start2 === null ? motion.from : motion.to);
      return;
    }
    const ease = motion.ease ?? ATTRS[ATTR.motionEase].default;
    lane({ kind: "motion", region: region2, from: start2, to: end, cues: { from: motion.from, to: motion.to } });
    every((seconds2) => {
      if (seconds2 < start2) return true;
      const u = Math.min(1, (seconds2 - start2) / (end - start2));
      if (u < 1) {
        motion.draw(eased(ease, u), seconds2);
        return true;
      }
      arrive(region2, motion, seconds2);
      return false;
    });
  }
  function region(where) {
    if (where === void 0) {
      warn("PAGE_MOTION_UNDECLARED", null, null, { value: "A frame hook" });
      return;
    }
    lane({ kind: "region", region: where, from: null, to: null, cues: { from: null, to: null } });
  }
  function ending(animation) {
    const end = animation.effect?.getComputedTiming().endTime;
    return typeof end === "number" ? end / MILLISECONDS : Number.POSITIVE_INFINITY;
  }

  // src/decktalk/runtime/src/telemetry.ts
  /*! The seam between the page and the recorder, and nothing else.
   *
   * A deck in a browser tab should pay nothing for being recordable. The runtime therefore reports
   * what it alone knows, which is when a cue ran and which line the voice reached, through one small
   * interface, and the arrays, the retention caps and the follow-up frame stamps live in the probe the
   * recorder injects. A page opened without the probe keeps the no-op default and allocates nothing.
   *
   * This module holds the interface, the default and the one setter, and the types of everything that
   * crosses the seam: the view the runtime publishes, the probe the recorder injects and the catalog
   * the two of them fill. The runtime and the probe are two bundles that meet only on `window`, so these
   * types are the one declaration both compile against. It has no DOM and no state a reader can see,
   * so a runtime module that wants to record something imports `recorder()` and calls it without ever
   * knowing whether anyone is listening.
   */
  var NOBODY = {
    cue() {},
    words() {},
  };
  var current2 = NOBODY;
  function recorder() {
    return current2;
  }
  function setRecorder(next) {
    current2 = next ?? NOBODY;
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
  function entries(raw) {
    return raw
      .split(LIST_SEPARATOR)
      .map((item) => {
        const mark = item.lastIndexOf(TIME_MARK);
        return { name: item.slice(0, mark), at: Number.parseFloat(item.slice(mark + 1)) };
      })
      .filter((entry) => entry.name && !Number.isNaN(entry.at));
  }
  function parseWords(raw, turnsRaw) {
    const turns = turnsRaw ? entries(turnsRaw) : [];
    return entries(raw)
      .map((entry) => {
        let turn = 0;
        while (turn < turns.length && turns[turn].at <= entry.at) turn += 1;
        const voice = turn ? turns[turn - 1].name : null;
        return { key: key(entry.name), at: entry.at, voice, turn: turn || null };
      })
      .filter((word2) => word2.key);
  }
  var TEXT_MAX = 24;
  var NEAR_THE_CUE_SECONDS = 1.5;
  var LAST_NUMBER = /(\d[\d,]*(?:\.\d+)?)(?!.*\d)/;
  var FIRST_NUMBER = /(\d[\d,]*(?:\.\d+)?)/;
  var EASE_POWER = 3;
  var THOUSANDS = /\B(?=(\d{3})+(?!\d))/g;
  function eased2(part) {
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
    const draw2 = (value) => {
      let body = value.toFixed(decimals);
      if (grouped) body = body.replace(THOUSANDS, ",");
      el.textContent = text.slice(0, at) + body + text.slice(at + written2.length);
    };
    const started2 = now();
    let live = true;
    scene.onLeave(() => {
      live = false;
    });
    every((at2) => {
      if (!live) return false;
      const part = Math.min(1, (at2 - started2) / seconds2);
      draw2(target * eased2(part));
      return part < 1;
    });
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
    const cueAt2 = now();
    const start2 = firstRun(keys, spoken, cueAt2);
    if (start2 < 0) {
      warn("PAGE_WORDS_NOT_FOUND", scene.slide, scene.cue, { value: text.slice(0, TEXT_MAX) });
      return;
    }
    const lead = scaled(WORD_STYLES[style2].seconds, motionScale());
    const due2 = [];
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
      due2.push(spoken[start2 + taken].at - lead);
      taken += 1;
    }
    let live = true;
    let told = false;
    scene.onLeave(() => {
      live = false;
    });
    const show2 = (word2) => {
      if (!live) return;
      word2.classList.add(CLASS.shown, styleClass("word", style2));
      if (told) return;
      told = true;
      recorder().words({
        text: text.slice(0, TEXT_MAX),
        cueAt: round(cueAt2),
        spokenAt: spoken[start2].at,
        count: keys.length,
        firstShown: round(now()),
      });
    };
    el.querySelectorAll(`.${CLASS.word}`).forEach((word2, index2) => {
      const at = due2[index2];
      if (at <= cueAt2) show2(word2);
      else schedule(at, "reveal", scene.cue ?? text.slice(0, TEXT_MAX), () => show2(word2));
    });
  }
  function resting(style2) {
    return style2 === "highlight" ? BACK_OPACITY : 0;
  }
  function firstRun(keys, spoken, cueAt2) {
    let found = -1;
    for (let at = 0; at + keys.length <= spoken.length; at += 1) {
      if (keys.some((one, offset) => spoken[at + offset].key !== one)) continue;
      found = at;
      if (spoken[at].at >= cueAt2 - NEAR_THE_CUE_SECONDS) break;
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
      said: /* @__PURE__ */ new Set(),
    };
    const onLeave = (fn) => stops.push(fn);
    const noted = (element, cue, action) =>
      playing.recorded && !playing.frozen
        ? {
            ...action,
            run: () => {
              action.run();
              reveal(element, cue, playing.at(cue) ?? now());
            },
          }
        : action;
    for (const target of momentElements(el)) {
      const element = target;
      const moments = momentsOf(element, slide.id);
      if (moments.some((one) => one.attr === ATTR.in)) hide2(element);
      for (const moment of moments) {
        const action = actionFor(element, moment.attr, moment.cue, slide, playing, held, onLeave);
        push(actions, moment.cue, noted(element, moment.cue, action));
      }
      for (const change of classChangesOf(element, slide.id)) {
        const action = {
          attr: ATTR.class,
          run: () => element.classList.add(change.name),
          line: classDescriptionOf(element, change.local) || null,
        };
        push(actions, change.cue, noted(element, change.cue, action));
      }
    }
    for (const container of el.querySelectorAll(`[${ATTR.spotlight}]`)) spotlight(container, slide, actions);
    wire(el, slide, { frozen: playing.frozen, recorded: playing.recorded, at: playing.at }, (cue, run2) =>
      push(actions, cue, { attr: ATTR.motion, run: run2, line: null }),
    );
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
  var OPENING_ENTRANCE = "cut";
  function arrival(el, cue, slide, playing, onLeave) {
    const opens = playing.opens.has(cue) && !playing.frozen;
    const style2 = opens ? OPENING_ENTRANCE : entranceOf(el);
    const seconds2 = opens ? 0 : entranceSeconds(el);
    const step = staggerSeconds(el);
    const text = fallback(el);
    return {
      attr: ATTR.in,
      run: () => {
        if (step === null) show(el, style2, seconds2, cue, playing.frozen);
        else {
          [...el.children].forEach((child, at) => {
            const one = child;
            one.style.animationDelay = `${opens ? 0 : step * at}s`;
            show(one, style2, seconds2, cue, playing.frozen);
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
  function show(el, style2, seconds2, cue, frozen2) {
    const entrance = styleClass("in", style2);
    el.classList.remove(CLASS.hidden);
    span(el, seconds2);
    el.classList.add(CLASS.shown);
    if (frozen2) return;
    el.classList.add(entrance);
    const played = el.getAnimations().find((one) => one instanceof CSSAnimation && one.animationName === entrance);
    const ends = played?.effect?.getComputedTiming().endTime;
    if (typeof ends !== "number") return;
    schedule(now() + ends / MILLISECONDS, "reveal", cue, () => el.classList.remove(entrance));
  }
  function spotlight(container, slide, actions) {
    const cued = [...container.children].filter((child) => written(child, ATTR.in));
    cued.forEach((child, at) => {
      const cue = cueId(slide.id, written(child, ATTR.in));
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
    const describe2 = written(el, ATTR.describe);
    if (describe2 === "") return null;
    const parts = [describe2 === null ? null : SENTENCE[ATTR.in]?.(describe2), text || null].filter(Boolean);
    return parts.length ? parts.join(" ") : null;
  }
  function subject(el, attr) {
    const describe2 = written(el, ATTR.describe);
    if (!describe2) return null;
    return SENTENCE[attr]?.(describe2) ?? null;
  }
  function fire(mounted, cue) {
    const list = mounted?.actions.get(cue);
    if (!mounted || !list?.length) return void 0;
    for (const action of list) action.run();
    const arrived = list.some((action) => action.attr === ATTR.in && action.line !== null);
    const lines = [];
    for (const action of list) {
      if (action.line === null) continue;
      if (arrived && action.attr === ATTR.back) continue;
      if (mounted.said.has(action.line)) continue;
      mounted.said.add(action.line);
      lines.push(action.line);
    }
    return lines.length ? lines.join(" ") : null;
  }

  // src/decktalk/runtime/src/modes.ts
  /*! The four things a DeckTalk page can be doing, and the one URL that decides which.
   *
   * A page with no query lists its scenes. `?scene=` plays one at the speed a person reads it.
   * `?cues=` plays one against the section clock, which is the mode a recording is made in.
   * `?freeze=` freezes one slide with its cues already fired, which is what a screenshot opens.
   *
   * Every key this module reads is one the contract publishes, and each is read once here so no other
   * module ever touches the URL. A preview goes one step further and asks the project for the cue
   * times the last run resolved, so reviewing a scene in a browser shows the film's own timing rather
   * than an invented one, and nothing on that path may fail a page that has no such file.
   */
  var SCENE = "scene";
  var FREEZE = "freeze";
  var CUES = "cues";
  var WORDS = "words";
  var TURNS = "turns";
  var T0 = "t0";
  var SPEED = "speed";
  var HUD = "hud";
  var CAPTION = "caption";
  var ON = "1";
  var SLOWEST = 0.05;
  var PREVIEW_STEP_SECONDS = 1;
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
  var frozen = params.has(FREEZE);
  var signalled = params.get(T0) === T0_SIGNAL;
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
  function done() {
    document.body.toggleAttribute(DONE_ATTR, true);
  }
  function cueAt(cue) {
    const found = state.cues.find((one) => one.id === cue);
    return found ? origin2 + found.at : null;
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
    const opens = at === null ? NOTHING : opening();
    const mounted = prepare(el, slide, { frozen, recorded: signalled, held, opens, words: state.words, at: cueAt });
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
  var NOTHING = /* @__PURE__ */ new Set();
  function opening() {
    return new Set(state.cues.filter((cue) => cue.at <= DUE_SLACK_SECONDS).map((cue) => cue.id));
  }
  function retire(incoming, outgoing, entrance) {
    const leaving = state.mounted;
    outgoing.classList.add(CLASS.leaving);
    const go = () => {
      if (leaving && leaving.el === outgoing) leaving.stop();
      outgoing.remove();
    };
    schedule(now() + slideSeconds(entrance) + FRAME_STEP_MS / MILLISECONDS, "retire", incoming.className, go);
  }
  function context(cue, slideId, at) {
    return { id: cue, at: round(at), frozen, slideId };
  }
  function fireCue(cue, due2) {
    state.fired.push(cue);
    const sentence = fire(state.mounted, cue);
    const slide = state.slide;
    const slideEl = state.mounted?.el ?? null;
    const ctx = context(cue, slide ? slide.id : "", now());
    const local = slide ? cue.slice(cueId(slide.id, "").length) : cue;
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
    if (sentence === void 0 && !own && !handled(cue) && !findSlide(cue) && !ownerOf(cue)) {
      warn("PAGE_CUE_UNKNOWN", slide?.id ?? null, cue);
    }
    recorder().cue({ id: cue, due: round(due2), ran: round(now()), frame: frameAt(), describe: sentence ?? null });
  }
  function parseCues(raw) {
    return raw
      .split(LIST_SEPARATOR)
      .map((token) => token.trim())
      .filter(Boolean)
      .map((token) => {
        const mark = token.lastIndexOf(TIME_MARK);
        return { id: token.slice(0, mark), at: Number.parseFloat(token.slice(mark + 1)) };
      })
      .filter((cue) => cue.id && !Number.isNaN(cue.at))
      .sort((a, b) => a.at - b.at);
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
    if (mode === "record") {
      for (const slide of scene.slides) {
        if (!mountAt.has(slide)) warn("PAGE_SLIDE_UNUSED", slide.id);
      }
    }
    const last2 = ordered[ordered.length - 1][0];
    mount(scene, ordered[0][0], null, origin2);
    if (ordered[0][0] === last2) done();
    for (const [slide] of ordered.slice(1)) {
      schedule(origin2 + mountAt.get(slide), "mount", slide.id, () => {
        mount(scene, slide, null, null);
        if (slide === last2) done();
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
    const last2 = scene.slides[scene.slides.length - 1];
    scene.slides.forEach((slide, index2) => {
      const mountAt = at;
      const cues = cueOrder(slide);
      cues.forEach((cue, order2) => {
        state.cues.push({ id: cue, at: mountAt - origin2 + ((order2 + 1) * PREVIEW_STEP_SECONDS) / speed });
      });
      const start2 = () => {
        mount(scene, slide, null, index2 === 0 ? origin2 : null);
        cues.forEach((cue, order2) => {
          schedule(mountAt + ((order2 + 1) * PREVIEW_STEP_SECONDS) / speed, "cue", cue, () =>
            fireCue(cue, mountAt + ((order2 + 1) * PREVIEW_STEP_SECONDS) / speed),
          );
        });
        if (slide === last2) done();
      };
      if (index2 === 0) start2();
      else schedule(mountAt, "mount", slide.id, start2);
      at += Math.max(slide.previewSeconds, (cues.length + 1) * PREVIEW_STEP_SECONDS) / speed;
    });
  }
  async function resolvedCues(sceneId) {
    let document_;
    try {
      const answer = await fetch(PREVIEW_CUE_TIMES);
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
      .filter((row2) => typeof row2.cue === "string" && typeof row2.at === "number")
      .map((row2) => ({ id: row2.cue, at: row2.at / speed }));
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
    done();
  }
  function index(note2) {
    state.mode = "index";
    hide();
    const div = document.createElement("div");
    div.id = "dt-index";
    const title = document.title || "decktalk scenes";
    div.appendChild(heading(title, note2));
    for (const scene of all().values()) {
      const head = document.createElement("h2");
      head.textContent = `Scene ${scene.id}: ${scene.name} `;
      head.appendChild(link(`?${SCENE}=${encodeURIComponent(scene.id)}`, "▶ play"));
      div.appendChild(head);
      const row2 = document.createElement("div");
      row2.className = "dt-slides";
      for (const slide of scene.slides) {
        const cues = cueOrder(slide);
        const anchor = link(`?${FREEZE}=${encodeURIComponent(slide.id)}`, `slide ${slide.id} `);
        const note_ = document.createElement("code");
        note_.textContent = `(${slide.previewSeconds}s${cues.length ? `, cues ${cues.join(" ")}` : ""})`;
        anchor.appendChild(note_);
        row2.appendChild(anchor);
      }
      div.appendChild(row2);
    }
    document.body.appendChild(div);
  }
  function heading(title, note2) {
    const out = document.createDocumentFragment();
    const h1 = document.createElement("h1");
    h1.textContent = title;
    out.appendChild(h1);
    if (note2) {
      const p = document.createElement("p");
      const bold = document.createElement("b");
      bold.textContent = note2;
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
    state.words = wordsRaw ? parseWords(wordsRaw, params.get(TURNS)) : null;
    const cuesRaw = params.get(CUES);
    const cues = cuesRaw ? parseCues(cuesRaw) : [];
    const chosen = params.get(SCENE);
    let listing = false;
    let waiting3 = Promise.resolve();
    if (params.get(CAPTION) === ON) {
      state.mode = "caption";
      captioning();
    } else if (frozen) {
      stop(params.get(FREEZE) ?? "", (order2, slide) => probe?.freezeCues(order2, slide, warn) ?? order2);
    } else if (chosen !== null || cues.length) {
      const scene = chosen !== null ? (all().get(chosen) ?? null) : (ownerOf(cues[0].id)?.scene ?? null);
      if (!scene) index(`unknown scene ${chosen ?? ""}`);
      else if (cues.length) play(scene, cues, "record");
      else
        waiting3 = resolvedCues(scene.id).then((resolved) =>
          resolved ? play(scene, resolved, "preview") : preview(scene),
        );
    } else {
      listing = true;
    }
    run(params.get(HUD) === ON ? report2 : void 0);
    if (!listing) return waiting3.then(() => ready(pan()));
    return waiting3
      .then(() => ready(pan()))
      .then(() => {
        measure(probe);
        index();
      });
  }
  function report2(seconds2) {
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
        undeclaredIn(el, slide.id);
        layer.removeChild(el);
      }
    }
    layer.remove();
    probe?.measure(state.catalog, {
      scenes: all(),
      pan: pan(),
      origin: frame(),
      scale: scale(),
      build: buildSlide,
    });
  }

  // src/decktalk/runtime/src/timeline.ts
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
  var source = () => document.getAnimations();
  var born2 = /* @__PURE__ */ new WeakMap();
  var meet = null;
  var numbered = /* @__PURE__ */ new WeakMap();
  var met = 0;
  var last = "";
  function adopt(at) {
    for (const animation of source()) {
      if (born2.has(animation)) continue;
      born2.set(animation, at);
      numbered.set(animation, met);
      met += 1;
      meet?.(animation);
    }
  }
  function signature() {
    return source()
      .map((animation) => {
        const standing = animation.effect?.getComputedTiming();
        const where = standing ? `${standing.progress}/${standing.currentIteration}` : `${animation.currentTime}`;
        return `${numbered.get(animation)}:${where}`;
      })
      .join(",");
  }
  function nextMove(animation, seconds2) {
    const rate = animation.playbackRate;
    if (rate === 0) return null;
    const standing = animation.effect?.getComputedTiming();
    const local = animation.currentTime;
    if (rate < 0 || typeof local !== "number" || !standing) return seconds2;
    const { delay, activeDuration } = standing;
    if (typeof delay !== "number" || typeof activeDuration !== "number") return seconds2;
    if (local >= delay + activeDuration) return null;
    if (local < delay) return seconds2 + (delay - local) / (rate * MILLISECONDS);
    return seconds2;
  }
  function whenMet(next) {
    meet = next;
  }
  function seek(seconds2) {
    for (const animation of source()) {
      const birth = born2.get(animation) ?? seconds2;
      animation.pause();
      animation.currentTime = Math.max(0, (seconds2 - birth) * MILLISECONDS * animation.playbackRate);
    }
  }
  var begun = false;
  function begin2() {
    if (begun) return;
    begun = true;
    adopt(0);
  }
  var timeline = {
    running(at) {
      if (driven() !== "stepped") return;
      begin2();
      adopt(at);
      seek(at);
    },
    ran(at) {
      if (driven() !== "stepped") return;
      begin2();
      adopt(at);
    },
    stepped(seconds2) {
      begin2();
      adopt(seconds2);
      seek(seconds2);
      const now2 = signature();
      const moved = now2 !== last;
      last = now2;
      return moved;
    },
    still(seconds2) {
      return earliest(...source().map((animation) => nextMove(animation, seconds2)));
    },
  };

  // src/decktalk/runtime/src/index.ts
  /*! decktalk-runtime.js: the page contract, as one script a deck includes and nothing else.
   *
   * Include it and declare a scene in markup. Nothing here needs JavaScript:
   *
   *   <script src="/__decktalk/decktalk-runtime.js"><\/script>
   *   <div data-scene="pitch" data-name="How often">
   *     <template data-slide="pitch.listing">
   *       <h1>Value still listed</h1>
   *       <p data-in="by-hour" data-describe="the hourly figure">by hour, through first pitch</p>
   *       <p data-in="share" data-describe="the share of games">one game in ten</p>
   *     </template>
   *   </div>
   *
   * An element has four moments, each the cue name of a cue whose phrase `cues.json` names: it
   * arrives, it steps back, it comes to the front and it leaves. A page that wants behaviour of its
   * own adds it beside the markup, and a page that would rather build a slide in script gives that
   * slide a render function instead of a template.
   *
   * This module is the facade and nothing else. It reads the probe's telemetry sink once, hands it to
   * the seam, and publishes `window.DeckTalk` for an author and `window.__decktalk` for whoever is
   * reading the page back. Nothing imports it, which is what keeps every other module testable.
   */
  var VERSION = "0.5.0";
  var GATE_SECONDS = 5;
  var gates = [];
  function injected() {
    return window.__decktalkProbe ?? null;
  }
  function gatesSettled() {
    if (!gates.length) return Promise.resolve();
    let done2 = false;
    const all_ = Promise.all(
      gates.map((gate) =>
        Promise.resolve(gate).then(null, (err) => warn("PAGE_WAIT_REJECTED", null, null, { value: String(err) })),
      ),
    ).then(() => {
      done2 = true;
    });
    const limit = new Promise((resolve2) => {
      setTimeout(() => {
        if (!done2) warn("PAGE_WAIT_UNSETTLED");
        resolve2();
      }, GATE_SECONDS * MILLISECONDS);
    });
    return Promise.race([all_, limit]);
  }
  var running = false;
  function boot() {
    if (running) return;
    running = true;
    setRecorder(injected()?.recorder);
    observe({
      running: (at) => timeline.running(at),
      ran: (at) => timeline.ran(at),
      stepped: (seconds2) => {
        const moved = timeline.stepped(seconds2);
        sample(seconds2);
        return moved || moving(seconds2);
      },
      still: (seconds2) => earliest(timeline.still(seconds2), stillUntil(seconds2)),
    });
    whenMet((animation) => {
      const seen = animation;
      const target = seen.effect instanceof KeyframeEffect ? seen.effect.target : null;
      born({ target, endSeconds: ending(seen) }, state.slide?.id ?? null);
    });
    setMotionScale(motionScale());
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
    /**
     * Move the section clock to one second and show that second, which is how the recorder draws a frame.
     *
     * The first call puts the page on the stepped clock for good: every task due by then runs, every
     * frame hook draws, and every animation is held at that second. It answers the second the next task
     * is due and whether anything the runtime knows of changed the picture since the step before.
     */
    stepTo,
    /**
     * Draw something every frame from the section second, such as a canvas, a typed line or a scene.
     *
     * The hook is handed the second the frame shows, so a recorded frame and a preview frame draw the
     * same picture for the same second. `region` is the element or selector it draws inside, which
     * verify masks, and a hook that names none is undeclared motion. A hook that throws is reported
     * once and stops drawing.
     */
    frame(fn, options = {}) {
      region(options.region);
      let told = false;
      every((seconds2) => {
        try {
          fn(seconds2);
          return true;
        } catch (err) {
          if (!told) warn("PAGE_FRAME_THREW", null, null, { value: String(err) });
          told = true;
          return false;
        }
      });
      return DeckTalk;
    },
    /**
     * Move one element from one cue to another in script, drawing `draw(progress, seconds)` every frame between them.
     *
     * The cues are cue ids, the length is their seconds apart, and progress is eased by one of the
     * contract's eases, so a script motion lands on its cue exactly as a declared one does.
     */
    motion(el, motion) {
      scripted(el, motion, cueAt);
      return DeckTalk;
    },
    /** Hold readiness until a promise of the page's own has settled. */
    waitFor(promise) {
      gates.push(Promise.resolve(promise));
      return DeckTalk;
    },
    /**
     * Every cue this page plays and the second on the section clock it is due, keyed by cue id.
     *
     * A composition page keys its own drawing on these, so a re-voiced line re-times it as it does a cue.
     */
    get cues() {
      return Object.fromEntries(state.cues.map((cue) => [cue.id, cueAt(cue.id) ?? cue.at]));
    },
    /**
     * Every word the section speaks, as the runtime matches it, the second the voice reaches it, and who says it.
     *
     * `voice` names the voice and `turn` counts the section's turns from one, so a page can place what
     * it draws by its voice without showing the name.
     */
    get words() {
      return (state.words ?? []).map((spoken) => ({
        word: spoken.key,
        at: spoken.at,
        voice: spoken.voice,
        turn: spoken.turn,
      }));
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
    get motions() {
      return report();
    },
    get reveals() {
      return reveals();
    },
    get frozen() {
      return isFrozen();
    },
    get signalled() {
      return waitsForSignal();
    },
    now,
    started,
    caption: (caption) => showCaption(caption),
    undeclared: undeclaredChange,
  };
  window.DeckTalk = DeckTalk;
  window.__decktalk = view;
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
  else queueMicrotask(boot);
})();
