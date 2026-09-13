# Track C docs notes (page runtime)

Exact replacements for the docs pass. Each entry names the page, the section, the text to
find, and the text that replaces it. Replacement text is complete sentences with no
semicolons and no version wording.

## docs/reference/runtime.mdx

### Intro code block (`html deck/index.html`)

Find:

```js
DeckTalk.on("3.2b", () => document.querySelector(".bars").classList.add("grow"));
```

Replace with:

```js
DeckTalk.on("3.2b", (slide) => slide.querySelector(".bars").classList.add("grow"));
```

### URL parameters, the `step` field

Replace the body of `<ResponseField name="step" type="string">` with:

> Freeze mode. Mount this step with every reveal in its end state and its cues fired in autoplay order. Animations run with zero duration, count-ups show their final value, typewriters show their full text, and synced text shows whole. Every handler receives `ctx.frozen` as true.

### URL parameters, a new `cue` field directly after `step`

```mdx
<ResponseField name="cue" type="string">
  With `step`, freeze at this cue rather than at the end of the step. The step's cues fire in autoplay order up to and including this one, an element whose `data-cue` is a later cue of the step stays hidden, and every other reveal shows. Handlers still receive `ctx.frozen` as true, so a figure that draws its end state when frozen still draws it. An id that is not one of the step's cues adds the warning `cue "X" is not one of step 3.1's cues`, and the whole step freezes. `decktalk shots --step 3.1 --cue 3.1eq` opens `?step=3.1&cue=3.1eq`.
</ResponseField>
```

### `DeckTalk.scene`, step fields `enter` and `on`

Replace the `enter` field with:

```mdx
<ResponseField name="enter" type="(slideElement, ctx) => void" default="null">
  Runs after the slide is in the DOM, for imperative setup. It receives the slide element and [the handler context](#handler-context), where `id` and `step` are both the step id and `at` is the second at which the step mounted. Keep what the cue handlers need on the slide element, as in `slide.fig = setup(slide, ctx.frozen)`, rather than in a global. An exception is logged and does not stop the scene.
</ResponseField>
```

Replace the `on` field with:

```mdx
<ResponseField name="on" type="{ [cueId]: (slideElement, ctx) => void }" default="{}">
  Per-step cue handlers, run after the `data-cue` reveals and before global handlers. Each receives the slide element of the mounted step and [the handler context](#handler-context), so it finds its elements with `slide.querySelector`. A handler that takes no arguments keeps working.
</ResponseField>
```

### A new section `## Handler context`, directly before `## DeckTalk methods and properties`

```mdx
## Handler context

A step's `enter`, its `on[id]` handlers, and every `DeckTalk.on(id, fn)` handler receive
two arguments: the slide element of the mounted step and a context object. A handler that
takes no arguments keeps working.

| Field | Meaning |
|---|---|
| `id` | The cue id. For `enter` it is the step id. |
| `at` | The second after narration t=0, to the millisecond, at which the cue fired or the step mounted. It is negative infinity while the clock has not started, which is the case for a frozen step. |
| `frozen` | True in freeze mode, where a handler should show its end state at once. |
| `step` | The id of the mounted step. |

```js deck/index.html
{ id: "4.1", hold: 28, cues: { "4.1valley": 2.5 },
  on: { "4.1valley": (slide) => slide.querySelector(".old").classList.add("struck") },
  render: () => `…` }
```
```

### `DeckTalk` methods, `on(id, fn)`

Replace the body with:

> Registers a global handler for cue `id`, called as `fn(slide, ctx)` with the slide element of the mounted step and [the handler context](#handler-context). Several handlers may share an id. A handler also stops an unknown cue id from being reported as a warning.

### `DeckTalk` methods, `fireCue(id)`

Replace the body with:

> Fires a cue now. It reveals the matching `data-cue` elements on the current slide, runs the step's `on[id]` and then the global handlers, each with the slide and [the handler context](#handler-context), and records the id in `window.__decktalk.fired`.

### Data attributes, `data-cue`

Replace the body with:

> Reveal on this cue in cue mode. In autoplay, or when the cue is not in `beats`, reveal at `data-at` seconds after the step mounts. In cue mode an id that is not in `beats` also adds a warning.

### Data attributes, `data-count` and `data-type`

Append to the end of each body:

> The element needs `data-cue` or `data-at` as well, because the effect runs when the element reveals, and without either the page warns.

### Data attributes, `data-sync`

Find:

> Pair it with `data-fx="none"`, because a fade on the container fights the per-word reveal.

Replace with:

> The element gets `data-fx="none"` unless it sets its own `data-fx`, because a fade on the container fights the per-word reveal. It needs `data-cue` or `data-at` as well, and without either it never reveals and the page warns.

### Console warnings table

Add these rows after the `cue "X" matches no element, handler, or step` row:

```md
| `step "3.2" owns no cue in ?beats=, so it never appears` | A step of the playing scene owns none of the cues in `beats`. Cue mode mounts only the steps that own a listed cue. |
| `data-cue "4.1change" is not in ?beats=, so it reveals at its data-at time after the mount` | In cue mode an element waits for a cue that `beats` does not list, usually a cue missing from `cues.json` or left out by `--allow-unresolved`. |
| `data-sync on an element without data-cue or data-at never reveals, so add data-at="0"` | An element has `data-sync`, `data-count`, or `data-type` but no trigger. The warning names the attribute. |
| `step "3.1" fires "3.1min" at 32 s but holds 30 s, so it fires on the next step` | In autoplay a time in the step's `cues` object is at or past its `hold`, so the cue fires after the next step mounts and finds none of its elements. The last step is exempt, because it holds until the end. |
| `cue "X" is not one of step 3.1's cues` | The `cue` parameter names an id that the frozen step does not list in `cues`. The whole step freezes. |
```

An existing row outside this track's change reads `KaTeX could not parse "…"`, while the
runtime's text is `data-tex could not be parsed: "…" (write \\ for every backslash inside a
template literal)`. The docs pass may correct the row to the runtime's text.

## docs/concepts/page-contract.mdx

### Modes table, the `frozen` row

Replace the row with:

```md
| `frozen` | `?step=ID` or `?step=ID&cue=CUE` | One step with every reveal in its end state and its cues fired. With `cue`, only the step's cues up to and including that one fire, and an element that waits for a later cue stays hidden. `decktalk shots` uses it. |
```

### The paragraph after the modes table

Find:

> When a cue fires, the matching `data-cue` elements reveal first, then the step's `on[id]` handler runs, then every `DeckTalk.on(id, fn)` handler. An element with `data-sync` goes one step further and appears one word at a time from the spoken words the recorder passes as `?words=`, so a line of text keeps pace with the voice.

Replace with:

> When a cue fires, the matching `data-cue` elements reveal first, then the step's `on[id]` handler runs, then every `DeckTalk.on(id, fn)` handler. Each handler receives the slide element of the mounted step and a context object `{ id, at, frozen, step }`, so it finds its elements with `slide.querySelector` and needs no global. [The runtime reference](/reference/runtime#handler-context) describes the fields. An element with `data-sync` goes one step further and appears one word at a time from the spoken words the recorder passes as `?words=`, so a line of text keeps pace with the voice. The runtime gives it no container animation unless it sets `data-fx`.

### Cue ownership, the paragraph that starts "A cue that no step owns"

Replace the paragraph with:

> A cue that no step owns is pushed onto `window.__decktalk.warnings` as an unknown cue id, unless a `DeckTalk.on` handler is registered for it. When no listed cue has an owner, nothing mounts and the page warns about that too. A step of the scene that owns no listed cue never appears, and the page warns `step "3.2" owns no cue in ?beats=, so it never appears`. An element whose `data-cue` is not listed reveals at its `data-at` time after the mount, and the page warns about that as well. The scaffold names every cue in its step's `cues` object, which also gives the browser preview its autoplay timing.

## docs/concepts/cues.mdx

### Which sections have cues, the paragraph that starts "The `beats` stage never checks"

Append after the sentence that ends "so a typo in `cues.json` is named rather than silently fired.":

> The page reports the reverse as well. An element whose `data-cue` is not among the section's cues warns `data-cue "4.1change" is not in ?beats=, so it reveals at its data-at time after the mount`, and a step that owns none of them warns `step "3.2" owns no cue in ?beats=, so it never appears`.

(Track B rewrites the first sentence of this paragraph for the static cross-reference in
`beats`. Apply its text first, then append this.)

### Unresolved cues

Find:

> The page then ignores the missing cue, and an element waiting for it appears at its `data-at` time after its step mounts.

Replace with:

> The page then ignores the missing cue, and an element waiting for it appears at its `data-at` time after its step mounts, with a warning that the id is not in `?beats=`.

## docs/reference/card.mdx

### Files table, the `deck/index.html` + `deck/decktalk-runtime.js` row

Find:

> `DeckTalk.scene(N, {name, camera?, steps: [{id, hold?, cues?, render, enter?, on?}]})`.

Replace with:

> `DeckTalk.scene(N, {name, camera?, steps: [{id, hold?, cues?, render, enter?, on?}]})`. `enter`, every `on[id]`, and `DeckTalk.on(id, fn)` are called as `(slide, {id, at, frozen, step})`.

Find, in the same row:

> `data-sync` (one word at a time as the voice says it, text word for word as spoken, with `data-fx="none"`)

Replace with:

> `data-sync` (one word at a time as the voice says it, text word for word as spoken, and no container animation unless `data-fx` is set)

Append to the end of the same row's text, before the links:

> `data-sync`, `data-count`, and `data-type` need `data-cue` or `data-at` on the same element.

### The paragraph under the files table

Find:

> and the page records an id no step owns in `window.__decktalk.warnings`.

Replace with:

> and the page records in `window.__decktalk.warnings` an id no step owns, a step that owns no listed cue, and a `data-cue` that is not listed.

### Command block

Find:

```text
decktalk shots [--section N --at S]         # PNG per step, or a frame from a playing section
```

Replace with:

```text
decktalk shots [--step ID [--cue CUE]]      # PNG per step, or one step frozen at a cue
decktalk shots --section N --at S           # a frame from a playing section
```

## docs/guides/slide-recipes.mdx

### A subtitle that follows the voice, the intro paragraph

Find:

> Give it `data-fx="none"`, since a fade on the container fights the per-word reveal.

Replace with:

> It needs no `data-fx`. The runtime gives a synced element no container animation, because a fade on the container would fight the per-word reveal.

### A subtitle that follows the voice, the HTML block

Remove ` data-fx="none"` from the four `data-sync` lines, so they read:

```html
<p class="line" data-cue="1.1b" data-sync>This is a narrated lesson, cut to the word.</p>
<p class="line" data-cue="5.1a" data-sync>Write the script.</p>
<p class="line" data-cue="5.1b" data-sync>Narrate it in your own voice.</p>
<p class="line" data-cue="5.1c" data-sync>Every reveal lands on its word.</p>
```

### A subtitle that follows the voice, the closing paragraph

Append after "because the preview has no word times.":

> To check the line at one cue without playing the section, open `?step=5.1&cue=5.1b`, which fires the step's cues up to `5.1b` and leaves `5.1c` hidden.

## docs/reference/cli.mdx

### `decktalk shots`

Find:

> Writes one PNG per step, or frames from a section while it plays with its real cues.

Replace with:

> Writes one PNG per step, one PNG of a step frozen at one of its cues, or frames from a section while it plays with its real cues.

Add this row to the flags table after `--step ID`:

```md
| `--cue CUE` | With `--step`, freeze the step at this cue. The step's cues fire in autoplay order up to and including this one, and every reveal that waits for a later cue stays hidden. The page warns when the id is not one of the step's cues, and the whole step is captured. |
```

After the paragraph that says where step screenshots land, add:

> A step frozen at a cue opens the page as `?step=ID&cue=CUE`, which [the runtime reference](/reference/runtime#url-parameters) describes.

(Track B picks the PNG file name for a `--cue` capture. The docs pass should add that path
to the "Step screenshots land at" sentence from Track B's notes.)
