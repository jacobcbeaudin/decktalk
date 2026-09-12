# __TITLE__ — narration script

Each "## N." section is one cut of the video. Bracketed directions are not spoken: `[beat]`
is a short pause, `[pause 3]` is a three second pause, and anything else in brackets is a
note to yourself. Write numbers and symbols the way you want them said ("two x", not "2x"),
because the cue phrases in `cues.json` match spoken words. To open with a clip of your own,
follow the comment block in `decktalk.toml` and add a `## 0. On camera` heading above
section 1.

---

## 1. Open

[Deck scene 1. Title, then the subtitle, then the lower third types in.]

Welcome. This is a narrated lesson, cut to the word. [beat] Every visual you see lands on
the word that introduces it.

## 2. Three lines

[Deck scene 2. Three labels reveal one at a time, then a tile counts up.]

Here is how it works. [beat] First, you write the script in markdown. [beat] Second, you
narrate it in your own voice. [beat] Third, every reveal lands on a word. Three steps, and
one take.

[Step 2.2.]

And when a line comes out wrong, change a sentence and only that section re-renders.

## 3. Gradient descent — 0:40 to 1:20

[Deck scene 3. A loss bowl draws, a point steps down it, and the update rule appears.]

Now a short lesson. Here is gradient descent, at a high level. [beat] Start with a loss
surface. Think of it as a bowl. [beat] Pick a point anywhere on it. [beat] The gradient
says which way is uphill, so take a step the other way. [beat] The learning rate sets
the size of that step. [beat] Step again. [beat] And again, and each step is smaller as
the slope flattens out.

[beat] Pause and think: what happens if the learning rate is too large?

[pause 3]

You overshoot, and bounce from side to side. [beat] Small enough, and you settle at the
bottom.

## 4. Before and after

[Deck scene 4. A vague prompt appears word by word, and a flat reply fades in.]

The same idea works for teaching how to prompt. Here is a vague prompt. [beat] Is this
query okay? [beat] The reply is polite, and it is useless. It tells you what the query
does, which you already knew.

[Step 4.2. A better prompt, and a better reply.]

Now give the model a role, the stakes, and a shape for its answer. [beat] You are
reviewing a query that runs nightly on a two billion row orders table. List correctness
and performance problems as a numbered list, most severe first, with a one-line fix for
each. [beat] Same model, and now it finds the unindexed join, [beat] the filter that
defeats the index, [beat] and the columns nobody reads.

## 5. Close

[Deck scene 5. Three lines return, then the end card.]

That is the whole idea. [beat] Write the script. [beat] Narrate it in your own voice.
[beat] And every reveal lands on its word.

[Step 5.2. The end card.]

The script is the edit. [beat] Change a sentence, and only that section renders again.
[beat] Made with DeckTalk.
