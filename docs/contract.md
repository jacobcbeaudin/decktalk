# The page contract

A DeckTalk page is an HTML file that `decktalk record` can drive from the narration. The
contract is a handful of URL parameters and one global. `decktalk-runtime.js` implements
it; a page can also implement it by hand.

## URL parameters

| Parameter | Meaning |
|---|---|
| `scene=N` | Play scene N. Scene ids are strings; the scaffold uses the script's section numbers. |
| `beats=id@s,id@s,…` | Cue mode. Fire cue `id` at `s` seconds after narration t=0. Times come from `build/audio/beats.json`, relative to the section's start in the continuous narration. |
| `t0=S` | Seconds after the page's `load` event at which narration t=0 falls. The recorder waits `S` (default 0.5) after load, then starts the clock and flashes the frame magenta so the assembler can align exactly. |
| `step=ID` | Freeze mode. Mount step ID with every reveal in its end state and its handler cues fired. Used by `decktalk shots` and for review. |
| `speed=X` | Autoplay time scale. Ignored in cue mode. |
| `hud=1` | Overlay mode, scene, step and clock. Never record with it on. |
| none | Index page: every scene and step with play and freeze links. |

## Globals

- `window.__sceneReady`: optional Promise. The recorder awaits it before starting the
  clock, so a page can wait for fonts, images or data. The runtime sets it to
  `document.fonts.ready` unless the page set its own.
- `window.__decktalk`: `{ mode, scene, step, cues, fired, catalog, now() }`. `catalog`
  lists every scene and its step ids (used by `decktalk shots`); `fired` is the list of
  cue ids fired so far.
- `document.body.dataset.done = "1"` once the last step has mounted.

## Timing semantics (cue mode)

1. Cues are sorted by time. Each cue belongs to a step: the step with the same id, or
   whose `cues` list names it, or whose id is the longest prefix of the cue id
   (`4.2b1` belongs to `4.2`; `9a` belongs to `9`).
2. A step mounts at the earliest of its cues. The first cued step mounts at t=0
   regardless, so a section never opens on an empty stage. Steps with no cue never show.
   The last cued step holds until the recorder stops.
3. When a step mounts, elements with `data-cue` whose cue is listed stay hidden until
   that cue fires. Every other reveal (`data-at`, or a `data-cue` not in the list)
   fires `data-at` seconds after the mount, default 0.
4. When a cue fires: matching `data-cue` elements reveal, then the step's `on[id]`
   handler, then every `DeckTalk.on(id, fn)` handler.

Autoplay (no `beats=`): steps mount in order, each holding `hold` seconds divided by
`speed`; reveals fire at `data-at`; a step's `cues: { id: seconds }` object fires
handler cues at those seconds after the mount.

## Authoring API

```js
DeckTalk.scene(id, {
  name: "Shown on the index",
  camera: "push",                       // slow 1.00 -> 1.03 push over the scene; omit for none
  steps: [{
    id: "3.1",                          // also a cue id; default "<scene>.<n>"
    hold: 8,                            // autoplay seconds
    cues: ["3.1draw"] | { "3.1draw": 0.5 },  // ownership, and autoplay timing for handler cues
    render: ({ scene, step, frozen }) => "<div class='slide'>…</div>",
    enter: (slideEl, { frozen }) => {}, // after mount, for imperative setup
    on: { "3.1draw": () => {} },        // per-step cue handlers
  }],
});
DeckTalk.on("3.1draw", fn);             // global cue handler
DeckTalk.start();                       // automatic on DOMContentLoaded when scenes exist
```

Markup attributes inside a step: `data-cue`, `data-at`, `data-fx`, `data-dur`,
`data-count` (`first` to count the first number instead of the last), `data-type`
(ms per character), `data-tex` (+ `data-display` for display math).

The runtime creates `#dt-stage` (1920×1080, scaled to fit the window) with `#dt-cam`
and `#dt-pan` inside; slides are `.dt-slide` children of the pan layer. Style the stage
and your slides however you like; the runtime's own CSS is prefixed `dt-`.

## Build artifacts a page or script may read

| File | Shape |
|---|---|
| `build/audio/timeline.json` | `{narration, total_seconds, estimated, sections: {"NN": {title, start, end, duration, speech_end, words: [{word, start, end}]}}}`, absolute seconds in `narration.mp3`. |
| `build/audio/beats.json` | `{"NN": "cue@seconds,…"}`, seconds relative to the section start. |
| `build/audio/manifest.json` | One entry per narrated section: file, words file, hash, durations. |
| `build/rec/NN-scene.json` | What the recorder did and where narration t=0 sits in the webm. |

## Recording alignment

The recorder opens the page, waits for `load`, awaits `__sceneReady`, waits `t0`
seconds, then injects a full-frame magenta div for 120 ms. `decktalk measure` finds the
last magenta frame in the webm and writes `lead_in_seconds` to the sidecar; the
assembler trims that off the head so video t=0 equals narration t=0 to within a frame.
