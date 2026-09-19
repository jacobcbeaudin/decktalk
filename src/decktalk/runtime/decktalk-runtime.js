/* decktalk-runtime.js — gives an HTML page the recorder's page contract.
 *
 * Include it and declare a scene in markup. Nothing here needs JavaScript:
 *
 *   <script src="decktalk-runtime.js"></script>
 *   <div data-scene="3" data-name="How often" data-camera="push">
 *     <template data-slide="3.1" data-hold="8">
 *       <h1>Value still listed</h1>
 *       <p data-cue="3.1by">by hour, through first pitch</p>
 *       <div class="tile" data-cue="3.1b" data-text="count">1 in 10</div>
 *     </template>
 *   </div>
 *
 * A page that wants behaviour adds it beside the markup, and a page that would rather build its
 * slides in script gives the slide a `render` function instead of a <template>:
 *
 *   <script>
 *     DeckTalk.scene(3, { name: "How often", camera: "push", slides: [
 *       { id: "3.1", hold: 8, render: () => `<h1>Value still listed</h1>` },
 *     ]});
 *     DeckTalk.on("3.1b", (slide) => slide.querySelector(".bars").classList.add("grow"));
 *   </script>
 *
 * URL contract (what the recorder and the screenshot command send)
 *   ?scene=N                 play scene N
 *   &cues=id@s,id@s,…        cue mode: fire cue `id` at `s` seconds after narration t=0.
 *                            The first cued slide is mounted before the clock starts, so the
 *                            recording never opens on an empty stage. Every later slide mounts at
 *                            the earliest of its cues. A slide with no listed cue never shows, and
 *                            the last cued slide holds forever.
 *   &t0=S                    seconds after page load at which narration t=0 falls.
 *                            t0=signal waits for DeckTalk.startClock(), which the recorder sends
 *   &words=word@s,…          the section's spoken words, which data-text="spoken" reveals against
 *   &prevwords=word@s,…      the previous section's spoken words, in seconds after that section
 *                            starts. The runtime ignores them. A page that opens on the previous
 *                            section's last frame reads them from DeckTalk.params
 *   ?slide=ID                freeze slide ID with everything revealed (screenshots, review)
 *   &speed=X                 preview time scale (cue mode ignores it)
 *   &hud=1                   overlay scene · slide · clock
 *   (no params)              index page listing every scene and slide
 *
 * Scene markup
 *   data-scene="N"           a scene wrapper, and every <template data-slide> inside it is a slide
 *   data-name="…"            the scene's name, which the index page and the catalog show
 *   data-camera="push"       the slow push-in, which only a played scene runs
 *   data-slide="ID"          on a <template>, one slide's markup, cloned when the slide mounts
 *   data-hold="s"            how long that slide holds in preview mode (default 8)
 *   data-owns="id id"        cue ids this slide owns that do not start with its own id (rare)
 *   data-preview="id@s id@s" preview seconds after the mount, which grant no ownership
 *
 * Markup inside a slide
 *   data-cue="id"            reveal on cue `id`
 *   data-delay="s"           reveal s seconds after the slide mounts. An element carrying both
 *                            data-cue and data-delay is a mistake, and is reported
 *   data-reveal="rise|fade|draw|drop|pop|dim|instant"   reveal effect (default rise)
 *   data-duration="s"        animation length, and the length of a count-up
 *   data-describe="…"        what this reveal shows, in a sentence, for the transcript
 *   data-text="count"        count the last number in the text up from 0 on reveal.
 *                            data-text="count first" counts the first number instead
 *   data-text="type 40ms"    type the text at that many milliseconds per character on reveal
 *   data-text="spoken"       reveal the element word by word as each word is spoken. The text
 *                            must match a run of the section's spoken words, which the recorder
 *                            passes as &words=word@s,word@s,… (punctuation and case are ignored).
 *                            The element gets data-reveal="instant" unless it sets its own.
 *   data-tex="…"             typeset with KaTeX if window.katex is present, and data-tex-display
 *                            typesets in display mode. Inside a <template> a backslash is written
 *                            once, which is the reason to prefer markup over a render string
 *
 * Cue ownership: a cue belongs to the slide whose id is the longest prefix of the cue id
 * ("4.2b1" -> slide "4.2", "9a" -> "9"), or to the slide whose `owns` list names it. A cue id that
 * starts with no slide id has to be listed in some slide's `owns`. `preview` is timing only: it
 * says when a cue fires in preview mode and grants no ownership.
 *
 * Handlers: a slide's `enter(slide, ctx)`, its `on[id](slide, ctx)`, and every
 * `DeckTalk.on(id, (slide, ctx) => …)` receive the mounted slide element and a context
 * object { id, at, frozen, slideId }. `id` is the cue id (the slide id for `enter`), `at` is the
 * second after narration t=0, `frozen` is true in freeze mode, and `slideId` is the id of the
 * mounted slide. A handler that takes no arguments keeps working.
 *
 * The page exposes window.__decktalk { mode, scene, slide, cues, fired, catalog, warnings, ready,
 * now() }. `ready` is owned by the runtime and resolves once fonts are loaded, once window.katex
 * exists when the page uses [data-tex] or loads KaTeX (polled for up to 5 s), and once every
 * promise the page handed to DeckTalk.waitFor has settled. The mode is one of index, preview, cue
 * and freeze. The mode `live`, which takes its clock from an audio element, is reserved and is
 * not implemented.
 * Anything the runtime cannot honor (an unknown cue id, a cue no slide owns, a cue that reveals no
 * element and runs no handler, a slide that owns no listed cue, a data-cue that is not listed, an
 * element with both data-cue and data-delay, a text mode with no trigger, a preview cue past its
 * slide's hold, KaTeX never arriving or refusing a data-tex value) is pushed onto
 * __decktalk.warnings, which the recorder reads back and logs. A warning never throws.
 *
 * This file carries the page contract and nothing else. The instrumentation a DeckTalk command
 * needs from a page it is recording lives in decktalk-probe.js, which the recorder injects before
 * the page loads and which a page never references: the cover over the first paint, the helper
 * that says when a page has settled, the boxes of the measured catalog, and the freeze that stops
 * at one cue. This file asks for two of those through window.__dtprobe and runs every mode on its
 * own when the probe is absent, which is every page a person opens.
 */
(() => {
  // The runtime loads as a classic <script>, not a module, so this directive is what makes it strict.
  "use strict";
  const VERSION = "0.4.0"; // x-release-please-version
  // Every selector sits inside :where(), so it carries no specificity and any page rule wins, and
  // the stylesheet is prepended to <head> so a page rule of equal weight wins on order too.
  const CSS = `
  :where([data-scene]){display:none}
  :where(#dt-stage){position:absolute;left:0;top:0;width:1920px;height:1080px;overflow:hidden;transform-origin:0 0}
  :where(#dt-camera,#dt-pan){position:absolute;inset:0}
  :where(#dt-camera.dt-push){animation:dt-push var(--dt-scene-dur,20s) linear forwards}
  @keyframes dt-push{from{transform:scale(1)}to{transform:scale(1.03)}}
  :where(.dt-slide){position:absolute;inset:0}
  :where(.dt-slide.dt-enter){animation:dt-fadein var(--dt-xfade,.35s) ease both}
  :where(.dt-slide.dt-leave){animation:dt-fadeout var(--dt-xfade,.35s) ease both;pointer-events:none}
  :where(.dt-word){opacity:0;transition:opacity .12s ease}
  :where(.dt-word.dt-shown){opacity:1}
  @keyframes dt-fadein{from{opacity:0}to{opacity:1}}
  @keyframes dt-fadeout{from{opacity:1}to{opacity:0}}
  :where(.dt-reveal){opacity:0}
  :where(.dt-reveal[data-reveal=dim]){opacity:1}
  :where(.dt-reveal.dt-shown){opacity:1;animation:dt-rise .3s cubic-bezier(.2,.7,.2,1) both}
  @keyframes dt-rise{from{opacity:0;transform:translateY(10px)}to{opacity:1;transform:none}}
  :where(.dt-reveal.dt-shown[data-reveal=fade]){animation-name:dt-fadein}
  :where(.dt-reveal.dt-shown[data-reveal=draw]){animation-name:dt-draw;animation-timing-function:linear;stroke-dasharray:1;stroke-dashoffset:1}
  @keyframes dt-draw{from{stroke-dashoffset:1}to{stroke-dashoffset:0}}
  :where(.dt-reveal.dt-shown[data-reveal=drop]){animation-name:dt-drop;animation-duration:.35s}
  @keyframes dt-drop{from{opacity:0;transform:translateY(-24px)}to{opacity:1;transform:none}}
  :where(.dt-reveal.dt-shown[data-reveal=pop]){animation-name:dt-pop;animation-duration:.5s}
  @keyframes dt-pop{0%{opacity:0;transform:scale(.6)}45%{opacity:1;transform:scale(1.08)}100%{opacity:1;transform:scale(1)}}
  :where(.dt-reveal.dt-shown[data-reveal=dim]){animation-name:dt-dim;animation-duration:1.2s}
  @keyframes dt-dim{from{opacity:1}to{opacity:.16}}
  :where(.dt-reveal.dt-shown[data-reveal=instant]){animation:none}
  :where(.dt-frozen .dt-reveal.dt-shown,.dt-frozen .dt-slide,.dt-frozen #dt-camera){animation-duration:0s!important;animation-delay:0s!important}
  :where(#dt-hud){position:fixed;left:12px;top:12px;z-index:2147483000;font:14px/1.4 ui-monospace,Menlo,monospace;color:#fff;background:rgba(0,0,0,.6);padding:6px 10px;border-radius:6px;pointer-events:none;white-space:pre}
  :where(#dt-index){font:16px/1.5 system-ui,sans-serif;max-width:900px;margin:40px auto;padding:0 24px;color:inherit}
  :where(#dt-index h1){font-size:28px}:where(#dt-index h2){font-size:20px;margin-top:28px}
  :where(#dt-index a){color:inherit;font-weight:600;text-decoration:underline;text-underline-offset:3px;margin-right:16px}
  :where(#dt-index code){color:inherit;opacity:.7}
  :where(#dt-index .dt-slides){display:flex;flex-wrap:wrap;gap:8px 4px}
  `;

  const params = new URLSearchParams(location.search);
  // decktalk-probe.js, when a DeckTalk command injected it before this page loaded. It is read in
  // two places and nowhere else, and a page that no command is driving has none.
  const probe = window.__dtprobe ?? null;
  const SIGNAL = params.get("t0") === "signal"; // the recorder starts the clock itself
  const T0 = SIGNAL ? 0 : parseFloat(params.get("t0") || "0") || 0;
  const SPEED = Math.max(0.05, parseFloat(params.get("speed") || "1") || 1);
  const HUD = params.get("hud") === "1";
  const SCENES = new Map(); // id (string) -> scene
  const HANDLERS = new Map(); // cue id -> [fn]
  const GATES = []; // promises the page handed to DeckTalk.waitFor
  const GATE_SECONDS = 5; // how long readiness waits for one of those promises
  const state = {
    mode: "index",
    scene: null,
    slide: null, // the slide mounted last
    mounted: null, // the slide element mounted last, which every handler receives
    cues: [],
    fired: [],
    catalog: [],
    warnings: [], // what the runtime could not honor, read back by the recorder
    origin: null, // performance.now() at window load (ms)
    queue: [], // [{t, kind, id, run}] sorted by t
    started: false,
    scale: 1, // the stage's current fit scale, which turns a client rect into stage pixels
    words: null,
    frameAt: 0, // the rAF timestamp of the frame the queue last ran in (ms)
  };
  const frozen = params.has("slide");

  // ---- time ----------------------------------------------------------------------
  const setOrigin = () => {
    if (state.origin === null) state.origin = performance.now();
  };
  if (!SIGNAL) {
    if (document.readyState === "complete") setOrigin();
    else window.addEventListener("load", setOrigin, { once: true });
  }
  const now = () => (state.origin === null ? -Infinity : (performance.now() - state.origin) / 1000);

  // ---- DOM ------------------------------------------------------------------------
  let stage, camera, pan, hud;
  // Prepended rather than appended, so a page rule of the same weight wins on document order.
  const style = document.createElement("style");
  style.id = "dt-style";
  style.textContent = CSS;
  const head = document.head || document.documentElement;
  head.insertBefore(style, head.firstChild);

  function ensureStage() {
    if (stage) return;
    stage = document.getElementById("dt-stage");
    if (!stage) {
      stage = document.createElement("div");
      stage.id = "dt-stage";
      document.body.appendChild(stage);
    }
    camera = document.createElement("div");
    camera.id = "dt-camera";
    pan = document.createElement("div");
    pan.id = "dt-pan";
    camera.appendChild(pan);
    stage.appendChild(camera);
    if (HUD) {
      hud = document.createElement("div");
      hud.id = "dt-hud";
      document.body.appendChild(hud);
    }
    fitStage();
    window.addEventListener("resize", fitStage);
  }
  function fitStage() {
    const s = Math.min(window.innerWidth / 1920, window.innerHeight / 1080);
    const x = (window.innerWidth - 1920 * s) / 2,
      y = (window.innerHeight - 1080 * s) / 2;
    state.scale = s;
    stage.style.transform = `translate(${x}px, ${y}px) scale(${s})`;
  }
  const esc = (s) =>
    String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
  // A recording is only as good as the frames the compositor produced. A gap between two
  // animation frames longer than a few frames means a reveal was captured late, so the gap
  // is recorded for the recorder to judge.
  const frameGaps = [];
  const spokenLog = []; // what each data-text="spoken" element matched, for the recording log
  // When each cue was due, when it ran, and when the frame that ran it and the two frames after it
  // began, all in seconds on the narration clock. A cue that ran on time but whose next frame began
  // late was held up by the frame that drew it. A cue that ran late was held up before its frame.
  const cueLog = [];
  // Animation frames longer than 50 ms after narration t=0, with when Chromium presented them.
  const longFrames = [];
  const clockAt = (ms) => (state.origin === null ? null : +((ms - state.origin) / 1000).toFixed(3));
  function watchFrames() {
    let last = null;
    const tick = (t) => {
      if (last !== null && t - last > 100) frameGaps.push({ at: +now().toFixed(3), ms: Math.round(t - last) });
      last = t;
      requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
    if (
      !(window.PerformanceObserver && (PerformanceObserver.supportedEntryTypes || []).includes("long-animation-frame"))
    )
      return;
    new PerformanceObserver((list) => {
      for (const e of list.getEntries()) {
        if (state.origin === null || e.startTime < state.origin || longFrames.length >= 5000) continue;
        longFrames.push({
          start: clockAt(e.startTime),
          ms: Math.round(e.duration),
          render: clockAt(e.renderStart),
          presented: e.presentationTime ? clockAt(e.presentationTime) : null,
        });
      }
    }).observe({ type: "long-animation-frame" });
  }
  // What the reveals of one cue describe themselves as, joined into one sentence, or null.
  // The transcript reads it, so a reveal a viewer cannot see is written down in its author's words.
  function describeOf(id) {
    const said = [];
    for (const el of pan.querySelectorAll(`.dt-slide:not(.dt-leave) [data-cue="${cssEscape(id)}"]`)) {
      const text = (el.dataset.describe || "").trim();
      if (text && !said.includes(text)) said.push(text);
    }
    return said.length ? said.join(" ") : null;
  }
  function logCue(id, due) {
    const entry = {
      id,
      due: +due.toFixed(3),
      ran: +now().toFixed(3),
      frame: clockAt(state.frameAt),
      describe: describeOf(id),
      next: null,
      after: null,
    };
    cueLog.push(entry);
    requestAnimationFrame((t1) => {
      entry.next = clockAt(t1);
      requestAnimationFrame((t2) => {
        entry.after = clockAt(t2);
      });
    });
  }
  // Each distinct warning is recorded once and echoed to the console.
  function warn(msg) {
    if (state.warnings.includes(msg)) return;
    state.warnings.push(msg);
    console.warn(`decktalk: ${msg}`);
  }

  // ---- scenes ---------------------------------------------------------------------
  function makeSlide(sid, slide, i) {
    return {
      id: String(slide.id ?? `${sid}.${i + 1}`),
      hold: Number(slide.hold ?? 8),
      render: slide.render || null,
      markup: slide.markup || null, // a <template data-slide> element, cloned on every mount
      owns: (slide.owns || []).map(String),
      preview: normalizePreview(slide.preview),
      enter: slide.enter || null,
      on: slide.on || {},
    };
  }
  function scene(id, def) {
    const sid = String(id);
    const slides = (def.slides || []).map((slide, i) => makeSlide(sid, slide, i));
    SCENES.set(sid, { id: sid, name: def.name || `Scene ${sid}`, camera: def.camera || null, slides });
    return DeckTalk;
  }
  // preview: {a: 1.5, b: 3}, seconds after the mount, used by preview mode and by the freeze order.
  function normalizePreview(preview) {
    const out = new Map();
    if (preview && typeof preview === "object") {
      for (const [k, v] of Object.entries(preview)) out.set(String(k), Number(v) || 0);
    }
    return out;
  }
  // A whitespace-separated attribute: "a b" for data-owns, "a@1.5 b@3" for data-preview.
  const tokens = (raw) => (raw || "").trim().split(/\s+/).filter(Boolean);
  function slideFromTemplate(tpl) {
    const preview = {};
    for (const tok of tokens(tpl.dataset.preview)) {
      const i = tok.lastIndexOf("@");
      if (i > 0) preview[tok.slice(0, i)] = parseFloat(tok.slice(i + 1)) || 0;
    }
    return {
      id: tpl.dataset.slide,
      hold: tpl.dataset.hold ? parseFloat(tpl.dataset.hold) : undefined,
      owns: tokens(tpl.dataset.owns),
      preview,
      markup: tpl,
    };
  }
  // Scenes declared in markup: <div data-scene="3"> holding one <template data-slide> per slide.
  // A scene a script already registered keeps its definition, and each of its slides that defines
  // no render takes the markup of the template with its id.
  function readMarkupScenes() {
    document.querySelectorAll("[data-scene]").forEach((wrap) => {
      const sid = String(wrap.dataset.scene);
      const templates = [...wrap.querySelectorAll("template[data-slide]")].filter((tpl) => {
        if (tpl.dataset.slide) return true;
        warn(`scene "${sid}" holds a <template data-slide> with no id, so it is ignored`);
        return false;
      });
      const existing = SCENES.get(sid);
      if (!existing) {
        if (!templates.length) {
          warn(`scene "${sid}" holds no <template data-slide> element`);
          return;
        }
        scene(sid, {
          name: wrap.dataset.name || undefined,
          camera: wrap.dataset.camera || null,
          slides: templates.map(slideFromTemplate),
        });
        return;
      }
      for (const tpl of templates) {
        const slide = existing.slides.find((s) => s.id === tpl.dataset.slide);
        if (!slide) {
          existing.slides.push(makeSlide(sid, slideFromTemplate(tpl), existing.slides.length));
          continue;
        }
        if (slide.render) {
          warn(`slide "${slide.id}" has both a render function and a <template data-slide>, so the template is unused`);
          continue;
        }
        slide.markup = tpl;
        // The script already declared the slide, so only its markup comes from the template. A
        // hold, an owns list or a preview object on the template would be read by nobody.
        if (tpl.dataset.hold || tpl.dataset.owns || tpl.dataset.preview)
          warn(`slide "${slide.id}" is declared in script, so its template's hold, owns and preview are ignored`);
      }
    });
  }
  function on(id, fn) {
    const list = HANDLERS.get(String(id)) || [];
    list.push(fn);
    HANDLERS.set(String(id), list);
    return DeckTalk;
  }
  function findSlide(slideId) {
    for (const sc of SCENES.values()) {
      const slide = sc.slides.find((s) => s.id === String(slideId));
      if (slide) return { scene: sc, slide: slide };
    }
    return null;
  }
  // The slide that owns a cue id, within one scene (or across all scenes when sc is null). The id
  // prefix is the rule. The `owns` list is the exception a cue id that carries no slide id needs.
  function ownerOf(cueId, sc) {
    const scenes = sc ? [sc] : [...SCENES.values()];
    let best = null;
    for (const s of scenes) {
      for (const slide of s.slides) {
        if (slide.id === cueId || slide.owns.includes(cueId)) return { scene: s, slide: slide };
        if (cueId.startsWith(slide.id) && (!best || slide.id.length > best.slide.id.length))
          best = { scene: s, slide: slide };
      }
    }
    return best;
  }

  // ---- reveals --------------------------------------------------------------------
  // An element's text as its author wrote it, kept here rather than on the element, because the
  // page carries the author's attributes and the runtime's own markers are the contract's alone.
  const FULL_TEXT = new WeakMap();
  // The second a word of a data-text="spoken" element is spoken, held here for the same reason.
  const SPOKEN_AT = new WeakMap();
  // What a mounted slide has to stop when it leaves. A typewriter that is still typing and a word
  // reveal that is still waiting both paint a detached element, and a recording pays for the frame.
  const STOPS = new WeakMap();
  function onStop(el, fn) {
    const slideEl = el.closest(".dt-slide");
    if (!slideEl) return;
    const list = STOPS.get(slideEl) || [];
    list.push(fn);
    STOPS.set(slideEl, list);
  }
  function stopSlide(slideEl) {
    for (const fn of STOPS.get(slideEl) || []) fn();
    STOPS.delete(slideEl);
  }
  // data-text="count", "count first", "type 40ms" or "spoken": the first word is the mode, the rest its argument.
  const textMode = (el) => (el.dataset.text ?? "").trim().split(/\s+/)[0] || null;
  const textArg = (el) => (el.dataset.text ?? "").trim().split(/\s+/).slice(1).join(" ");
  function reveal(el) {
    if (el.classList.contains("dt-shown")) return;
    el.classList.add("dt-shown");
    const mode = textMode(el);
    if (mode === "count") countUp(el);
    if (mode === "type") typewriter(el);
    if (mode === "spoken") syncWords(el);
  }
  const wordKey = (w) => w.toLowerCase().replace(/[^a-z0-9]/g, "");
  function parseWords(raw) {
    return raw
      .split(",")
      .map((item) => {
        const at = item.lastIndexOf("@");
        return { k: wordKey(item.slice(0, at)), t: parseFloat(item.slice(at + 1)) };
      })
      .filter((w) => w.k && !Number.isNaN(w.t));
  }
  // Reveal one word at a time, each at the second the voice reaches it. The element's text
  // is matched against the section's spoken words, preferring the run nearest the cue.
  function syncWords(el) {
    const full = FULL_TEXT.get(el) ?? el.textContent;
    const words = state.words;
    if (frozen || !words || !words.length) return;
    const parts = full.split(/(\s+)/);
    const keys = parts
      .filter((t) => t.trim())
      .map(wordKey)
      .filter(Boolean);
    if (!keys.length) return;
    const t0 = now();
    let start = -1;
    for (let i = 0; i + keys.length <= words.length; i++) {
      let ok = true;
      for (let j = 0; j < keys.length; j++)
        if (words[i + j].k !== keys[j]) {
          ok = false;
          break;
        }
      if (!ok) continue;
      start = i;
      if (words[i].t >= t0 - 1.5) break;
    }
    if (start < 0) {
      warn(`data-text="spoken" text not found in the spoken words: "${full.slice(0, 40)}"`);
      return;
    }
    spokenLog.push({ text: full.slice(0, 24), cueAt: +t0.toFixed(3), runAt: words[start].t, n: keys.length });
    el.textContent = "";
    let wi = 0;
    parts.forEach((t) => {
      if (!t.trim()) {
        el.appendChild(document.createTextNode(t));
        return;
      }
      const span = document.createElement("span");
      span.className = "dt-word";
      span.textContent = t;
      SPOKEN_AT.set(span, wordKey(t) ? words[start + wi++].t : -Infinity);
      el.appendChild(span);
    });
    const entry = spokenLog[spokenLog.length - 1];
    let live = true;
    onStop(el, () => {
      live = false;
    });
    const tick = () => {
      if (!live) return;
      const n = now();
      let pending = false;
      el.querySelectorAll(".dt-word:not(.dt-shown)").forEach((sp) => {
        if (n >= SPOKEN_AT.get(sp) - 0.02) {
          sp.classList.add("dt-shown");
          if (entry && entry.firstOn === undefined) entry.firstOn = +n.toFixed(3);
        } else pending = true;
      });
      if (pending) requestAnimationFrame(tick);
    };
    tick();
  }
  function countUp(el) {
    const full = FULL_TEXT.get(el) ?? el.textContent;
    const m = textArg(el) === "first" ? full.match(/(\d[\d,]*(?:\.\d+)?)/) : full.match(/(\d[\d,]*(?:\.\d+)?)(?!.*\d)/);
    if (!m || frozen) {
      el.textContent = full;
      return;
    }
    const target = parseFloat(m[1].replace(/,/g, "")),
      decimals = (m[1].split(".")[1] || "").length,
      commas = m[1].includes(",");
    const fmt = (v) => {
      let s = v.toFixed(decimals);
      if (commas) s = s.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
      return full.slice(0, m.index) + s + full.slice(m.index + m[1].length);
    };
    const t0 = performance.now(),
      dur = (parseFloat(el.dataset.duration) * 1000 || 900) / (state.mode === "preview" ? SPEED : 1);
    let live = true;
    onStop(el, () => {
      live = false;
    });
    const tick = (t) => {
      if (!live) return;
      const k = Math.min(1, (t - t0) / dur),
        e = 1 - (1 - k) ** 3;
      el.textContent = fmt(target * e);
      if (k < 1) requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
  }
  function typewriter(el) {
    const full = FULL_TEXT.get(el) ?? el.textContent;
    if (frozen) {
      el.textContent = full;
      return;
    }
    const ms = (parseFloat(textArg(el)) || 40) / (state.mode === "preview" ? SPEED : 1);
    // The box keeps the size of its finished text, so nothing around it shifts while typing.
    el.style.minWidth = `${el.offsetWidth}px`;
    el.style.minHeight = `${el.offsetHeight}px`;
    el.textContent = "";
    let i = 0;
    const id = setInterval(() => {
      el.textContent = full.slice(0, ++i);
      if (i >= full.length) clearInterval(id);
    }, ms);
    onStop(el, () => clearInterval(id));
  }
  function typeset(root) {
    if (!window.katex) {
      if (root.querySelector("[data-tex]")) watchKatex();
      return;
    }
    root.querySelectorAll("[data-tex]:not([data-typeset])").forEach((el) => {
      try {
        window.katex.render(el.dataset.tex, el, {
          throwOnError: false,
          displayMode: el.hasAttribute("data-tex-display"),
        });
        el.dataset.typeset = "1";
      } catch (_) {
        /* keep plain text */
      }
      // With throwOnError off a bad value renders in red instead of throwing, so it is reported here.
      if (el.querySelector(".katex-error")) warn(`data-tex could not be parsed: "${el.dataset.tex}"`);
    });
  }
  // In cue mode a later slide mounts long after katexReady ran, so a slide that mounts [data-tex]
  // without KaTeX starts one check, five seconds later, which warns if it is still plain text.
  let katexWatched = false;
  function watchKatex() {
    if (katexWatched) return;
    katexWatched = true;
    setTimeout(() => {
      if (!window.katex && document.querySelector("[data-tex]:not([data-typeset])"))
        warn("KaTeX did not load within 5 s, so [data-tex] elements stay plain text");
    }, 5000);
  }
  // Resolves once window.katex exists, when the page needs it, or after 5 s with a warning.
  // A page needs KaTeX when it has a [data-tex] element in the document or a KaTeX script tag.
  function katexReady() {
    const wants = document.querySelector("[data-tex]") || document.querySelector('script[src*="katex"]');
    if (!wants || window.katex) return Promise.resolve(true);
    return new Promise((resolve) => {
      const started = performance.now();
      const tick = () => {
        if (window.katex) {
          if (pan) typeset(pan);
          resolve(true);
          return;
        }
        if (performance.now() - started > 5000) {
          warn("KaTeX did not load within 5 s, so [data-tex] elements stay plain text");
          resolve(true);
          return;
        }
        setTimeout(tick, 100);
      };
      tick();
    });
  }

  // ---- queue ----------------------------------------------------------------------
  function schedule(t, kind, id, run) {
    state.queue.push({ t, kind, id, run });
    // A mount at one second comes before anything else at that second, because a cue and a timed
    // reveal both need the slide that carries them to be on the stage already.
    const rank = (kind) => (kind === "mount" ? 0 : 1);
    state.queue.sort((a, b) => a.t - b.t || rank(a.kind) - rank(b.kind));
  }
  function loop(frameAt) {
    state.frameAt = frameAt ?? performance.now();
    const t = now();
    while (state.queue.length && state.queue[0].t <= t) state.queue.shift().run();
    if (hud)
      hud.textContent = `${state.mode} · scene ${state.scene?.id ?? "-"} · slide ${state.slide?.id ?? "-"} · t ${Math.max(0, t).toFixed(2)}s`;
    requestAnimationFrame(loop);
  }

  // ---- mounting -------------------------------------------------------------------
  // The slide element with its markup in place and its equations typeset, but with no reveal
  // wired and no handler run. Mounting and measuring both start here.
  function buildSlide(sc, slide) {
    const el = document.createElement("div");
    el.className = "dt-slide";
    el.dataset.slide = slide.id;
    if (slide.markup) el.appendChild(slide.markup.content.cloneNode(true));
    else if (slide.render) {
      // Every other page callback is wrapped, and this one is reached from the readiness chain, so
      // a render that throws has to become a warning rather than a rejection nobody expects.
      try {
        el.innerHTML = slide.render({ scene: sc, slide: slide, frozen });
      } catch (err) {
        warn(`slide "${slide.id}" has a render function that threw: ${err}`);
      }
    }
    typeset(el);
    return el;
  }
  // listedCues is the set of cue ids in ?cues=, given in cue mode only. held is the set of cue
  // ids whose elements a freeze at one cue leaves hidden, given in freeze mode only. `at` is the
  // second on the narration clock this mount counts as, which the first mount of a run fixes at
  // T0 because it happens before the clock starts.
  function mountSlide(sc, slide, listedCues, held, at) {
    const old = pan.querySelector(".dt-slide:not(.dt-leave)");
    const slideEl = buildSlide(sc, slide);
    // The first slide of a section is already on the stage when the recorder's cover lifts, so it
    // enters with no animation. An animated entrance there would draw the empty stage for a frame.
    if (old) slideEl.classList.add("dt-enter");
    const mountT = at ?? now();
    state.slide = slide;
    state.mounted = slideEl;
    if (old) {
      old.classList.remove("dt-enter");
      old.classList.add("dt-leave");
      setTimeout(() => {
        stopSlide(old);
        old.remove();
      }, 400);
    }
    // A <script> inside a cloned template runs on this line, so by now exactly one slide is live
    // and a reveal measures the box its author laid out.
    pan.appendChild(slideEl);
    // A text mode runs when its element reveals, so an element with no cue and no timer never runs it.
    slideEl.querySelectorAll("[data-text]").forEach((el) => {
      if (el.hasAttribute("data-cue") || el.hasAttribute("data-delay")) return;
      warn(
        `data-text="${el.dataset.text}" on an element without data-cue or data-delay never reveals, so add data-delay="0"`,
      );
    });
    slideEl.querySelectorAll("[data-cue],[data-delay]").forEach((el) => {
      el.classList.add("dt-reveal");
      // A fade on the container would fight the per-word reveal, so data-text="spoken" implies no animation.
      if (textMode(el) === "spoken" && !el.hasAttribute("data-reveal")) el.dataset.reveal = "instant";
      if (textMode(el)) FULL_TEXT.set(el, el.textContent);
      if (el.dataset.duration)
        el.style.animationDuration = `${parseFloat(el.dataset.duration) / (state.mode === "preview" ? SPEED : 1)}s`;
      const cueId = el.dataset.cue;
      if (cueId && el.hasAttribute("data-delay"))
        warn(`data-cue "${cueId}" is on an element that also sets data-delay, so the delay is ignored`);
      if (frozen) {
        if (!(cueId && held?.has(cueId))) reveal(el);
        return;
      }
      if (cueId) {
        if (!listedCues) return; // preview mode: the slide's preview seconds fire it
        if (listedCues.has(cueId)) return; // its cue event reveals it
        warn(`data-cue "${cueId}" is not in ?cues=, so it shows as soon as the slide mounts`);
        reveal(el);
        return;
      }
      const delay = (parseFloat(el.dataset.delay) || 0) / (state.mode === "preview" ? SPEED : 1);
      if (delay <= 0) reveal(el);
      else schedule(mountT + delay, "reveal", "", () => reveal(el));
    });
    if (slide.enter) {
      try {
        slide.enter(slideEl, { id: slide.id, at: +mountT.toFixed(3), frozen, slideId: slide.id });
      } catch (err) {
        warn(`slide "${slide.id}" has an enter function that threw: ${err}`);
      }
    }
    return slideEl;
  }
  // ---- firing a cue ---------------------------------------------------------------
  function fire(id) {
    state.fired.push(id);
    const hits = pan.querySelectorAll(`.dt-slide:not(.dt-leave) [data-cue="${cssEscape(id)}"]`);
    hits.forEach(reveal);
    const slide = state.slide,
      slideEl = state.mounted;
    const ctx = { id, at: +now().toFixed(3), frozen, slideId: slide ? slide.id : null };
    const handled = !!(slide && typeof slide.on[id] === "function");
    if (handled) {
      try {
        slide.on[id](slideEl, ctx);
      } catch (err) {
        warn(`cue "${id}" has a slide handler that threw: ${err}`);
      }
    }
    (HANDLERS.get(id) || []).forEach((fn) => {
      try {
        fn(slideEl, ctx);
      } catch (err) {
        warn(`cue "${id}" has a handler that threw: ${err}`);
      }
    });
    // A cue that reveals nothing and runs nothing is almost always a typo between cues.json,
    // the slide's preview object and a data-cue attribute, so it is reported rather than ignored.
    if (!hits.length && !handled && !HANDLERS.has(id) && !findSlide(id))
      warn(`cue "${id}" matches no element, handler, or slide`);
  }
  const cssEscape = (s) => (window.CSS?.escape ? window.CSS.escape(s) : s.replace(/["\\]/g, "\\$&"));
  function startCamera(sc, seconds) {
    if (sc.camera !== "push" || frozen) return;
    camera.style.setProperty("--dt-scene-dur", `${Math.max(4, seconds)}s`);
    camera.classList.add("dt-push");
  }

  // ---- modes ----------------------------------------------------------------------
  // index, preview, cue and freeze are the modes. `live`, which takes its clock from an audio
  // element's play, pause and seek, is reserved for the site's hero and is not implemented.
  function parseCues(raw) {
    return raw
      .split(",")
      .map((tok) => tok.trim())
      .filter(Boolean)
      .map((tok) => {
        const i = tok.lastIndexOf("@");
        return { id: tok.slice(0, i), t: parseFloat(tok.slice(i + 1)) };
      })
      .filter((c) => c.id && !Number.isNaN(c.t))
      .sort((a, b) => a.t - b.t);
  }
  function playCues(sc, cues) {
    state.mode = "cue";
    watchFrames();
    state.scene = sc;
    state.cues = cues;
    const listed = new Set(cues.map((c) => c.id));
    const mountAt = new Map(); // slide -> t
    for (const c of cues) {
      const owner = ownerOf(c.id, sc);
      if (!owner) {
        if (!HANDLERS.has(c.id)) warn(`unknown cue id ${c.id} (no slide id or owns list matches it)`);
        continue;
      }
      mountAt.set(owner.slide, Math.min(mountAt.get(owner.slide) ?? Infinity, c.t));
    }
    const slides = [...mountAt.entries()].sort((a, b) => a[1] - b[1]);
    if (!slides.length) {
      warn("no slide owns any listed cue, so nothing will mount");
      return;
    }
    sc.slides.forEach((slide) => {
      if (!mountAt.has(slide)) warn(`slide "${slide.id}" owns no cue in ?cues=, so it never appears`);
    });
    const last = slides[slides.length - 1][0];
    const tEnd = Math.max(...cues.map((c) => c.t));
    startCamera(sc, tEnd - T0 + 8);
    // The first cued slide is mounted here, before the clock starts and before the recorder's
    // cover comes off, so no frame of the recording is ever drawn on an empty stage. Its own
    // reveals still wait for their cues.
    mountSlide(sc, slides[0][0], listed, null, T0);
    if (slides[0][0] === last) document.body.dataset.done = "1";
    for (const [slide, t] of slides.slice(1)) {
      schedule(T0 + t, "mount", slide.id, () => {
        mountSlide(sc, slide, listed);
        if (slide === last) document.body.dataset.done = "1";
      });
    }
    for (const c of cues) {
      schedule(T0 + c.t, "cue", c.id, () => {
        logCue(c.id, T0 + c.t);
        fire(c.id);
      });
    }
  }
  function playPreview(sc) {
    state.mode = "preview";
    state.scene = sc;
    let t = T0;
    const total = sc.slides.reduce((s, slide) => s + slide.hold, 0) / SPEED;
    startCamera(sc, total);
    sc.slides.forEach((slide, i) => {
      const at = t;
      // A cue at or past the hold fires after the next slide has mounted, where none of its
      // elements or handlers exist. The last slide holds until the end, so its cues always land.
      if (i < sc.slides.length - 1) {
        slide.preview.forEach((delay, id) => {
          if (delay >= slide.hold)
            warn(
              `slide "${slide.id}" fires "${id}" at ${delay} s but holds ${slide.hold} s, so it fires on the next slide`,
            );
        });
      }
      const fireOwn = () => {
        slide.preview.forEach((delay, id) => {
          schedule(at + delay / SPEED, "cue", id, () => fire(id));
        });
        if (i === sc.slides.length - 1) document.body.dataset.done = "1";
      };
      // As in cue mode, the first slide is on the stage before the first frame is drawn.
      if (i === 0) {
        mountSlide(sc, slide, null, null, T0);
        fireOwn();
      } else {
        schedule(at, "mount", slide.id, () => {
          mountSlide(sc, slide, null);
          fireOwn();
        });
      }
      t += slide.hold / SPEED;
    });
  }
  // A slide's cue ids in preview order: its preview cues by their seconds, then the cues it owns
  // and the cues its elements name, each in declared order.
  function cueOrder(slide) {
    const timed = [...slide.preview.entries()].map(([id, delay], i) => ({ id, delay, i }));
    const rest = [...slide.owns, ...markupCues(slide)].filter((id) => !slide.preview.has(id));
    return [
      ...timed.sort((a, b) => a.delay - b.delay || a.i - b.i).map((c) => c.id),
      ...rest.filter((id, i) => rest.indexOf(id) === i),
    ];
  }
  // The cue ids a <template data-slide> names, in document order, so an attributes-only slide
  // needs no preview object to have an order. A slide built by a render function is not rendered
  // here, because a render may touch the live page, so its order is its owns and preview lists.
  const MARKUP_CUES = new WeakMap();
  function markupCues(slide) {
    if (!MARKUP_CUES.has(slide))
      MARKUP_CUES.set(
        slide,
        slide.markup ? [...slide.markup.content.querySelectorAll("[data-cue]")].map((el) => el.dataset.cue) : [],
      );
    return MARKUP_CUES.get(slide);
  }

  // ---- the catalog ----------------------------------------------------------------
  function catalogOf() {
    return [...SCENES.values()].map((sc) => ({
      scene: sc.id,
      name: sc.name,
      camera: sc.camera,
      slides: sc.slides.map((s) => s.id),
      cues: Object.fromEntries(sc.slides.map((s) => [s.id, cueOrder(s)])),
    }));
  }
  // ---- freeze and the index page --------------------------------------------------
  // Freeze a slide with its cues fired in preview order. Every cue fires, which is the still a
  // page author opens from the index and the screenshot command records, unless the probe hands
  // back a shorter list. An element that waits for a cue that never fires stays hidden.
  function freeze(slideId) {
    const found = findSlide(slideId);
    if (!found) return renderIndex(`unknown slide ${slideId}`);
    state.mode = "freeze";
    state.scene = found.scene;
    document.documentElement.classList.add("dt-frozen");
    const order = cueOrder(found.slide);
    const firing = probe ? probe.freezeCues(order, found.slide.id, warn) : order;
    mountSlide(found.scene, found.slide, null, new Set(order.slice(firing.length)), 0);
    for (const id of firing) fire(id);
    document.body.dataset.done = "1";
  }
  function renderIndex(note) {
    state.mode = "index";
    if (stage) stage.style.display = "none";
    document.body.style.overflow = "auto";
    const div = document.createElement("div");
    div.id = "dt-index";
    const title = document.title || "decktalk scenes";
    div.innerHTML = `<h1>${esc(title)}</h1>${note ? `<p><b>${esc(note)}</b></p>` : ""}
      <p>The stage is 1920 by 1080. <code>?scene=N</code> previews a scene. <code>?slide=ID</code> freezes a slide.
      <code>&amp;speed=2</code> runs a preview faster. <code>&amp;hud=1</code> shows the clock.
      <code>&amp;cues=id@s,…</code> with <code>&amp;t0=</code> cues a scene from narration.</p>
      ${[...SCENES.values()]
        .map(
          (sc) => `<h2>Scene ${esc(sc.id)} — ${esc(sc.name)} <a href="?scene=${esc(sc.id)}">▶ play</a></h2>
        <div class="dt-slides">${sc.slides
          .map((slide) => {
            const cues = cueOrder(slide);
            return `<a href="?slide=${esc(slide.id)}">slide ${esc(slide.id)} <code>(${slide.hold}s${cues.length ? `, cues ${esc(cues.join(" "))}` : ""})</code></a>`;
          })
          .join("")}</div>`,
        )
        .join("")}`;
    document.body.appendChild(div);
  }

  // ---- start ----------------------------------------------------------------------
  // A page's own readiness promise is its own business, so a rejection is a warning rather than a
  // failure, and a promise that never settles gives up after GATE_SECONDS with a warning of its own.
  function gatesSettled() {
    if (!GATES.length) return Promise.resolve(null);
    let done = false;
    const all = Promise.all(
      GATES.map((promise) =>
        Promise.resolve(promise).then(null, (err) => warn(`a DeckTalk.waitFor promise rejected: ${err}`)),
      ),
    ).then(() => {
      done = true;
    });
    const limit = new Promise((resolve) =>
      setTimeout(() => {
        if (!done) warn(`a DeckTalk.waitFor promise did not settle within ${GATE_SECONDS} s`);
        resolve(null);
      }, GATE_SECONDS * 1000),
    );
    return Promise.race([all, limit]);
  }
  function start() {
    if (state.started) return;
    state.started = true;
    readMarkupScenes();
    state.catalog = catalogOf();
    ensureStage();
    const cuesRaw = params.get("cues");
    const cues = cuesRaw ? parseCues(cuesRaw) : [];
    const wordsRaw = params.get("words");
    state.words = wordsRaw ? parseWords(wordsRaw) : null;
    let index = false;
    if (frozen) {
      freeze(params.get("slide"));
    } else if (params.has("scene") || cues.length) {
      let sc = params.has("scene") ? SCENES.get(String(params.get("scene"))) : null;
      if (!sc && cues.length) sc = ownerOf(cues[0].id, null)?.scene || null;
      if (!sc) renderIndex(`unknown scene ${params.get("scene") ?? ""}`);
      else if (cues.length) playCues(sc, cues);
      else playPreview(sc);
    } else {
      index = true;
    }
    // Fonts, then KaTeX, then whatever the page is waiting for, and the index page last, so a
    // reader that awaits `ready` sees a laid-out page. Every reader treats a rejection as
    // readiness, so the chain never rejects: a gate that fails and a gate that never settles are
    // both warnings, and the index page is drawn either way. A probe measures the catalog's boxes
    // first, because the index page hides the stage those boxes are measured in.
    const fonts = Promise.resolve(document.fonts ? document.fonts.ready : null).catch(() => null);
    window.__decktalk.ready = fonts
      .then(katexReady)
      .then(gatesSettled)
      .then(() => {
        if (index) {
          probe?.measure(state.catalog, { scenes: SCENES, pan, origin: stage, scale: state.scale, build: buildSlide });
          renderIndex();
        }
        return true;
      });
    requestAnimationFrame(loop);
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
  else queueMicrotask(start);

  const DeckTalk = {
    version: VERSION,
    scene,
    on,
    start,
    startClock: setOrigin,
    reveal,
    fire,
    findSlide,
    // A page with a readiness condition of its own adds it here instead of replacing a global.
    waitFor(promise) {
      GATES.push(Promise.resolve(promise));
      return DeckTalk;
    },
    get scenes() {
      return SCENES;
    },
    params,
  };
  window.DeckTalk = DeckTalk;
  window.__decktalk = {
    version: VERSION,
    ready: null, // the readiness promise, owned by the runtime and set by start()
    get frameGaps() {
      return frameGaps;
    },
    get spokenLog() {
      return spokenLog;
    },
    get cueLog() {
      return cueLog;
    },
    get longFrames() {
      return longFrames;
    },
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
      return state.warnings;
    },
    get catalog() {
      return state.catalog.length ? state.catalog : catalogOf();
    },
    now,
  };
})();
