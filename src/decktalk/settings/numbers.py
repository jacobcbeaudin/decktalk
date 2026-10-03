"""Every number that is deliberately not a setting, published with its formula and its reason.

A derived number is written as its expression, read off the settings it depends on, and a constant
is a fact about a codec, a standard or a tool. `NUMBERS` is what the reference page, `config
explain` and a declared relation read, so a reader who cannot find a setting learns the number is
deliberately not one.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from decktalk.findings import Code
from decktalk.page import CAPTURE_FPS, MEASURABLE_SPAN_SECONDS
from decktalk.settings import BLOCK_PX, CLICK_LEVEL_DBFS, GUARD_FRAMES, REPORT_FRAME_GAP_MS, Settings
from decktalk.tomlmap import Nature

PROBE_SCALE = 4
"""Calibration: how many times smaller than the film every frame is before two frames are compared."""

PROBE_SENTENCE = "Derived: every share is measured over the frame scaled down by PROBE_SCALE, at every frame size."
"""Why the probe is the size it is, said once for its width and its height."""

BLOCK_SENTENCE = (
    "Derived: one pixel per H.264 transform block, so the block average cancels the encoder's ringing at every "
    "frame size rather than only at 1080p."
)
"""Why the block-averaged copy is the size it is, said once for its width and its height."""


@dataclass(frozen=True)
class Number:
    """One number that is deliberately not a setting, published with its formula and its reason.

    A derived number is written as its expression and never as its value, so a reader who looks for
    it among the settings meets the arithmetic instead of nothing. A constant is a
    fact about a codec, a standard or a tool DeckTalk drives, and the sentence says which.
    """

    id: str
    formula: str
    reads: tuple[str, ...]
    unit: str | None
    nature: Nature
    sentence: str
    at: Callable[[Settings], float]
    decides: tuple[Code, ...] = ()

    @property
    def kind(self) -> str:
        """Whether this number is computed from the keys or fixed, which is what `x-numbers` publishes."""
        return "derived" if self.nature is Nature.DERIVED else "constant"

    @classmethod
    def fixed(cls, name: str, value: float, unit: str, nature: Nature, sentence: str) -> Number:
        """A number that reads no key, whose formula is its own value and whose value never moves."""
        return cls(name, str(value), (), unit, nature, sentence, lambda _settings: value)


def probe_width(settings: Settings) -> int:
    """The width every frame is scaled to before a comparison."""
    return settings.video.width // PROBE_SCALE


def probe_height(settings: Settings) -> int:
    """The height every frame is scaled to before a comparison."""
    return settings.video.height // PROBE_SCALE


def block_width(settings: Settings) -> int:
    """The width of the block-averaged copy of a frame, one pixel per H.264 transform block."""
    return settings.video.width // BLOCK_PX


def block_height(settings: Settings) -> int:
    """The height of the block-averaged copy of a frame, one pixel per H.264 transform block."""
    return settings.video.height // BLOCK_PX


def reference_lead_seconds(settings: Settings) -> float:
    """How far before a cue the reference frame is read, which is the allowance plus the grid guard."""
    return (
        settings.verify.cue_offset_max_ms / 1000
        + GUARD_FRAMES / CAPTURE_FPS
        + settings.verify.reference_lead_extra_ms / 1000
    )


NUMBERS: tuple[Number, ...] = (
    Number(
        id="verify.probe_width",
        formula="video.width // PROBE_SCALE",
        reads=("video.width", "PROBE_SCALE"),
        unit="pixels",
        nature=Nature.DERIVED,
        sentence=PROBE_SENTENCE,
        at=probe_width,
    ),
    Number(
        id="verify.probe_height",
        formula="video.height // PROBE_SCALE",
        reads=("video.height", "PROBE_SCALE"),
        unit="pixels",
        nature=Nature.DERIVED,
        sentence=PROBE_SENTENCE,
        at=probe_height,
    ),
    Number(
        id="verify.block_width",
        formula="video.width // BLOCK_PX",
        reads=("video.width", "BLOCK_PX"),
        unit="pixels",
        nature=Nature.DERIVED,
        sentence=BLOCK_SENTENCE,
        at=block_width,
    ),
    Number(
        id="verify.block_height",
        formula="video.height // BLOCK_PX",
        reads=("video.height", "BLOCK_PX"),
        unit="pixels",
        nature=Nature.DERIVED,
        sentence=BLOCK_SENTENCE,
        at=block_height,
    ),
    Number(
        id="verify.reference_lead_seconds",
        formula="verify.cue_offset_max_ms / 1000 + GUARD_FRAMES / CAPTURE_FPS + verify.reference_lead_extra_ms / 1000",
        reads=("verify.cue_offset_max_ms", "GUARD_FRAMES", "CAPTURE_FPS", "verify.reference_lead_extra_ms"),
        unit="seconds",
        nature=Nature.DERIVED,
        sentence=(
            "Derived: the reference frame has to sit outside the window the offset limit allows, or the "
            "check compares a cue against a frame the same cue had already changed."
        ),
        at=reference_lead_seconds,
        decides=(Code.CUE_OFF, Code.CUE_NO_ONSET),
    ),
    Number.fixed(
        "CAPTURE_FPS",
        CAPTURE_FPS,
        "frames per second",
        Nature.TRUTH,
        "Truth: the rate the recorder captures at, which DeckTalk cannot set and so never asks for.",
    ),
    Number.fixed(
        "PROBE_SCALE",
        PROBE_SCALE,
        "times",
        Nature.CALIBRATION,
        (
            "Calibration: a frame a quarter the film's size keeps every share a cue can move while comparing "
            "a sixteenth of the pixels."
        ),
    ),
    Number.fixed(
        "BLOCK_PX",
        BLOCK_PX,
        "pixels",
        Nature.TRUTH,
        "Truth: the H.264 transform block the block-averaged copy of a frame cancels ringing over.",
    ),
    Number.fixed(
        "GUARD_FRAMES",
        GUARD_FRAMES,
        "frames",
        Nature.TRUTH,
        "Truth: half a frame of rounding guard on each side of the window the offset limit allows.",
    ),
    Number.fixed(
        "REPORT_FRAME_GAP_MS",
        REPORT_FRAME_GAP_MS,
        "milliseconds",
        Nature.CALIBRATION,
        "Calibration: the runtime's own reporting floor, under which a frame gap cannot be seen.",
    ),
    Number.fixed(
        "CLICK_LEVEL_DBFS",
        CLICK_LEVEL_DBFS,
        "dBFS",
        Nature.TRUTH,
        "Truth: the level DeckTalk generates its own click at, which every click floor sits under.",
    ),
    Number.fixed(
        "MEASURABLE_SPAN_SECONDS",
        MEASURABLE_SPAN_SECONDS,
        "seconds",
        Nature.TRUTH,
        (
            "Truth: the span at which an effect covers its own cue, which is the ceiling every declared "
            "span, every scaled span and every staggered total is held under."
        ),
    ),
)
"""Every number that is not a setting, with the formula or the fact that fixes it."""

NUMBERS_BY_ID: dict[str, Number] = {number.id: number for number in NUMBERS}
