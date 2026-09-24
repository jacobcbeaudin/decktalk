"""Caption cues: where a line breaks, where a cue ends, and which spelling the viewer reads."""

from __future__ import annotations

from decktalk.artifacts.words import Word
from decktalk.captions.layout import CAPTION_MAX_CHARS, CAPTION_MIN_SECONDS, caption_cues, display_words


def _spoken(text: str, start: float = 0.0, step: float = 0.4, gap_after: str | None = None) -> list[Word]:
    words: list[Word] = []
    t = start
    for w in text.split():
        words.append(Word(w, round(t, 3), round(t + 0.3, 3)))
        t += step
        if gap_after is not None and w == gap_after:
            t += 3.0
    return words


def _film(*words: tuple[str, float, float]) -> list[Word]:
    return [Word(w, start, end) for w, start, end in words]


def test_caption_cues_group_whole_sentences_and_keep_lines_short():
    text = (
        "Welcome. This is a narrated deck, cut to the word. Every visual you see lands on the word "
        "that names it, and nothing drifts. Short."
    )
    cues = caption_cues(_spoken(text))
    for cue in cues:
        assert 1 <= len(cue.lines) <= 2
        assert all(len(line) <= CAPTION_MAX_CHARS for line in cue.lines)
    # The one-word sentences join a neighbour, and a cue of two sentences breaks its line between them.
    assert [c.lines for c in cues] == [
        ("Welcome.", "This is a narrated deck, cut to the word."),
        ("Every visual you see lands on the word", "that names it, and nothing drifts. Short."),
    ]
    for a, b in zip(cues, cues[1:], strict=False):
        assert a.end <= b.start  # a cue never overlaps the next one
    assert cues[0].end == cues[1].start  # the tail stops at the next cue
    assert cues[-1].end == round(cues[-1].end, 3) == 10.1  # last word end 9.9 plus the 0.2 s tail


def test_caption_cues_do_not_strand_step_at_a_line_start():
    """The demo film's words. A break here would strand "step." at the start of a line after "for every"."""
    words = _film(
        ("For", 127.312, 127.463), ("a", 127.51, 127.533), ("big", 127.58, 127.742), ("model,", 127.8, 128.125),
        ("the", 128.148, 128.218), ("chips", 128.264, 128.532), ("do", 128.59, 128.706),
        ("billions", 128.822, 129.298), ("of", 129.344, 129.402), ("billions", 129.46, 129.89),
        ("of", 129.936, 130.006), ("small", 130.076, 130.378), ("calculations", 130.447, 131.271),
        ("for", 131.341, 131.469), ("every", 131.55, 131.806), ("step.", 131.875, 132.398),
    )  # fmt: skip
    cues = caption_cues(words)
    assert [(c.start, c.end, c.lines) for c in cues] == [
        (127.312, 128.822, ("For a big model, the chips do",)),
        (128.822, 132.598, ("billions of billions of small", "calculations for every step.")),
    ]


def test_caption_cues_do_not_strand_of_chips_on_a_cue_of_its_own():
    """The demo film's words. A break here would leave "of chips." as a cue of its own."""
    words = _film(
        ("The", 133.164, 133.268), ("large", 133.338, 133.593), ("language", 133.652, 134.035),
        ("models", 134.081, 134.395), ("behind", 134.464, 134.859), ("AI", 134.94, 135.254),
        ("chat", 135.3, 135.567), ("apps", 135.625, 135.857), ("split", 135.974, 136.322),
        ("the", 136.368, 136.438), ("work", 136.484, 136.705), ("across", 136.751, 137.088),
        ("thousands", 137.158, 137.622), ("of", 137.657, 137.715), ("chips.", 137.773, 138.504),
    )  # fmt: skip
    cues = caption_cues(words)
    assert [(c.start, c.end, c.lines) for c in cues] == [
        (133.164, 135.974, ("The large language models", "behind AI chat apps")),
        (135.974, 138.704, ("split the work across thousands of chips.",)),
    ]


def test_caption_cues_edge_cases():
    # A one-word sentence joins a neighbour, and alone in its section it is a cue of its own.
    cues = caption_cues(_spoken("Update your video the way you update a doc. DeckTalk. Open source, and free."))
    assert [c.lines for c in cues] == [
        ("Update your video the way", "you update a doc."),
        ("DeckTalk. Open source, and free.",),
    ]
    assert [c.lines for c in caption_cues(_spoken("Hello."))] == [("Hello.",)]

    # A sentence too long for two lines splits at the clause mark, then away from function words.
    cues = caption_cues(
        _spoken("In a big model, testing knobs one at a time would take billions of tries for every step.")
    )
    assert [c.lines for c in cues] == [
        ("In a big model,", "testing knobs one at a time"),
        ("would take billions", "of tries for every step."),
    ]

    # Words with no punctuation at all still wrap into lines that fit, with no one-word line or cue.
    cues = caption_cues(_spoken(" ".join(["knob"] * 40)))
    assert all(len(line) <= CAPTION_MAX_CHARS and " " in line for c in cues for line in c.lines)
    assert sum(len(line.split()) for c in cues for line in c.lines) == 40

    # A silence of 1.0 s ends the cue, even inside a sentence. A shorter one does not.
    joined = _film(("It", 0.0, 0.3), ("guesses,", 0.4, 0.7), ("and", 1.6, 1.8), ("waits.", 1.9, 2.3))
    assert [c.text for c in caption_cues(joined)] == ["It guesses, and waits."]
    split = _film(("It", 0.0, 0.3), ("guesses,", 0.4, 0.7), ("and", 1.7, 1.9), ("waits.", 2.0, 2.4))
    assert [(c.start, c.end, c.text) for c in caption_cues(split)] == [
        (0.0, 1.0, "It guesses,"),
        (1.7, 2.7, "and waits."),
    ]

    # No cue is on screen for under a second, unless the next cue begins before that.
    short = _film(("Yes.", 0.0, 0.2), ("No.", 3.0, 3.2))
    assert [(c.start, c.end) for c in caption_cues(short)] == [(0.0, 1.0), (3.0, 4.0)]
    crowded = _film(("Yes.", 0.0, 0.2), ("No.", 0.5, 0.7))
    assert caption_cues(crowded)[0].end - caption_cues(crowded)[0].start >= CAPTION_MIN_SECONDS


def test_caption_cues_split_on_a_long_pause_and_never_cross_sections():
    cues = caption_cues(_spoken("one two three four five six", gap_after="three"))
    assert len(cues) == 2 and cues[0].text == "one two three" and cues[1].text == "four five six"
    assert caption_cues([]) == []


def test_display_words_restores_punctuation_and_case():
    words = [Word("welcome", 0, 1), Word("this", 1, 2), Word("is", 2, 3), Word("two", 3, 4), Word("x", 4, 5)]
    text = "Welcome. This is two x."
    out = display_words(words, text)
    assert [w.word for w in out] == ["Welcome.", "This", "is", "two", "x."]
    assert out[0].start == 0 and out[-1].end == 5
    # An alignment that cannot be made returns the words untouched.
    assert [w.word for w in display_words(words, "completely different text here")] == [w.word for w in words]
