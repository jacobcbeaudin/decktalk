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
  var WIRE_MARK = ":";
  function wireId(slide, local) {
    return `${slide}${WIRE_MARK}${local}`;
  }

  // src/decktalk/runtime/src/probe/probe.ts
  /*! The recorder's instrumentation, injected into a page before it loads.
   *
   * Every DeckTalk command that opens a page drives a headless Chromium, and what that command needs
   * from the page is not what a reader needs: a cover over the first paint so the recording starts on
   * a known frame, a helper that says when the page has settled, a measured catalog that says where
   * every reveal sits, a freeze that stops at one cue, and a record of how the page behaved while it
   * was captured. None of that belongs to a deck, so none of it is in the runtime a deck loads.
   *
   * Playwright adds this file with `add_init_script`, so it runs before the page's own scripts and no
   * page ever references it. A page opened without it plays, previews, freezes and lists its scenes
   * the same, and keeps nothing.
   *
   * What the recorder calls
   *   window.__dtprobe.cover()   draw the magenta cover and the keep-alive from the first paint
   *   window.__dtprobe.lift()    remove the cover and start the page clock on the next animation
   *                              frame, resolving to the performance.now() of that frame, which is
   *                              the recording's narration t=0
   *   window.__dtprobe.ready()   the page's fonts and its window.__decktalk.ready, whichever exist
   *   window.__dtprobe.report()  everything the recorder reads back off the page, in one call
   *
   * What the runtime reads, and only when this file is there
   *   window.__dtprobe.recorder                          the telemetry sink, read once at startup
   *   window.__dtprobe.freezeCues(order, slideId, warn)  which of a frozen slide's cues fire
   *   window.__dtprobe.measure(catalog, stage)           one box row per element, onto the catalog
   *
   * This module imports the contract and the telemetry seam and nothing else, which is what keeps the
   * split honest: the probe knows the vocabulary and it knows the sink, and it knows no DOM the
   * runtime owns.
   */
  var COVER_ID = "__t0cover";
  var KEEPALIVE_ID = "__dtkeepalive";
  var GAP_MS = 100;
  var AFTER = "after";
  var BEFORE = "before";
  var KEEP = 5e3;
  var TEXT_MAX = 80;
  function cover() {
    const add = () => {
      if (document.getElementById(COVER_ID)) return;
      const parent = document.body || document.documentElement;
      const sheet = document.createElement("div");
      sheet.id = COVER_ID;
      sheet.style.cssText = "position:fixed;inset:0;background:#ff00ff;z-index:2147483647;pointer-events:none";
      parent.appendChild(sheet);
      const alive = document.createElement("div");
      alive.id = KEEPALIVE_ID;
      alive.setAttribute("aria-hidden", "true");
      alive.style.cssText = `position:fixed;right:1px;bottom:1px;width:2px;height:2px;background:#808080;opacity:.03;z-index:2147483647;pointer-events:none;animation:${KEEPALIVE_ID} .5s linear infinite`;
      const style = document.createElement("style");
      style.textContent = `@keyframes ${KEEPALIVE_ID}{to{transform:rotate(360deg)}}`;
      alive.appendChild(style);
      parent.appendChild(alive);
    };
    if (document.documentElement) add();
    else document.addEventListener("DOMContentLoaded", add, { once: true });
  }
  var origin = null;
  function clockAt(ms) {
    return origin === null ? null : Number(((ms - origin) / 1e3).toFixed(3));
  }
  function lift() {
    return new Promise((resolve) => {
      document.getElementById(COVER_ID)?.remove();
      requestAnimationFrame((at) => {
        origin = at;
        window.DeckTalk?.startClock?.();
        resolve(performance.now());
      });
    });
  }
  function ready() {
    const fonts = document.fonts ? document.fonts.ready : null;
    const runtime = window.__decktalk?.ready;
    return Promise.all([fonts, runtime].map((p) => Promise.resolve(p).catch(() => null))).then(() => true);
  }
  var cues = [];
  var words = [];
  var frameGaps = [];
  var longFrames = [];
  var recorder = {
    cue(event) {
      if (cues.length >= KEEP) return;
      const row = { ...event, next: null, after: null };
      cues.push(row);
      requestAnimationFrame((next) => {
        row.next = clockAt(next);
        requestAnimationFrame((after) => {
          row.after = clockAt(after);
        });
      });
    },
    words(event) {
      if (words.length < KEEP) words.push(event);
    },
  };
  function watchFrames() {
    let last = null;
    const tick = (at) => {
      if (last !== null && at - last > GAP_MS && frameGaps.length < KEEP) {
        frameGaps.push({ at: clockAt(at), ms: Math.round(at - last) });
      }
      last = at;
      requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
    const kinds = window.PerformanceObserver ? PerformanceObserver.supportedEntryTypes || [] : [];
    if (!kinds.includes("long-animation-frame")) return;
    new PerformanceObserver((list) => {
      for (const entry of list.getEntries()) {
        if (origin === null || entry.startTime < origin || longFrames.length >= KEEP) continue;
        longFrames.push({
          start: clockAt(entry.startTime),
          ms: Math.round(entry.duration),
          render: entry.renderStart === void 0 ? null : clockAt(entry.renderStart),
          presented: entry.presentationTime === void 0 ? null : clockAt(entry.presentationTime),
        });
      }
    }).observe({ type: "long-animation-frame" });
  }
  function report() {
    const view = window.__decktalk ?? {};
    return {
      version: view.version ?? null,
      mode: view.mode ?? null,
      scene: view.scene ?? null,
      slide: view.slide ?? null,
      warnings: view.warnings ?? [],
      catalog: view.catalog ?? [],
      cues,
      words,
      frameGaps,
      longFrames,
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
      const written = el.getAttribute(name);
      if (written !== null) attrs[name] = written;
    }
    const moments = {};
    for (const name of MOMENT_ATTRS) {
      const local = attrs[name];
      if (local) moments[name] = wireId(slideId, local);
    }
    return {
      attrs,
      moments,
      text: (el.textContent ?? "").trim().replace(/\s+/g, " ").slice(0, TEXT_MAX),
      box: boxOf(el, frame, scale),
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
  function measure(catalog, stage) {
    const layer = document.createElement("div");
    layer.id = "dt-measure";
    layer.style.cssText = "position:absolute;inset:0;visibility:hidden";
    stage.pan.appendChild(layer);
    const frame = stage.origin.getBoundingClientRect();
    const scale = stage.scale || 1;
    for (const entry of catalog) {
      const scene = stage.scenes.get(entry.scene);
      if (!scene) continue;
      entry.elements = {};
      for (const slide of scene.slides) {
        const slideEl = stage.build(scene, slide);
        layer.appendChild(slideEl);
        entry.elements[slide.id] = rowsFor(slideEl, slide.id, frame, scale);
        layer.removeChild(slideEl);
      }
    }
    layer.remove();
    return catalog;
  }
  watchFrames();
  window.__dtprobe = { cover, lift, ready, report, recorder, freezeCues, measure };
})();
