/* decktalk-runtime.js — gives an HTML page the recorder's page contract.
 *
 * Include it, define scenes, and `decktalk record` can drive the page from narration:
 *
 *   <script src="decktalk-runtime.js"></script>
 *   <script>
 *     DeckTalk.scene(3, { name: "How often", camera: "push", steps: [
 *       { id: "3.1", hold: 8, render: () => `
 *           <h1>Value still listed</h1>
 *           <p data-at="1.2">by hour, through first pitch</p>
 *           <div class="tile" data-cue="3.1b" data-count>1 in 10</div>` },
 *       { id: "3.2", hold: 6, render: () => `…` },
 *     ]});
 *     DeckTalk.on("3.2b", () => document.querySelector(".bars").classList.add("grow"));
 *   </script>
 *
 * URL contract (what the recorder and the screenshot tool send)
 *   ?scene=N                 play scene N
 *   &beats=id@s,id@s,…       cue mode: fire cue `id` at `s` seconds after narration t=0.
 *                            A step mounts at the earliest of its cues (the first cued step
 *                            at t=0, so the stage is never empty); steps with no cue never
 *                            show; the last cued step holds forever.
 *   &t0=S                    seconds after page load at which narration t=0 falls
 *   ?step=ID                 freeze step ID with everything revealed (screenshots, review)
 *   &speed=X                 autoplay time scale (cue mode ignores it)
 *   &hud=1                   overlay scene · step · clock
 *   (no params)              index page listing every scene and step
 *
 * Markup inside a step
 *   data-cue="id"            reveal on cue `id` (cue mode) or at data-at seconds after the
 *                            step mounts (autoplay, or when the cue is not in ?beats=)
 *   data-at="s"              reveal s seconds after the step mounts (no cue needed)
 *   data-fx="rise|fade|draw|drop|pop|dim|none"   reveal animation (default rise)
 *   data-dur="s"             animation length
 *   data-count               count the last number in the text up from 0 on reveal
 *   data-type="ms"           type the text at ms per character on reveal
 *   data-tex="…"             typeset with KaTeX if window.katex is present
 *
 * Cue ownership: a cue belongs to the step with the same id, or whose `cues` list names
 * it, or whose id is the longest prefix of the cue id ("4.2b1" -> step "4.2", "9a" -> "9").
 *
 * The page exposes window.__decktalk { mode, scene, step, cues, fired, catalog, now() } and
 * sets window.__sceneReady (fonts loaded) unless the page set its own.
 */
(function () {
  "use strict";

  const CSS = `
  #dt-stage{position:absolute;left:0;top:0;width:1920px;height:1080px;overflow:hidden;transform-origin:0 0}
  #dt-cam,#dt-pan{position:absolute;inset:0}
  #dt-cam.dt-push{animation:dt-push var(--dt-scene-dur,20s) linear forwards}
  @keyframes dt-push{from{transform:scale(1)}to{transform:scale(1.03)}}
  .dt-slide{position:absolute;inset:0}
  .dt-slide.dt-enter{animation:dt-fadein var(--dt-xfade,.35s) ease both}
  .dt-slide.dt-enter.dt-first{animation:dt-slidein .3s ease both}
  .dt-slide.dt-leave{animation:dt-fadeout var(--dt-xfade,.35s) ease both;pointer-events:none}
  @keyframes dt-slidein{from{opacity:0;transform:translateX(24px)}to{opacity:1;transform:none}}
  @keyframes dt-fadein{from{opacity:0}to{opacity:1}}
  @keyframes dt-fadeout{from{opacity:1}to{opacity:0}}
  .dt-reveal{opacity:0}
  .dt-reveal[data-fx=dim]{opacity:1}
  .dt-reveal.dt-on{opacity:1;animation:dt-rise .7s cubic-bezier(.2,.7,.2,1) both}
  @keyframes dt-rise{from{opacity:0;transform:translateY(14px)}to{opacity:1;transform:none}}
  .dt-reveal.dt-on[data-fx=fade]{animation-name:dt-fadein}
  .dt-reveal.dt-on[data-fx=draw]{animation-name:dt-draw;animation-timing-function:linear;stroke-dasharray:1;stroke-dashoffset:1}
  @keyframes dt-draw{from{stroke-dashoffset:1}to{stroke-dashoffset:0}}
  .dt-reveal.dt-on[data-fx=drop]{animation-name:dt-drop;animation-duration:.5s}
  @keyframes dt-drop{from{opacity:0;transform:translateY(-24px)}to{opacity:1;transform:none}}
  .dt-reveal.dt-on[data-fx=pop]{animation-name:dt-pop;animation-duration:.9s}
  @keyframes dt-pop{0%{opacity:0;transform:scale(.4)}60%{opacity:1;transform:scale(1.12)}100%{opacity:1;transform:scale(1)}}
  .dt-reveal.dt-on[data-fx=dim]{animation-name:dt-dim;animation-duration:1.2s}
  @keyframes dt-dim{from{opacity:1}to{opacity:.16}}
  .dt-reveal.dt-on[data-fx=none]{animation:none}
  .dt-frozen .dt-reveal.dt-on,.dt-frozen .dt-slide,.dt-frozen #dt-cam{animation-duration:0s!important;animation-delay:0s!important}
  #dt-hud{position:fixed;left:12px;top:12px;z-index:2147483000;font:14px/1.4 ui-monospace,Menlo,monospace;color:#fff;background:rgba(0,0,0,.6);padding:6px 10px;border-radius:6px;pointer-events:none;white-space:pre}
  #dt-index{font:16px/1.5 system-ui,sans-serif;max-width:900px;margin:40px auto;padding:0 24px;color:#eee}
  #dt-index h1{font-size:28px}#dt-index h2{font-size:20px;margin-top:28px}
  #dt-index a{color:#7ee787;text-decoration:none;margin-right:16px}#dt-index code{color:#9aa4b2}
  #dt-index .dt-steps{display:flex;flex-wrap:wrap;gap:8px 4px}
  `;

  const params = new URLSearchParams(location.search);
  const T0 = parseFloat(params.get("t0") || "0") || 0;
  const SPEED = Math.max(0.05, parseFloat(params.get("speed") || "1") || 1);
  const HUD = params.get("hud") === "1";
  const SCENES = new Map(); // id (string) -> scene
  const HANDLERS = new Map(); // cue id -> [fn]
  const state = {
    mode: "index",
    scene: null,
    step: null,
    cues: [],
    fired: [],
    catalog: [],
    origin: null, // performance.now() at window load (ms)
    queue: [], // [{t, kind, id, run}] sorted by t
    started: false,
    lastMountAt: 0,
  };
  const frozen = params.has("step");

  // ---- time ----------------------------------------------------------------------
  const setOrigin = () => { if (state.origin === null) state.origin = performance.now(); };
  if (document.readyState === "complete") setOrigin();
  else window.addEventListener("load", setOrigin, { once: true });
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
    cam.id = "dt-cam";
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
    const x = (window.innerWidth - 1920 * s) / 2, y = (window.innerHeight - 1080 * s) / 2;
    stage.style.transform = `translate(${x}px, ${y}px) scale(${s})`;
  }
  const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

  // ---- scenes ---------------------------------------------------------------------
  function scene(id, def) {
    const sid = String(id);
    const steps = (def.steps || []).map((st, i) => ({
      id: String(st.id ?? `${sid}.${i + 1}`),
      hold: Number(st.hold ?? 8),
      render: st.render || (() => ""),
      cues: normalizeCues(st.cues),
      enter: st.enter || null,
      on: st.on || {},
    }));
    SCENES.set(sid, { id: sid, name: def.name || `Scene ${sid}`, camera: def.camera || null, steps });
    return DeckTalk;
  }
  // cues: ["a","b"] (ownership only) or {a: 1.5, b: 3} (ownership + autoplay seconds after mount)
  function normalizeCues(c) {
    const out = new Map();
    if (Array.isArray(c)) c.forEach((k) => out.set(String(k), null));
    else if (c && typeof c === "object") Object.entries(c).forEach(([k, v]) => out.set(String(k), Number(v) || 0));
    return out;
  }
  function on(id, fn) {
    const list = HANDLERS.get(String(id)) || [];
    list.push(fn);
    HANDLERS.set(String(id), list);
    return DeckTalk;
  }
  function findStep(stepId) {
    for (const sc of SCENES.values()) {
      const st = sc.steps.find((s) => s.id === String(stepId));
      if (st) return { scene: sc, step: st };
    }
    return null;
  }
  // The step that owns a cue id, within one scene (or across all scenes when sc is null).
  function ownerOf(cueId, sc) {
    const scenes = sc ? [sc] : [...SCENES.values()];
    let best = null;
    for (const s of scenes) {
      for (const st of s.steps) {
        if (st.id === cueId || st.cues.has(cueId)) return { scene: s, step: st };
        if (cueId.startsWith(st.id) && (!best || st.id.length > best.step.id.length)) best = { scene: s, step: st };
      }
    }
    return best;
  }

  // ---- reveals --------------------------------------------------------------------
  function reveal(el) {
    if (el.classList.contains("dt-on")) return;
    el.classList.add("dt-on");
    if (el.hasAttribute("data-count")) countUp(el);
    if (el.hasAttribute("data-type")) typewriter(el);
  }
  function countUp(el) {
    const full = el.dataset.ccFull ?? el.textContent;
    const m = el.dataset.count === "first" ? full.match(/(\d[\d,]*(?:\.\d+)?)/) : full.match(/(\d[\d,]*(?:\.\d+)?)(?!.*\d)/);
    if (!m || frozen) { el.textContent = full; return; }
    const target = parseFloat(m[1].replace(/,/g, "")), decimals = (m[1].split(".")[1] || "").length, commas = m[1].includes(",");
    const fmt = (v) => { let s = v.toFixed(decimals); if (commas) s = s.replace(/\B(?=(\d{3})+(?!\d))/g, ","); return full.slice(0, m.index) + s + full.slice(m.index + m[1].length); };
    const t0 = performance.now(), dur = (parseFloat(el.dataset.dur) * 1000 || 900) / (state.mode === "autoplay" ? SPEED : 1);
    const tick = (t) => { const k = Math.min(1, (t - t0) / dur), e = 1 - Math.pow(1 - k, 3); el.textContent = fmt(target * e); if (k < 1) requestAnimationFrame(tick); };
    requestAnimationFrame(tick);
  }
  function typewriter(el) {
    const full = el.dataset.ccFull ?? el.textContent;
    if (frozen) { el.textContent = full; return; }
    const ms = (parseFloat(el.dataset.type) || 40) / (state.mode === "autoplay" ? SPEED : 1);
    el.textContent = "";
    let i = 0;
    const id = setInterval(() => { el.textContent = full.slice(0, ++i); if (i >= full.length) clearInterval(id); }, ms);
  }
  function typeset(root) {
    if (!window.katex) return;
    root.querySelectorAll("[data-tex]:not([data-typeset])").forEach((el) => {
      try { window.katex.render(el.dataset.tex, el, { throwOnError: false, displayMode: el.hasAttribute("data-display") }); el.dataset.typeset = "1"; } catch (_) { /* keep plain text */ }
    });
  }

  // ---- queue ----------------------------------------------------------------------
  function schedule(t, kind, id, run) {
    state.queue.push({ t, kind, id, run });
    state.queue.sort((a, b) => a.t - b.t || (a.kind === "mount" ? -1 : b.kind === "mount" ? 1 : 0));
  }
  function loop() {
    const t = now();
    while (state.queue.length && state.queue[0].t <= t) state.queue.shift().run();
    if (hud) hud.textContent = `${state.mode} · scene ${state.scene?.id ?? "-"} · step ${state.step?.id ?? "-"} · t ${Math.max(0, t).toFixed(2)}s`;
    requestAnimationFrame(loop);
  }

  // ---- mounting -------------------------------------------------------------------
  let mounted = 0;
  function mountStep(sc, st, listedCues) {
    const old = pan.querySelector(".dt-slide:not(.dt-leave)");
    const slide = document.createElement("div");
    slide.className = `dt-slide dt-enter${mounted === 0 ? " dt-first" : ""}`;
    slide.dataset.step = st.id;
    slide.innerHTML = st.render({ scene: sc, step: st, frozen });
    mounted++;
    typeset(slide);
    const mountT = now();
    state.step = st;
    state.lastMountAt = mountT;
    slide.querySelectorAll("[data-cue],[data-at]").forEach((el) => {
      el.classList.add("dt-reveal");
      if (el.hasAttribute("data-count") || el.hasAttribute("data-type")) el.dataset.ccFull = el.textContent;
      if (el.dataset.dur) el.style.animationDuration = `${parseFloat(el.dataset.dur) / (state.mode === "autoplay" ? SPEED : 1)}s`;
      if (frozen) { reveal(el); return; }
      const cueId = el.dataset.cue;
      if (cueId && listedCues && listedCues.has(cueId)) return; // its cue event reveals it
      const at = (parseFloat(el.dataset.at) || 0) / (state.mode === "autoplay" ? SPEED : 1);
      if (at <= 0) reveal(el); else schedule(mountT + at, "reveal", cueId || "", () => reveal(el));
    });
    if (old) { old.classList.remove("dt-enter"); old.classList.add("dt-leave"); setTimeout(() => old.remove(), 400); }
    pan.appendChild(slide);
    if (st.enter) { try { st.enter(slide, { frozen }); } catch (e) { console.error(e); } }
    return slide;
  }
  function fireCue(id) {
    state.fired.push(id);
    pan.querySelectorAll(`.dt-slide:not(.dt-leave) [data-cue="${CSS_escape(id)}"]`).forEach(reveal);
    const st = state.step;
    if (st && typeof st.on[id] === "function") { try { st.on[id](); } catch (e) { console.error(e); } }
    (HANDLERS.get(id) || []).forEach((fn) => { try { fn(); } catch (e) { console.error(e); } });
  }
  const CSS_escape = (s) => (window.CSS && CSS.escape ? CSS.escape(s) : s.replace(/["\\]/g, "\\$&"));
  function startCamera(sc, seconds) {
    if (sc.camera !== "push" || frozen) return;
    cam.style.setProperty("--dt-scene-dur", `${Math.max(4, seconds)}s`);
    cam.classList.add("dt-push");
  }

  // ---- modes ----------------------------------------------------------------------
  function parseBeats(raw) {
    return raw.split(",").map((tok) => tok.trim()).filter(Boolean).map((tok) => {
      const i = tok.lastIndexOf("@");
      return { id: tok.slice(0, i), t: parseFloat(tok.slice(i + 1)) };
    }).filter((c) => c.id && !isNaN(c.t)).sort((a, b) => a.t - b.t);
  }
  function playCues(sc, cues) {
    state.mode = "cues";
    state.scene = sc;
    state.cues = cues;
    const listed = new Set(cues.map((c) => c.id));
    const mountAt = new Map(); // step -> t
    for (const c of cues) {
      const owner = ownerOf(c.id, sc);
      if (!owner) { if (!HANDLERS.has(c.id)) console.warn("decktalk: unknown cue id", c.id); continue; }
      mountAt.set(owner.step, Math.min(mountAt.get(owner.step) ?? Infinity, c.t));
    }
    const steps = [...mountAt.entries()].sort((a, b) => a[1] - b[1]);
    if (!steps.length) { console.warn("decktalk: no step owns any listed cue"); return; }
    // The first cued step mounts at narration t=0 so the section never opens on an empty
    // stage; its listed reveals still wait for their own cues.
    steps[0][1] = Math.min(steps[0][1], 0);
    const last = steps[steps.length - 1][0];
    const tEnd = Math.max(...cues.map((c) => c.t));
    startCamera(sc, tEnd - T0 + 8);
    steps.forEach(([st, t]) => schedule(T0 + t, "mount", st.id, () => { mountStep(sc, st, listed); if (st === last) document.body.dataset.done = "1"; }));
    cues.forEach((c) => schedule(T0 + c.t, "cue", c.id, () => fireCue(c.id)));
  }
  function playAuto(sc) {
    state.mode = "autoplay";
    state.scene = sc;
    let t = T0;
    const total = sc.steps.reduce((s, st) => s + st.hold, 0) / SPEED;
    startCamera(sc, total);
    sc.steps.forEach((st, i) => {
      const at = t;
      schedule(at, "mount", st.id, () => {
        mountStep(sc, st, null);
        st.cues.forEach((delay, id) => { if (delay !== null) schedule(at + delay / SPEED, "cue", id, () => fireCue(id)); });
        if (i === sc.steps.length - 1) document.body.dataset.done = "1";
      });
      t += st.hold / SPEED;
    });
  }
  function freeze(stepId) {
    const found = findStep(stepId);
    if (!found) return renderIndex(`unknown step ${esc(stepId)}`);
    state.mode = "frozen";
    state.scene = found.scene;
    document.documentElement.classList.add("dt-frozen");
    mountStep(found.scene, found.step, null);
    found.step.cues.forEach((_d, id) => fireCue(id));
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
      <p>1920×1080. <code>?scene=N</code> autoplays a scene; <code>?step=ID</code> freezes a step; <code>&amp;speed=2</code> runs faster;
      <code>&amp;hud=1</code> shows the clock; <code>&amp;beats=id@s,…</code> (+<code>&amp;t0=</code>) cues from narration.</p>
      ${[...SCENES.values()].map((sc) => `<h2>Scene ${esc(sc.id)} — ${esc(sc.name)} <a href="?scene=${esc(sc.id)}">▶ play</a></h2>
        <div class="dt-steps">${sc.steps.map((st) => `<a href="?step=${esc(st.id)}">step ${esc(st.id)} <code>(${st.hold}s${st.cues.size ? `, cues ${[...st.cues.keys()].join(" ")}` : ""})</code></a>`).join("")}</div>`).join("")}`;
    document.body.appendChild(div);
  }

  // ---- start ----------------------------------------------------------------------
  function start() {
    if (state.started) return;
    state.started = true;
    state.catalog = [...SCENES.values()].map((sc) => ({ scene: sc.id, name: sc.name, steps: sc.steps.map((s) => s.id) }));
    if (!window.__sceneReady) window.__sceneReady = document.fonts ? document.fonts.ready.then(() => true) : Promise.resolve(true);
    ensureStage();
    const beatsRaw = params.get("beats");
    const cues = beatsRaw ? parseBeats(beatsRaw) : [];
    if (frozen) {
      freeze(params.get("step"));
    } else if (params.has("scene") || cues.length) {
      let sc = params.has("scene") ? SCENES.get(String(params.get("scene"))) : null;
      if (!sc && cues.length) sc = ownerOf(cues[0].id, null)?.scene || null;
      if (!sc) renderIndex(`unknown scene ${esc(params.get("scene") ?? "")}`);
      else if (cues.length) playCues(sc, cues);
      else playAuto(sc);
    } else {
      renderIndex();
    }
    requestAnimationFrame(loop);
  }
  document.addEventListener("DOMContentLoaded", () => { if (SCENES.size) start(); });

  const DeckTalk = { scene, on, start, reveal, fireCue, findStep, get scenes() { return SCENES; }, params };
  window.DeckTalk = DeckTalk;
  window.__decktalk = {
    get mode() { return state.mode; },
    get scene() { return state.scene?.id ?? null; },
    get step() { return state.step?.id ?? null; },
    get cues() { return state.cues; },
    get fired() { return state.fired; },
    get catalog() { return state.catalog.length ? state.catalog : [...SCENES.values()].map((sc) => ({ scene: sc.id, name: sc.name, steps: sc.steps.map((s) => s.id) })); },
    now,
  };
})();
