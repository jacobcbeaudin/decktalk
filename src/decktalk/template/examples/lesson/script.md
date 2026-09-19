# __TITLE__ — narration script

One section, about two minutes. Bracketed directions are not spoken: `[beat]` is a short pause and
`[pause 4]` is a four second pause. The page draws one figure before narration t=0 and moves it on
each cue, so nothing heavy is built while the clock runs.

---

## 1. How AI learns

[Scene 1 of deck/lesson.html. A box of knobs learns from examples, a ball steps down a bowl, and
the lesson ends on chips that do the math.]

How does AI learn? [beat] From examples, not rules people write. [beat] Learning from examples is called machine learning. [beat]
An AI model is like a box of knobs. [beat] It guesses, [beat] and a meter shows its error, how wrong the guess was. [beat]
Learning nudges the knobs. [beat] It guesses again, and now it's right, with a lower error. [beat]
Then a new example, [beat] and a smaller nudge fixes it. [beat]
Example after example, the model gets better.
[pause 1] But which way should each knob turn? [beat]
In a big model, testing knobs one at a time would take billions of tries for every step. [beat]
Instead, one round of math gives a direction for every knob at once.
[pause 1] Picture two knobs. [beat]
The floor is every way to set them. [beat]
The height is the error. [beat]
For this lesson it is a bowl.
[pause 2] The ball is where the knobs are set right now. [beat]
It can't see the whole bowl, only the slope where it stands. [beat]
So it steps downhill, [beat] again and again, and the steps shrink as the ground flattens. [beat]
That is gradient descent.
[pause 1] One setting makes every step longer or shorter. [beat]
Too long, and it bounces past the bottom. [beat] Too short, and it is very slow.
[pause 1] Zoom out, and the ground can have many valleys. [beat] The ball finds a low one, not always the lowest.
[pause 2] Today's AI is trained with a version of this. [beat] Inside, the knobs are grids of numbers, called tensors.
[pause 1] Chips like GPUs and TPUs multiply grids, row times column, all at once. [beat] For a big model, the chips do billions of billions of small calculations for every step. [beat]
The large language models behind AI chat apps split the work across thousands of chips.
