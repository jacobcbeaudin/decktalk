# Spoken math, numbers and identifiers

The voice reads `script.md` exactly as it is written, the word timings come from that same text, and
a cue phrase is matched against it word for word. Anything written as a symbol is therefore read by
the voice as a guess, cued by a phrase that cannot match, and shown in the captions as the guess.
Write the spoken form.

## Write every number as a word

- `2` becomes "two", `1/2` becomes "one half", `0.5` becomes "nought point five" or "zero point
  five", and `42` becomes "forty two".
- `v2.3` becomes "version two point three".
- A year, a port number or a long identifier is read digit by digit when that is how a person says
  it, such as "port eight thousand" or "port eight zero eight zero".

## Write every symbol as a word

| Written | Spoken |
| --- | --- |
| `x^2` | x squared |
| `x^3` | x cubed |
| `x^n` | x to the n |
| `f'(x)` | f prime of x |
| `dy/dx` | d y by d x |
| `\frac{a}{b}` | a over b |
| `\sqrt{x}` | the square root of x |
| `\sum` | the sum of |
| `\int` | the integral of |
| `=` | equals |
| `\approx` | is about |
| `<` and `>` | is less than, is greater than |
| `\theta`, `\pi`, `\alpha` | theta, pi, alpha |
| `\nabla f` | the gradient of f |
| `\partial f / \partial x` | the partial derivative of f with respect to x |

## Say the grouping aloud

Spoken math is ambiguous even when every symbol is a word. "Sine of x squared" names two different
expressions. Remove the ambiguity in one of three ways.

1. Say the grouping. "The sine of x squared, with x squared inside the sine."
2. Name the inner part first. "Call x squared u, so we have the sine of u."
3. Say the parentheses. "Open bracket x plus h, close bracket, squared."

Prefer the second form in a derivation, because it gives the inner part a name that later sentences
can reuse.

## Keep one notation

One letter means one thing for the whole video. If `g` is the inner function in section 3, it is not
a gradient in section 5. Say each letter's meaning the first time it appears.

## Pace a derivation

- One equation per sentence.
- A `[beat]` before the sentence that introduces an equation.
- A `[pause 2]` or longer after a new equation, so a learner can read it.
- Each bracketed direction sits on its own, never inside a paragraph, because a bracket inside a
  paragraph changes the text that is sent to the voice.

## Code and product names

- Respell a name only when the voice says it wrongly, and accept that the respelling becomes the
  cue phrase and the caption too. Tell the user when you respell a name.
- A method call such as `res.json()` is spoken as "res dot j s o n". A generic such as
  `Promise<Response>` is spoken as "a promise of a response".
- A file path is spoken as its words, such as "script dot m d" for `script.md`.

## Check before voicing

Run `decktalk narrate --dry-run --json` and read the `text` of every row of `narrate.sections`. That
is the exact string the voice receives. Rewrite any digit, caret, slash, prime, equals sign, Greek
letter or bracket that is still in it. Then show the author a two-column table of each spoken phrase
and the mathematics it stands for, and wait for the author to confirm that the words and the symbols
say the same thing.
