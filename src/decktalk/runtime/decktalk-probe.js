/* decktalk-probe.js — the recorder's instrumentation, injected into a page before it loads.
 *
 * Every DeckTalk command that opens a page drives a headless Chromium, and what it needs from that
 * page is not what a reader needs: a cover over the first paint so the recording starts on a known
 * frame, a helper that says when the page has settled, a measured catalog that says where every
 * reveal sits, and a freeze that stops at one cue. Those four things live here rather than in
 * decktalk-runtime.js, which carries the page contract alone. The recorder adds this file with
 * Playwright's add_init_script, so it runs before the page's own scripts, and no page ever
 * references it. A page opened without it plays, previews, freezes and lists its scenes the same.
 *
 * What the recorder calls
 *   window.__dtprobe.cover()   draw the magenta cover and the keep-alive from the first paint
 *   window.__dtprobe.lift()    remove the cover and start the page clock on the next animation
 *                              frame, resolving to the performance.now() of that frame, which is
 *                              the recording's narration t=0
 *   window.__dtprobe.ready()   the page's fonts and its window.__decktalk.ready, whichever exist
 *
 * What decktalk-runtime.js calls, and only when this file is there
 *   window.__dtprobe.freezeCues(order, slideId, warn)  which of a frozen slide's cues fire
 *   window.__dtprobe.measure(catalog, stage)           one box row per element, onto the catalog
 *
 * URL parameters this file owns, which the runtime never reads
 *   &after=ID    with ?slide=, freeze at cue ID instead of revealing everything: the slide's cues
 *                in preview order up to and including ID fire, and later cues stay hidden
 *   &before=ID   with ?slide= and no &after=, freeze just before cue ID, so the cues before it in
 *                preview order fire and ID and later stay hidden
 */
(() => {
  // The probe loads as an init script, not a module, so this directive is what makes it strict.
  "use strict";
  const VERSION = "0.4.0"; // x-release-please-version
  const COVER_ID = "__t0cover";
  const KEEPALIVE_ID = "__dtkeepalive";

  // ---- the cover ------------------------------------------------------------------
  // The page is covered in magenta from its first paint until the narration clock starts, so the
  // first clean frame in the recording is t=0 no matter when the recorder began capturing.
  //
  // The cover comes with a keep-alive: a 2 px square in the bottom-right corner that turns for the
  // whole recording. Chromium's screencast emits a frame only when the compositor paints one, and
  // a static cover paints once, so without motion the cover might never be recorded. The keep-alive
  // outlives the cover on purpose. Playwright stamps each frame by when it was swapped, rounded
  // down to its 25 fps grid, and a busy compositor swaps later in the frame than an idle one. If
  // the motion stopped with the cover, the cover-off frame at t=0 would be stamped busy and every
  // later reveal on a still page stamped idle, one or two frames earlier, so reveals would record
  // 30 to 90 ms ahead of their words. It is mid-gray at 3 percent opacity, so it moves a pixel's
  // luma by four steps at most, which is under verify's diff levels of 12 and 40 and far too small
  // to move the frame averages that cover detection and the black checks read. A screenshot never
  // asks for the cover, so it never shows in one.
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
      alive.style.cssText =
        "position:fixed;right:1px;bottom:1px;width:2px;height:2px;background:#808080;opacity:.03;" +
        `z-index:2147483647;pointer-events:none;animation:${KEEPALIVE_ID} .5s linear infinite`;
      const style = document.createElement("style");
      style.textContent = `@keyframes ${KEEPALIVE_ID}{to{transform:rotate(360deg)}}`;
      alive.appendChild(style);
      parent.appendChild(alive);
    };
    if (document.documentElement) add();
    else document.addEventListener("DOMContentLoaded", add, { once: true });
  }
  // The frame that shows the cover gone is composited on the next animation frame, and that frame
  // is the recording's t=0, so the clock starts there rather than at the moment of this call.
  function lift() {
    return new Promise((resolve) => {
      document.getElementById(COVER_ID)?.remove();
      requestAnimationFrame(() => {
        window.DeckTalk?.startClock?.();
        resolve(performance.now());
      });
    });
  }

  // ---- the wait helper ------------------------------------------------------------
  // A page is settled when its fonts have loaded and the runtime's own readiness promise has
  // resolved. Either may be missing, on a page that is not a deck or in a browser without the
  // fonts API, and a page that is missing both is ready as it stands.
  function ready() {
    const fonts = document.fonts ? document.fonts.ready : null;
    const runtime = window.__decktalk?.ready;
    return Promise.all([fonts, runtime].map((p) => Promise.resolve(p).catch(() => null))).then(() => true);
  }

  // ---- freezing at one cue --------------------------------------------------------
  // The runtime freezes a slide with every cue fired, which is the frozen slide the index page
  // links and the screenshot command opens. `preflight` needs the slide one cue earlier, so it
  // asks for a stop and this is the only reader of the two parameters that name it.
  function freezeCues(order, slideId, warn) {
    const params = new URLSearchParams(location.search);
    const after = params.get("after");
    const before = params.get("before");
    const stop = after || before;
    if (!stop) return order;
    const k = order.indexOf(stop);
    if (k < 0) {
      warn(`cue "${stop}" is not one of slide ${slideId}'s cues`);
      return order;
    }
    return order.slice(0, after ? k + 1 : k);
  }

  // ---- the measured catalog -------------------------------------------------------
  // One row per element a slide reveals, and one per direct child it never reveals, each with its
  // box in stage pixels, so a check that cannot look at a picture still knows where every reveal
  // sits and what it says.
  function boxOf(el, origin, scale) {
    const r = el.getBoundingClientRect();
    return {
      x: Math.round((r.left - origin.left) / scale),
      y: Math.round((r.top - origin.top) / scale),
      w: Math.round(r.width / scale),
      h: Math.round(r.height / scale),
    };
  }
  function rowsFor(slideEl, origin, scale) {
    const text = (el) => el.textContent.trim().replace(/\s+/g, " ").slice(0, 80);
    const rows = [...slideEl.querySelectorAll("[data-cue],[data-delay]")].map((el) => ({
      cue: el.dataset.cue ?? null,
      delay: el.hasAttribute("data-delay") ? parseFloat(el.dataset.delay) || 0 : null,
      reveal: el.dataset.reveal ?? null,
      describe: el.dataset.describe ?? null,
      tex: el.dataset.tex ?? null,
      text: text(el),
      box: boxOf(el, origin, scale),
    }));
    for (const el of slideEl.children) {
      if (el.matches("[data-cue],[data-delay]") || el.querySelector("[data-cue],[data-delay]")) continue;
      rows.push({
        cue: null,
        delay: null,
        reveal: null,
        describe: el.dataset.describe ?? null,
        tex: el.dataset.tex ?? null,
        text: text(el),
        box: boxOf(el, origin, scale),
      });
    }
    return rows;
  }
  // Every slide is laid out once in a hidden layer of the stage, measured and thrown away. The
  // runtime asks for this on the index page alone, which is the page the static scan opens, so no
  // recording and no preview ever lays a slide out twice.
  function measure(catalog, stage) {
    const layer = document.createElement("div");
    layer.id = "dt-measure";
    layer.style.cssText = "position:absolute;inset:0;visibility:hidden";
    stage.pan.appendChild(layer);
    const origin = stage.origin.getBoundingClientRect();
    const scale = stage.scale || 1;
    for (const entry of catalog) {
      const scene = stage.scenes.get(entry.scene);
      entry.elements = {};
      for (const slide of scene.slides) {
        const slideEl = stage.build(scene, slide);
        layer.appendChild(slideEl);
        entry.elements[slide.id] = rowsFor(slideEl, origin, scale);
        layer.removeChild(slideEl);
      }
    }
    layer.remove();
    return catalog;
  }

  window.__dtprobe = { version: VERSION, cover, lift, ready, freezeCues, measure };
})();
