# Slide patterns

A scene is markup. Write attributes, not JavaScript. Every pattern here goes inside a
`<template data-slide>`, which the runtime clones when the slide mounts.

## The page

The head loads KaTeX from the copies in `deck/katex/` and then the runtime, in that order, so the
first slide is already typeset when the recorder starts the clock.

```html
<link rel="stylesheet" href="./katex/katex.min.css">
<script src="./katex/katex.min.js"></script>
<script src="./decktalk-runtime.js"></script>
```

## The scene wrapper

```html
<div data-scene="3" data-name="How it works">
  <template data-slide="3.1" data-hold="12">
    <h1 data-cue="3.1title" data-describe="the heading, One idea">One idea</h1>
    <p data-cue="3.1detail" data-reveal="fade" data-describe="the line that follows it">
      The line that follows it
    </p>
  </template>
  <template data-slide="3.2" data-hold="10">
    <h1 data-cue="3.2next" data-describe="the heading, The next idea">The next idea</h1>
  </template>
</div>
```

| Attribute | Where | What it does |
| --- | --- | --- |
| `data-scene="3"` | the wrapper | Declares the scene a `[[section]]` table names |
| `data-name="How it works"` | the wrapper | The scene name in the index and the catalog |
| `data-camera="push"` | the wrapper | Runs the camera push over the scene |
| `data-slide="3.1"` | a `<template>` | Declares one slide |
| `data-hold="12"` | a `<template>` | Seconds the slide holds in preview |
| `data-owns="odd-one"` | a `<template>` | Cue ids this slide owns that do not start with its id |
| `data-preview="3.1title@0.5"` | a `<template>` | When each cue fires in preview, as `id@seconds` pairs |

A cue belongs to the slide whose id is the longest prefix of the cue id, so slide `3.1` owns
`3.1title`. Use `data-owns` only for a cue id that breaks that rule.

A slide that genuinely needs code keeps a `render` function on a scene registered with
`DeckTalk.scene(3, ...)`, and then every backslash, backtick and `${` inside its template literal
has to be escaped. Markup has none of those traps, so reach for `render` only when the user asks.

## The attributes

| Attribute | What it does |
| --- | --- |
| `data-cue="3.1title"` | The element waits for that cue and reveals on it |
| `data-delay="1.2"` | The element reveals 1.2 seconds after the slide mounts, with no cue |
| `data-reveal="rise\|fade\|draw\|drop\|pop\|dim\|instant"` | The reveal effect, and `rise` is the default |
| `data-duration="0.6"` | The length of the animation, and the length of a count-up |
| `data-text="count"` | The last number in the text counts up from zero on reveal |
| `data-text="type 40ms"` | The text types in at that many milliseconds per character |
| `data-text="spoken"` | The text appears word by word as each word is spoken |
| `data-tex="x^2"` | The element is typeset by KaTeX |
| `data-tex-display` | The element is typeset in display mode |
| `data-describe="..."` | One sentence saying what the reveal shows, which the transcript page reads |

Choose `fade` for an equation, `pop` for the one result a section builds to, `draw` for a curve that
sets `pathLength="1"`, and `dim` for the part of a picture that steps back.

## An equation

```html
<div class="eq" data-cue="3.2chain" data-describe="the chain rule, written out">
  <span data-tex-display data-tex="\frac{d}{dx} f(g(x)) = f'(g(x)) \, g'(x)">
    d by d x of f of g of x equals ( f prime of g of x ) times ( g prime of x )
  </span>
</div>
```

Inside a `<template data-slide>` every backslash is written once, as above. Inside a `render`
template literal every backslash has to be written twice, because the literal eats a single one. The
text inside the element is the fallback that shows when KaTeX does not load, so it carries its own
brackets and is correct mathematics on its own.

## A code sample

```html
<pre class="code" data-cue="4.1call" data-reveal="fade"><code>const res = await client.users.list()</code></pre>
```

- Each code line sits on one physical line, because the template's own indentation shows.
- Write `&lt;` and `&amp;` for `<` and `&`. In a `render` template literal escape a backtick and a
  dollar-brace as well, and double every backslash.
- Fade a whole container in on one cue. A typed line reads as no change to the checker.
- Copy a syntax highlighter into `deck/` when one is needed, and set
  `window.__decktalk.ready` to a promise that waits for it.

## A screenshot

```html
<img class="screenshot" src="../media/inbox.png" alt="The inbox with one unread message"
     data-cue="5.1inbox" data-reveal="fade">
```

- Capture at two times the stage scale, so text stays sharp at 1920 by 1080.
- Capture from a demo account. Never show a real customer's name, email or invoice.
- Give a screenshot at least four seconds before the next reveal on the same slide.
- Highlight a region by revealing a box over it on its own cue, or by dimming the rest of the image.
  Never hide the check with a highlight that is too small to see.

## The frame

- The stage is 1920 by 1080, and the page is scaled to it.
- Body text is 36 pixels or larger.
- The bottom fifteen percent of the frame is the caption band. Keep every cued element out of it.
- Four reveals is the most one slide should carry, and three equations is the most.
- Scope every class name to its scene, such as `.s3-eq`, so two scenes on one page cannot collide.

## What not to write

- No frame clock, no canvas drawing, no three.js and no hand-written colour ramp.
- No `render` function where a `<template data-slide>` does the same job.
- No `enter` or `on` handler for an effect that `data-reveal` already gives.
- No network address in a `src` or an `href`.
- No fake product user interface drawn in HTML.
