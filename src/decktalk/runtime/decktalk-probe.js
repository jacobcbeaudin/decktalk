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
  var CUE_MARK = ":";
  var ENGINE_PATH = "/__decktalk/";
  var PREVIEW_CUE_TIMES = `${ENGINE_PATH}cue-times.json`;
  var CLIP_FRAMES = `${ENGINE_PATH}clip/`;
  var PREVIEW_CANVAS = `${ENGINE_PATH}canvas.json`;
  function cueId(slide, local) {
    return `${slide}${CUE_MARK}${local}`;
  }

  // src/decktalk/runtime/src/probe/probe.ts
  /*! The recorder's instrumentation, injected into a page before it loads.
   *
   * Every DeckTalk command that opens a page drives a headless Chromium, and what that command needs
   * from the page is not what a reader needs: a step that moves the section clock to one recorded
   * frame, a helper that says when the page has settled, a measured catalog that says where every
   * reveal sits, a freeze that stops at one cue, and a record of what the page did while it was
   * recorded. None of that belongs to a deck, so none of it is in the runtime a deck loads.
   *
   * Playwright adds this file with `add_init_script`, so it runs before the page's own scripts and no
   * page ever references it. A page opened without it plays, previews, freezes and lists its scenes
   * the same, and keeps nothing.
   *
   * What the recorder calls
   *   window.__decktalkProbe.step(t, s) move the section clock to second t and answer the next due second,
   *                                    whether the picture may have changed, the earliest second it next
   *                                    can and what the page changed undeclared since the step before,
   *                                    once the page has drawn it, or refuse once s seconds pass without that
   *   window.__decktalkProbe.started(s) answer once every animation the page set playing has started, or
   *                                    refuse once s seconds pass without that
   *   window.__decktalkProbe.ready()   the page's fonts and its window.__decktalk.ready, whichever exist
   *   window.__decktalkProbe.report()  everything the recorder reads back off the page, in one call
   *   window.__decktalkProbe.within(p, s) what p settles with, or a refusal once s seconds pass on the
   *                                    browser's own timer, which every other call is raced through
   *
   * What the runtime reads, and only when this file is there
   *   window.__decktalkProbe.recorder                          the telemetry sink, read once at startup
   *   window.__decktalkProbe.freezeCues(order, slideId, warn)  which of a frozen slide's cues fire
   *   window.__decktalkProbe.measure(catalog, canvas)          one box row per element, onto the catalog
   *
   * This module imports the contract and the telemetry seam and nothing else, which is what keeps the
   * split honest: the probe knows the vocabulary and it knows the sink, and it knows no DOM the
   * runtime owns.
   */
  var KEEP = 5e3;
  var TEXT_MAX = 80;
  var AFTER = "after";
  var BEFORE = "before";
  var nativeFrame = window.requestAnimationFrame.bind(window);
  var nativeTimer = window.setTimeout.bind(window);
  var nativeCancel = window.clearTimeout.bind(window);
  var decoded = /* @__PURE__ */ new WeakMap();
  var SELF_DRAWN = "canvas, video, iframe, embed, object";
  var SHOWN = { opacityProperty: true, visibilityProperty: true };
  var touched = true;
  var touch = () => {
    touched = true;
  };
  var between = [];
  function keep(records) {
    if (!records.length) return;
    touched = true;
    for (const record of records) {
      if (between.length >= KEEP) return;
      between.push(inDocument(record.target));
    }
  }
  var OBSERVED = { subtree: true, childList: true, attributes: true, characterData: true };
  var watcher = new MutationObserver(keep);
  watcher.observe(document, OBSERVED);
  document.addEventListener("scroll", touch, { capture: true, passive: true });
  var roots = /* @__PURE__ */ new Set();
  function reached(root) {
    if (root && !roots.has(root)) {
      roots.add(root);
      watcher.observe(root, OBSERVED);
      root.addEventListener("scroll", touch, { capture: true, passive: true });
    }
    return root;
  }
  function inDocument(node) {
    let shown2 = node;
    for (let root = shown2.getRootNode(); root instanceof ShadowRoot; root = shown2.getRootNode()) shown2 = root.host;
    return shown2;
  }
  function wrapMethod(owner, name, after) {
    const own = Object.getOwnPropertyDescriptor(owner, name);
    const original = own?.value;
    if (!own?.configurable || typeof original !== "function") return;
    Object.defineProperty(owner, name, {
      ...own,
      value: function (...args) {
        let answer;
        try {
          answer = original.apply(this, args);
          return answer;
        } finally {
          after(answer);
        }
      },
    });
  }
  function wrapAccessor(owner, name, side, after) {
    const own = Object.getOwnPropertyDescriptor(owner, name);
    const original = own?.[side];
    if (!own?.configurable || typeof original !== "function") return;
    const wrapped = function (...args) {
      return after(original.apply(this, args));
    };
    Object.defineProperty(owner, name, { ...own, [side]: wrapped });
  }
  wrapMethod(Element.prototype, "attachShadow", (root) => reached(root));
  wrapAccessor(Element.prototype, "shadowRoot", "get", (root) => reached(root));
  wrapAccessor(ElementInternals.prototype, "shadowRoot", "get", (root) => reached(root));
  var SHEET_INTERFACES = /^(?:StyleSheet|MediaList|StylePropertyMap|CSS\w*(?:Rule|StyleSheet|Declaration|Properties))$/;
  var SHEET_WRITERS = /* @__PURE__ */ new Set([
    "insertRule",
    "deleteRule",
    "addRule",
    "removeRule",
    "appendRule",
    "replace",
    "replaceSync",
    "setProperty",
    "removeProperty",
    "appendMedium",
    "deleteMedium",
    "set",
    "append",
    "delete",
    "clear",
  ]);
  function written(answer) {
    touch();
    if (answer instanceof Promise) answer.then(touch, touch);
  }
  var declarations = /* @__PURE__ */ new WeakMap();
  function heard(declaration) {
    if (typeof declaration !== "object" || declaration === null) return declaration;
    let view = declarations.get(declaration);
    if (!view) {
      view = new Proxy(declaration, {
        get(target, key) {
          const value = Reflect.get(target, key, target);
          return typeof value === "function" ? value.bind(target) : value;
        },
        set(target, key, value) {
          const done = Reflect.set(target, key, value, target);
          touch();
          return done;
        },
      });
      declarations.set(declaration, view);
    }
    return view;
  }
  var scope = window;
  for (const name of Object.getOwnPropertyNames(window).filter((one) => SHEET_INTERFACES.test(one))) {
    const prototype = scope[name]?.prototype;
    if (typeof prototype !== "object" || prototype === null) continue;
    for (const [member, own] of Object.entries(Object.getOwnPropertyDescriptors(prototype))) {
      if (member === "style" && own.get) wrapAccessor(prototype, member, "get", heard);
      if (own.set) wrapAccessor(prototype, member, "set", written);
      else if (SHEET_WRITERS.has(member)) wrapMethod(prototype, member, written);
    }
  }
  var adopted = /* @__PURE__ */ new WeakMap();
  function readopted() {
    let changed = false;
    for (const holder of [document, ...roots]) {
      const now = holder.adoptedStyleSheets;
      const before = adopted.get(holder) ?? [];
      if (now.length === before.length && now.every((sheet, at) => sheet === before[at])) continue;
      adopted.set(holder, [...now]);
      changed = true;
    }
    return changed;
  }
  function shown(el) {
    if (!el.checkVisibility(SHOWN)) return false;
    const box = el.getBoundingClientRect();
    return (
      box.width > 0 &&
      box.height > 0 &&
      box.right > 0 &&
      box.bottom > 0 &&
      box.left < innerWidth &&
      box.top < innerHeight
    );
  }
  function selfDrawn() {
    for (const holder of [document, ...roots]) {
      for (const el of holder.querySelectorAll(SELF_DRAWN)) if (shown(el)) return el;
    }
    return null;
  }
  function named(el) {
    const id = el.id ? `#${el.id}` : "";
    const classes = [...el.classList].map((one) => `.${one}`).join("");
    return `<${el.localName}${id}${classes}>`;
  }
  var PAGE_CLOCK = "__pwClock";
  function nextTimer(seconds) {
    const clock = window[PAGE_CLOCK]?.controller;
    if (typeof clock?._firstTimer !== "function" || typeof clock.performanceNow !== "function") return seconds;
    const timer = clock._firstTimer();
    if (!timer) return null;
    return seconds + Math.max(0, timer.callAt - clock.performanceNow()) / MILLISECONDS;
  }
  function sooner(one, other) {
    if (one === null) return other;
    if (other === null) return one;
    return Math.min(one, other);
  }
  function mutated() {
    const seen = readopted() || touched || watcher.takeRecords().length > 0;
    touched = false;
    between = [];
    return seen;
  }
  var answered = false;
  function undeclared() {
    keep(watcher.takeRecords());
    const changed = between;
    between = [];
    if (!answered || !changed.length) return null;
    return window.__decktalk?.undeclared(changed) ?? null;
  }
  function rendered() {
    return new Promise((done) => {
      nativeFrame(() => {
        const channel = new MessageChannel();
        channel.port1.onmessage = () => done();
        channel.port2.postMessage(null);
      });
    });
  }
  function outstanding() {
    const waits = [];
    if (document.fonts && document.fonts.status !== "loaded") waits.push(document.fonts.ready);
    for (const image of Array.from(document.images)) {
      const source = image.currentSrc || image.src;
      if (decoded.get(image) === source) continue;
      waits.push(
        image
          .decode()
          .catch(() => void 0)
          .then(() => decoded.set(image, source)),
      );
    }
    return waits;
  }
  async function drawn() {
    let waited = false;
    for (;;) {
      await rendered();
      const waits = outstanding();
      if (!waits.length) return waited;
      waited = true;
      await Promise.all(waits);
    }
  }
  function step(seconds, waitSeconds) {
    const outside = undeclared();
    const stepped = window.DeckTalk?.stepTo?.(seconds) ?? { due: null, changed: true, stillUntil: seconds };
    const touchedBefore = mutated();
    const late = `the page had not drawn second ${seconds} within ${waitSeconds} seconds`;
    return within(drawn(), waitSeconds, late).then((waited) => {
      const touchedWhileDrawn = mutated();
      const drawing = selfDrawn();
      answered = true;
      const heard2 = waited || touchedWhileDrawn || drawing !== null;
      return {
        due: stepped.due,
        changed: stepped.changed || touchedBefore || heard2,
        stillUntil: heard2 ? seconds : sooner(stepped.stillUntil, nextTimer(seconds)),
        undeclared: outside,
        selfDrawn: drawing && named(drawing),
      };
    });
  }
  function started(waitSeconds) {
    const pending = () => document.getAnimations().filter((animation) => animation.pending);
    const settle = async () => {
      for (let waiting = pending(); waiting.length; waiting = pending()) {
        await Promise.all(waiting.map((animation) => animation.ready.catch(() => void 0)));
      }
    };
    const late = `the page's animations had not started within ${waitSeconds} seconds`;
    return within(
      settle().then(() => true),
      waitSeconds,
      late,
    );
  }
  function within(answer, seconds, late = `the page did not answer within ${seconds} seconds`) {
    return new Promise((settle, refuse) => {
      const timer = nativeTimer(() => refuse(new Error(late)), seconds * MILLISECONDS);
      answer.then(
        (value) => {
          nativeCancel(timer);
          settle(value);
        },
        (error) => {
          nativeCancel(timer);
          refuse(error);
        },
      );
    });
  }
  function ready() {
    const fonts = document.fonts ? document.fonts.ready : null;
    const runtime = window.__decktalk?.ready;
    return Promise.all([fonts, runtime].map((p) => Promise.resolve(p).catch(() => null))).then(() => true);
  }
  var cues = [];
  var words = [];
  var recorder = {
    cue(event) {
      if (cues.length < KEEP) cues.push(event);
    },
    words(event) {
      if (words.length < KEEP) words.push(event);
    },
  };
  function report() {
    const view = window.__decktalk;
    return {
      version: view?.version ?? null,
      mode: view?.mode ?? null,
      scene: view?.scene ?? null,
      slide: view?.slide ?? null,
      warnings: view?.warnings ?? [],
      catalog: view?.catalog ?? [],
      cues,
      words,
      motions: view?.motions ?? [],
      reveals: view?.reveals ?? [],
    };
  }
  function freezeCues(order, slideId, warn) {
    const params = new URLSearchParams(location.search);
    const after = params.get(AFTER);
    const before = params.get(BEFORE);
    const stop = after || before;
    if (!stop) return order;
    const at = order.indexOf(stop);
    if (at < 0) {
      warn("PAGE_FREEZE_CUE_UNKNOWN", slideId, stop);
      return order;
    }
    return order.slice(0, after ? at + 1 : at);
  }
  var ELEMENT_ATTRS = Object.keys(ATTRS).filter((name) =>
    ATTRS[name].on.some((subject) => subject === "element" || subject === "container"),
  );
  var STAGGER = "data-stagger";
  var MOMENT_ATTRS = ELEMENT_ATTRS.filter((name) => ATTRS[name].kind === "moment");
  function boxOf(el, frame, scale) {
    const rect = el.getBoundingClientRect();
    return {
      x: Math.round((rect.left - frame.left) / scale),
      y: Math.round((rect.top - frame.top) / scale),
      w: Math.round(rect.width / scale),
      h: Math.round(rect.height / scale),
    };
  }
  function rowFor(el, slideId, frame, scale) {
    const attrs = {};
    for (const name of ELEMENT_ATTRS) {
      const written2 = el.getAttribute(name);
      if (written2 !== null) attrs[name] = written2;
    }
    const moments = {};
    for (const name of MOMENT_ATTRS) {
      const local = attrs[name];
      if (local) moments[name] = cueId(slideId, local);
    }
    return {
      attrs,
      moments,
      text: (el.textContent ?? "").trim().replace(/\s+/g, " ").slice(0, TEXT_MAX),
      box: boxOf(el, frame, scale),
      // The count a stagger's span is worked out from, which no attribute carries, because `data-stagger`
      // gives the step between children and says nothing about how many of them there are.
      children: el.hasAttribute(STAGGER) ? el.children.length : 0,
    };
  }
  function rowsFor(slideEl, slideId, frame, scale) {
    const rows = [...slideEl.querySelectorAll(MOMENT_SELECTOR)].map((el) => rowFor(el, slideId, frame, scale));
    for (const el of slideEl.children) {
      if (el.matches(MOMENT_SELECTOR) || el.querySelector(MOMENT_SELECTOR)) continue;
      rows.push(rowFor(el, slideId, frame, scale));
    }
    return rows;
  }
  function measure(catalog, canvas) {
    const layer = document.createElement("div");
    layer.id = "dt-measure";
    layer.style.cssText = "position:absolute;inset:0;visibility:hidden";
    canvas.pan.appendChild(layer);
    const frame = canvas.origin.getBoundingClientRect();
    const scale = canvas.scale || 1;
    for (const entry of catalog) {
      const scene = canvas.scenes.get(entry.scene);
      if (!scene) continue;
      entry.elements = {};
      for (const slide of scene.slides) {
        const slideEl = canvas.build(scene, slide);
        layer.appendChild(slideEl);
        entry.elements[slide.id] = rowsFor(slideEl, slide.id, frame, scale);
        layer.removeChild(slideEl);
      }
    }
    layer.remove();
    return catalog;
  }
  var probe = { step, started, ready, report, within, recorder, freezeCues, measure };
  window.__decktalkProbe = probe;
})();
