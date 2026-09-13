# __TITLE__ — narration script

Each "## N." section is one cut of the video. Bracketed directions are not spoken: `[beat]`
is a short pause, `[pause 4]` is a four second pause, and anything else in brackets is a
note to yourself. Write numbers and symbols the way you want them said ("forty one", not
"41"), because the cue phrases in `cues.json` match spoken words, and a `data-sync` line on a
slide must be word for word what the voice says. To open with a clip of your own, follow the
comment block in `decktalk.toml` and add a `## 0. On camera` heading above section 1.

---

## 1. Open

[Deck scene 1. A line of script lights up word by word while the curve draws and the number lands.]

The curve rises, then the number lands. [beat] You just watched DeckTalk. [beat] That
sentence is a line of markdown. My cloned voice read it, and every reveal landed on its
word, because the audio came back with a timestamp for every word. [beat] Here is how one
file does that.

## 2. Four files

[Deck scene 2. Four panels rise in turn, then the shared phrase lights in every one.]

A project is four files that refer to each other by one number. [beat] The script says
what the voice says. [beat] The voice comes back with a time for every word. [beat] A cue
names the phrase a visual waits for. [beat] And a slide names the cue. [beat] One number
ties them together, so the picture can only ever land on the word. [beat] That is the tool.
Now a lesson where every picture waits for its word.

## 3. Gradient descent — 0:40 to 1:00

[Deck scene 3. A loss surface fades up, a ball steps down it, and the update rule builds.]

Start with a loss surface. For this lesson it is a bowl. [beat] Pick a point, theta.
[beat] The gradient points uphill, so follow minus the gradient. [beat] Take a step, and
the learning rate, eta, scales its length. [beat] Again, [beat] and again. The steps shrink
because the slope does. [beat] That is the rule. The next theta [beat] is this theta, minus
eta [beat] times the gradient here.

[beat] Pause and think: what if eta is too large?

[pause 4]

You overshoot and bounce across the bowl. [beat] Small enough, and you settle at the
bottom, for this surface. [beat] Now change one sentence of that lesson.

## 4. The edit

[Deck scene 4. One phrase is struck and retyped, and the build table fills in row by row.]

Change one sentence. [beat] Say the bowl is a valley. [beat] Build again. [beat] Section
one is cached. [beat] Two, cached. [beat] Three is synthesized, forty one seconds of new
narration. [beat] Four and five, cached. [beat] The video is rebuilt, and verify measures
the reveal on the word learning rate: ten milliseconds.

## 5. Close

[Deck scene 5. The end card.]

The script is the edit. [beat] Edit the words. The video follows. [beat] Made with DeckTalk.
[beat] The docs are at docs dot decktalk dot app.
