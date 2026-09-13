# __TITLE__ — narration script

Each "## N." section is one cut of the video. Bracketed directions are not spoken: `[beat]`
is a short pause, `[pause 4]` is a four second pause, and anything else in brackets is a
note to yourself. Write numbers and symbols the way you want them said ("forty one", not
"41"), because the cue phrases in `cues.json` match spoken words, and a `data-sync` line on a
slide must be word for word what the voice says. To open with a clip of your own, follow the
comment block in `decktalk.toml` and add a `## 0. On camera` heading above section 1.

---

## 1. Open

[Deck scene 1. A bowl and a ball appear on their words. The ball steps down on each count word, and each count word's start time appears under its box.]

A bowl. [beat] A ball. [beat] Watch it step down on my count. [beat] One. [beat] Two, three. [beat]
I wrote the words and drew the pictures, [beat] and each picture waits for its word.

## 2. How it works

[Deck scene 2. The wordmark moves to the top. A script card and a slide card appear, a waveform runs under one line, and the cue word joins its drawing.]

This is DeckTalk. [beat] You write a script and simple slides. [beat] It reads your script aloud, [beat]
and you pick the word each picture waits for. [beat] Change the script, and the video follows. [beat]
Here is a lesson I made with it.

## 3. How AI learns

[Lesson page scene 3. A box of knobs learns from examples, a ball steps down a bowl, and the lesson ends on chips that do the math.]

How does AI learn? [beat] From examples, not rules people write. [beat]
An AI model is like a box of knobs. [beat] It guesses, [beat] and a meter shows how wrong. [beat]
Learning turns the knobs until the meter drops, [beat] example after example.
[pause 1] But which way should each knob turn? [beat]
In a big model, testing knobs one at a time would take billions of tries for every step. [beat]
Instead, one round of math gives a direction for every knob at once.
[pause 1] Picture two knobs. [beat]
The floor is every way to set them. [beat]
The height is how wrong. [beat]
For this lesson it is a bowl.
[pause 2] The ball is where the knobs are set right now. [beat]
It can't see the whole bowl, only the slope where it stands. [beat]
So it steps downhill, [beat] again and again, and the steps shrink as the ground flattens. [beat]
That is gradient descent.
[pause 1] One setting makes every step longer or shorter. [beat]
Too long, and it bounces past the bottom. [beat] Too short, and it is very slow.
[pause 1] Zoom out, and the ground can have many valleys, [beat] each about as low.
[pause 2] Today's AI is trained with a version of this. [beat] One step takes billions of billions of small sums.
[pause 1] Inside, the knobs are grids of numbers, called tensors. [beat] Chips like GPUs do math on a whole grid at once. [beat]
Big models split the work across thousands of chips.

## 4. Thousands of chips

[Clip at media/broll-chips.mp4, about four seconds of chip racks with no voice. The narration pauses here.]

## 5. The edit

[Deck scene 4. The script gains four words, the rebuild voices only section 3 again, and verify shows the later cues moved with their words.]

Now change one sentence, [beat] the kind of edit that used to mean recording again. [beat]
Say a viewer asks why downhill is good. [beat] So I add four words, [beat] and make the video again. [beat]
Nothing re-recorded or re-edited by hand. [beat] The height is how wrong, so lower is better. [beat]
Every later picture moved to stay on its word.

## 6. Close

[Deck scene 5. The end card.]

Update your video the way you update a doc. [beat] DeckTalk. Open source, narrated presentations, cut to the word.
[beat] Make your own at decktalk dot app.
