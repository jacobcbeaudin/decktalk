"""Every knob DeckTalk publishes, with the range that is safe to turn it through.

One dataclass per table, composed into `Settings`. Each field is declared with `tune()`, which
carries everything any surface says about that key: what it changes, what its default is, the
range the loader enforces, the wider range its type would admit, its true unit, which findings it
moves, which file it belongs in, where its value is expected to come from, what a value at the
edge risks and which other key or published number it relates to. Nothing about a key is written
anywhere else, so the JSON Schema, the reference page, `config explain` and a finding that names
a knob are four renderings of one row.

Five layers set a key, lowest to highest: the default here, the same table in the per-machine
file, the same table in the project's `decktalk.toml`, the environment variable named
`DECKTALK_<TABLE>_<KEY>`, and a `--set table.key=value` given for one run. `load()` returns both
the resolved tree and the record of which layer set each key, because a caller that is told a
value and not its layer cannot tell a deliberate choice from a default.

Two rules keep the published range honest. The published range is the safe range: a bound is here
because a value past it deletes a check, corrupts the evidence a later stage measures or breaks a
tool, and the loader refuses outside it. A number that is not a knob is published too: every
derived expression and every named constant is in `NUMBERS` with its formula and its reason, so a
reader who cannot find a knob learns the number is deliberately not one.
"""

from __future__ import annotations

import logging
import os
import sys
import tomllib
from collections.abc import Callable, Iterator, Mapping, MutableMapping
from dataclasses import Field as DataField
from dataclasses import dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any, cast

import tomlkit
from pydantic import BaseModel, Field, JsonValue

from .errors import InputError
from .findings import MODEL, Code, Location, ProjectPath
from .locate import locate, refused_line
from .page import CAPTURE_FPS, MEASURABLE_SPAN_SECONDS
from .results import Layer, LayerValue, Scope
from .tomlmap import (
    A_LUMA,
    A_PERCENT,
    A_SHARE,
    Bounds,
    Key,
    Nature,
    Source,
    did_you_mean,
    env_names,
    from_mapping,
    read_value,
    registry,
    tune,
    unknown_key_message,
    unknown_key_warnings,
)

log = logging.getLogger(__name__)

PROJECT_FILE = "decktalk.toml"
ENV_PREFIX = "decktalk"

X264_PRESETS = ("ultrafast", "superfast", "veryfast", "faster", "fast", "medium", "slow", "slower", "veryslow")
"""Truth: the words x264 accepts, so a wrong one fails at load rather than inside a render."""

COLOR_SCHEMES = ("light", "dark", "no-preference")
"""Truth: the values Chromium reports for `prefers-color-scheme`."""

BLOCK_PX = 8
"""Truth: the H.264 transform block the block-averaged copy of a frame cancels ringing over."""

GUARD_FRAMES = 1.5
"""Truth: half a frame on each side of a two-frame window, which is the grid rounding guard."""

REPORT_FRAME_GAP_MS = 100
"""Calibration: the runtime's own reporting floor, which no frame-gap limit may go under."""

CLICK_LEVEL_DBFS = -24.0
"""Truth: the level DeckTalk generates its own click at, which every click floor sits under."""


@dataclass(frozen=True)
class VideoConfig:
    """These keys set the frame size and the encoding of every recording and of the final mp4."""

    width: int = tune(
        1920,
        "Frame width in pixels, for every recording and the final mp4.",
        unit="pixels",
        bounds=Bounds(ge=320, le=7680),
        see_also=("verify.probe_width", "verify.block_width"),
    )
    height: int = tune(
        1080,
        "Frame height in pixels, for every recording and the final mp4.",
        unit="pixels",
        bounds=Bounds(ge=240, le=4320),
        see_also=("verify.probe_height", "verify.block_height"),
    )
    output_fps: int = tune(
        25,
        "Frame rate of the final mp4. The recorder's own rate is measured and is not a knob.",
        unit="frames per second",
        bounds=Bounds(enum=(25, 30, 50, 60)),
        requires="video.output_fps >= CAPTURE_FPS",
        hazard=(
            "Encoding below the rate the recorder captured at drops presented frames, which both loses "
            "motion a viewer saw and corrupts the frames verify measures its own verdicts from."
        ),
        see_also=("CAPTURE_FPS",),
    )
    preset: str = tune(
        "medium",
        "x264 preset. Use veryfast for drafts.",
        bounds=Bounds(enum=X264_PRESETS),
    )
    crf: int = tune(
        18,
        "x264 quality. A lower value gives higher quality and a larger file.",
        bounds=Bounds(ge=0, le=32),
        typed=Bounds(ge=0, le=51),
        hazard=(
            "Above about 32 the encoder's own ringing is larger than a small reveal, so a cue that played "
            "reads as no change and the check that would have caught it passes instead."
        ),
        decides=(Code.CUE_NO_CHANGE, Code.CUE_THIN_CHANGE, Code.PAGE_THIN_DRAW),
    )
    audio_bitrate: str = tune(
        "192k",
        "AAC bitrate of the final mp4.",
        bounds=Bounds(enum=("96k", "128k", "160k", "192k", "256k", "320k")),
    )
    sample_rate: int = tune(
        48000,
        "Audio sample rate of the final mp4. Every audio input is resampled to it once.",
        unit="hertz",
        bounds=Bounds(enum=(44100, 48000)),
        nature=Nature.APPARATUS,
    )
    channels: int = tune(
        2,
        "Audio channels of the final mp4.",
        bounds=Bounds(enum=(1, 2)),
    )
    slate_color: str = tune(
        "0x0e1116",
        "Color of the plain frame that plays when a slate image cannot be rendered.",
    )


@dataclass(frozen=True)
class NarrationConfig:
    """These keys govern how the script is turned into audio."""

    model: str = tune("eleven_multilingual_v2", "Speech model. `[voice] model` and `narrate --model` override it.")
    output_format: str = tune(
        "mp3_44100_128", "Audio format that the speech provider returns. It is part of the narration cache key."
    )
    mp3_bitrate: str = tune(
        "128k",
        "Bitrate of the mp3 files that DeckTalk writes, which are the click tracks and the joined narration.",
        bounds=Bounds(enum=("64k", "96k", "128k", "160k", "192k", "256k")),
    )
    words_per_minute: int = tune(
        140,
        "Pacing of the estimated length in the narrate table.",
        unit="words per minute",
        bounds=Bounds(ge=60, le=300),
    )
    silent_words_per_minute: int = tune(
        150,
        "Pacing of the click track in a build without voice.",
        unit="words per minute",
        bounds=Bounds(ge=60, le=300),
    )
    lead_seconds: float = tune(
        0.5,
        "Silence before the first word of every spoken section, so the picture changes before the voice "
        "speaks. A section's own `lead_seconds` replaces it.",
        unit="seconds",
        bounds=Bounds(ge=0, le=5),
    )
    silent_beat_seconds: float = tune(
        0.7,
        "Seconds each beat adds to a section's length in a build without voice.",
        unit="seconds",
        bounds=Bounds(ge=0, le=10),
    )
    tail_min_seconds: float = tune(
        0.7,
        "Silence after the last word of every spoken section, so a cut never falls on speech. The take is "
        "placed so that exactly this much follows its last sound. A section's own `tail_seconds` replaces it.",
        unit="seconds",
        bounds=Bounds(ge=0, le=10),
        decides=(Code.CUT_SPEECH,),
    )
    cache_dir: str = tune(
        "",
        "Directory that holds the take files and their words files, each named by its content hash. It is "
        "empty for `build/narrate/` inside the project, and a path here keeps the voiced takes when `build/` "
        "is deleted and lets many projects share one cache.",
        unit="path",
        scope=Scope.MACHINE,
        nature=Nature.APPARATUS,
        see_also=("tools.cache_dir",),
    )
    context_chars: int = tune(
        1500,
        "Characters of each neighbour section sent with a request, for continuous prosody.",
        unit="characters",
        bounds=Bounds(ge=0, le=5000),
    )
    timeout_seconds: int = tune(
        180,
        "Seconds before a speech request times out.",
        unit="seconds",
        bounds=Bounds(ge=10, le=1800),
        nature=Nature.APPARATUS,
    )
    sound_end_noise_dbfs: float = tune(
        -35.0,
        "Level under which the tail of a take counts as silence, which is where the take's sound ends.",
        unit="dBFS",
        bounds=Bounds(ge=-60, le=-25),
        decides=(Code.CUT_SPEECH,),
        hazard=(
            "Above about -25 dBFS the scan calls quiet speech silence, so the take is placed early and the "
            "cut that follows it lands on a word."
        ),
        see_also=("narration.sound_end_min_run_seconds", "verify.cut_max_dbfs"),
    )
    sound_end_min_run_seconds: float = tune(
        0.05,
        "Shortest run under the noise level that counts as the end of a take's sound.",
        unit="seconds",
        bounds=Bounds(ge=0.02, le=0.5),
        decides=(Code.CUT_SPEECH,),
        see_also=("narration.sound_end_noise_dbfs",),
    )


@dataclass(frozen=True)
class RecordConfig:
    """These keys tune the headless Chromium recording and how narration t=0 is found in it."""

    settle_seconds: float = tune(
        0.5,
        "Shortest wait after the page is ready and before narration t=0.",
        unit="seconds",
        bounds=Bounds(ge=0, le=30),
    )
    min_cover_seconds: float = tune(
        1.5,
        "Shortest time from the start of the recorder to narration t=0.",
        unit="seconds",
        bounds=Bounds(ge=0, le=30),
    )
    browser_path: str = tune(
        "",
        "Chromium executable that `record` drives. It is empty for the build DeckTalk fetches itself, and a "
        "path here is what a managed machine sets: a named executable that will not launch is never a reason "
        "to download another.",
        unit="path",
        scope=Scope.MACHINE,
        nature=Nature.APPARATUS,
    )
    color_scheme: str = tune(
        "light",
        "Color scheme that Chromium reports to the page.",
        bounds=Bounds(enum=COLOR_SCHEMES),
    )
    retries: int = tune(
        2,
        "How many more times `record` records a section whose frames stalled.",
        bounds=Bounds(ge=0, le=10),
        see_also=("record.frame_gap_max_ms",),
    )
    screenshot_settle_seconds: float = tune(
        0.4,
        "Seconds that `storyboard` waits before each slide panel.",
        unit="seconds",
        bounds=Bounds(ge=0, le=10),
    )
    cover_scan_seconds: float = tune(
        4.0,
        "Seconds at the start of each recording that `record` scans for the magenta cover.",
        unit="seconds",
        bounds=Bounds(ge=0, le=30),
    )
    fallback_first_paint_seconds: float = tune(
        1.1,
        "Guessed first paint when `record` finds no cover and no painted frame. `record` adds `settle_seconds` to it.",
        unit="seconds",
        bounds=Bounds(ge=0, le=10),
    )
    cover_luma_min: float = tune(
        70.0,
        "A cover frame has an average luma above this.",
        unit="luma",
        bounds=Bounds(ge=40, le=120),
        typed=A_LUMA,
        nature=Nature.APPARATUS,
    )
    cover_luma_max: float = tune(
        140.0,
        "A cover frame has an average luma below this.",
        unit="luma",
        bounds=Bounds(ge=80, le=200),
        typed=A_LUMA,
        nature=Nature.APPARATUS,
    )
    cover_chroma_min: float = tune(
        165.0,
        "A cover frame has an average U and an average V above this.",
        unit="luma",
        bounds=Bounds(ge=128, le=255),
        typed=A_LUMA,
        nature=Nature.APPARATUS,
    )
    painted_peak_luma_min: float = tune(
        60.0,
        "A painted frame has a brightest luma above this.",
        unit="luma",
        bounds=Bounds(ge=1, le=200),
        typed=A_LUMA,
    )
    painted_mean_luma_max: float = tune(
        120.0,
        "A painted frame has an average luma below this, so a white flash does not count as the picture.",
        unit="luma",
        bounds=Bounds(ge=20, le=240),
        typed=A_LUMA,
    )
    truncated_slack_seconds: float = tune(
        0.5,
        "Allowed shortfall of a recording against its requested length.",
        unit="seconds",
        bounds=Bounds(ge=0, le=5),
        decides=(Code.PAGE_TRUNCATED,),
    )
    frame_gap_max_ms: int = tune(
        150,
        "Longest gap between two presented frames after narration t=0. A longer gap makes `record` try again.",
        unit="milliseconds",
        bounds=Bounds(ge=REPORT_FRAME_GAP_MS, le=2000),
        requires="record.frame_gap_max_ms >= REPORT_FRAME_GAP_MS",
        decides=(Code.PAGE_STALLED,),
        hazard=(
            "The runtime reports a gap no finer than its own reporting floor, so a limit under that floor "
            "makes every section stall and every retry spend the recording again."
        ),
        see_also=("REPORT_FRAME_GAP_MS", "record.retries"),
    )


@dataclass(frozen=True)
class AudioConfig:
    """These keys set the mix mechanics. Levels are project content and live in `[mix]`."""

    duck_ramp_seconds: float = tune(
        0.5,
        "Ramp of the music duck at each edge of a spoken span or a clip.",
        unit="seconds",
        bounds=Bounds(ge=0, le=5),
    )
    ambience_ramp_seconds: float = tune(
        1.0,
        "Ramp of the ambience bed at each edge of its span.",
        unit="seconds",
        bounds=Bounds(ge=0, le=10),
    )
    ambience_pad_seconds: float = tune(
        0.5,
        "Seconds that the ambience bed extends past each edge of its section.",
        unit="seconds",
        bounds=Bounds(ge=0, le=10),
    )
    marker_mute_ramp_seconds: float = tune(
        0.04,
        "Ramp into and out of a marker's mute.",
        unit="seconds",
        bounds=Bounds(ge=0, le=1),
    )
    marker_boost_ramp_seconds: float = tune(
        0.3,
        "Ramp into and out of a marker's swell.",
        unit="seconds",
        bounds=Bounds(ge=0, le=5),
    )


@dataclass(frozen=True)
class VerifyConfig:
    """These keys are the limits `verify` judges the assembled mp4 against."""

    after_dip_seconds: float = tune(
        0.2,
        "Seconds after a section start to the frame that the start check reads.",
        unit="seconds",
        bounds=Bounds(ge=0, le=2),
        decides=(Code.PAGE_BLACK,),
    )
    reference_lead_extra_ms: float = tune(
        0.0,
        "Extra lead added to the reference frame beyond the one the offset limit implies.",
        unit="milliseconds",
        bounds=Bounds(ge=0, le=80),
        hazard=(
            "The lead is already the offset limit plus the grid guard, so extra lead is only ever the "
            "escape hatch for a deck whose reference frame is still inside its own reveal. Above about 80 "
            "milliseconds the reference frame reaches back into the previous cue and the check compares two "
            "changes rather than one."
        ),
        see_also=("verify.cue_offset_max_ms", "verify.reference_lead_seconds"),
    )
    probe_delays_seconds: tuple[float, ...] = tune(
        (0.7, 1.5),
        "Seconds after the cue time for each probe. The later probe catches a slow reveal.",
        unit="seconds",
        bounds=Bounds(min_items=1, items=Bounds(gt=0, le=10)),
        decides=(Code.CUE_NO_CHANGE,),
    )
    probe_diff_luma: int = tune(
        40,
        "Luma difference a pixel must exceed to count as changed, for the probes and the control shares.",
        unit="luma",
        bounds=Bounds(ge=4, le=200),
        typed=A_LUMA,
        decides=(Code.CUE_NO_CHANGE, Code.CUE_THIN_CHANGE),
        hazard=(
            "A high value counts only a change that crosses most of the range, so a reveal that fades in or "
            "moves a light element on a light slide reads as no change at all."
        ),
        see_also=("verify.onset_diff_luma",),
    )
    changed_share_min_percent: float = tune(
        0.1,
        "Smallest share of the frame, in percent, that the reported probe must find changed.",
        unit="percent",
        bounds=Bounds(ge=0.001, le=5),
        typed=A_PERCENT,
        decides=(Code.CUE_NO_CHANGE, Code.CUE_THIN_CHANGE),
        hazard=(
            "Above about 5 percent only a change across most of the slide passes, so every small reveal in "
            "the deck reports a failure it cannot fix."
        ),
        see_also=("verify.margin_min_points", "verify.thin_change_factor"),
    )
    margin_min_points: float = tune(
        0.1,
        "Smallest margin, in percentage points, between the reported probe and its control.",
        unit="percentage points",
        bounds=Bounds(ge=0.001, le=5),
        typed=A_PERCENT,
        decides=(Code.CUE_NO_CHANGE, Code.CUE_THIN_CHANGE),
        see_also=("verify.changed_share_min_percent",),
    )
    thin_change_factor: float = tune(
        3.0,
        "A passing cue whose changed share or margin is under this many times its floor is uncertain "
        "rather than clean. A value of 1 turns that second opinion off.",
        bounds=Bounds(ge=1, le=10),
        decides=(Code.CUE_THIN_CHANGE,),
    )
    onset_rise_points: float = tune(
        0.01,
        "Rise in the changed share from one frame to the next that marks the onset, in percentage points.",
        unit="percentage points",
        bounds=Bounds(ge=0.001, le=1),
        typed=A_PERCENT,
        decides=(Code.CUE_OFF, Code.CUE_NO_ONSET),
        hazard=(
            "The rise is measured over a quarter-size frame, so one point is already tens of thousands of "
            "pixels. Above about one point no frame ever qualifies, every cue reports a null offset, and "
            "the check that names a late reveal stops running while the run still exits clean."
        ),
        see_also=("verify.onset_diff_luma", "verify.cue_offset_max_ms"),
    )
    onset_diff_luma: int = tune(
        12,
        "Luma difference a pixel must exceed to count as changed, for the onset scan alone.",
        unit="luma",
        bounds=Bounds(ge=2, le=60),
        typed=A_LUMA,
        decides=(Code.CUE_OFF, Code.CUE_NO_ONSET),
        see_also=("verify.probe_diff_luma",),
    )
    click_search_seconds: float = tune(
        0.25,
        "Seconds on each side of the cued word's start that the click search covers.",
        unit="seconds",
        bounds=Bounds(ge=0.02, le=2),
        decides=(Code.CUE_OFF,),
    )
    click_floor_dbfs: float = tune(
        -38.0,
        "Level a sample must exceed for the click search to call it the click.",
        unit="dBFS",
        bounds=Bounds(ge=-60, le=-24),
        decides=(Code.CUE_OFF,),
        hazard=(
            "DeckTalk generates its own click at -24 dBFS, so a floor at or above that level finds no click "
            "at all and every a/v row measures nothing while reporting no failure."
        ),
        see_also=("CLICK_LEVEL_DBFS",),
    )
    cue_offset_max_ms: float = tune(
        80.0,
        "How far the measured onset may sit from the cue time, early or late.",
        unit="milliseconds",
        bounds=Bounds(ge=20, le=400),
        decides=(Code.CUE_OFF,),
        hazard=(
            "A viewer sees a reveal land late at about a fifth of a second, so above roughly 200 "
            "milliseconds the limit passes films whose pictures visibly miss their words."
        ),
        see_also=("verify.av_offset_max_ms", "verify.reference_lead_seconds"),
    )
    av_offset_max_ms: float = tune(
        120.0,
        "How far the click may sit from the word it marks, early or late. It is wider than the cue limit "
        "because the click carries the encoder's own jitter as well.",
        unit="milliseconds",
        bounds=Bounds(ge=80, le=500),
        decides=(Code.CUE_OFF,),
        hazard=(
            "An mp3 frame smears a transient across about 26 milliseconds and the encode adds its own, so a "
            "limit under 80 milliseconds reports a failure that no edit to the film can clear."
        ),
        see_also=("verify.cue_offset_max_ms",),
    )
    black_max_luma: float = tune(
        60.0,
        "A frame whose brightest luma is at most this is black, whether `record` or `verify` reads it.",
        unit="luma",
        bounds=Bounds(ge=1, le=200),
        typed=A_LUMA,
        decides=(Code.PAGE_BLACK,),
        see_also=("record.painted_peak_luma_min",),
    )
    cut_window_seconds: float = tune(
        0.15,
        "Seconds of narration before each cut that the cut check measures.",
        unit="seconds",
        bounds=Bounds(ge=0.01, le=2),
        decides=(Code.CUT_SPEECH,),
    )
    cut_max_dbfs: float = tune(
        -40.0,
        "Loudest level the cut window may reach before the cut counts as falling on speech.",
        unit="dBFS",
        bounds=Bounds(ge=-90, le=-20),
        decides=(Code.CUT_SPEECH,),
        see_also=("narration.sound_end_noise_dbfs",),
    )
    cut_change_max_percent: float = tune(
        0.1,
        "Largest share of the frame, in percent, that may change across a cut into a seamless section.",
        unit="percent",
        bounds=Bounds(ge=0.001, le=10),
        typed=A_PERCENT,
        decides=(Code.CUT_POP,),
    )


@dataclass(frozen=True)
class VoiceConfig:
    """These keys are what the speech provider is asked for and what its characters cost."""

    stability: float = tune(
        0.55,
        "How closely the voice holds one delivery across takes.",
        bounds=A_SHARE,
        hazard="The provider takes a share and refuses anything else, so a value read as a percentage fails "
        "the request rather than the load.",
    )
    similarity_boost: float = tune(
        0.75,
        "How closely the voice holds to the original recording it was cloned from.",
        bounds=A_SHARE,
    )
    style: float = tune(
        0.0,
        "How much expressive style the voice adds beyond the words.",
        bounds=A_SHARE,
    )
    speaker_boost: bool = tune(
        True,
        "Whether the provider applies its own speaker boost to the take.",
    )
    speed: float = tune(
        1.0,
        "How fast the voice speaks, as a multiple of its natural pace.",
        bounds=Bounds(ge=0.7, le=1.2),
        hazard="The provider refuses a multiple outside its own range, so a slower read is a script change "
        "rather than a knob.",
    )
    price_per_1000_characters: float = tune(
        0.0,
        "What this project's plan charges per thousand characters of speech.",
        unit="currency per 1000 characters",
        bounds=Bounds(ge=0, le=100),
        source=Source.STATED,
        evidence="the plan page of the account whose key this project uses",
        hazard=(
            "It is zero until somebody states it, and a spend cap refuses a run while the price is still "
            "the default, because DeckTalk would otherwise be capping a spend against a number it invented."
        ),
    )


@dataclass(frozen=True)
class LoudnessConfig:
    """These keys are the programme loudness the final mp4 is normalised to and judged against."""

    target_lufs: float = tune(
        -16.0,
        "Programme loudness the final mp4 is normalised to.",
        unit="LUFS",
        bounds=Bounds(ge=-30, le=-9),
        decides=(Code.MIX_LOUDNESS,),
    )
    true_peak_max_dbtp: float = tune(
        -1.5,
        "Highest true peak the normalised mix may reach.",
        unit="dBTP",
        bounds=Bounds(ge=-9, le=-0.1),
        decides=(Code.MIX_LOUDNESS,),
        hazard=(
            "Four times oversampling under-reads a true peak and the AAC encode overshoots on top of that, "
            "so a ceiling above about -1 dBTP reports a clean mix that clips on a listener's decoder."
        ),
    )
    range_max_lu: float = tune(
        11.0,
        "Widest loudness range the normalised mix may hold.",
        unit="LU",
        bounds=Bounds(ge=1, le=20),
        decides=(Code.MIX_LOUDNESS,),
    )


@dataclass(frozen=True)
class MixConfig:
    """The tuning half of `[mix]`. The music, the ambience and the levels are project content."""

    loudness: LoudnessConfig = field(default_factory=LoudnessConfig)


@dataclass(frozen=True)
class MotionConfig:
    """These keys decide how much motion a build renders, which changes the film it produces."""

    reduce: bool = tune(
        False,
        "Render every slide as the reduced-motion variant, which is the film a viewer who asks for less "
        "motion should be given.",
        decides=(Code.PAGE_CLASS_NOT_REDUCED,),
        see_also=("motion.scale",),
    )
    scale: float = tune(
        1.0,
        "Multiplier on every declared motion span and duration in the deck.",
        bounds=Bounds(ge=0.25, le=4.0),
        decides=(Code.PAGE_MOTION_OVERRUN,),
        hazard=(
            "A scale above one slows motion down, and a span slowed past the measurable ceiling makes its "
            "own cue unmeasurable, so each scaled span is clamped at that ceiling rather than obeyed."
        ),
        see_also=("motion.reduce", "MEASURABLE_SPAN_SECONDS"),
    )


@dataclass(frozen=True)
class ElevenLabsConfig:
    """These keys point at the ElevenLabs API and size its soundscape requests."""

    api_base: str = tune("https://api.elevenlabs.io/v1", "Base URL of the ElevenLabs API.")
    sound_model: str = tune(
        "eleven_text_to_sound_v2",
        "Model for ambience and sound effect requests. A soundscape table can name its own.",
    )
    music_model: str = tune("music_v2", "Model for music requests. A soundscape table can name its own.")
    music_bitrate: str = tune(
        "192k",
        "Bitrate of the music file that `soundscape` joins from its chunks.",
        bounds=Bounds(enum=("96k", "128k", "160k", "192k", "256k", "320k")),
    )
    max_music_chunk_seconds: int = tune(
        300,
        "Longest music request. Longer music is requested in chunks and crossfaded.",
        unit="seconds",
        bounds=Bounds(ge=30, le=600),
        nature=Nature.APPARATUS,
    )
    music_crossfade_seconds: int = tune(
        2,
        "Crossfade between two music chunks.",
        unit="seconds",
        bounds=Bounds(ge=0, le=30),
    )
    ambience_seconds: float = tune(
        25.0,
        "Length of a generated ambience bed. A soundscape table can set `duration_seconds` instead.",
        unit="seconds",
        bounds=Bounds(ge=1, le=60),
    )
    ambience_prompt_influence: float = tune(
        0.3,
        "Prompt influence of an ambience request. A soundscape table can set its own.",
        bounds=A_SHARE,
    )
    effect_seconds: float = tune(
        0.5,
        "Length of a generated sound effect. A soundscape table can set `duration_seconds` instead.",
        unit="seconds",
        bounds=Bounds(ge=0.1, le=30),
    )
    effect_prompt_influence: float = tune(
        0.5,
        "Prompt influence of a sound effect request. A soundscape table can set its own.",
        bounds=A_SHARE,
    )
    timeout_seconds: int = tune(
        600,
        "Seconds before a sound or music request times out.",
        unit="seconds",
        bounds=Bounds(ge=10, le=3600),
        nature=Nature.APPARATUS,
    )


@dataclass(frozen=True)
class OutputConfig:
    """These keys switch the files a build writes beside the final mp4 and how long they are kept."""

    timestamped_copy: bool = tune(False, "Write a second copy of the final mp4 named with the date and time.")
    events_keep_runs: int = tune(
        20,
        "How many runs of event files `build/events/` keeps before the oldest is deleted.",
        unit="runs",
        bounds=Bounds(ge=1, le=1000),
    )


@dataclass(frozen=True)
class ToolsConfig:
    """These keys name the tools DeckTalk drives, for a machine that supplies its own."""

    ffmpeg: str = tune(
        "",
        "ffmpeg executable. It is empty for the build DeckTalk fetches itself.",
        unit="path",
        scope=Scope.MACHINE,
        nature=Nature.APPARATUS,
    )
    ffprobe: str = tune(
        "",
        "ffprobe executable. It is empty for the build DeckTalk fetches itself.",
        unit="path",
        scope=Scope.MACHINE,
        nature=Nature.APPARATUS,
    )
    cache_dir: str = tune(
        "",
        "Directory the fetched Chromium and ffmpeg builds live in. It is empty for the standard per-user cache.",
        unit="path",
        scope=Scope.MACHINE,
        nature=Nature.APPARATUS,
        see_also=("narration.cache_dir",),
    )


@dataclass(frozen=True)
class HostConfig:
    """These keys are facts about this machine that a run measures rather than a person chooses."""

    presentation_bias_ms: float = tune(
        0.0,
        "How long this machine takes to present a frame the page has already drawn, which verify "
        "subtracts from a measured offset when the record still matches the run.",
        unit="milliseconds",
        bounds=Bounds(ge=-200, le=200),
        scope=Scope.MACHINE,
        nature=Nature.APPARATUS,
        source=Source.MEASURED,
        evidence="decktalk doctor --measure",
        decides=(Code.CUE_OFF,),
        hazard=(
            "A bias written by hand is a guess subtracted from every measurement, which moves whichever "
            "verdict the guess was chosen to move. It is measured or it is zero."
        ),
    )


@dataclass(frozen=True)
class Settings:
    """Every tunable with its default, in the order the reference and the schema publish them."""

    video: VideoConfig = field(default_factory=VideoConfig)
    narration: NarrationConfig = field(default_factory=NarrationConfig)
    record: RecordConfig = field(default_factory=RecordConfig)
    audio: AudioConfig = field(default_factory=AudioConfig)
    verify: VerifyConfig = field(default_factory=VerifyConfig)
    voice: VoiceConfig = field(default_factory=VoiceConfig)
    mix: MixConfig = field(default_factory=MixConfig)
    motion: MotionConfig = field(default_factory=MotionConfig)
    elevenlabs: ElevenLabsConfig = field(default_factory=ElevenLabsConfig)
    output: OutputConfig = field(default_factory=OutputConfig)
    tools: ToolsConfig = field(default_factory=ToolsConfig)
    host: HostConfig = field(default_factory=HostConfig)


KEYS: tuple[Key, ...] = registry(Settings)
"""Every settings key in declaration order, which is the one list every surface renders."""

BY_ID: dict[str, Key] = {key.id: key for key in KEYS}
"""Every key by the dotted name a diagnostic prints, `config set` takes and `--set` spells."""

SHARED_TABLES = frozenset(("voice", "mix", "mix.loudness"))
"""The tables that hold tuning keys beside project content, so the settings loader warns for neither."""

DOCUMENT_TABLES = ("project", "section", "transition", "soundscape")
"""The tables of `decktalk.toml` that are project content rather than tuning, named so a refusal can say so.

`voice` and `mix` are missing on purpose: each holds tuning keys declared above beside content keys
the document owns, so neither is wholly one thing.
"""

STANDALONE_ENV = frozenset(("DECKTALK_PROJECT", "DECKTALK_CONFIG", "DECKTALK_ALLOW_ANY_API_BASE"))
"""The three variables DeckTalk reads that name no key. Every other DECKTALK_ name is a key or a typo."""


@dataclass(frozen=True)
class Number:
    """One number that is deliberately not a knob, published with its formula and its reason.

    A derived number is written as its expression and never as its value, so a reader who goes
    looking for a knob that used to exist meets the arithmetic instead of nothing. A constant is a
    fact about a codec, a standard or a tool DeckTalk drives, and the sentence says which.
    """

    id: str
    formula: str
    reads: tuple[str, ...]
    unit: str | None
    nature: Nature
    sentence: str
    at: Callable[[Settings], object]
    decides: tuple[Code, ...] = ()

    @property
    def kind(self) -> str:
        """Whether this number is computed from the keys or fixed, which is what `x-numbers` publishes."""
        return "derived" if self.nature is Nature.DERIVED else "constant"


def probe_width(settings: Settings) -> int:
    """The width every frame is scaled to before a comparison, which is a quarter of the frame."""
    return settings.video.width // 4


def probe_height(settings: Settings) -> int:
    """The height every frame is scaled to before a comparison, which is a quarter of the frame."""
    return settings.video.height // 4


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
        formula="video.width // 4",
        reads=("video.width",),
        unit="pixels",
        nature=Nature.DERIVED,
        sentence="Derived: a quarter-size frame is what every share is measured over, at every frame size.",
        at=probe_width,
    ),
    Number(
        id="verify.probe_height",
        formula="video.height // 4",
        reads=("video.height",),
        unit="pixels",
        nature=Nature.DERIVED,
        sentence="Derived: a quarter-size frame is what every share is measured over, at every frame size.",
        at=probe_height,
    ),
    Number(
        id="verify.block_width",
        formula="video.width // BLOCK_PX",
        reads=("video.width", "BLOCK_PX"),
        unit="pixels",
        nature=Nature.DERIVED,
        sentence=(
            "Derived: one pixel per H.264 transform block, so the block average cancels the encoder's "
            "ringing at every frame size rather than only at 1080p."
        ),
        at=block_width,
    ),
    Number(
        id="verify.block_height",
        formula="video.height // BLOCK_PX",
        reads=("video.height", "BLOCK_PX"),
        unit="pixels",
        nature=Nature.DERIVED,
        sentence="Derived: one pixel per H.264 transform block, as the block width is.",
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
    Number(
        id="CAPTURE_FPS",
        formula=str(CAPTURE_FPS),
        reads=(),
        unit="frames per second",
        nature=Nature.TRUTH,
        sentence="Truth: the rate the recorder captures at, which DeckTalk cannot set and so never asks for.",
        at=lambda _settings: CAPTURE_FPS,
    ),
    Number(
        id="BLOCK_PX",
        formula=str(BLOCK_PX),
        reads=(),
        unit="pixels",
        nature=Nature.TRUTH,
        sentence="Truth: the H.264 transform block the block-averaged copy of a frame cancels ringing over.",
        at=lambda _settings: BLOCK_PX,
    ),
    Number(
        id="GUARD_FRAMES",
        formula=str(GUARD_FRAMES),
        reads=(),
        unit="frames",
        nature=Nature.TRUTH,
        sentence="Truth: half a frame of rounding guard on each side of the window the offset limit allows.",
        at=lambda _settings: GUARD_FRAMES,
    ),
    Number(
        id="REPORT_FRAME_GAP_MS",
        formula=str(REPORT_FRAME_GAP_MS),
        reads=(),
        unit="milliseconds",
        nature=Nature.CALIBRATION,
        sentence="Calibration: the runtime's own reporting floor, under which a frame gap cannot be seen.",
        at=lambda _settings: REPORT_FRAME_GAP_MS,
    ),
    Number(
        id="CLICK_LEVEL_DBFS",
        formula=str(CLICK_LEVEL_DBFS),
        reads=(),
        unit="dBFS",
        nature=Nature.TRUTH,
        sentence="Truth: the level DeckTalk generates its own click at, which every click floor sits under.",
        at=lambda _settings: CLICK_LEVEL_DBFS,
    ),
    Number(
        id="MEASURABLE_SPAN_SECONDS",
        formula=str(MEASURABLE_SPAN_SECONDS),
        reads=(),
        unit="seconds",
        nature=Nature.TRUTH,
        sentence=(
            "Truth: the span at which an effect covers its own cue, which is the ceiling every declared "
            "span, every scaled span and every staggered total is held under."
        ),
        at=lambda _settings: MEASURABLE_SPAN_SECONDS,
    ),
)
"""Every number that is not a knob, with the formula or the fact that fixes it."""

NUMBERS_BY_ID: dict[str, Number] = {number.id: number for number in NUMBERS}


class Layers(BaseModel):
    """What every layer said about every key, which is the record `config explain` renders.

    It is built at load and again at reload, so a watch loop that sees an edited `decktalk.toml`
    sees the layer that set each key move with it. A finding that names a knob quotes the winning
    row, because a value without its layer cannot tell a deliberate choice from a default.
    """

    model_config = MODEL

    rows: dict[str, tuple[LayerValue, ...]] = Field(
        description="Every key by its dotted name, with one row per layer that stated it, lowest first."
    )

    def of(self, key: str) -> tuple[LayerValue, ...]:
        """Every layer that stated this key, lowest first, which always begins with the default."""
        return self.rows.get(key, ())

    def winner(self, key: str) -> LayerValue:
        """The layer in force for this key, which is the highest one that stated it."""
        rows = self.of(key)
        if not rows:
            raise KeyError(key)
        return rows[-1]


@dataclass(frozen=True)
class Loaded:
    """The settings tree and the record of where every value in it came from.

    It is a plain frozen object rather than a model, because no command prints it whole: a caller
    holds the tree and renders the record, and validating a dataclass tree a second time on every
    load would buy nothing a test does not already hold.
    """

    settings: Settings
    layers: Layers


class SettingWrite(BaseModel):
    """What a write to a settings file changed, or would change on a dry run.

    It reports the effective value as well as the written one, because a write to the project file
    that an environment variable still shadows changes the file and not the run, and an agent that
    is told only what it wrote will believe the opposite.
    """

    model_config = MODEL

    key: str = Field(description="The key's dotted name.")
    value: JsonValue = Field(description="The value this call wrote, or would write.")
    previous: JsonValue = Field(None, description="The value that file held before, or null when it held none.")
    scope: Scope = Field(description="Which file the write landed in.")
    file: ProjectPath = Field(description="The file that was written.")
    line: int | None = Field(None, ge=1, description="The line the key now sits on in that file, or null.")
    dry_run: bool = Field(description="True when the call reported the change and wrote nothing.")
    effective: JsonValue = Field(None, description="The value in force after the write.")
    layer: Layer = Field(description="Which layer the effective value now comes from.")
    shadowed: bool = Field(description="True when a higher layer still decides this key despite the write.")


def machine_config_path() -> Path:
    """The per-machine settings file. DECKTALK_CONFIG names a different one."""
    override = os.environ.get("DECKTALK_CONFIG")
    if override:
        return Path(override)
    if sys.platform == "darwin":
        root = Path.home() / "Library" / "Application Support"
    elif sys.platform == "win32":
        root = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    else:
        root = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return root / "decktalk" / PROJECT_FILE


def read_toml(path: Path) -> dict[str, Any]:
    """One TOML file parsed, or {} when it is absent. Malformed TOML is refused with its own line."""
    if not path.exists():
        return {}
    try:
        with path.open("rb") as fh:
            return tomllib.load(fh)
    except tomllib.TOMLDecodeError as exc:
        raise InputError(
            f"{path.name} is not valid TOML: {exc}.",
            hint="Fix the line this message names, which is usually a quote or a bracket left open.",
            location=Location(where=path.name, file=path, line=refused_line(exc)),
        ) from exc


def read_project_toml(root: Path) -> dict[str, Any]:
    """The project's `decktalk.toml`, or {} when the project has none yet."""
    return read_toml(root / PROJECT_FILE)


def read_machine_toml(path: Path | None = None) -> dict[str, Any]:
    """The per-machine tuning tables, refusing any key that belongs in the project instead.

    The file is restricted by key and not by table, because a limit is a statement about the film
    and the file ships whatever the runner believes. A project-scoped key found here is refused by
    name rather than warned about, since a warning would put a correctly spelled key in a weaker
    class than a typo and a reader of the JSON never sees a log line at all.
    """
    path = path or machine_config_path()
    data = read_toml(path)
    if not data:
        return {}
    text = path.read_text(encoding="utf-8")
    tables = {key.table.split(".")[0] for key in KEYS}
    unknown = sorted(set(data) - tables)
    if unknown:
        raise InputError(
            f"{path.name}: {', '.join(unknown)} is not a tuning table, so it does not belong in this file.",
            hint=f"The tables the per-machine file may hold are {', '.join(sorted(tables))}.",
            location=Location(where=path.name, file=path),
        )
    for dotted, _value in _flatten(data):
        key = BY_ID.get(dotted)
        if key is None:
            continue
        if key.scope is not Scope.MACHINE:
            raise InputError(
                f"{path.name}: '{dotted}' is {key.scope.value}-scoped, so it belongs in the project's "
                f"{PROJECT_FILE} where the film that ships carries it.",
                hint=f"Remove it from this file and run `decktalk config set {dotted} <value> --project`.",
                location=Location(where=f"[{key.table}] {key.name}", file=path, line=locate(text, dotted)),
            )
    for message in key_warnings(data, path.name):
        log.warning(message)
    return data


def key_warnings(doc: Mapping[str, Any], where: str) -> list[str]:
    """One warning per key inside a tuning table that DeckTalk does not read, with a near name when there is one.

    A table that also holds project content, such as `[voice]`, is left alone, because the document
    parser owns the rest of that table and warning here would call one of its keys unknown.
    """
    out: list[str] = []
    for table in sorted({key.table for key in KEYS} - SHARED_TABLES):
        found = _table(doc, table)
        if found is None:
            continue
        known = {key.name for key in KEYS if key.table == table}
        out += unknown_key_warnings(found, known, f"{where}: [{table}]")
    return out


def env_warnings(environ: Mapping[str, str] | None = None) -> list[str]:
    """One warning per DECKTALK_ variable DeckTalk does not read, naming the closest one it does.

    A variable DeckTalk does not read has no effect, so the warning is what tells a reader that a
    typed name never took hold.
    """
    env = os.environ if environ is None else environ
    known = env_names(Settings, ENV_PREFIX) | STANDALONE_ENV
    return [
        unknown_key_message(name, known, "environment")
        for name in sorted(n for n in env if n.startswith("DECKTALK_") and n not in known)
    ]


def _table(doc: Mapping[str, Any], dotted: str) -> Mapping[str, Any] | None:
    """One nested table of a parsed document by its dotted name, or null when it is not there."""
    found: Any = doc
    for part in dotted.split("."):
        if not isinstance(found, Mapping) or part not in found:
            return None
        found = found[part]
    return found if isinstance(found, Mapping) else None


def _flatten(doc: Mapping[str, Any], prefix: str = "") -> Iterator[tuple[str, Any]]:
    """Every scalar of a parsed document with its dotted name, so a key can be looked up by id."""
    for name, value in doc.items():
        dotted = f"{prefix}{name}"
        if isinstance(value, Mapping):
            yield from _flatten(value, f"{dotted}.")
        else:
            yield dotted, value


def merge_tables(base: Mapping[str, Any], over: Mapping[str, Any]) -> dict[str, Any]:
    """`over` on top of `base`, table by table, all the way down, which is how tuning tables nest."""
    out: dict[str, Any] = {k: dict(v) if isinstance(v, Mapping) else v for k, v in base.items()}
    for k, v in over.items():
        if isinstance(v, Mapping) and isinstance(out.get(k), dict):
            out[k] = merge_tables(out[k], v)
        else:
            out[k] = dict(v) if isinstance(v, Mapping) else v
    return out


def route(overrides: tuple[str, ...]) -> dict[str, str]:
    """Every `--set table.key=value` pair read into keys, refusing anything that is not one.

    The whole tuple reaches both the machine and the project, and the pairs are routed by the scope
    each key publishes, so no caller has to know which layer a key belongs to. A key nobody knows is
    refused before a stage is imported, with the nearest key it could have meant.
    """
    out: dict[str, str] = {}
    for pair in overrides:
        name, sep, value = pair.partition("=")
        key = name.strip()
        if not sep:
            raise InputError(
                f"--set takes table.key=value, and '{pair}' has no '='.",
                hint="Write it as --set verify.cue_offset_max_ms=120.",
            )
        if key.split(".")[0] in DOCUMENT_TABLES:
            raise InputError(
                f"'{key}' is project content rather than a knob, so no override can set it.",
                hint=f"Edit [{key.split('.')[0]}] in {PROJECT_FILE} instead.",
            )
        if key not in BY_ID:
            raise InputError(
                f"'{key}' is not a settings key.{did_you_mean(key, BY_ID)}",
                hint="Run `decktalk schema settings` for every key DeckTalk reads.",
            )
        out[key] = value
    return out


def scoped(overrides: Mapping[str, str], scope: Scope) -> dict[str, str]:
    """The overrides that belong to one scope, which is how the machine and the project each take their own."""
    return {key: value for key, value in overrides.items() if BY_ID[key].scope is scope}


def load(
    root: Path | None = None,
    *,
    project: Mapping[str, Any] | None = None,
    machine: Mapping[str, Any] | None = None,
    machine_path: Path | None = None,
    environ: Mapping[str, str] | None = None,
    overrides: tuple[str, ...] = (),
) -> Loaded:
    """Every key resolved through the five layers, with the record of which layer set each one.

    An override is spelled the way an environment variable is, a string the key's own type reads,
    so one conversion serves both and an override cannot be admitted by a route the environment is
    refused by. It sits above the environment because it is given for one run on purpose.
    """
    env = dict(os.environ if environ is None else environ)
    from_machine = dict(machine) if machine is not None else read_machine_toml(machine_path)
    from_project = dict(project) if project is not None else (read_project_toml(root) if root else {})
    pairs = route(overrides)
    for message in env_warnings(env):
        log.warning(message)
    if project is not None or root:
        for message in key_warnings(from_project, PROJECT_FILE):
            log.warning(message)
    base = merge_tables(from_machine, from_project)
    env_and_overrides = {**env, **{BY_ID[key].environment: value for key, value in pairs.items()}}
    settings = from_mapping(Settings, base=base, prefixes=[ENV_PREFIX], environ=env_and_overrides)
    _require(settings)
    files = {
        Layer.MACHINE: machine_path or (machine_config_path() if machine is None else None),
        Layer.PROJECT: (root / PROJECT_FILE) if root else None,
    }
    return Loaded(settings=settings, layers=_layers(settings, from_machine, from_project, env, pairs, files))


def _layers(
    settings: Settings,
    machine: Mapping[str, Any],
    project: Mapping[str, Any],
    environ: Mapping[str, str],
    overrides: Mapping[str, str],
    files: Mapping[Layer, Path | None],
) -> Layers:
    """One row per layer that stated each key, lowest first, which is what the merge itself cannot say.

    The layers are read separately rather than after merging, because a merged mapping has already
    forgotten which file wrote each key, and the file is half of what makes the record useful.
    """
    texts = {layer: path.read_text(encoding="utf-8") if path and path.exists() else "" for layer, path in files.items()}
    rows: dict[str, tuple[LayerValue, ...]] = {}
    for key in KEYS:
        found = [LayerValue(layer=Layer.DEFAULT, value=_json(key.default))]
        for layer, doc in ((Layer.MACHINE, machine), (Layer.PROJECT, project)):
            stated = _stated(doc, key.id)
            if stated is not _ABSENT:
                path = files.get(layer)
                found.append(
                    LayerValue(
                        layer=layer,
                        value=_json(stated),
                        file=path,
                        line=locate(texts.get(layer, ""), key.id) if path else None,
                    )
                )
        if key.environment in environ:
            found.append(LayerValue(layer=Layer.ENVIRONMENT, value=environ[key.environment]))
        if key.id in overrides:
            found.append(LayerValue(layer=Layer.OVERRIDE, value=overrides[key.id]))
        # The winning row carries the value the tree holds rather than the text a layer wrote, so a
        # reader of the record and a reader of the settings never disagree about one number.
        found[-1] = found[-1].model_copy(update={"value": _json(value_of(settings, key.id))})
        rows[key.id] = tuple(found)
    return Layers(rows=rows)


_ABSENT = object()
"""The answer to a lookup for a key a layer never stated, which None cannot be because None is a value."""


def _stated(doc: Mapping[str, Any], dotted: str) -> object:
    """What one layer's document says about one key, or `_ABSENT` when it says nothing."""
    found: object = doc
    for part in dotted.split("."):
        if not isinstance(found, Mapping) or part not in found:
            return _ABSENT
        found = found[part]
    return found


def _json(value: object) -> JsonValue:
    """One value as JSON carries it, which turns the tuple a TOML array becomes into a list."""
    if isinstance(value, tuple):
        return [_json(item) for item in value]
    return cast("JsonValue", value)


def value_of(settings: Settings, dotted: str) -> object:
    """The value one dotted key holds in a settings tree."""
    found: object = settings
    for part in dotted.split("."):
        found = getattr(found, part)
    return found


def _require(settings: Settings) -> None:
    """Every cross-table relation a key declares, enforced once the whole tree is built.

    A relation is a comparison an editor cannot check, because one side of it lives in another
    table or is a published number rather than a key, so the loader is the only place it can hold.
    """
    for key in KEYS:
        if key.requires is None:
            continue
        left, op, right = key.requires.split()
        if not _compare(_side(settings, left), op, _side(settings, right)):
            raise InputError(
                f"{key.id} must satisfy {key.requires}, and it does not.",
                hint=key.hazard,
            )


def _side(settings: Settings, token: str) -> float:
    """One side of a declared relation, which is a key, a published number or a literal."""
    if token in BY_ID:
        return float(cast("float", value_of(settings, token)))
    if token in NUMBERS_BY_ID:
        return float(cast("float", NUMBERS_BY_ID[token].at(settings)))
    return float(token)


def _compare(left: float, op: str, right: float) -> bool:
    """The four comparisons a declared relation may use."""
    return {
        ">=": left >= right,
        "<=": left <= right,
        ">": left > right,
        "<": left < right,
    }[op]


def parse_value(key: Key, text: str) -> object:
    """One value as a command line spells it, read as the key's own type and held to its range.

    A value that reaches `config set` and a value that reaches `--set` are the same string, so both
    are read here and both meet the same refusal.
    """
    return read_value(key.annotation, text, where=key.id, field=_declared(key), from_env=True)


def _declared(key: Key) -> DataField[Any]:
    """The dataclass field that declares this key, found by walking the table path of its id.

    The walk resolves each table's annotation by name, because the module is written under future
    annotations and a field's declared type is the string the author wrote.
    """
    holder: Any = Settings
    for part in key.table.split("."):
        annotation = next(f for f in fields(holder) if f.name == part).type
        holder = globals()[annotation] if isinstance(annotation, str) else annotation
        if not is_dataclass(holder):
            raise KeyError(key.id)
    return next(f for f in fields(holder) if f.name == key.name)


def write(
    path: Path,
    key: str,
    value: str,
    *,
    scope: Scope,
    dry_run: bool = False,
) -> SettingWrite:
    """Set one key in one file, through the whole loader, keeping every comment the file already has.

    The would-be file is built first and loaded whole, so a value that no run could use never lands
    and the refusal a caller meets is the loader's own, with its file, its line and its near name.
    The document is edited rather than rewritten, because a person wrote the comments around the
    key and a writer that dumped a parsed tree would delete them the first time an agent turned a
    knob.
    """
    known = BY_ID.get(key)
    if known is None:
        raise InputError(
            f"'{key}' is not a settings key.{did_you_mean(key, BY_ID)}",
            hint="Run `decktalk schema settings` for every key DeckTalk reads.",
        )
    if known.scope is not scope:
        other = "--machine" if known.scope is Scope.MACHINE else "--project"
        raise InputError(
            f"'{key}' is {known.scope.value}-scoped, so it cannot be written to the {scope.value} file.",
            hint=f"Run `decktalk config set {key} {value} {other}`.",
        )
    if known.source is Source.MEASURED:
        raise InputError(
            f"'{key}' is measured rather than chosen, so a value written by hand would be a guess.",
            hint=f"Run `{known.evidence}`.",
        )
    typed = parse_value(known, value)
    document = tomlkit.parse(path.read_text(encoding="utf-8")) if path.exists() else tomlkit.document()
    previous = _stated(document, key)
    _put(document, key.split("."), typed)
    text = tomlkit.dumps(document)
    _validate(text, path, scope)
    if not dry_run:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return SettingWrite(
        key=key,
        value=_json(typed),
        previous=None if previous is _ABSENT else _json(previous),
        scope=scope,
        file=path,
        line=locate(text, key),
        dry_run=dry_run,
        **_after(path, key, scope, typed),
    )


def _put(document: MutableMapping[str, Any], parts: list[str], value: object) -> None:
    """One key set in a parsed document, adding the tables it sits in when they are not there yet."""
    table = document
    for part in parts[:-1]:
        if part not in table:
            table[part] = tomlkit.table()
        table = cast("MutableMapping[str, Any]", table[part])
    table[parts[-1]] = value


def _validate(text: str, path: Path, scope: Scope) -> None:
    """The whole settings tree built on the would-be file, so a bad value never reaches the disk."""
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:  # pragma: no cover  (tomlkit writes only valid TOML)
        raise InputError(f"{path.name} would not be valid TOML: {exc}.") from exc
    if scope is Scope.MACHINE:
        _machine_scope(data, path)
    load(
        machine=data if scope is Scope.MACHINE else {},
        project=data if scope is Scope.PROJECT else {},
        environ={},
    )


def _machine_scope(data: Mapping[str, Any], path: Path) -> None:
    """The per-machine rule applied to a would-be file, which is the same refusal a read makes."""
    for dotted, _value in _flatten(data):
        key = BY_ID.get(dotted)
        if key is not None and key.scope is not Scope.MACHINE:
            raise InputError(
                f"{path.name}: '{dotted}' is {key.scope.value}-scoped and does not belong in this file.",
                hint=f"Run `decktalk config set {dotted} <value> --project`.",
            )


def _after(path: Path, key: str, scope: Scope, typed: object) -> dict[str, Any]:
    """The value in force once this write lands, and whether a higher layer still decides the key.

    A write that a higher layer shadows changes the file and not the run, so the call says so
    rather than reporting a new value the next command will not use.
    """
    written = {key: typed}
    tree = load(
        machine=_nested(written) if scope is Scope.MACHINE else {},
        project=_nested(written) if scope is Scope.PROJECT else {},
        machine_path=path if scope is Scope.MACHINE else None,
    )
    winner = tree.layers.winner(key)
    own = Layer.MACHINE if scope is Scope.MACHINE else Layer.PROJECT
    return {
        "effective": _json(value_of(tree.settings, key)),
        "layer": winner.layer,
        "shadowed": winner.layer is not own,
    }


def _nested(flat: Mapping[str, object]) -> dict[str, Any]:
    """Dotted keys as the nested tables a layer is read from."""
    out: dict[str, Any] = {}
    for dotted, value in flat.items():
        table = out
        parts = dotted.split(".")
        for part in parts[:-1]:
            table = table.setdefault(part, {})
        table[parts[-1]] = value
    return out


__all__ = [
    "BY_ID",
    "DOCUMENT_TABLES",
    "ENV_PREFIX",
    "KEYS",
    "NUMBERS",
    "NUMBERS_BY_ID",
    "PROJECT_FILE",
    "SHARED_TABLES",
    "STANDALONE_ENV",
    "AudioConfig",
    "ElevenLabsConfig",
    "HostConfig",
    "Layers",
    "Loaded",
    "LoudnessConfig",
    "MixConfig",
    "MotionConfig",
    "NarrationConfig",
    "Number",
    "OutputConfig",
    "RecordConfig",
    "SettingWrite",
    "Settings",
    "ToolsConfig",
    "VerifyConfig",
    "VideoConfig",
    "VoiceConfig",
    "env_warnings",
    "key_warnings",
    "load",
    "machine_config_path",
    "merge_tables",
    "parse_value",
    "read_machine_toml",
    "read_project_toml",
    "read_toml",
    "route",
    "scoped",
    "value_of",
    "write",
]
