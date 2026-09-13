# __TITLE__ — narration script

Each "## N." section is one cut of the video. Bracketed directions are not spoken: `[beat]`
is a short pause, `[pause 4]` is a four second pause, and anything else in brackets is a
note to yourself. Write numbers and symbols the way you want them said ("forty one", not
"41"), because the cue phrases in `cues.json` match spoken words, and a `data-sync` line on a
slide must be word for word what the voice says. To open with a clip of your own, follow the
comment block in `decktalk.toml` and add a `## 0. On camera` heading above section 1.

---

## 1. Open

[Deck scene 1. A line of script lights up word by word while the curve draws and the number lands. Then each word's start time rises under it.]

The curve rises, then the number lands. [beat] You just watched DeckTalk. [beat] That
sentence is a line of markdown. [beat] My cloned voice read it, and every reveal landed on its
word. [beat] Here is the project behind it.

## 2. Three files and a voice

[Deck scene 2. Four dim panels light in turn, and then the shared id lights in every one.]

You write three files, and the voice returns a fourth. [beat] The script says what the voice
says. [beat] The voice comes back with a time for every word. [beat] A cue names the phrase a
visual waits for. [beat] And the slide names the cue. [beat] One id ties them together, so the
picture can only land on its word. [beat] Now watch it teach.

## 3. Gradient descent

[Deck scene 3. A loss surface stands over the plane of two weights from the first frame. A preview ball hops to the bottom and leaves a faint path. Arrows on the floor show the gradient and its negative. The ball steps down in shrinking steps, the figure moves aside for the update rule, three more learning rates show what goes wrong, and the ball settles at the minimum.]

Training a model means tuning its weights to shrink its error, the loss. [beat] The loss is a
function of many variables, one per weight. [beat] Keep two, theta one and theta two, and the
loss above them is a surface. For this lesson it is a bowl. [beat] Gradient descent heads for
a local minimum by stepping downhill, again and again. [beat] Pick a point, theta, and its
height is the loss. [beat] The gradient points uphill, in the plane of the weights, so follow
minus the gradient. [beat] Take a step, and the learning rate, eta, scales its length. [beat]
The steps shrink because the slope does. [beat] That is the rule. The next theta [beat] is this
theta, minus eta [beat] times the gradient here. [beat] Too large, and you zigzag. [beat]
Larger still, and you fly out. [beat] Too small, and you crawl. [beat] The best eta is big
enough not to crawl, and small enough not to bounce. [beat] Now change one sentence.

## 4. The edit

[Deck scene 4. One word is struck and retyped, and the log of the rebuild and the verify row fill in line by line.]

The bowl becomes a valley. [beat] Build again. [beat] Sections one and two, unchanged. [beat]
Three is voiced again. [beat] For this lesson it is a valley. [beat] Four and five, unchanged.
[beat] Then decktalk verify measures every reveal against its word.

## 5. Close

[Deck scene 5. The end card.]

The script is the edit. [beat] DeckTalk. Open source, narrated presentations, cut to the word.
[beat] Make your own at decktalk dot app.
