# __TITLE__ — narration script

One section, about two minutes. Bracketed directions are not spoken: `[beat]` is a short pause and
`[pause 2]` is a two second pause. Every number is written the way the voice must say it, because a
cue phrase is matched against these words.

---

## 1. How a model learns

[Scene 1 of deck/lesson.html. Examples take the place of rules, one guess is corrected, and the same
small step repeats until the error stops falling.]

How does a model learn? [beat] From examples. [beat]
Here are four of them: a picture, and the word that goes with it. [beat]
Not from rules a person writes out by hand. [beat]
Learning this way has a name, and the name is machine learning.
[pause 2] A model is a box of knobs, and every knob is one number it can turn. [beat]
It looks at an example, and it guesses. [beat]
A meter shows the error, which is how wrong that guess was. [beat]
Learning nudges the knobs. [beat]
It guesses again, and this time it is right, with a lower error.
[pause 2] But which way should each knob turn? [beat]
Turning one knob at a time would take billions of tries for every step. [beat]
Instead, one round of maths gives a direction for every knob at once.
[pause 2] Picture the error as a hill. [beat]
The model stands somewhere on it, and it cannot see the whole hill, only the slope under its feet. [beat]
So it steps downhill, again and again, and the steps shrink as the ground flattens. [beat]
That is gradient descent.
[pause 2] One setting decides how long each step is. [beat]
Too long, and the model bounces past the bottom. [beat]
Too short, and it crawls.
[pause 2] Inside a real model the knobs are grids of numbers. [beat]
A large one holds four hundred billion of them. [beat]
Chips multiply whole grids at once, which is why training needs them.
