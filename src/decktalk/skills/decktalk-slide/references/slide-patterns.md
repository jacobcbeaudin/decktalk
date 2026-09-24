# Slide patterns

A scene is markup. Write attributes, not JavaScript. Every pattern here goes inside a
`<template data-slide>`, which the runtime clones when the slide mounts.

## The page

The head loads the typesetter from the copies in `deck/katex/` and then the runtime, in that order,
so the first slide is already typeset when the recorder starts the clock. A page with no equation on
it needs the runtime alone.

```html
<link rel="stylesheet" href="./katex/katex.min.css">
<script src="./katex/katex.min.js"></script>
<script src="./decktalk-runtime.js"></script>
```

## The scene wrapper

```html
<div data-scene="3" data-name="How it works">
  <template data-slide="3.1" data-hold="12" data-describe="the idea and the line under it">
    <h1 data-in="title" data-describe="the heading, One idea">One idea</h1>
    <p data-in="detail" data-in-style="fade" data-describe="the line that follows it">
      The line that follows it
    </p>
  </template>
  <template data-slide="3.2" data-hold="10" data-enter="cut" data-describe="the next idea">
    <h1 data-in="next" data-describe="the heading, The next idea">The next idea</h1>
  </template>
</div>
```

The slide qualifies every local name written inside it, so `data-in="title"` in slide `3.1` is the
cue `3.1:title`. `data-owns` is for a local name no attribute mentions, because a handler serves it.

## The four moments

| Attribute | What it does |
| --- | --- |
| `data-in="expand"` | The element arrives at that cue |
| `data-back="cancel"` | It steps back, dimmed and still readable |
| `data-front="result"` | It returns to full strength |
| `data-out="result"` | It leaves |

Everything else is either how a moment looks or what it means. `decktalk schema page` prints every
row with its values, its default and its range, so read that rather than guessing.

## An equation

```html
<div class="s3-eq" data-in="chain" data-in-style="fade" data-describe="the chain rule, written out">
  <span data-tex-display data-tex="\frac{d}{dx} f(g(x)) = f'(g(x)) \, g'(x)">
    d by d x of f of g of x equals ( f prime of g of x ) times ( g prime of x )
  </span>
</div>
```

A backslash is written once in markup. The text inside the element is the fallback that shows when
the typesetter does not load, so it carries its own brackets and is correct mathematics on its own.

## A code sample

```html
<pre class="s4-code" data-in="call" data-in-style="fade"><code>const res = await client.users.list()</code></pre>
```

- Each code line sits on one physical line, because the template's own indentation shows.
- Write `&lt;` and `&amp;` for `<` and `&`.
- Fade a whole container in on one cue. A line that types in reads as no change to the checker.
- Copy a syntax highlighter into `deck/` when one is needed, and hold the page's readiness until it
  has loaded.

## A screenshot

```html
<img class="s5-shot" src="../media/inbox.png" alt="The inbox with one unread message"
     data-in="inbox" data-in-style="fade" data-describe="the inbox, with one unread message">
```

- Capture at twice the stage scale, so text stays sharp at 1920 by 1080.
- Capture from a demo account. Never show a real customer's name, email or invoice.
- Give a screenshot at least four seconds before the next reveal on the same slide.
- Highlight a region by revealing a filled box over it on its own cue, or by dimming the rest with a
  step back. Never hide the check behind a highlight too small to see.

## A list that walks

```html
<ul data-steps>
  <li data-in="first" data-describe="step one">Guess</li>
  <li data-in="second" data-describe="step two">Measure the error</li>
  <li data-in="third" data-describe="step three">Nudge the knobs</li>
</ul>
```

Each child comes to the front as it arrives and the ones before it step back, from the cues the
children already declare. A container may instead spread one cue over its children, and that whole
spread has to stay under half a second or the cue can no longer be measured.

## A state the page's own stylesheet draws

```html
<p class="s2-answer" data-in="guess"
   data-class="nudge:stale|right:live"
   data-describe-class="nudge:the answer is set aside|right:the answer turns green"
   data-describe="the model's answer">cat</p>
```

A class change is the one thing the contract cannot bound, so it always carries its own sentence, and
the page's stylesheet has to drop the motion under the reduced-motion class or the page is told so.

## The frame

- The stage is 1920 by 1080, and the page is scaled to it.
- Body text is 36 pixels or larger.
- The bottom fifteen percent is the caption band. Keep every cued element out of it.
- Four reveals is the most one slide should carry, and three equations is the most.
- Scope every class name to its scene, such as `.s3-eq`, so two scenes on one page cannot collide.

## What not to write

- No frame clock, no canvas drawing and no hand-written colour ramp.
- No render function where a `<template data-slide>` does the same job.
- No handler for an effect an attribute already gives.
- No network address in a `src` or an `href`.
- No fake product interface drawn in HTML.
- No seconds. The project's cue file owns every second in the film.
