"""The verdict strings every check prints, and which of them are certain.

A certain verdict names something that is wrong for sure, such as a page that threw or a
recording that stopped early, so the read-only commands exit 1 on it. An uncertain verdict
ends in a question mark and names something that is probably wrong, such as a dark frame
that may be a dark slide, so those commands exit 1 on it only with `--strict`. A shortfall
against `min_seconds` in beats, a loudness miss in assemble, and a `warning` row in doctor
(KaTeX not cached, so pages load it from a CDN) are uncertain too, although they have no
verdict string of their own. The passing verdicts are not findings at all, and
neither are the warnings the runtime records in the page.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

# Certain verdicts.
PAGE_ERROR = "PAGE ERROR"
STALLED = "STALLED"
TRUNCATED = "TRUNCATED"
NO_COVER = "NO COVER"
BLACK = "BLACK"
SPEECH_AT_CUT = "SPEECH AT CUT"
OFF_CUE = "OFF CUE"
NO_CHANGE = "NO CHANGE"
UNRESOLVED = "UNRESOLVED"
UNKNOWN = "UNKNOWN"
MISSING = "MISSING"

# Uncertain verdicts.
BLACK_UNSURE = "BLACK?"
KATEX_UNSURE = "KATEX?"

# Passing verdicts, which are never findings.
CHANGED = "changed"
QUIET = "quiet"
OK = "ok"
SKIPPED = "skipped"

CERTAIN: frozenset[str] = frozenset(
    {PAGE_ERROR, STALLED, TRUNCATED, NO_COVER, BLACK, SPEECH_AT_CUT, OFF_CUE, NO_CHANGE, UNRESOLVED, UNKNOWN, MISSING}
)
UNCERTAIN: frozenset[str] = frozenset({BLACK_UNSURE, KATEX_UNSURE})
PASSING: frozenset[str] = frozenset({CHANGED, QUIET, OK, SKIPPED})

# The longest names come first, so that "BLACK?" is matched before "BLACK".
_KNOWN = sorted(CERTAIN | UNCERTAIN | PASSING, key=len, reverse=True)


def is_certain(verdict: str) -> bool:
    """True when the verdict is certain. A detail after the name, as in "STALLED 1840ms", is ignored."""
    return any(verdict == name or verdict.startswith(name + " ") for name in CERTAIN)


def split(text: str) -> list[str]:
    """The verdict names in a space-joined verdict string such as "BLACK? STALLED 1840ms".

    A detail that follows a name, which is any word containing a digit, is dropped. A word
    that is neither a known name nor a detail is returned as a verdict of its own, so a new
    verdict is counted as uncertain rather than ignored.
    """
    out: list[str] = []
    rest = text.strip()
    while rest:
        name = next((n for n in _KNOWN if rest == n or rest.startswith(n + " ")), None)
        if name is None:
            word = rest.split(" ", 1)[0]
            if not any(ch.isdigit() for ch in word):
                out.append(word)
            name = word
        else:
            out.append(name)
        rest = rest[len(name) :].strip()
    return out


@dataclass(frozen=True)
class Findings:
    """How many certain and uncertain findings a command produced."""

    certain: int = 0
    uncertain: int = 0

    def __add__(self, other: Findings) -> Findings:
        return Findings(self.certain + other.certain, self.uncertain + other.uncertain)

    def to_dict(self) -> dict[str, int]:
        return {"certain": self.certain, "uncertain": self.uncertain}


def count(verdicts: Iterable[str]) -> Findings:
    """Tally verdict strings, each of which may hold several space-joined names."""
    certain = uncertain = 0
    for text in verdicts:
        for name in split(text):
            if is_certain(name):
                certain += 1
            elif name not in PASSING:
                uncertain += 1
    return Findings(certain, uncertain)
