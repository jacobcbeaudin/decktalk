/* decktalk-runtime.js — gives an HTML page the recorder's page contract.
 *
 * Include it, define scenes, and `decktalk record` can drive the page from narration:
 *
 *   <script src="decktalk-runtime.js"></script>
 *   <script>
 *     DeckTalk.scene(3, { name: "How often", camera: "push", slides: [
 *       { id: "3.1", hold: 8, render: () => `
 *           <h1>Value still listed</h1>
 *           <p data-delay="1.2">by hour, through first pitch</p>
 *           <div class="tile" data-cue="3.1b" data-text="count">1 in 10</div>` },
 *       { id: "3.2", hold: 6, render: () => `…` },
 *     ]});
 *     DeckTalk.on("3.2b", (slide) => slide.querySelector(".bars").classList.add("grow"));
 *   </script>
 *
 * URL contract (what the recorder and the screenshot command send)
 *   ?scene=N                 play scene N
 *   &cues=id@s,id@s,…        cue mode: fire cue `id` at `s` seconds after narration t=0.
 *                            A slide mounts at the earliest of its cues (the first cued slide
 *                            at t=0, so the stage is never empty); slides with no cue never
 *                            show; the last cued slide holds forever.
 *   &t0=S                    seconds after page load at which narration t=0 falls;
 *                            t0=signal waits for DeckTalk.startClock() (what the recorder sends)
 *   &prevwords=word@s,…      the previous section's spoken words, in seconds after that section
 *                            starts. The runtime ignores them; a page that opens on the previous
 *                            section's last frame reads them from DeckTalk.params
 *   ?slide=ID                freeze slide ID with everything revealed (screenshots, review)
 *   &after=ID                with ?slide=, freeze at cue ID instead: the slide's cues fire in
 *                            preview order up to and including ID, and later cues stay hidden
 *   &before=ID               with ?slide= and no &after=, freeze just before cue ID: the slide's cues
 *                            before ID in preview order fire, and ID and later cues stay hidden
 *   &speed=X                 preview time scale (cue mode ignores it)
 *   &hud=1                   overlay scene · slide · clock
 *   (no params)              index page listing every scene and slide
 *
 * Markup inside a slide
 *   data-cue="id"            reveal on cue `id` (cue mode) or at data-delay seconds after the
 *                            slide mounts (preview, or when the cue is not in ?cues=)
 *   data-delay="s"           reveal s seconds after the slide mounts (no cue needed)
 *   data-reveal="rise|fade|draw|drop|pop|dim|instant"   reveal effect (default rise)
 *   data-duration="s"        animation length, and the length of a count-up
 *   data-text="count"        count the last number in the text up from 0 on reveal;
 *                            data-text="count first" counts the first number instead
 *   data-text="type 40ms"    type the text at that many milliseconds per character on reveal
 *   data-text="spoken"       reveal the element word by word as each word is spoken. The text
 *                            must match a run of the section's spoken words, which the recorder
 *                            passes as &words=word@s,word@s,… (punctuation and case are ignored).
 *                            The element gets data-reveal="instant" unless it sets its own data-reveal.
 *   data-tex="…"             typeset with KaTeX if window.katex is present; data-tex-display
 *                            typesets in display mode
 *
 * Cue ownership: a cue belongs to the slide with the same id, or whose `owns` list or
 * `preview` object names it, or whose id is the longest prefix of the cue id ("4.2b1" -> slide
 * "4.2", "9a" -> "9"). `owns` lists cue ids; `preview` maps a cue id to its seconds after the
 * mount, which preview mode uses.
 *
 * Handlers: a slide's `enter(slide, ctx)`, its `on[id](slide, ctx)`, and every
 * `DeckTalk.on(id, (slide, ctx) => …)` receive the mounted slide element and a context
 * object { id, at, frozen, slideId }. `id` is the cue id (the slide id for `enter`), `at` is the
 * second after narration t=0, `frozen` is true in freeze mode, and `slideId` is the id of the
 * mounted slide. A handler that takes no arguments keeps working.
 *
 * The page exposes window.__decktalk { mode, scene, slide, cues, fired, catalog, warnings, ready, now() }
 * and sets window.__decktalk.ready unless the page set it first. That promise resolves once fonts
 * are loaded and, when the page uses [data-tex] or loads KaTeX, once window.katex exists
 * (polled for up to 5 s). The mode is one of index, preview, cue and freeze. Anything the runtime
 * cannot honor (an unknown cue id, a cue no slide owns, a cue that reveals no element and runs no
 * handler, a slide that owns no listed cue, a data-cue that is not listed, a text mode with no
 * trigger, a preview cue past its slide's hold, KaTeX never arriving or refusing a data-tex value)
 * is pushed onto __decktalk.warnings, which the recorder reads back and logs. A warning never throws.
 */
(() => {
  // The runtime loads as a classic <script>, not a module, so this directive is what makes it strict.
  "use strict";
  const CSS = `
  #dt-stage{position:absolute;left:0;top:0;width:1920px;height:1080px;overflow:hidden;transform-origin:0 0}
  #dt-camera,#dt-pan{position:absolute;inset:0}
  #dt-camera.dt-push{animation:dt-push var(--dt-scene-dur,20s) linear forwards}
  @keyframes dt-push{from{transform:scale(1)}to{transform:scale(1.03)}}
  .dt-slide{position:absolute;inset:0}
  .dt-slide.dt-enter{animation:dt-fadein var(--dt-xfade,.35s) ease both}
  .dt-slide.dt-enter.dt-first{animation:dt-slidein .3s ease both}
  .dt-slide.dt-leave{animation:dt-fadeout var(--dt-xfade,.35s) ease both;pointer-events:none}
  .dt-word{opacity:0;transition:opacity .12s ease}
  .dt-word.dt-shown{opacity:1}
  @keyframes dt-slidein{from{opacity:0;transform:translateX(24px)}to{opacity:1;transform:none}}
  @keyframes dt-fadein{from{opacity:0}to{opacity:1}}
  @keyframes dt-fadeout{from{opacity:1}to{opacity:0}}
  .dt-reveal{opacity:0}
  .dt-reveal[data-reveal=dim]{opacity:1}
  .dt-reveal.dt-shown{opacity:1;animation:dt-rise .3s cubic-bezier(.2,.7,.2,1) both}
  @keyframes dt-rise{from{opacity:0;transform:translateY(10px)}to{opacity:1;transform:none}}
  .dt-reveal.dt-shown[data-reveal=fade]{animation-name:dt-fadein}
  .dt-reveal.dt-shown[data-reveal=draw]{animation-name:dt-draw;animation-timing-function:linear;stroke-dasharray:1;stroke-dashoffset:1}
  @keyframes dt-draw{from{stroke-dashoffset:1}to{stroke-dashoffset:0}}
  .dt-reveal.dt-shown[data-reveal=drop]{animation-name:dt-drop;animation-duration:.35s}
  @keyframes dt-drop{from{opacity:0;transform:translateY(-24px)}to{opacity:1;transform:none}}
  .dt-reveal.dt-shown[data-reveal=pop]{animation-name:dt-pop;animation-duration:.5s}
  @keyframes dt-pop{0%{opacity:0;transform:scale(.6)}45%{opacity:1;transform:scale(1.08)}100%{opacity:1;transform:scale(1)}}
  .dt-reveal.dt-shown[data-reveal=dim]{animation-name:dt-dim;animation-duration:1.2s}
  @keyframes dt-dim{from{opacity:1}to{opacity:.16}}
  .dt-reveal.dt-shown[data-reveal=instant]{animation:none}
  .dt-frozen .dt-reveal.dt-shown,.dt-frozen .dt-slide,.dt-frozen #dt-camera{animation-duration:0s!important;animation-delay:0s!important}
  #dt-hud{position:fixed;left:12px;top:12px;z-index:2147483000;font:14px/1.4 ui-monospace,Menlo,monospace;color:#fff;background:rgba(0,0,0,.6);padding:6px 10px;border-radius:6px;pointer-events:none;white-space:pre}
  #dt-index{font:16px/1.5 system-ui,sans-serif;max-width:900px;margin:40px auto;padding:0 24px;color:inherit}
  #dt-index h1{font-size:28px}#dt-index h2{font-size:20px;margin-top:28px}
  #dt-index a{color:inherit;font-weight:600;text-decoration:underline;text-underline-offset:3px;margin-right:16px}#dt-index code{color:inherit;opacity:.7}
  #dt-index .dt-slides{display:flex;flex-wrap:wrap;gap:8px 4px}
  `;

  const params = new URLSearchParams(location.search);
  const SIGNAL = params.get("t0") === "signal"; // the recorder starts the clock itself
  const T0 = SIGNAL ? 0 : parseFloat(params.get("t0") || "0") || 0;
  const SPEED = Math.max(0.05, parseFloat(params.get("speed") || "1") || 1);
  const HUD = params.get("hud") === "1";
  const SCENES = new Map(); // id (string) -> scene
  const HANDLERS = new Map(); // cue id -> [fn]
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
    lastMountAt: 0,
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
  let stage, cam, pan, hud;
  function ensureStage() {
    if (stage) return;
    const style = document.createElement("style");
    style.id = "dt-style";
    style.textContent = CSS;
    document.head.appendChild(style);
    stage = document.getElementById("dt-stage");
    if (!stage) {
      stage = document.createElement("div");
      stage.id = "dt-stage";
      document.body.appendChild(stage);
    }
    cam = document.createElement("div");
    cam.id = "dt-camera";
    pan = document.createElement("div");
    pan.id = "dt-pan";
    cam.appendChild(pan);
    stage.appendChild(cam);
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
    stage.style.transform = `translate(${x}px, ${y}px) scale(${s})`;
  }
  const esc = (s) =>
    String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
  // Each distinct warning is recorded once and echoed to the console.
  // A recording is only as good as the frames the compositor produced. A gap between two
  // animation frames longer than a few frames means a reveal was captured late, so the gap
  // is recorded for the recorder to judge.
  const frameGaps = [];
  const spokenLog = []; // what each data-text="spoken" element matched, for the recording log
  // When each cue was due, when it ran, and when the frame that ran it and the two frames after it
  // began, all in seconds on the narration clock. A cue that ran on time but whose next frame began
  // late was held up by the frame that drew it; a cue that ran late was held up before its frame.
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
  function logCue(id, due) {
    const entry = {
      id,
      due: +due.toFixed(3),
      ran: +now().toFixed(3),
      frame: clockAt(state.frameAt),
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
  function warn(msg) {
    if (state.warnings.includes(msg)) return;
    state.warnings.push(msg);
    console.warn(`decktalk: ${msg}`);
  }

  // ---- scenes ---------------------------------------------------------------------
  function scene(id, def) {
    const sid = String(id);
    const slides = (def.slides || []).map((st, i) => ({
      id: String(st.id ?? `${sid}.${i + 1}`),
      hold: Number(st.hold ?? 8),
      render: st.render || (() => ""),
      cues: normalizeCues(st.owns, st.preview),
      enter: st.enter || null,
      on: st.on || {},
    }));
    SCENES.set(sid, { id: sid, name: def.name || `Scene ${sid}`, camera: def.camera || null, slides });
    return DeckTalk;
  }
  // owns: ["a","b"] (ownership only) and preview: {a: 1.5, b: 3} (ownership + preview seconds after mount)
  function normalizeCues(owns, preview) {
    const out = new Map();
    if (Array.isArray(owns)) {
      for (const k of owns) out.set(String(k), null);
    }
    if (preview && typeof preview === "object") {
      for (const [k, v] of Object.entries(preview)) out.set(String(k), Number(v) || 0);
    }
    return out;
  }
  function on(id, fn) {
    const list = HANDLERS.get(String(id)) || [];
    list.push(fn);
    HANDLERS.set(String(id), list);
    return DeckTalk;
  }
  function findSlide(slideId) {
    for (const sc of SCENES.values()) {
      const st = sc.slides.find((s) => s.id === String(slideId));
      if (st) return { scene: sc, slide: st };
    }
    return null;
  }
  // The slide that owns a cue id, within one scene (or across all scenes when sc is null).
  function ownerOf(cueId, sc) {
    const scenes = sc ? [sc] : [...SCENES.values()];
    let best = null;
    for (const s of scenes) {
      for (const st of s.slides) {
        if (st.id === cueId || st.cues.has(cueId)) return { scene: s, slide: st };
        if (cueId.startsWith(st.id) && (!best || st.id.length > best.slide.id.length)) best = { scene: s, slide: st };
      }
    }
    return best;
  }

  // ---- reveals --------------------------------------------------------------------
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
    const full = el.dataset.ccFull ?? el.textContent;
    const words = state.words;
    if (frozen || !words || !words.length) return;
    const tokens = full.split(/(\s+)/);
    const keys = tokens
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
    tokens.forEach((t) => {
      if (!t.trim()) {
        el.appendChild(document.createTextNode(t));
        return;
      }
      const span = document.createElement("span");
      span.className = "dt-word";
      span.textContent = t;
      span.dataset.spokenAt = wordKey(t) ? words[start + wi++].t : -Infinity;
      el.appendChild(span);
    });
    const entry = spokenLog[spokenLog.length - 1];
    const tick = () => {
      const n = now();
      let pending = false;
      el.querySelectorAll(".dt-word:not(.dt-shown)").forEach((sp) => {
        if (n >= parseFloat(sp.dataset.spokenAt) - 0.02) {
          sp.classList.add("dt-shown");
          if (entry && entry.firstOn === undefined) entry.firstOn = +n.toFixed(3);
        } else pending = true;
      });
      if (pending) requestAnimationFrame(tick);
    };
    tick();
  }
  function countUp(el) {
    const full = el.dataset.ccFull ?? el.textContent;
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
    const tick = (t) => {
      const k = Math.min(1, (t - t0) / dur),
        e = 1 - (1 - k) ** 3;
      el.textContent = fmt(target * e);
      if (k < 1) requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
  }
  function typewriter(el) {
    const full = el.dataset.ccFull ?? el.textContent;
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
      if (el.querySelector(".katex-error"))
        warn(
          `data-tex could not be parsed: "${el.dataset.tex}" (write \\\\ for every backslash inside a template literal)`,
        );
    });
  }
  // In cue mode the first slide mounts after the clock starts, so katexReady finds no [data-tex]
  // element and resolves without waiting. A slide that mounts [data-tex] without KaTeX therefore
  // starts one check, five seconds later, which warns if the equations are still plain text.
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
    state.queue.sort((a, b) => a.t - b.t || (a.kind === "mount" ? -1 : b.kind === "mount" ? 1 : 0));
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
  let mounted = 0;
  // listedCues is the set of cue ids in ?cues=, given in cue mode only. held is the set of cue
  // ids whose elements a freeze at one cue leaves hidden, given in freeze mode only.
  function mountSlide(sc, st, listedCues, held) {
    const old = pan.querySelector(".dt-slide:not(.dt-leave)");
    const slide = document.createElement("div");
    slide.className = `dt-slide dt-enter${mounted === 0 ? " dt-first" : ""}`;
    slide.dataset.slide = st.id;
    slide.innerHTML = st.render({ scene: sc, slide: st, frozen });
    mounted++;
    typeset(slide);
    const mountT = now();
    state.slide = st;
    state.mounted = slide;
    state.lastMountAt = mountT;
    // A text mode runs when its element reveals, so an element with no cue and no timer never runs it.
    slide.querySelectorAll("[data-text]").forEach((el) => {
      if (el.hasAttribute("data-cue") || el.hasAttribute("data-delay")) return;
      warn(
        `data-text="${el.dataset.text}" on an element without data-cue or data-delay never reveals, so add data-delay="0"`,
      );
    });
    slide.querySelectorAll("[data-cue],[data-delay]").forEach((el) => {
      el.classList.add("dt-reveal");
      // A fade on the container would fight the per-word reveal, so data-text="spoken" implies no animation.
      if (textMode(el) === "spoken" && !el.hasAttribute("data-reveal")) el.dataset.reveal = "instant";
      if (textMode(el)) el.dataset.ccFull = el.textContent;
      if (el.dataset.duration)
        el.style.animationDuration = `${parseFloat(el.dataset.duration) / (state.mode === "preview" ? SPEED : 1)}s`;
      const cueId = el.dataset.cue;
      if (frozen) {
        if (!(cueId && held?.has(cueId))) reveal(el);
        return;
      }
      if (cueId && listedCues?.has(cueId)) return; // its cue event reveals it
      if (cueId && listedCues)
        warn(`data-cue "${cueId}" is not in ?cues=, so it reveals at its data-delay time after the mount`);
      const at = (parseFloat(el.dataset.delay) || 0) / (state.mode === "preview" ? SPEED : 1);
      if (at <= 0) reveal(el);
      else schedule(mountT + at, "reveal", cueId || "", () => reveal(el));
    });
    if (old) {
      old.classList.remove("dt-enter");
      old.classList.add("dt-leave");
      setTimeout(() => old.remove(), 400);
    }
    pan.appendChild(slide);
    if (st.enter) {
      try {
        st.enter(slide, { id: st.id, at: +mountT.toFixed(3), frozen, slideId: st.id });
      } catch (e) {
        console.error(e);
      }
    }
    return slide;
  }
  function fire(id) {
    state.fired.push(id);
    const hits = pan.querySelectorAll(`.dt-slide:not(.dt-leave) [data-cue="${CSS_escape(id)}"]`);
    hits.forEach(reveal);
    const st = state.slide,
      slide = state.mounted;
    const ctx = { id, at: +now().toFixed(3), frozen, slideId: st ? st.id : null };
    const handled = !!(st && typeof st.on[id] === "function");
    if (handled) {
      try {
        st.on[id](slide, ctx);
      } catch (e) {
        console.error(e);
      }
    }
    (HANDLERS.get(id) || []).forEach((fn) => {
      try {
        fn(slide, ctx);
      } catch (e) {
        console.error(e);
      }
    });
    // A cue that reveals nothing and runs nothing is almost always a typo between cues.json,
    // the slide's preview object and a data-cue attribute, so it is reported rather than ignored.
    if (!hits.length && !handled && !HANDLERS.has(id) && !findSlide(id))
      warn(`cue "${id}" matches no element, handler, or slide`);
  }
  const CSS_escape = (s) => (window.CSS && CSS.escape ? CSS.escape(s) : s.replace(/["\\]/g, "\\$&"));
  function startCamera(sc, seconds) {
    if (sc.camera !== "push" || frozen) return;
    cam.style.setProperty("--dt-scene-dur", `${Math.max(4, seconds)}s`);
    cam.classList.add("dt-push");
  }

  // ---- modes ----------------------------------------------------------------------
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
        if (!HANDLERS.has(c.id)) warn(`unknown cue id ${c.id} (no slide id, owns list or preview object matches it)`);
        continue;
      }
      mountAt.set(owner.slide, Math.min(mountAt.get(owner.slide) ?? Infinity, c.t));
    }
    const slides = [...mountAt.entries()].sort((a, b) => a[1] - b[1]);
    if (!slides.length) {
      warn("no slide owns any listed cue, so nothing will mount");
      return;
    }
    sc.slides.forEach((st) => {
      if (!mountAt.has(st)) warn(`slide "${st.id}" owns no cue in ?cues=, so it never appears`);
    });
    // The first cued slide mounts at narration t=0 so the section never opens on an empty
    // stage; its listed reveals still wait for their own cues.
    slides[0][1] = Math.min(slides[0][1], 0);
    const last = slides[slides.length - 1][0];
    const tEnd = Math.max(...cues.map((c) => c.t));
    startCamera(sc, tEnd - T0 + 8);
    for (const [st, t] of slides) {
      schedule(T0 + t, "mount", st.id, () => {
        mountSlide(sc, st, listed);
        if (st === last) document.body.dataset.done = "1";
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
    const total = sc.slides.reduce((s, st) => s + st.hold, 0) / SPEED;
    startCamera(sc, total);
    sc.slides.forEach((st, i) => {
      const at = t;
      // A cue at or past the hold fires after the next slide has mounted, where none of its
      // elements or handlers exist. The last slide holds until the end, so its cues always land.
      if (i < sc.slides.length - 1) {
        st.cues.forEach((delay, id) => {
          if (delay !== null && delay >= st.hold)
            warn(`slide "${st.id}" fires "${id}" at ${delay} s but holds ${st.hold} s, so it fires on the next slide`);
        });
      }
      schedule(at, "mount", st.id, () => {
        mountSlide(sc, st, null);
        st.cues.forEach((delay, id) => {
          if (delay !== null) schedule(at + delay / SPEED, "cue", id, () => fire(id));
        });
        if (i === sc.slides.length - 1) document.body.dataset.done = "1";
      });
      t += st.hold / SPEED;
    });
  }
  // A slide's cue ids in preview order: timed cues by their seconds, then listed cues, each in declared order.
  function cueOrder(st) {
    return [...st.cues.entries()]
      .map(([id, delay], i) => ({ id, delay: delay ?? Infinity, i }))
      .sort((a, b) => a.delay - b.delay || a.i - b.i)
      .map((c) => c.id);
  }
  function catalogOf() {
    return [...SCENES.values()].map((sc) => ({
      scene: sc.id,
      name: sc.name,
      slides: sc.slides.map((s) => s.id),
      cues: Object.fromEntries(sc.slides.map((s) => [s.id, cueOrder(s)])),
    }));
  }
  // Freeze a slide with its cues fired in preview order: every cue, or with afterId only the cues
  // up to and including it, or with beforeId only the cues before it. An element that waits for
  // a later cue stays hidden.
  function freeze(slideId, afterId, beforeId) {
    const found = findSlide(slideId);
    if (!found) return renderIndex(`unknown slide ${esc(slideId)}`);
    state.mode = "freeze";
    state.scene = found.scene;
    document.documentElement.classList.add("dt-frozen");
    const order = cueOrder(found.slide);
    let firing = order;
    const stop = afterId || beforeId;
    if (stop) {
      const k = order.indexOf(stop);
      if (k < 0) warn(`cue "${stop}" is not one of slide ${found.slide.id}'s cues`);
      else firing = order.slice(0, afterId ? k + 1 : k);
    }
    mountSlide(found.scene, found.slide, null, new Set(order.slice(firing.length)));
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
    div.innerHTML = `<h1>${esc(title)}</h1>${note ? `<p><b>${note}</b></p>` : ""}
      <p>1920×1080. <code>?scene=N</code> previews a scene; <code>?slide=ID</code> freezes a slide; <code>&amp;speed=2</code> runs faster;
      <code>&amp;hud=1</code> shows the clock; <code>&amp;cues=id@s,…</code> (+<code>&amp;t0=</code>) cues from narration.</p>
      ${[...SCENES.values()]
        .map(
          (sc) => `<h2>Scene ${esc(sc.id)} — ${esc(sc.name)} <a href="?scene=${esc(sc.id)}">▶ play</a></h2>
        <div class="dt-slides">${sc.slides.map((st) => `<a href="?slide=${esc(st.id)}">slide ${esc(st.id)} <code>(${st.hold}s${st.cues.size ? `, cues ${[...st.cues.keys()].join(" ")}` : ""})</code></a>`).join("")}</div>`,
        )
        .join("")}`;
    document.body.appendChild(div);
  }

  // ---- start ----------------------------------------------------------------------
  function start() {
    if (state.started) return;
    state.started = true;
    state.catalog = catalogOf();
    ensureStage();
    const cuesRaw = params.get("cues");
    const cues = cuesRaw ? parseCues(cuesRaw) : [];
    const wordsRaw = params.get("words");
    state.words = wordsRaw ? parseWords(wordsRaw) : null;
    if (frozen) {
      freeze(params.get("slide"), params.get("after"), params.get("before"));
    } else if (params.has("scene") || cues.length) {
      let sc = params.has("scene") ? SCENES.get(String(params.get("scene"))) : null;
      if (!sc && cues.length) sc = ownerOf(cues[0].id, null)?.scene || null;
      if (!sc) renderIndex(`unknown scene ${esc(params.get("scene") ?? "")}`);
      else if (cues.length) playCues(sc, cues);
      else playPreview(sc);
    } else {
      renderIndex();
    }
    // Set after the mode has mounted its first slide, so a frozen slide's [data-tex] is in the DOM.
    if (!window.__decktalk.ready) {
      const fonts = document.fonts ? document.fonts.ready : Promise.resolve();
      window.__decktalk.ready = fonts.then(katexReady);
    }
    requestAnimationFrame(loop);
  }
  document.addEventListener("DOMContentLoaded", () => {
    if (SCENES.size) start();
  });

  const DeckTalk = {
    scene,
    on,
    start,
    startClock: setOrigin,
    reveal,
    fire,
    findSlide,
    get scenes() {
      return SCENES;
    },
    params,
  };
  window.DeckTalk = DeckTalk;
  window.__decktalk = {
    ready: null, // the readiness promise, set by start() unless the page set it first
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
