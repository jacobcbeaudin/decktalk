/* decktalk.ai. One script over every page, no library, no build step.
   Everything that moves reads the Halfway build in data.js and stage.js: the words with their
   times, the cues with their times, and the deck's own scenes. The stage is a pure function of t,
   so the silent replay, the voice clock, the scrubber and the keyboard all draw the same frame. */
(() => {
  // Every .cut starts at opacity 0 and is revealed by the observer on the last line of this file,
  // so anything that throws in between leaves the films, how it works, the edit section and the
  // install commands invisible: the hero and the footer, and no way to install anywhere on the
  // page. This is registered before anything else can throw, so the page shows itself regardless.
  const showEverything = () => {
    for (const el of document.querySelectorAll(".cut, .lanes")) el.classList.add("in");
  };
  addEventListener("error", showEverything, { once: true });

  const $ = (sel, el = document) => el.querySelector(sel);
  const $$ = (sel, el = document) => [...el.querySelectorAll(sel)];

  /* Every voice on the page, so one starting silences the rest. The hero, the chapter stage and
     the two takes are built in separate onPage closures and cannot see each other, so the takes
     silenced only each other: pressing one while the chapters played put two readings of the same
     script over each other, in the same cloned voice, which sounds like a fault in the product
     rather than in the page.

     The hero registers too, although on the landing page it is the only voice: the sentence above
     says every voice, and a page that one day holds the film beside a take must not rediscover the
     fault. What a hero registers is a mute rather than a stop, because the film is the picture as
     well as the voice: another press takes its sound, never the thing it is showing. */
  const voices = new Set();
  const addVoice = (stop) => voices.add(stop);
  const silenceOtherVoices = (mine) => {
    for (const stop of voices) if (stop !== mine) stop();
  };

  /* Every command on the site is one component: a `.pill` holding the prompt, the command in
     [data-cmd], and an empty slot. The button is made here rather than written into the markup, so
     a page whose script never ran shows a command and no control that cannot do what it says,
     and the prompt is not selectable, so selecting the line by hand still yields a line that runs.
     Wired before the film is, and before the data.js guard below, because the command is the thing
     a visitor came to take away and the film is the thing they came to watch. */
  for (const slot of $$(".pill .copy")) {
    const cmd = $("[data-cmd]", slot.parentElement);
    if (!cmd) continue;
    const b = document.createElement("button");
    b.type = "button";
    b.textContent = "Copy";
    // The visible word is the whole accessible name, and polite live text announces the change.
    b.setAttribute("aria-live", "polite");
    let back = 0;
    b.addEventListener("click", async () => {
      clearTimeout(back);
      try {
        await navigator.clipboard.writeText(cmd.textContent);
        b.textContent = "Copied";
      } catch {
        getSelection().selectAllChildren(cmd);
        b.textContent = "Selected";
      }
      back = setTimeout(() => {
        b.textContent = "Copy";
      }, 1500);
    });
    slot.append(b);
  }

  const D = window.HALFWAY;
  const S = window.HALFWAY_STAGE;
  if (!D || !S) {
    showEverything();
    return;
  }
  const RM = matchMedia("(prefers-reduced-motion: reduce)").matches;
  // The voice clips are not in git. They stream from the media host under names that carry their digest,
  // written into data.js by the build script beside the clip bytes they name.
  const MEDIA = "https://media.decktalk.ai/";
  const VOICE = MEDIA + D.media.hero;
  document.documentElement.style.setProperty("--beat", `${Math.round(D.beat * 1000)}ms`);

  const words = D.sections.flatMap((s, si) => s.words.map(([text, start, end]) => ({ text, start, end, si })));
  const cues = D.sections.flatMap((s) => s.cues);
  const cueAt = Object.fromEntries(cues.map((c) => [c.cue, c]));
  const lines = D.sections.flatMap((s) =>
    s.lines.map(([a, b]) => ({ a, b, start: words[a].start, end: words[b].end })),
  );
  const fmt = (t) => `${Math.floor(t / 60)}:${String(Math.floor(t % 60)).padStart(2, "0")}`;
  const clamp = (v, a, b) => Math.min(b, Math.max(a, v));
  const esc = (s) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/"/g, "&quot;");
  const sectionAt = (t) => D.sections.reduce((acc, s) => (t >= s.start ? s : acc), D.sections[0]);
  const wordAt = (t) => {
    let k = -1;
    for (let i = 0; i < words.length && words[i].start <= t; i++) k = i;
    return k;
  };
  for (const el of $$("[data-total]")) el.textContent = fmt(D.total);

  /* Each page carries only part of this DOM: index.html has the hero, how.html has the chapters and
     the edit section, and both have the cuts and the pills. Every part below asks for the element it
     needs before it runs, so the other page runs none of it and neither page throws. The error guard
     above stays a guard, and never has to fire on a page that is simply missing a section. */
  const onPage = (sel, part) => {
    const el = $(sel);
    if (el) part(el);
  };

  /* The waveform glyph on a sound control follows the clip's real loudness at the playhead: five bars,
     the centre one at t and the others 40 and 80 ms behind it, so the glyph moves with the voice
     while playing, while scrubbing and while muted alike. Under reduced motion it holds a resting shape. */
  const ENV = D.envelope;
  const levelAt = (env, t) => {
    const k = Math.floor(t / ENV.step);
    const db = k >= 0 && k < env.length ? env[k] : ENV.floor;
    return clamp((db - ENV.floor - 6) / 44, 0.2, 1);
  };
  const WAVE_TAPS = [0.08, 0.04, 0, 0.04, 0.08];
  function drawWave(el, env, t) {
    if (RM) return;
    const bars = el.children;
    for (let k = 0; k < bars.length; k++)
      bars[k].style.transform = `scaleY(${levelAt(env, t - WAVE_TAPS[k]).toFixed(2)})`;
  }
  /* The glyph moves only while sound plays. Otherwise it holds the stylesheet's resting shape. */
  function restWave(el) {
    for (const bar of el.children) bar.style.transform = "";
  }

  /* The page lights the city grid dimly from frame 0, so the opening frame reads as a night map before
     "one city" brings it up to full. The film's cue still lands on its word, from this ground rather than from nothing. */
  const GROUND = { "1.1:city": 0.38 };

  /* The runtime's curve: cubic-bezier(.2,.7,.2,1), solved for x. */
  const ease = (() => {
    const [x1, y1, x2, y2] = [0.2, 0.7, 0.2, 1];
    const bx = (u) => 3 * x1 * u * (1 - u) ** 2 + 3 * x2 * u ** 2 * (1 - u) + u ** 3;
    const by = (u) => 3 * y1 * u * (1 - u) ** 2 + 3 * y2 * u ** 2 * (1 - u) + u ** 3;
    return (x) => {
      if (x <= 0) return 0;
      if (x >= 1) return 1;
      let lo = 0;
      let hi = 1;
      for (let i = 0; i < 20; i++) {
        const mid = (lo + hi) / 2;
        if (bx(mid) < x) lo = mid;
        else hi = mid;
      }
      return by((lo + hi) / 2);
    };
  })();

  /* ---------------------------------------------------------------- the stage: the deck's scenes, drawn at t */
  const style = document.createElement("style");
  style.textContent = S.css;
  document.head.append(style);

  function buildStage(root, id) {
    root.innerHTML = Object.entries(S.scenes)
      .map(([n, markup]) => {
        const scoped = markup.replace(/id="s(\d)-/g, `id="${id}-s$1-`).replace(/url\(#s(\d)-/g, `url(#${id}-s$1-`);
        return `<div class="scene" data-scene="${n}" hidden>${scoped}</div>`;
      })
      .join("");
    const reveals = [];
    for (const el of $$("[data-in]", root)) {
      // A slide names each moment by its own local word, and the id cues.json carries is that word
      // under the slide it was written in, which is what stage.js publishes beside every scene.
      const cue = cueAt[`${S.slides[el.closest("[data-scene]").dataset.scene]}:${el.dataset.in}`];
      if (!cue) continue;
      const mode = "words" in el.dataset ? "words" : el.dataset.count ? "count" : null;
      let type = el.dataset.inStyle || (mode === "words" ? "cut" : S.unwritten["data-in-style"]);
      const dur = Number.parseFloat(el.dataset.inSeconds) || S.entrances[type];
      const r = { el, at: cue.at, type, dur, mode, ground: GROUND[cue.cue] ?? 0 };
      if (mode === "count") {
        const full = el.textContent;
        const m = full.match(/(\d[\d,]*)(?!.*\d)/);
        r.full = full;
        r.target = Number.parseInt(m[1].replace(/,/g, ""), 10);
        r.prefix = full.slice(0, m.index);
        r.suffix = full.slice(m.index + m[1].length);
      }
      if (mode === "words") {
        const spoken = el.textContent.trim().split(/\s+/);
        el.innerHTML = spoken.map((w) => `<span class="sw">${w}</span> `).join("");
        r.spans = $$(".sw", el);
        r.times = spoken.map((_, k) => words[cue.first + k]?.start ?? cue.at);
        type = "cut";
        r.type = type;
      }
      if (type === "draw") el.style.strokeDasharray = "1";
      reveals.push(r);
    }
    const scenes = Object.fromEntries($$(".scene", root).map((el) => [el.dataset.scene, el]));
    let shown = null;
    function render(t) {
      const sec = sectionAt(t);
      const key = String(sec.n);
      if (shown !== key) {
        for (const [n, el] of Object.entries(scenes)) el.hidden = n !== key;
        shown = key;
      }
      for (const r of reveals) {
        if (!scenes[key].contains(r.el)) continue;
        const p = r.dur > 0 ? clamp((t - r.at) / r.dur, 0, 1) : t >= r.at ? 1 : 0;
        const on = t >= r.at;
        const st = r.el.style;
        if (r.mode === "spoken") {
          r.spans.forEach((sp, k) => {
            sp.style.opacity = t >= r.times[k] ? "1" : "0";
          });
          st.opacity = on ? "1" : "0";
          continue;
        }
        if (r.mode === "count") {
          const k = RM ? (on ? 1 : 0) : clamp((t - r.at) / (r.dur || 0.9), 0, 1);
          const v = Math.round(r.target * (1 - (1 - k) ** 3));
          r.el.textContent = r.prefix + v.toLocaleString("en-US") + r.suffix;
          st.opacity = on ? "1" : "0";
          continue;
        }
        const e = RM ? (on ? 1 : 0) : ease(p);
        switch (r.type) {
          case "fade":
            st.opacity = String(on ? (RM ? 1 : r.ground + (1 - r.ground) * p) : r.ground);
            break;
          case "draw":
            st.opacity = on ? "1" : "0";
            st.strokeDashoffset = String(on ? 1 - (RM ? 1 : p) : 1);
            break;
          case "settle":
            st.opacity = String(e);
            st.transform = `translateY(${4 * (1 - e)}px)`;
            break;
          case "pop":
            st.opacity = String(e);
            st.transform = `scale(${1 + 0.04 * Math.sin(Math.PI * e)})`;
            break;
          case "cut":
            st.opacity = on ? "1" : "0";
            break;
          default: // rise: a 10 px rise on the runtime's curve
            st.opacity = String(e);
            st.transform = `translateY(${10 * (1 - e)}px)`;
        }
      }
    }
    const frame = root.parentElement;
    const fit = () => root.style.setProperty("--s", String(frame.clientWidth / 1920));
    new ResizeObserver(fit).observe(frame);
    fit();
    return { root, render };
  }

  /* ---------------------------------------------------------------- one polite live region */
  const live = $("[data-live]");
  let pending = null; // {text, after}: an announcement held until the spoken word's beat ends
  function announce(text, holdUntil) {
    if (!text) return;
    if (holdUntil !== undefined) {
      pending = { text, after: holdUntil };
      return;
    }
    live.textContent = "";
    requestAnimationFrame(() => {
      live.textContent = text;
    });
  }
  function flushAnnouncements(t) {
    if (pending && t >= pending.after) {
      const { text } = pending;
      pending = null;
      announce(text);
    }
  }
  const describeAt = (t) => {
    let last = null;
    for (const c of cues) if (c.at <= t) last = c;
    return last ? last.describe : "";
  };

  /* ---------------------------------------------------------------- a player: one clock, one stage */
  function makePlayer(opts) {
    const stage = buildStage(opts.stageEl, opts.id);
    let pausedByPage = false; // paused by the page leaving view or the tab hiding, so it resumes on return
    const P = {
      t: 0,
      playing: false,
      ended: false,
      sound: false,
      rate: 1,
      audio: null,
      raf: 0,
      last: 0,
      visible: true,
      autoplaying: false,
    };
    const ensureAudio = () => {
      if (P.audio) return P.audio;
      const a = new Audio(VOICE);
      a.preload = "auto";
      a.preservesPitch = true;
      a.playbackRate = P.rate;
      a.addEventListener("ended", () => end());
      a.addEventListener("pause", () => {
        if (P.sound && P.playing && !P.ended && a.currentTime < D.total - 0.05) pause();
      });
      P.audio = a;
      return a;
    };
    function draw() {
      stage.render(P.t);
      opts.onDraw?.(P.t);
      flushAnnouncements(P.t);
    }
    function loop(now) {
      if (!P.playing) return;
      if (P.sound && P.audio) {
        if (P.audio.readyState >= 1) P.t = P.audio.currentTime;
      } else P.t += (Math.max(0, now - P.last) / 1000) * P.rate;
      P.last = now;
      if (P.t >= D.total) return end();
      draw();
      P.raf = requestAnimationFrame(loop);
    }
    function setState(state) {
      opts.onState?.(state, P);
    }
    function play({ silent = false } = {}) {
      pausedByPage = false;
      if (silent) {
        P.sound = false;
        if (P.audio) P.audio.pause();
        setSoundUI(false);
      }
      if (P.ended || P.t >= D.total) P.t = 0;
      P.ended = false;
      P.playing = true;
      P.last = performance.now();
      if (P.sound) {
        const a = ensureAudio();
        a.muted = false;
        // A just-created Audio cannot seek until its metadata has loaded, and the mp3 loads only on press.
        if (a.readyState >= 1) a.currentTime = P.t;
        else
          a.addEventListener(
            "loadedmetadata",
            () => {
              a.currentTime = P.t;
            },
            { once: true },
          );
        a.play().catch(() => {
          // The host refused, or the file did not arrive. The film keeps playing silently, which is
          // what it would have done anyway, but the control stops claiming sound is on.
          P.sound = false;
          setSoundUI(false);
          announce("The voice could not be played. The film is playing without sound.");
        });
      }
      cancelAnimationFrame(P.raf);
      P.raf = requestAnimationFrame(loop);
      setState("playing");
    }
    function pause() {
      P.playing = false;
      cancelAnimationFrame(P.raf);
      if (P.audio) P.audio.pause();
      for (const w of opts.waves || []) restWave(w);
      if (pending) {
        const { text } = pending;
        pending = null;
        announce(text);
      }
      setState("paused");
    }
    function end() {
      P.playing = false;
      P.ended = true;
      P.autoplaying = false;
      cancelAnimationFrame(P.raf);
      P.t = D.total;
      if (P.audio) P.audio.pause();
      // P.sound is kept: a viewer who chose sound and watched to the end gets sound on the replay.
      draw();
      setState("ended");
    }
    function seek(t, { announceIt = false, until } = {}) {
      P.t = clamp(t, 0, D.total);
      P.ended = false;
      if (P.audio && P.audio.readyState >= 1) {
        P.audio.currentTime = P.t;
        if (P.sound && Math.abs(P.audio.currentTime - P.t) > 0.5) {
          P.sound = false;
          P.audio.pause();
          setSoundUI(false);
        }
      }
      draw();
      if (announceIt) {
        const k = wordAt(P.t);
        const hold = P.sound && P.playing && k >= 0 ? (until ?? words[k].end) : undefined;
        announce(describeAt(P.t), hold);
      }
      if (P.playing) {
        P.last = performance.now();
      } else setState("paused");
    }
    function setSoundUI(on) {
      // One name, one state attribute. A toggle's name must not move when its state does, which is
      // what aria-pressed is for: swapping both announced the state twice. The waveform is the
      // sighted half of the same fact.
      for (const b of opts.soundBtns || []) b.setAttribute("aria-pressed", on ? "true" : "false");
      if (!on) for (const w of opts.waves || []) restWave(w);
    }
    /* Silence, without taking the picture away. This is what the player registers as its voice, so
       a press on any other voice on the page turns this one off: the film carries on exactly where
       it was, silently, which is what it was doing before the viewer asked for sound. Idempotent,
       so a second voice starting while a third is starting costs nothing. */
    function mute() {
      if (!P.sound) return;
      P.sound = false;
      if (P.audio) {
        // The clock keeps following the muted audio while it plays, so nothing jumps.
        if (P.playing && P.audio.readyState >= 1) P.t = P.audio.currentTime;
        P.audio.muted = true;
        P.audio.pause();
      }
      P.last = performance.now();
      setSoundUI(false);
    }
    /* The sound control: a press turns sound on where the film already is, and a press while sound
       is on mutes without stopping. At the end there is nothing left to hear, so a press replays. */
    function toggleSound() {
      if (P.sound && !P.ended) {
        mute();
        return;
      }
      P.sound = true;
      P.autoplaying = false;
      // Sound is a setting, not a restart. The button sits where every player puts mute, so a press
      // means "let me hear this", not "start again", except at the end, where there is nothing left
      // to hear and a press is the replay the ended state offers.
      if (P.ended) P.t = 0;
      P.ended = false;
      const a = ensureAudio();
      a.muted = false;
      a.currentTime = P.t;
      // Not playing until the audio says so: a rejected play() used to leave P.playing true with a
      // pause icon over a still film, and the next press read as "stop" and did nothing visible.
      //
      // The clock also stops while the voice loads. It used to keep running on the wall clock, and
      // the moment the mp3's metadata arrived the loop started reading audio.currentTime instead,
      // which was still the press position on an element that had not started: the film ran ahead
      // of the press and then jumped backwards to it. Holding the frame is the honest picture of
      // what is happening, which is waiting.
      const wasPlaying = P.playing;
      P.playing = false;
      cancelAnimationFrame(P.raf);
      setState("loading");
      P.last = performance.now();
      a.play()
        .then(() => {
          // Another voice on the page may have taken this one's sound while the clip was loading.
          // The film goes back to whatever it was doing before the press, and the control never
          // reports a sound that was already given up.
          if (!P.sound) {
            a.pause();
            setSoundUI(false);
            if (wasPlaying) {
              P.playing = true;
              P.last = performance.now();
              cancelAnimationFrame(P.raf);
              P.raf = requestAnimationFrame(loop);
              setState("playing");
            } else setState("paused");
            return;
          }
          // Seeking again here, because a press during the load may have moved P.t and the element
          // was not ready to honour the currentTime set before play() was called.
          a.currentTime = P.t;
          P.playing = true;
          P.last = performance.now();
          cancelAnimationFrame(P.raf);
          P.raf = requestAnimationFrame(loop);
          setSoundUI(true);
          setState("playing");
        })
        .catch(() => {
          // A mute while the clip was loading rejects the pending play() with AbortError. That is
          // the viewer's own choice, or another voice's, and not a refused voice, so nothing is
          // announced: the film goes back to what it was doing, the way the resolved case does.
          if (!P.sound) {
            setSoundUI(false);
            if (wasPlaying) {
              P.playing = true;
              P.last = performance.now();
              cancelAnimationFrame(P.raf);
              P.raf = requestAnimationFrame(loop);
              setState("playing");
            } else {
              P.playing = false;
              setState("paused");
            }
            return;
          }
          // A press that never became sound has to end somewhere the viewer can see, and it must
          // not cost them the film they already had: a missing clip or a refused play leaves the
          // hero exactly as it was before the press, and the live region says which.
          P.sound = false;
          setSoundUI(false);
          if (wasPlaying) {
            P.playing = true;
            P.last = performance.now();
            cancelAnimationFrame(P.raf);
            P.raf = requestAnimationFrame(loop);
            setState("playing");
            announce("The voice could not be played. The film is playing without sound.");
          } else {
            P.playing = false;
            setState("paused");
            announce("The voice could not be played. Press play to watch without sound.");
          }
        });
    }
    function setRate(rate) {
      P.rate = rate;
      if (P.audio) P.audio.playbackRate = rate;
    }
    /* Nothing runs while the stage is off screen or the tab is hidden. */
    const io = new IntersectionObserver(
      (es) => {
        P.visible = es[0].isIntersecting;
        if (!P.visible && P.playing) {
          pause();
          pausedByPage = true;
        } else if (P.visible && pausedByPage) {
          pausedByPage = false;
          play();
        }
      },
      { threshold: 0.2 },
    );
    io.observe(opts.stageEl);
    document.addEventListener("visibilitychange", () => {
      if (document.hidden && P.playing) {
        pause();
        pausedByPage = true;
      } else if (!document.hidden && pausedByPage && P.visible) {
        pausedByPage = false;
        play();
      }
    });
    const stop = () => {
      pause();
      pausedByPage = false;
    };
    Object.assign(P, { play, pause, stop, mute, seek, toggleSound, setRate, draw, end });
    return P;
  }

  /* ---------------------------------------------------------------- the hero */
  onPage("#hero", (heroEl) => {
    const band = { prev: $("[data-prev]", heroEl), cur: $("[data-cur]", heroEl) };
    const scrub = $("[data-scrub]", heroEl);
    const pauseBtn = $("[data-pause]", heroEl);
    const heroTc = $("[data-tc]", heroEl);
    const heroWaves = $$("[data-sound] .wave");
    scrub.max = String(D.total);
    // The last input on the scrubber, so the thumb follows the film again half a second later.
    // -Infinity rather than 0 because under reduced motion the only draw is the one at load, when
    // performance.now() is still under 500 and `now - 0 > 500` reads as a scrub in progress: the
    // thumb sat at 0 while the timecode and aria-valuetext said 3.2 seconds.
    let scrubHeld = -Infinity;
    let bandLine = -1;
    let bandWord = -1;
    let bandLive = false;
    function drawBand(t) {
      let li = -1;
      for (let i = 0; i < lines.length && lines[i].start <= t; i++) li = i;
      if (li < 0) {
        if (bandLine !== -1) {
          band.prev.textContent = "";
          band.cur.textContent = "";
          bandLine = -1;
          bandWord = -1;
        }
        return;
      }
      const L = lines[li];
      const k = wordAt(t);
      if (li !== bandLine) {
        band.prev.textContent =
          li > 0
            ? words
                .slice(lines[li - 1].a, lines[li - 1].b + 1)
                .map((w) => w.text)
                .join(" ")
            : "";
        band.cur.innerHTML = words
          .slice(L.a, L.b + 1)
          .map((w, i) => `<span class="w" data-i="${L.a + i}" hidden>${w.text}</span>`)
          .join(" ");
        bandLine = li;
        bandWord = -1;
        // A sighted viewer reads this line under the frame and watches the picture arrive on its word.
        // Both stage frames are aria-hidden, so without this the film ran its whole length in silence
        // for a screen reader. One line at a time, with the picture the cue names, is the same film
        // told out loud, and `describe` is already in data.js because the deck writes it for the films
        // DeckTalk builds. Only while playing: a seek announces through its own debounce.
        if (hero.playing) {
          const said = words
            .slice(L.a, L.b + 1)
            .map((w) => w.text)
            .join(" ");
          // The picture this line brings, not the last one anywhere: describeAt(t) returns the cue
          // standing at t, which on a line that starts before its own cue is the previous line's.
          const shown = cues
            .filter((c) => c.first >= L.a && c.first <= L.b)
            .map((c) => c.describe)
            .filter(Boolean)
            .join(" ");
          announce(shown ? `${said.replace(/[.,;:]$/, "")}. ${shown}` : said);
        }
      }
      if (k !== bandWord || bandLive !== hero.playing) {
        for (const sp of $$(".w", band.cur)) {
          const i = Number(sp.dataset.i);
          sp.hidden = i > k;
          sp.classList.toggle("live", i === k && hero.playing);
        }
        bandWord = k;
        bandLive = hero.playing;
      }
    }
    const hero = makePlayer({
      id: "h",
      stageEl: $("[data-stage]", heroEl),
      soundBtns: $$("[data-sound]"),
      waves: heroWaves,
      onDraw(t) {
        drawBand(t);
        if (hero.sound) for (const w of heroWaves) drawWave(w, ENV.hero, t);
        if (performance.now() - scrubHeld > 500) scrub.value = String(t);
        const k = wordAt(t);
        scrub.setAttribute("aria-valuetext", `${t.toFixed(1)} seconds${k >= 0 ? `, ${words[k].text}` : ""}`);
        heroTc.textContent = fmt(t);
      },
      onState(state) {
        pauseBtn.dataset.state = state;
        pauseBtn.setAttribute(
          "aria-label",
          { playing: "Pause", paused: "Play", ended: "Replay from the start", loading: "Loading the voice" }[state],
        );
        drawBand(hero.t);
      },
    });
    /* The film's voice in the registry at the top of this file. It is a mute and not a stop: the
       chapter stage starting is a reason for the film to go quiet, never a reason for it to hold
       still. Registered before the first press, because the autoplay is silent and the press that
       gives the film a voice is the one that has to take everyone else's. */
    const muteHero = () => hero.mute();
    addVoice(muteHero);
    /* Every press that can end with this film making a sound takes the others' first. A press that
       only mutes is not one of them: it is the same test toggleSound makes to decide which half it
       is doing, so the hero's own mute button never reaches across the page and stops the
       chapters. Silencing before the press rather than after keeps it true of the press that fails
       too, since a refused play() leaves the film silent and everything else already quiet. */
    const heroWillSound = () => !(hero.sound && !hero.ended);
    const claimTheVoice = () => {
      if (heroWillSound()) silenceOtherVoices(muteHero);
    };
    pauseBtn.addEventListener("click", () => {
      if (hero.playing) hero.stop();
      else {
        // A replay honours the sound the viewer chose rather than starting over in silence.
        if (hero.sound) silenceOtherVoices(muteHero);
        hero.play(hero.ended ? { silent: !hero.sound } : {});
      }
    });
    scrub.addEventListener("input", () => {
      scrubHeld = performance.now();
      hero.seek(Number.parseFloat(scrub.value));
    });
    // A run of arrow presses announces once, after the last one.
    let scrubAnnounce = 0;
    scrub.addEventListener("change", () => {
      clearTimeout(scrubAnnounce);
      scrubAnnounce = setTimeout(() => hero.seek(Number.parseFloat(scrub.value), { announceIt: true }), 400);
    });
    for (const b of $$("[data-sound]"))
      b.addEventListener("click", () => {
        claimTheVoice();
        hero.toggleSound();
      });
    // Reduced motion: the hero opens on the `1.1:forty` frame and plays only on press. Otherwise it plays once, silently.
    if (RM) {
      hero.seek(cueAt["1.1:forty"].at + 0.6);
    } else {
      hero.autoplaying = true;
      hero.play({ silent: true });
    }
  });

  /* ---------------------------------------------------------------- how it works: the pinned stage and the catch-up transcript */
  onPage("#catchup", (catchup) => {
    const hiwStage = $("[data-stage]", catchup);
    const hiwPlay = $("[data-hiw-play]", catchup);
    const hiwTc = $("[data-tc]", catchup);
    const speedBtn = $("[data-speed]", catchup);
    const transcriptEl = $("[data-transcript]", catchup);
    const verifyEl = $("[data-verify]", catchup);
    const picker = $("[data-picker]", catchup);
    const pickedEl = $("[data-picked]", catchup);
    let picked = 2;
    let cursor = -1; // the roving word cursor, an index into `words`
    const hiw = makePlayer({
      id: "w",
      stageEl: hiwStage,
      onDraw(t) {
        hiwTc.textContent = `${Math.max(0, t - D.sections[picked - 1].start).toFixed(1)} s`;
        const k = wordAt(t);
        // Live marks where the stage's clock is, playing or parked. A chapter parks it, and the lit word
        // and lit cue tick are how a reader sees which word the frame above them is standing on.
        for (const b of $$(".w", transcriptEl)) {
          const i = Number(b.dataset.i);
          b.classList.toggle("said", i < k);
          b.classList.toggle("live", i === k);
        }
        for (const a of $$(".at", transcriptEl)) a.classList.toggle("live", Math.abs(Number(a.dataset.at) - t) < 0.35);
      },
      onState(state) {
        hiwPlay.dataset.state = state;
        $("span", hiwPlay).textContent = state === "playing" ? "Pause" : "Play from here";
        hiwPlay.setAttribute("aria-label", state === "playing" ? "Pause" : "Play from here, with sound");
      },
    });
    hiw.sound = true; // this stage's play button is the voice clock; nothing plays until it is pressed
    const stopHiw = () => hiw.stop();
    addVoice(stopHiw);
    hiwPlay.addEventListener("click", () => {
      if (hiw.playing) return hiw.stop();
      if (hiw.ended || hiw.t >= D.total) hiw.t = D.sections[picked - 1].start;
      silenceOtherVoices(stopHiw);
      hiw.sound = true;
      hiw.play();
    });
    speedBtn.addEventListener("click", () => {
      const slow = speedBtn.getAttribute("aria-pressed") !== "true";
      speedBtn.setAttribute("aria-pressed", slow ? "true" : "false");
      hiw.setRate(slow ? 0.75 : 1);
    });

    function buildTranscript(n) {
      const sec = D.sections[n - 1];
      const phraseOf = new Map();
      for (const c of sec.cues) for (let k = 0; k < c.n; k++) phraseOf.set(c.first + k, c);
      let out = "";
      let openCue = null;
      sec.words.forEach(([text], k) => {
        const i = sec.first + k;
        const c = phraseOf.get(i);
        if (c && c !== openCue) {
          out += `<span class="phrase">`;
          openCue = c;
        }
        out += `<button type="button" class="w" data-i="${i}" tabindex="-1" aria-label="${esc(text)}, ${(words[i].start - sec.start).toFixed(2)} seconds into the section">${esc(text)}</button>`;
        if (openCue && (k === sec.words.length - 1 || phraseOf.get(i + 1) !== openCue)) {
          const at = (openCue.at - sec.start).toFixed(2);
          out += `</span><a class="at" href="#cue-${openCue.cue}" data-cue="${openCue.cue}" data-at="${openCue.at}" aria-label="Cue ${openCue.cue} on ${esc(openCue.on)}, at ${at} seconds into the section">${at}</a>`;
          openCue = null;
        }
        out += " ";
      });
      transcriptEl.innerHTML = out;
      cursor = sec.first;
      const first = $(`.w[data-i="${cursor}"]`, transcriptEl);
      first.tabIndex = 0;
      first.classList.add("cursor");
      pickedEl.textContent = String(n);
      verifyEl.innerHTML = verifyLine(sec);
    }
    function verifyLine(sec) {
      const parts = sec.cues
        .filter((c) => D.verify[c.cue] !== undefined)
        .map((c) => `<span>${c.cue} <b>${D.verify[c.cue] >= 0 ? "+" : "−"}${Math.abs(D.verify[c.cue])} ms</b></span>`);
      return parts.length ? `verify · ${parts.join(" · ")}` : "";
    }
    function setCursor(i) {
      const prev = $(`.w[data-i="${cursor}"]`, transcriptEl);
      const next = $(`.w[data-i="${i}"]`, transcriptEl);
      if (!next) return;
      if (prev) {
        prev.tabIndex = -1;
        prev.classList.remove("cursor");
      }
      next.tabIndex = 0;
      next.classList.add("cursor");
      next.focus();
      cursor = i;
    }
    // A cue's time is rounded to the frame, so it can sit a few ms after its first word's start. The first word
    // of a phrase seeks to its cue, so the picture the word brings is the one described. A still frame is taken
    // once the reveal is up, within the word, since the frame at the cue's own instant is where the rise begins.
    const wordSeek = (i, still = false) => {
      const c = cues.find((c) => c.first === i);
      if (!c) return words[i].start;
      const at = Math.max(words[i].start, c.at);
      return still ? Math.min(at + 0.3, Math.max(at, words[i].end - 0.02)) : at;
    };
    transcriptEl.addEventListener("click", (e) => {
      const w = e.target.closest(".w");
      const a = e.target.closest(".at");
      if (w) {
        setCursor(Number(w.dataset.i));
        hiw.seek(wordSeek(cursor, true), { announceIt: true });
      }
      if (a) {
        e.preventDefault();
        const c = cueAt[a.dataset.cue];
        // Announced once, and while the voice plays, held until the cue phrase has been spoken.
        hiw.seek(c.at, { announceIt: true, until: words[c.first + c.n - 1].end });
      }
    });
    transcriptEl.addEventListener("keydown", (e) => {
      if (!e.target.classList.contains("w")) return;
      const d = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 }[e.key];
      if (e.key === "Enter") {
        // Enter plays from the cursor word with sound. A pointer click only moves the stage.
        e.preventDefault();
        hiw.sound = true;
        hiw.seek(wordSeek(cursor));
        hiw.play();
        announce(describeAt(hiw.t), words[cursor].end);
      } else if (d) {
        e.preventDefault();
        const sec = D.sections[picked - 1];
        setCursor(clamp(cursor + d, sec.first, sec.first + sec.words.length - 1));
      } else if (e.key === "Home" || e.key === "End") {
        e.preventDefault();
        const sec = D.sections[picked - 1];
        setCursor(e.key === "Home" ? sec.first : sec.first + sec.words.length - 1);
      }
    });

    // The section picker: a radiogroup with arrow keys.
    D.sections.forEach((s) => {
      const b = document.createElement("button");
      b.type = "button";
      b.setAttribute("role", "radio");
      b.setAttribute("aria-checked", s.n === picked ? "true" : "false");
      b.setAttribute("aria-label", `Section ${s.n}, ${s.chapter}`);
      b.tabIndex = s.n === picked ? 0 : -1;
      b.dataset.n = String(s.n);
      b.textContent = String(s.n);
      picker.append(b);
    });
    function pick(n, focus = false) {
      picked = n;
      for (const b of $$("[role=radio]", picker)) {
        const on = Number(b.dataset.n) === n;
        b.setAttribute("aria-checked", on ? "true" : "false");
        b.tabIndex = on ? 0 : -1;
        if (on && focus) b.focus();
      }
      buildTranscript(n);
      const sec = D.sections[n - 1];
      hiw.stop();
      hiw.seek(sec.cues[0].at + 0.3);
    }
    picker.addEventListener("click", (e) => {
      const b = e.target.closest("[role=radio]");
      if (b) pick(Number(b.dataset.n));
    });
    picker.addEventListener("keydown", (e) => {
      const d = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 }[e.key];
      if (!d) return;
      e.preventDefault();
      pick(((picked - 1 + d + D.sections.length) % D.sections.length) + 1, true);
    });
    buildTranscript(picked);

    /* The five chapters follow one line, "Twenty minutes for her. Twenty minutes for you.": cues 2.1:her and 2.1:you.
       Native scroll alone decides the chapter; the stage draws that chapter's moment when nobody is playing it.

       Each chapter owns a moment no other chapter shows, so the pinned stage answers every scroll:

         1  the script          the section's first frame. The city is there, her and you are there, and nothing
                                the script names has been drawn yet: a script is all that exists at this step.
         2  the voiced words    the middle of the section, on the word "pin". The transcript below the stage
                                divides at the same word, and the clock reads the seconds that word was given.
         3  the cue             40 ms before 2.1:her. The cue's phrase is lit in the transcript and its picture is
                                not on the stage: this is the waiting the chapter describes.
         4  the slide           2.1:her has landed. Every picture in the frame now carries the cue it was written
                                with, the same `data-in` the panel beside it quotes.
         5  the measured film   2.1you has landed too, and each badge turns into what `decktalk verify` measured
                                for that landing. The frame stops being a picture of the film and becomes its report. */
    const her = cueAt["2.1:her"];
    const you = cueAt["2.1:you"];
    const pin = cueAt["2.1:pin"];
    const sec2 = D.sections[1];
    // 0.32 s after a cue is far enough into a 0.3 s reveal to read as landed, and near enough to light the cue's
    // own tick in the transcript, which holds for 0.35 s either side.
    const chapterT = { 1: sec2.start + 0.02, 2: pin.at + 0.32, 3: her.at - 0.04, 4: her.at + 0.32, 5: you.at + 0.32 };
    const take = (i) => (words[i].start - sec2.start - D.lead).toFixed(3);
    const panels = {
      script: `<span class="fn">script.md</span><pre>## 2. Halfway\n\n<span class="t">…at a place worth the trip. [beat]</span>\n<span class="hl">Twenty minutes for her.</span> <span class="t">[beat]</span> <span class="hl">Twenty minutes for you.</span>\n\n<span class="t">## 2. begins section 2 of 4. [beat] is a short pause and is not spoken.</span></pre>`,
      words: `<span class="fn">build/narrate/${sec2.hash}.words.json</span><pre>${[...Array(8).keys()]
        .map((k) => {
          const i = her.first + k;
          return `{ "word": "${words[i].text.replace(/[.,]$/, "")}", "start": ${take(i)}, "end": ${(words[i].end - sec2.start - D.lead).toFixed(3)} }`;
        })
        .join("\n")}</pre>`,
      cues: `<span class="fn">cues.json → build/cue-times.json</span><pre>{ "cue": "<span class="cue">2.1:her</span>", "on": "<span class="hl">Twenty minutes for her</span>" }\n<span class="t">→ ${(her.at - sec2.start).toFixed(2)} s into the section. The recording starts ${D.lead} s after the section begins, so words.json says ${take(her.first)}.</span>\n{ "cue": "<span class="cue">2.1:you</span>", "on": "<span class="hl">Twenty minutes for you</span>" }\n<span class="t">→ ${(you.at - sec2.start).toFixed(2)} s into the section.</span></pre>`,
      slide: `<span class="fn">deck/index.html</span><pre>&lt;div class="pill" <span class="cue">data-in="her"</span> data-describe="${esc(her.describe)}"&gt;20 min&lt;/div&gt;\n&lt;div class="pill" <span class="cue">data-in="you"</span> data-describe="${esc(you.describe)}"&gt;20 min&lt;/div&gt;\n<span class="t">&lt;!-- decktalk-runtime.js shows each data-in element at its cue's second. data-describe is the sentence this reveal writes into the transcript the build publishes beside the film. --&gt;</span></pre>`,
      verify: `<span class="fn">decktalk verify</span><pre>${sec2.cues
        .map(
          (c) =>
            `${c.cue.padEnd(12)} ${String(D.verify[c.cue] >= 0 ? `+${D.verify[c.cue]}` : D.verify[c.cue]).padStart(4)} ms  <span class="ok">changed</span>`,
        )
        .join(
          "\n",
        )}\n<span class="t">${cues.length} cues in the ${Math.floor(D.total)}-second cut this stage replays, every picture within ${Math.max(...cues.map((c) => Math.abs(D.verify[c.cue] ?? 0)))} ms of its word. +20 ms means the picture appeared 20 ms after its cue's second, −20 ms before it. "changed" is verify's word for a picture that appeared at its cue.</span></pre>`,
    };
    for (const [name, html] of Object.entries(panels)) $(`[data-panel="${name}"]`).innerHTML = html;

    /* Chapters 4 and 5 label the pictures on the stage itself. The cue name is already on the element:
       it is the slide's own `data-in`, which chapter 4's panel quotes, so only the measured landing
       needs writing on. The badges are drawn by the stylesheet from these two attributes.

       They are also why the verify line under the frame no longer waits for chapter 5 to appear. It used to
       be the chapter's whole reward, and it was the section's cue list a second time, in a column that grew
       three lines taller at exactly the scroll position where sticky could least afford it. It now reads as
       what it is, a property of the section you picked, from the first chapter on, and chapter 5's reward
       is on the pictures, where each number belongs to the landing it measures. */
    for (const el of $$(".scene > [data-in]", hiwStage)) {
      const ms = D.verify[`${S.slides[el.closest("[data-scene]").dataset.scene]}:${el.dataset.in}`];
      if (ms !== undefined) el.dataset.cueMs = `${ms >= 0 ? "+" : "−"}${Math.abs(ms)} ms`;
    }

    let chapter = 0;
    const chapterIO = new IntersectionObserver(
      (es) => {
        for (const e of es) {
          if (!e.isIntersecting) continue;
          const n = Number(e.target.dataset.chapter);
          if (n !== chapter) {
            chapter = n;
            if (!hiw.playing) hiw.seek(chapterT[n]);
            hiwStage.classList.toggle("named", n === 4);
            hiwStage.classList.toggle("measured", n === 5);
          }
        }
      },
      { rootMargin: "-45% 0px -45% 0px" },
    );
    for (const el of $$("[data-chapter]")) chapterIO.observe(el);
    hiw.seek(chapterT[1]);
  });

  /* ---------------------------------------------------------------- change a sentence */
  onPage("#edit", () => {
    const E = D.edit;
    for (const name of ["before", "after"]) $(`[data-hash="${name}"]`).textContent = E[name].hash;
    const takeAudio = {};
    const stopTakes = () => {
      for (const a of Object.values(takeAudio)) a.pause();
    };
    addVoice(stopTakes);
    for (const b of $$("[data-take]")) {
      const name = b.dataset.take;
      const wave = $(".wave", b);
      let raf = 0;
      const follow = () => {
        const a = takeAudio[name];
        drawWave(wave, ENV[name], a.currentTime);
        if (!a.paused) raf = requestAnimationFrame(follow);
      };
      b.addEventListener("click", () => {
        if (!takeAudio[name]) takeAudio[name] = new Audio(MEDIA + D.media[name]);
        const a = takeAudio[name];
        if (!a.paused) {
          a.pause();
          return;
        }
        for (const [other, oa] of Object.entries(takeAudio)) if (other !== name) oa.pause();
        silenceOtherVoices(stopTakes);
        a.currentTime = 0;
        a.onplay = () => {
          b.setAttribute("aria-pressed", "true");
          cancelAnimationFrame(raf);
          raf = requestAnimationFrame(follow);
        };
        a.onpause = () => {
          b.setAttribute("aria-pressed", "false");
          cancelAnimationFrame(raf);
          restWave(wave);
        };
        a.onended = a.onpause;
        a.play().catch(() => {});
      });
    }
    // The edit, file by file, from data.js: the words that changed with the words either side of
    // them, so the page names every place the number lives rather than only the spoken sentence.
    const diff = $("[data-diff]");
    if (diff) {
      diff.innerHTML = E.diff
        .map((f) => {
          const rows = f.changes
            .map(
              (c) =>
                `<li class="row"><span class="at">${esc(c.where)}${c.label ? `<i> · ${esc(c.label)}</i>` : ""}</span>` +
                `<span class="txt">${c.prefix ? `<span class="ctx">${esc(c.prefix)}</span> ` : ""}` +
                // "was" and "now" are read, not seen: a strike and a colour say nothing out loud, and
                // without them the row announced as "eleven fourteen thousand".
                `<span class="sr">was </span><s>${esc(c.before)}</s><span class="sr">, now</span> ` +
                `<b>${esc(c.after)}</b>` +
                `${c.suffix ? ` <span class="ctx">${esc(c.suffix)}</span>` : ""}</span></li>`,
            )
            .join("");
          return `<li class="file"><span class="fn">${esc(f.file)}</span><ul class="rows">${rows}</ul></li>`;
        })
        .join("");
    }
    const lanes = $("[data-lanes]");
    const cellsHtml = [];
    let k = 0;
    const cell = (cls, text) => `<div class="cell ${cls}" style="--k:${k++}">${text}</div>`;
    cellsHtml.push(
      `<div class="hd"></div>${D.sections.map((s) => `<div class="hd"><b>${s.n}</b>${s.chapter}</div>`).join("")}`,
    );
    cellsHtml.push(
      `<div class="rl">narrate</div>${D.sections.map((s) => cell(s.n === E.section ? "live" : "", s.n === E.section ? "voiced" : "cached")).join("")}`,
    );
    cellsHtml.push(
      `<div class="rl">record</div>${D.sections.map((s) => cell(s.n === E.section ? "live" : "", s.n === E.section ? "recorded" : "kept")).join("")}`,
    );
    cellsHtml.push(`<div class="rl">assemble</div>${cell("span", "four sections, straight cuts, one mp4")}`);
    lanes.innerHTML = cellsHtml.join("");
    const NUM = ["", "one", "two", "three", "four", "five", "six"];
    const num = (n) => NUM[n] ?? String(n);
    lanes.setAttribute(
      "aria-label",
      `After the edit to section ${E.section}, the narrate stage voiced section ${E.section} and reused the cached takes of the other ${num(E.kept)}, the record stage filmed section ${E.section} and kept the other ${num(E.kept)}, and the assemble stage cut all ${num(D.sections.length)} into one mp4.`,
    );
    // The money is the section's second claim, so it is a sentence on the page rather than the last
    // line of a log behind a disclosure.
    const C = E.cost;
    $("[data-cost]").innerHTML =
      `The rebuild cost <b>about $${C.usd.toFixed(2)}</b>: ${C.characters} characters sent to the voice, at $${C.rate.toFixed(2)} per 1,000 characters, the price set in <code>decktalk.toml</code>. The other ${num(C.cached)} sections cost nothing.`;
    // Each line is its own block, so a line longer than the box wraps under a hanging indent instead
    // of running off the right edge of a phone with the evidence on it.
    // The stage lines the rebuild really printed, and the one stage it did the work in.
    const redone = (E.lanes.find((l) => l.stage === "record") ?? { sections: [] }).sections.filter(
      (s) => s.outcome === "ok",
    );
    const log = $("[data-log]");
    const logLines = [`<span class="ln"><span class="p">$</span> decktalk build</span>`];
    for (const ln of E.log) {
      const isLive = redone.length === 1 && /^\s*Record\s/.test(ln);
      logLines.push(`<span class="ln${isLive ? " live" : ""}">${esc(ln)}</span>`);
    }
    logLines.push(
      `<span class="ln gloss">Each section is its take plus the ${D.lead} s lead and ${D.tail} s tail, so ${num(D.sections.length)} takes make a ${Math.round(E.film_seconds)} s film.</span>`,
    );
    log.innerHTML = logLines.join("");
    // The claim under the log is the rebuilt film's own measurement: every cue verify measured, and the largest offset.
    const offsets = Object.values(E.verify);
    if (offsets.length)
      $("[data-verify-sum]").textContent =
        `: ${offsets.length} cues in the full ${fmt(E.film_seconds)} film, none more than ${Math.max(...offsets.map(Math.abs))} ms off.`;
  });

  /* ---------------------------------------------------------------- cuts */
  const io = new IntersectionObserver(
    (es) => {
      for (const e of es) {
        // Already scrolled past counts as seen. This observer is created on the last line of this
        // file, so anything above the viewport by the time it attaches reports isIntersecting
        // false, is never entered again, and stays at opacity 0 for the rest of the visit. A
        // reader on a slow phone who flicks down the page while the script is still parsing lost
        // whole sections that way, permanently, with no way to get them back but a reload.
        if (e.isIntersecting || e.boundingClientRect.bottom < 0) {
          e.target.classList.add("in");
          io.unobserve(e.target);
        }
      }
    },
    { threshold: 0.15 },
  );
  for (const el of $$(".cut")) io.observe(el);
})();
