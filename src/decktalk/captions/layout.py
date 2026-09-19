"""Words become caption cues: where each cue starts and ends, and how its one or two lines break.

    build/out/<name>.srt   the cues as SubRip
    build/out/<name>.vtt   the same cues as WebVTT

A cue never spans a section boundary, so a caption is always the speech of one section. A cue stays
on screen for at least `CAPTION_MIN_SECONDS`, unless the next cue begins before that.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

from ..artifacts.words import Word

CAPTION_MAX_CHARS = 42  # The longest line a cue may carry, in characters. A cue has at most two lines.
CAPTION_MIN_SILENCE = 1.0  # A silence at least this long between two words always ends the cue.
CAPTION_TAIL = 0.2  # Seconds a cue lingers after its last word, unless the next cue begins first.
# The shortest a cue may stay on screen, unless the next cue needs the room. A caption held for less
# than a second cannot be read, which is what WCAG success criterion 1.2.2 asks captions to be.
CAPTION_MIN_SECONDS = 1.0
CAPTION_MAX_UNITS = 8  # The most sentences one cue is ever considered to hold.

# Short words a line should not end on and a split should not touch, so "billions of billions" stays whole.
_FUNCTION_WORDS = frozenset("a an the of to in on at for and or but by with as is it its my your".split())
_SENTENCE_END_RE = re.compile(r"[.?!][\"'”’)]*$")
_CLAUSE_END_RE = re.compile(r"[,;:—–-]$")


@dataclass(frozen=True)
class CaptionCue:
    start: float
    end: float
    lines: tuple[str, ...]

    @property
    def text(self) -> str:
        return "\n".join(self.lines)


def _ends_sentence(token: str) -> bool:
    return bool(_SENTENCE_END_RE.search(token))


def _ends_clause(token: str) -> bool:
    return bool(_CLAUSE_END_RE.search(token))


def _is_function_word(token: str) -> bool:
    return re.sub(r"[^\w']", "", token.lower().replace("’", "'")) in _FUNCTION_WORDS


def _join(words: list[Word]) -> str:
    return " ".join(w.word for w in words)


def _wrap(words: list[Word], max_chars: int, *, several: bool = False) -> tuple[list[str], bool] | None:
    """One line if the words fit, else the best break into two lines, or None when no break fits.

    Returns the lines and whether the break falls after a sentence end. The break goes after a
    sentence end, else after a clause mark, while the shorter line is at least a third of the
    longer. A cue of `several` sentences takes a sentence-end break even when lopsided. Otherwise
    the lines are balanced, and a line that ends on a function word costs the most. No line is a
    single word unless that word is a whole sentence.
    """
    whole = _join(words)
    if len(whole) <= max_chars:
        return [whole], True
    best: tuple[tuple[int, int], list[str], bool] | None = None
    for i in range(1, len(words)):
        first, second = words[:i], words[i:]
        la, lb = len(_join(first)), len(_join(second))
        if la > max_chars or lb > max_chars:
            continue
        if any(len(part) == 1 and not _ends_sentence(part[0].word) for part in (first, second)):
            continue
        last, nxt = first[-1].word, second[0].word
        even = min(la, lb) >= max(la, lb) / 3
        if _ends_sentence(last) and (even or several):
            rank = 0
        elif _ends_clause(last) and even:
            rank = 1
        else:
            rank = 2
        score = abs(la - lb) + 16 * _is_function_word(last) + 4 * _is_function_word(nxt)
        if best is None or (rank, score) < best[0]:
            best = ((rank, score), [_join(first), _join(second)], _ends_sentence(last))
    return (best[1], best[2]) if best else None


def _split_sentence(words: list[Word], max_chars: int) -> list[list[Word]]:
    """Split a sentence too long for two lines into parts that each fit in two lines.

    The split goes at the clause mark nearest the middle when both parts keep a third of the
    characters, else at the word boundary nearest the middle with no function word on either
    side. Each part keeps at least three words. A sentence with no such boundary splits at the
    boundary nearest the middle, so no line runs past max_chars.
    """
    if len(words) < 2 or _wrap(words, max_chars) is not None:
        return [words]
    total = len(_join(words))
    ranked: list[tuple[int, float, int]] = []
    for i in range(3, len(words) - 2):
        cut = len(_join(words[:i]))
        token, nxt = words[i - 1].word, words[i].word
        if _ends_clause(token) and min(cut, total - cut) >= total / 3:
            ranked.append((0, abs(cut - total / 2), i))
        elif not _ends_clause(token) and not _is_function_word(token) and not _is_function_word(nxt):
            ranked.append((1, abs(cut - total / 2), i))
    if not ranked:  # no boundary follows the rules, so take the middle rather than overflow a line
        ranked = [(2, abs(len(_join(words[:i])) - total / 2), i) for i in range(1, len(words))]
    i = min(ranked)[2]
    return _split_sentence(words[:i], max_chars) + _split_sentence(words[i:], max_chars)


def display_words(words: list[Word], text: str) -> list[Word]:
    """The same words carrying the script's own spelling, so captions keep punctuation and case.

    Word lists come back from the voice with punctuation stripped. The script text is
    walked in step with them, matching each word to the next token whose letters agree,
    and a token that matches supplies the display form. When the two cannot be aligned,
    the words are returned as they are.
    """
    tokens = text.split()
    out: list[Word] = []
    j = 0
    for w in words:
        key = _letters(w.word)
        k = j
        while k < len(tokens) and k < j + 3 and _letters(tokens[k]) != key:
            k += 1
        if k < len(tokens) and k < j + 3:
            out.append(Word(tokens[k], w.start, w.end))
            j = k + 1
        else:
            return list(words)
    return out


def _letters(token: str) -> str:
    return "".join(ch for ch in token.lower() if ch.isalnum())


def caption_cues(
    words: list[Word],
    *,
    max_chars: int = CAPTION_MAX_CHARS,
    min_silence: float = CAPTION_MIN_SILENCE,
) -> list[CaptionCue]:
    """Cues for one section's words, so a cue never spans a section boundary.

    Every word keeps its start time. A cue ends before any silence of min_silence or more.
    Between those breaks, whole sentences are grouped into cues at the lowest cost: each cue
    costs 1000, a cue of several sentences whose line break cannot fall at a sentence end
    costs 1200 more, a two-word cue 400 more, and a one-word cue 5000 more. A sentence too long
    for two lines is split first (see `_split_sentence`), and each part is a cue of its own.
    Each cue has one or two lines of at most max_chars (see `_wrap`).
    """
    if not words:
        return []
    runs: list[list[Word]] = [[]]
    for w in words:
        if runs[-1] and w.start - runs[-1][-1].end >= min_silence:
            runs.append([])
        runs[-1].append(w)

    groups: list[list[Word]] = []
    for run in runs:
        sentences: list[list[Word]] = [[]]
        for w in run:
            sentences[-1].append(w)
            if _ends_sentence(w.word):
                sentences.append([])
        units = [part for s in sentences if s for part in _split_sentence(s, max_chars)]
        groups += _group_units(units, max_chars)

    cues: list[CaptionCue] = []
    for i, group in enumerate(groups):
        wrapped = _wrap(group, max_chars, several=True)
        lines = wrapped[0] if wrapped else [_join(group)]
        end = max(group[-1].end + CAPTION_TAIL, group[0].start + CAPTION_MIN_SECONDS)
        if i + 1 < len(groups):  # a cue lingers briefly after its last word, but never into the next cue
            end = min(end, groups[i + 1][0].start)
        cues.append(CaptionCue(round(group[0].start, 3), round(max(end, group[-1].end), 3), tuple(lines)))
    return cues


def _group_units(units: list[list[Word]], max_chars: int) -> list[list[Word]]:
    """Group consecutive units (whole sentences, or parts of a split one) into cues at the lowest cost."""

    def cost(chunk: list[list[Word]]) -> int | None:
        several = len(chunk) > 1
        if several and not all(_ends_sentence(u[-1].word) for u in chunk):
            return None  # only whole sentences share a cue
        flat = [w for u in chunk for w in u]
        wrapped = _wrap(flat, max_chars, several=several)
        if wrapped is None:
            return None if several else 6000  # a unit that cannot wrap still gets a cue of its own
        c = 1000 + (1200 if several and not wrapped[1] else 0)
        return c + (5000 if len(flat) == 1 else 400 if len(flat) == 2 else 0)

    n = len(units)
    best = [0.0] + [math.inf] * n
    back = [0] * (n + 1)
    for j in range(1, n + 1):
        for i in range(max(0, j - CAPTION_MAX_UNITS), j):
            c = cost(units[i:j])
            if c is not None and best[i] + c < best[j]:
                best[j], back[j] = best[i] + c, i
    groups: list[list[Word]] = []
    j = n
    while j > 0:
        groups.append([w for u in units[back[j] : j] for w in u])
        j = back[j]
    return groups[::-1]
