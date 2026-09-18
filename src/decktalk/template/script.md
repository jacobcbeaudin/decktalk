# __TITLE__ — narration script

Each "## N." section is one cut of the video. Bracketed directions are not spoken: `[beat]`
is a short pause, `[pause 4]` is a four second pause, and anything else in brackets is a
note to yourself. Write numbers and symbols the way you want them said ("forty one", not
"41"), because the cue phrases in `cues.json` match spoken words, and a `data-text="spoken"` line on a
slide must be word for word what the voice says. To open with a clip of your own, follow the
comment block in `decktalk.toml` and add a `## 0. On camera` heading above section 1.

---

## 1. Open

[Deck scene 1. A bowl and a ball appear on their words. The ball steps down on each count word, and each count word's start time appears under its box.]

A bowl. [beat] A ball. [beat] Watch it step down on my count. [beat] One. [beat] Two, three. [beat]
I wrote the words and drew the pictures, [beat] and each picture waits for its word.

## 2. How it works

[Deck scene 2. It opens on the Open's last frame. The count clears and the wordmark moves to the top. A script card and a slide card appear, a waveform runs under one line, the cue word joins its drawing, and the slide card pushes in to become the lesson.]

You just watched DeckTalk. [beat] You write a script and simple slides. [beat] It reads your script aloud, [beat]
and you pick the word each picture waits for. [beat] Change the script, and the video follows. [beat]
Here is a lesson I made with it.

## 3. How AI learns

[Lesson page scene 3. A box of knobs learns from examples, a ball steps down a bowl, and the lesson ends on chips that do the math.]

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
That is gradient descent. [beat] It's what the ball did on my count.
[pause 1] One setting makes every step longer or shorter. [beat]
Too long, and it bounces past the bottom. [beat] Too short, and it is very slow.
[pause 1] Zoom out, and the ground can have many valleys. [beat] The ball finds a low one, not always the lowest.
[pause 2] Today's AI is trained with a version of this. [beat] Inside, the knobs are grids of numbers, called tensors.
[pause 1] Chips like GPUs and TPUs multiply grids, row times column, all at once. [beat] For a big model, the chips do billions of billions of small calculations for every step. [beat]
The large language models behind AI chat apps split the work across thousands of chips.

## 4. The edit

[Deck scene 4. Opens on the lesson's last picture. The whole frame shrinks into a video player, five part chips appear, the script opens under them, and the player jumps back to the start.]

That's the lesson. [beat] Now let's step out of it. [beat] Everything you just watched is one video, [beat] in five parts, [beat] made from one script. [beat]
Remember how it started?

## 5. The edit

[Clip at media/edit-before.mov. The Open as first built plays inside the player, with its own sound. The narration pauses here. Until the file is in place, a slate plays.]

## 6. The edit

[Deck scene 6. One word is added to the script and a new box waits for it. Build reads only part one aloud again, and the player jumps back to the start.]

Say I want one more step. [beat] I add one word, [beat] four, [beat] and tell a new box to wait for it. [beat] I press build. [beat]
Only part one is read aloud again. [beat] The rest keep their voice. [beat] Here is the new start.

## 7. The edit

[Clip at media/edit-after.mov. The rebuilt Open plays inside the same player, with its own sound. The narration pauses here. Until the file is in place, a slate plays.]

## 8. The edit

[Deck scene 7. Before and after side by side with their count times, a check, and the player grows back into the video.]

One more count, one more box. [beat] Every picture in the start moved to stay on its word, [beat] and each one is checked. [beat]
Now let's step back in.

## 9. Close

[Deck scene 5. The end card.]

Update your video the way you update a doc. [beat] DeckTalk. Open source, narrated presentations, cut to the word.
[beat] Make your own at decktalk dot AI.
