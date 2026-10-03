"""Every setting DeckTalk publishes, with the range that is safe to turn it through.

One dataclass per table, composed into `Settings`. Each field is declared with `tune()`, which
carries everything any surface says about that key: what it changes, what its default is, the
range the loader enforces, the wider range its type would admit, its true unit, which findings it
moves, which file it belongs in, where its value is expected to come from, what a value at the
edge risks and which other key or published number it relates to. Nothing about a key is written
anywhere else, so the JSON Schema, the reference page, `config explain` and a finding that names
a setting are four renderings of one row.

Five layers set a key, lowest to highest: the default here, the same table in the per-machine
file, the same table in the project's `decktalk.toml`, the environment variable named
`DECKTALK_<TABLE>_<KEY>`, and a `--set table.key=value` given for one run. `load()` returns both
the resolved tree and the record of which layer set each key, because a caller that is told a
value and not its layer cannot tell a deliberate choice from a default.

Two rules keep the published range honest. The published range is the safe range: a bound is here
because a value past it deletes a check, corrupts the evidence a later stage measures or breaks a
tool, and the loader refuses outside it. A number that is not a setting is published too: every
derived expression and every named constant is in `NUMBERS` with its formula and its reason, so a
reader who cannot find a setting learns the number is deliberately not one.
"""

from __future__ import annotations

import functools
import operator
import sys
import tomllib
from collections.abc import Callable, Iterator, Mapping, MutableMapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

import tomlkit
from pydantic import Field, JsonValue
from pydantic_core import to_jsonable_python
from tomlkit.exceptions import ParseError

from .errors import InputError
from .files import current_text, replace_all
from .findings import Code, Location, Model
from .locate import locate, refused_line
from .page import CAPTURE_FPS, MEASURABLE_SPAN_SECONDS
from .results import ConfigSetResult, ConfigUnsetResult, Layer, LayerValue, Scope
from .tomlmap import (
    A_LUMA,
    A_PERCENT,
    A_SHARE,
    Bounds,
    Key,
    Nature,
    Source,
    did_you_mean,
    from_mapping,
    named_key,
    read_value,
    registry,
    tune,
    unknown_key_message,
    unknown_key_warnings,
)

PROJECT_FILE = "decktalk.toml"
ENV_PREFIX = "decktalk"

X264_PRESETS = ("ultrafast", "superfast", "veryfast", "faster", "fast", "medium", "slow", "slower", "veryslow")
"""Truth: the words x264 accepts, so a wrong one fails at load rather than inside a render."""

COLOR_SCHEMES = ("light", "dark", "no-preference")
"""Truth: the values Chromium reports for `prefers-color-scheme`."""

VOICE_ID = r"^[A-Za-z0-9_-]*$"
"""Truth: the letters a provider's voice id is spelled in, and nothing that could leave a path segment."""

SOME_PATH = r"^.+$"
"""Truth: a path with at least one character in it, which is what a folder that must exist is named by."""

HEX_COLOR = r"^(#|0[xX])[0-9A-Fa-f]{6}$"
"""Truth: a colour written as six hex digits after the prefix a stylesheet or ffmpeg reads, and nothing else."""

PAGE_POLICIES = ("trusted", "untrusted")
"""The two ways `record` treats a page: as the author's own work, or as a stranger's that may be hostile."""

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
        "Frame rate of the final mp4. The recorder's own rate is measured and is not a setting.",
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
    slate_color: str = tune(
        "0x0e1116",
        "Color of the plain frame that plays when a slate image cannot be rendered, as #RRGGBB or 0xRRGGBB.",
        bounds=Bounds(pattern=HEX_COLOR),
        hazard=(
            "The colour is placed inside an ffmpeg filter graph and the slate page's stylesheet, so any other "
            "text could add a filter that opens a file or a rule that loads a URL."
        ),
    )


@dataclass(frozen=True)
class NarrationConfig:
    """These keys govern how the script is turned into audio."""

    concurrency: int = tune(
        2,
        "How many sections `narrate` voices at once.",
        bounds=Bounds(ge=1, le=8),
        scope=Scope.MACHINE,
        nature=Nature.APPARATUS,
        hazard="A voice provider limits requests per account, and a busy answer is retried rather than paid twice.",
    )
    retries: int = tune(
        3,
        "How many more times `narrate` asks again when the voice provider answers that it is busy or failed.",
        bounds=Bounds(ge=0, le=10),
        scope=Scope.MACHINE,
        nature=Nature.APPARATUS,
    )
    mp3_bitrate: str = tune(
        "128k",
        "Bitrate of the mp3 files that DeckTalk writes, which are the placeholders and the joined narration.",
        bounds=Bounds(enum=("64k", "96k", "128k", "160k", "192k", "256k")),
    )
    words_per_minute: int = tune(
        140,
        "Pacing of the estimated length in the narrate table.",
        unit="words per minute",
        bounds=Bounds(ge=60, le=300),
    )
    placeholder_words_per_minute: int = tune(
        150,
        "Pacing of a placeholder, the click audio a section plays until its take is voiced.",
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
    placeholder_beat_seconds: float = tune(
        0.7,
        "Seconds each beat adds to the length of a placeholder take.",
        unit="seconds",
        bounds=Bounds(ge=0, le=10),
    )
    tail_seconds: float = tune(
        0.7,
        "Silence after the last word of every spoken section, so a cut never falls on speech. The take is "
        "placed so that exactly this much follows its last sound. A section's own `tail_seconds` replaces it.",
        unit="seconds",
        bounds=Bounds(ge=0, le=10),
        decides=(Code.CUT_SPEECH,),
    )
    takes_dir: str = tune(
        "takes",
        "Directory inside the project that holds every take the film plays and the words its voice sent with "
        "each, which you commit so a fresh clone builds the film with no key. The take index stays under the "
        "build, because it is a cache built again from the takes. A take is looked for here first and in the "
        "machine's take store, `store_dir`, second, and a take this project buys or finds there is written here.",
        unit="path",
        bounds=Bounds(pattern=SOME_PATH),
        hazard=(
            "An empty value, a directory outside the project, an absolute path, the project directory itself, a "
            "directory inside the build directory or one that holds it is refused when the project loads, "
            "because a voiced take must land in a folder the project keeps, and a project someone else wrote "
            "would otherwise choose where this machine reads and writes its takes."
        ),
        see_also=("narration.store_dir",),
    )
    store_dir: str = tune(
        "",
        "The take store: a folder outside every project where this machine keeps a second copy of every take "
        "it buys and the words its voice sent with it, each named by its input digest. A take is looked for "
        "here after `takes_dir`, and one found here is checked and copied into `takes_dir`, so a deck with the "
        "same words as another plays it without buying it, and a take nobody committed yet survives a "
        "`git clean`. Each take is written here once, and a store that already holds a good copy keeps it. "
        "It is empty for the `takes` folder in the per-user data folder, such as "
        "`~/Library/Application Support/decktalk/takes` on macOS.",
        unit="path",
        scope=Scope.MACHINE,
        nature=Nature.APPARATUS,
        hazard=(
            "It holds the spoken words of every script voiced on this machine, so keep it out of a synced or "
            "shared folder. A relative path, a folder inside the project or one inside the tool cache is "
            "refused when the project loads. Write an absolute path or one that starts with ~."
        ),
        see_also=("narration.takes_dir", "tools.cache_dir"),
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
    page_policy: str = tune(
        "trusted",
        "How far `record`, `check` and `storyboard` trust a page. `trusted` lets a page reach the network as "
        "a browser would. `untrusted` turns the Chromium sandbox on and refuses every request that is not "
        "for the project's own origin, through every channel a page can open. It is the machine's to set, "
        "so a project someone else wrote cannot trust its own page.",
        bounds=Bounds(enum=PAGE_POLICIES),
        scope=Scope.MACHINE,
        nature=Nature.APPARATUS,
        hazard=(
            "A service that renders pages other people wrote sets `untrusted` on its machine, because a "
            "trusted page can reach anything the machine can, including a cloud metadata endpoint."
        ),
    )
    concurrency: int = tune(
        0,
        "How many page sections `record` records at once. Zero chooses from the CPU this process may use, "
        "which inside a container is its quota rather than the host's core count.",
        bounds=Bounds(ge=0, le=16),
        scope=Scope.MACHINE,
        nature=Nature.APPARATUS,
        hazard=(
            "Each recording needs about two dedicated CPUs to present its frames on time, and a starved "
            "recording stalls or lands its reveals late, so a number above the machine's share costs "
            "correctness rather than only speed."
        ),
    )
    retries: int = tune(
        2,
        "How many more times `record` records a section whose frames stalled.",
        bounds=Bounds(ge=0, le=10),
        see_also=("record.frame_gap_max_ms",),
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
        decides=(Code.RECORD_TRUNCATED,),
    )
    frame_gap_max_ms: int = tune(
        150,
        "Longest gap between two presented frames after narration t=0. A longer gap makes `record` try again.",
        unit="milliseconds",
        bounds=Bounds(ge=REPORT_FRAME_GAP_MS, le=2000),
        requires="record.frame_gap_max_ms >= REPORT_FRAME_GAP_MS",
        decides=(Code.RECORD_STALLED,),
        hazard=(
            "The runtime reports a gap no finer than its own reporting floor, so a limit under that floor "
            "makes every section stall and every retry spend the recording again."
        ),
        see_also=("REPORT_FRAME_GAP_MS", "record.retries"),
    )


@dataclass(frozen=True)
class AudioConfig:
    """These keys are the final audio: its encoding, and the loudness it is normalised to and judged against."""

    bitrate: str = tune(
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
        "The loudness range the measurement is made against. It is not a limit, and no mix fails on it.",
        unit="LU",
        bounds=Bounds(ge=1, le=20),
    )


@dataclass(frozen=True)
class VerifyConfig:
    """These keys are the limits `verify` judges the assembled mp4 against."""

    after_dip_seconds: float = tune(
        0.2,
        "Seconds after a section start to the frame that the start check reads.",
        unit="seconds",
        bounds=Bounds(ge=0, le=2),
        decides=(Code.RECORD_BLACK,),
    )
    reference_lead_extra_ms: float = tune(
        0.0,
        "Extra lead added to the reference frame beyond the one the offset limit implies.",
        unit="milliseconds",
        bounds=Bounds(ge=0, le=80),
        decides=(Code.CUE_OFF,),
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
        decides=(Code.CUE_NO_CHANGE, Code.CUE_THIN_CHANGE, Code.PAGE_THIN_DRAW),
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
        "A passing cue whose changed share or margin is under this many times its floor is a warning "
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
        decides=(Code.RECORD_BLACK,),
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
    """These keys are the three every voice has: which provider reads the script, in which voice and at what pace.

    Everything a provider is particular about, its own fields, its model and its rate, is in that
    provider's own table, so changing `provider` never sends one vendor's fields or model to another.
    """

    provider: str = tune(
        "elevenlabs",
        "The speech provider that reads the script, which is a name the machine's own map answers. An "
        "unregistered name fails when `narrate` runs rather than at load. DeckTalk ships `elevenlabs`, the "
        "cloud voice whose own keys are `[elevenlabs]`, and `dtsp`, a voice served by a local server, whose "
        "own keys are `[dtsp]`.",
    )
    id: str = tune(
        "",
        "The voice that reads the script, as the provider names it. It is a published name rather than a "
        "credential, so it is committed with the project, and it is one of the inputs every take is named by. "
        "`DECKTALK_VOICE_ID` overrides it for anyone who keeps it out of the file.",
        bounds=Bounds(pattern=VOICE_ID),
        hazard=(
            "The id is placed in the path of every speech request, so a value that could hold a slash, a dot "
            "or a query would let a project somebody else wrote send the key to another endpoint of the service."
        ),
    )
    speed: float = tune(
        1.0,
        "How fast the voice speaks, as a multiple of its natural pace.",
        bounds=Bounds(ge=0.7, le=1.2),
        hazard="The provider refuses a multiple outside its own range, so a slower read is a script change "
        "rather than a setting.",
    )


@dataclass(frozen=True)
class MixConfig:
    """The settings half of `[mix]`, which is how each layer is ramped. Its files and levels are project content."""

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
        decides=(Code.PAGE_MOTION_OVERRUN, Code.PAGE_STAGGER_OVERRUN),
        hazard=(
            "A scale above one slows motion down, and a span slowed past the measurable ceiling makes its "
            "own cue unmeasurable, so each scaled span is clamped at that ceiling rather than obeyed."
        ),
        see_also=("motion.reduce", "MEASURABLE_SPAN_SECONDS"),
    )


BASE_URL_HAZARD = (
    "The script, and for a paid voice the key, travels to whatever host this names, so only the machine names "
    "it and a project file that sets it is refused."
)
"""Why a speech or sound base URL is the machine's to set, said once for every table that holds one."""


ELEVENLABS_MP3_FORMATS = (
    "mp3_22050_32",
    "mp3_44100_32",
    "mp3_44100_64",
    "mp3_44100_96",
    "mp3_44100_128",
    "mp3_44100_192",
)
"""Truth: the mp3 formats ElevenLabs speech returns, each `codec_rate_bitrate`, which every take ffmpeg reads is in."""


@dataclass(frozen=True)
class ElevenLabsConfig:
    """These keys are ElevenLabs's own: its speech fields, its model, its rate, its output format and its API.

    The key it is bought with is `ELEVENLABS_API_KEY`, and `base_url` is the machine's to set. The
    score stage buys from the same API with the same key.
    """

    model: str = tune(
        "eleven_multilingual_v2",
        "The ElevenLabs model that reads the script when `[voice] provider` is `elevenlabs`.",
    )
    stability: float = tune(
        0.55,
        "How closely the voice holds one delivery across takes.",
        bounds=A_SHARE,
        hazard="ElevenLabs takes a share and refuses anything else, so a value read as a percentage fails "
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
        "Whether ElevenLabs applies its own speaker boost to the take.",
    )
    dollars_per_1000_characters: float = tune(
        0.0,
        "What this project's ElevenLabs plan charges in US dollars per thousand characters of speech.",
        unit="US dollars per 1000 characters",
        bounds=Bounds(ge=0, le=100),
        source=Source.STATED,
        evidence="the plan page of the account whose key this project uses",
        hazard=(
            "It is zero until somebody states it, and a spend cap refuses a run while the price is still "
            "the default, because DeckTalk would otherwise be capping a spend against a number it invented. "
            "ElevenLabs bills per character, so a zero somebody stated still asks before it buys."
        ),
    )
    output_format: str = tune(
        "mp3_44100_128",
        "Audio format ElevenLabs returns each take in, as its own codec_rate_bitrate token. It is part of "
        "every take's digest, and its codec names the take's file.",
        bounds=Bounds(enum=ELEVENLABS_MP3_FORMATS),
        hazard="Every take already bought was bought in the format named here, so another one buys every take again.",
    )
    base_url: str = tune(
        "https://api.elevenlabs.io/v1",
        "Base URL of the ElevenLabs API, which the voice and the score both send their requests and the key to.",
        scope=Scope.MACHINE,
        nature=Nature.APPARATUS,
        hazard=BASE_URL_HAZARD,
    )


@dataclass(frozen=True)
class DtspConfig:
    """These keys are the `dtsp` voice's own: where its local server listens and the model it reads with.

    `dtsp` speaks the DeckTalk speech protocol to `decktalk-voice`, a separate local server that is
    not part of this repository. It needs no key, bills nothing, and `base_url` is the machine's to set.
    """

    base_url: str = tune(
        "http://127.0.0.1:8765",
        "Base URL of the local speech server, which the script is sent to.",
        scope=Scope.MACHINE,
        nature=Nature.APPARATUS,
        hazard=BASE_URL_HAZARD,
    )
    model: str = tune(
        "kokoro-82m",
        "The model the local server reads the script with when `[voice] provider` is `dtsp`, as the server names it.",
    )


SOUND_PRICE_HAZARD = (
    "It is zero until somebody states it, and a spend cap refuses a score while any rate it buys at is "
    "still the default, because DeckTalk would otherwise be capping a spend against a number it invented."
)
"""Why a sound rate nobody stated refuses a cap, said once for the three tables that state one."""


@dataclass(frozen=True)
class AmbienceConfig:
    """The settings half of `[score.ambience]`: how the bed is asked for. Its prompt and file are content."""

    model: str = tune("eleven_text_to_sound_v2", "Model the ambience bed is asked for from.")
    duration_seconds: float = tune(
        25.0,
        "Length of the ambience bed that is asked for, which `[mix]` loops under its sections.",
        unit="seconds",
        bounds=Bounds(ge=1, le=60),
    )
    prompt_influence: float = tune(
        0.3,
        "How closely the ambience bed follows its prompt.",
        bounds=A_SHARE,
    )
    dollars_per_minute: float = tune(
        0.0,
        "What this project's sound plan charges in US dollars per minute of ambience audio, which is priced by "
        "the second of audio asked for.",
        unit="US dollars per minute of audio",
        bounds=Bounds(ge=0, le=100),
        source=Source.STATED,
        evidence="the plan page of the account whose key buys the sound",
        hazard=SOUND_PRICE_HAZARD,
        see_also=("score.provider",),
    )


@dataclass(frozen=True)
class EffectsConfig:
    """The settings half of `[score.effects]`: how each effect is asked for unless its own table says."""

    model: str = tune(
        "eleven_text_to_sound_v2", "Model every effect is asked for from, unless its own table names one."
    )
    duration_seconds: float = tune(
        0.5,
        "Length of every effect that is asked for, unless its own table sets one.",
        unit="seconds",
        bounds=Bounds(ge=0.1, le=30),
    )
    prompt_influence: float = tune(
        0.5,
        "How closely every effect follows its prompt, unless its own table sets how closely.",
        bounds=A_SHARE,
    )
    dollars_per_minute: float = tune(
        0.0,
        "What this project's sound plan charges in US dollars per minute of effect audio, which is priced by "
        "the second of audio asked for.",
        unit="US dollars per minute of audio",
        bounds=Bounds(ge=0, le=100),
        source=Source.STATED,
        evidence="the plan page of the account whose key buys the sound",
        hazard=SOUND_PRICE_HAZARD,
        see_also=("score.provider",),
    )


@dataclass(frozen=True)
class MusicConfig:
    """The settings half of `[score.music]`: how the bed is asked for and joined. Its prompt is content."""

    model: str = tune("music_v2", "Model the music is asked for from.")
    duration_seconds: int = tune(
        360,
        "Length of the music that is asked for, which `[mix]` fades out at the end of the film.",
        unit="seconds",
        bounds=Bounds(ge=1, le=3600),
    )
    bitrate: str = tune(
        "192k",
        "Bitrate of the music file that `score` joins from its chunks.",
        bounds=Bounds(enum=("96k", "128k", "160k", "192k", "256k", "320k")),
    )
    max_chunk_seconds: int = tune(
        300,
        "Longest music request. Longer music is requested in chunks and crossfaded.",
        unit="seconds",
        bounds=Bounds(ge=30, le=600),
        nature=Nature.APPARATUS,
    )
    crossfade_seconds: int = tune(
        2,
        "Crossfade between two music chunks.",
        unit="seconds",
        bounds=Bounds(ge=0, le=30),
    )
    dollars_per_minute: float = tune(
        0.0,
        "What this project's sound plan charges in US dollars per minute of music audio, which is priced by "
        "the second of audio asked for.",
        unit="US dollars per minute of audio",
        bounds=Bounds(ge=0, le=100),
        source=Source.STATED,
        evidence="the plan page of the account whose key buys the sound",
        hazard=SOUND_PRICE_HAZARD,
        see_also=("score.provider",),
    )


@dataclass(frozen=True)
class ScoreConfig:
    """The settings half of `[score]`: how the music, the ambience and the effects are asked for.

    Each item's own table holds its prompt and its file, which are project content, beside the
    settings that size its request, so a key is spelled the same way the item it sizes spells it.
    """

    dir: str = tune(
        "score",
        "Directory inside the project that holds every sound the score bought and the ledger that records "
        "them, each named by its item, which you commit so a fresh clone mixes the film with no key. Music "
        "bought in parts keeps its parts here, and the piece joined from them is a cache under the build.",
        unit="path",
        bounds=Bounds(pattern=SOME_PATH),
        hazard=(
            "An empty value, a directory outside the project, an absolute path, the project directory itself, a "
            "directory inside the build directory or one that holds it is refused when the project loads, "
            "because a bought sound must land in a folder the project keeps, and a project someone else wrote "
            "would otherwise choose where this machine writes what it buys."
        ),
        see_also=("narration.takes_dir",),
    )
    provider: str = tune(
        "elevenlabs",
        "The sound provider the music, the ambience and the effects are bought from, which is a name the "
        "machine's own sound table answers. ElevenLabs is the only sound provider DeckTalk ships, and it buys "
        "with the key and `base_url` of `[elevenlabs]`.",
        see_also=("elevenlabs.base_url",),
    )
    format: str = tune(
        "mp3_44100_128",
        "Audio format the music, the ambience and the effects are asked for in. A take's format is its "
        "voice's own, such as `[elevenlabs] output_format`.",
        see_also=("elevenlabs.output_format",),
    )
    timeout_seconds: int = tune(
        600,
        "Seconds before a sound or music request times out.",
        unit="seconds",
        bounds=Bounds(ge=10, le=3600),
        nature=Nature.APPARATUS,
    )
    ambience: AmbienceConfig = field(default_factory=AmbienceConfig)
    music: MusicConfig = field(default_factory=MusicConfig)
    effects: EffectsConfig = field(default_factory=EffectsConfig)


type ProviderTable = ElevenLabsConfig | DtspConfig
"""The table a speech provider DeckTalk ships owns, one per adapter in the closed set.

Every one carries the provider's own fields and its default `model`, and its adapter declares which
key holds its base URL and which states its rate, so a reader above the speech layer asks the
adapter's declaration and never names a vendor.
"""


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
    events_max_bytes: int = tune(
        8_388_608,
        "Bytes one run's events file may reach before debug and info lines are left out of it. "
        "Lines about the run, its stages, its sections, its findings and its costs are always kept.",
        unit="bytes",
        bounds=Bounds(ge=65_536, le=1_073_741_824),
        scope=Scope.MACHINE,
        nature=Nature.APPARATUS,
        see_also=("output.events_keep_runs",),
        hazard="A file with no bound grows with every tool call a long film makes, on a disk a host shares.",
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
    timeout_seconds: float = tune(
        600.0,
        "Longest one ffmpeg or ffprobe call, or one wait on another fetch of ffmpeg, may run before it is stopped.",
        unit="seconds",
        bounds=Bounds(ge=10, le=7200),
        scope=Scope.MACHINE,
        nature=Nature.APPARATUS,
    )
    cache_dir: str = tune(
        "",
        "Directory the fetched Chromium and ffmpeg builds live in, Chromium in its ms-playwright folder. "
        "It is empty for the standard per-user cache.",
        unit="path",
        scope=Scope.MACHINE,
        nature=Nature.APPARATUS,
        hazard="A relative path is refused when the machine loads. Write an absolute path or one that starts with ~.",
        see_also=("narration.store_dir",),
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
    dtsp: DtspConfig = field(default_factory=DtspConfig)
    score: ScoreConfig = field(default_factory=ScoreConfig)
    output: OutputConfig = field(default_factory=OutputConfig)
    tools: ToolsConfig = field(default_factory=ToolsConfig)


KEYS: tuple[Key, ...] = registry(Settings)
"""Every settings key in declaration order, which is the one list every surface renders."""

BY_ID: dict[str, Key] = {key.id: key for key in KEYS}
"""Every key by the dotted name a diagnostic prints, `config set` takes and `--set` spells."""

SHARED_TABLES = frozenset(("mix", "score", "score.ambience", "score.effects", "score.music"))
"""The tables that hold settings beside project content, so the settings loader warns for neither.

The document parser owns these tables' unknown keys, because only it knows the content half.
"""

DOCUMENT_TABLES = ("project", "section", "transition")
"""The tables of `decktalk.toml` that are project content rather than tuning, named so a refusal can say so.

`mix` and `score` are missing on purpose: each holds settings declared above beside content
keys the document owns, so neither is wholly one thing.
"""

MACHINE_FILE = "machine.toml"
"""The per-machine settings file's name, which is not the project's so the two files are never confused."""

MACHINE_FILE_VARIABLE = "DECKTALK_MACHINE_FILE"
"""The variable that names a per-machine settings file other than the standard one."""

STANDALONE_ENV = frozenset(("DECKTALK_PROJECT", MACHINE_FILE_VARIABLE))
"""The two variables DeckTalk reads that name no key. Every other DECKTALK_ name is a key or a typo."""


@dataclass(frozen=True)
class Number:
    """One number that is deliberately not a setting, published with its formula and its reason.

    A derived number is written as its expression and never as its value, so a reader who goes
    looking for a setting that used to exist meets the arithmetic instead of nothing. A constant is a
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
    Number.fixed(
        "CAPTURE_FPS",
        CAPTURE_FPS,
        "frames per second",
        Nature.TRUTH,
        "Truth: the rate the recorder captures at, which DeckTalk cannot set and so never asks for.",
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


class Layers(Model):
    """What every layer said about every key, which is the record `config explain` renders.

    It is built at load and again at reload, so a watch loop that sees an edited `decktalk.toml`
    sees the layer that set each key move with it. A finding that names a setting quotes the winning
    row, because a value without its layer cannot tell a deliberate choice from a default.
    """

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


def machine_config_path(environ: Mapping[str, str], home: Path, platform: str = sys.platform) -> Path:
    """The per-machine settings file this environment names, which DECKTALK_MACHINE_FILE moves.

    The environment and the home directory are arguments, because the machine that owns them is the
    one reader of the process, and a host that builds its machine by hand names its own file.
    """
    override = environ.get(MACHINE_FILE_VARIABLE)
    if override:
        return Path(override)
    if platform == "darwin":
        root = home / "Library" / "Application Support"
    elif platform == "win32":
        root = Path(environ.get("APPDATA") or home / "AppData" / "Roaming")
    else:
        root = Path(environ.get("XDG_CONFIG_HOME") or home / ".config")
    return root / "decktalk" / MACHINE_FILE


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


WHERE_SCOPE_BELONGS = {
    Scope.MACHINE: "this machine's file, where the machine that runs the build states what it runs",
    Scope.PROJECT: f"the project's {PROJECT_FILE}, where the film that ships carries it",
}
"""Where each scope's keys are written, which is the sentence a refusal of a misplaced key ends on."""


def refuse_off_scope(data: Mapping[str, Any], allowed: Scope, *, file: Path, text: str | None = None) -> None:
    """Refuse the first key in `data` that belongs to a scope other than `allowed`.

    The refusal is symmetric. A per-machine file cannot carry a key about the film, and a project
    cannot carry a key about the machine, because those keys name executables and directories the
    machine trusts. A project that someone else wrote would otherwise choose the program DeckTalk
    launches as the browser, so a machine-scoped key in a project is refused rather than ignored.
    """
    for dotted, _value in _flatten(data):
        key = BY_ID.get(dotted)
        if key is None or key.scope is allowed:
            continue
        where = key.scope.value
        raise InputError(
            f"{file.name}: '{dotted}' is {where}-scoped, so it belongs in {WHERE_SCOPE_BELONGS[key.scope]}.",
            hint=f"Remove it from {file.name} and run `decktalk config set {dotted} <value> --scope {where}`.",
            location=Location(
                where=f"[{key.table}] {key.name}",
                file=file,
                line=locate(text, dotted) if text is not None else None,
            ),
        )


def read_machine_toml(path: Path) -> dict[str, Any]:
    """The per-machine tuning tables, refusing any key that belongs in the project instead.

    The file is restricted by key and not by table, because a limit is a statement about the film
    and the file ships whatever the runner believes. A project-scoped key found here is refused by
    name rather than warned about, since a warning would put a correctly spelled key in a weaker
    class than a typo and a reader of the JSON never sees a log line at all.
    """
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
    refuse_off_scope(data, Scope.MACHINE, file=path, text=text)
    return data


def key_warnings(doc: Mapping[str, Any], where: str) -> list[str]:
    """One warning per key inside a tuning table that DeckTalk does not read, with a near name when there is one.

    The near name is looked for in the same table first and then across every table, because a key
    that moved is still written under the table it used to live in, and no old name is read for it.
    A table that also holds project content, such as `[mix]`, is left alone, because the document
    parser owns the rest of that table and warning here would call one of its keys unknown.
    """
    out: list[str] = []
    tables = {key.table for key in KEYS}
    for table in sorted(tables - SHARED_TABLES):
        found = _table(doc, table)
        if found is None:
            continue
        known = {key.name for key in KEYS if key.table == table} | {
            inner.removeprefix(f"{table}.").split(".")[0] for inner in tables if inner.startswith(f"{table}.")
        }
        out += unknown_key_warnings(found, known, f"{where}: [{table}]", at=table, anywhere=BY_ID)
    return out


def env_warnings(environ: Mapping[str, str]) -> list[str]:
    """One warning per DECKTALK_ variable DeckTalk does not read, naming the closest one it does.

    A variable DeckTalk does not read has no effect, so the warning is what tells a reader that a
    typed name never took hold.
    """
    known = {key.environment for key in KEYS} | STANDALONE_ENV
    return [
        _unknown_variable(name, known)
        for name in sorted(n for n in environ if n.startswith("DECKTALK_") and n not in known)
    ]


def _unknown_variable(name: str, known: set[str]) -> str:
    """The warning for one variable, read as the key it spells so a key that moved tables is found.

    The variable is read as a key of the longest table its name opens with, and the key that one
    names, by the rule the files' own warnings use, is offered by its variable. A name that opens
    with no table is offered the closest variable by spelling.
    """
    spelled = name.removeprefix("DECKTALK_").lower()
    tables = sorted({key.table for key in KEYS}, key=len, reverse=True)
    table = next((t for t in tables if spelled.startswith(t.replace(".", "_") + "_")), None)
    if table is None:
        return unknown_key_message(name, known, "environment")
    dotted = f"{table}.{spelled.removeprefix(table.replace('.', '_') + '_')}"
    meant = named_key(dotted, BY_ID)
    if meant is None:
        return unknown_key_message(name, known, "environment")
    return f"environment: ignoring unknown key '{name}' (did you mean '{BY_ID[meant].environment}'?)."


def _table(doc: Mapping[str, Any], dotted: str) -> Mapping[str, Any] | None:
    """One nested table of a parsed document by its dotted name, or null when it is not there."""
    found = stated(doc, dotted)
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
                f"'{key}' is project content rather than a setting, so no override can set it.",
                hint=f"Edit [{key.split('.')[0]}] in {PROJECT_FILE} instead.",
            )
        key_named(key)
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
    environ: Mapping[str, str],
    overrides: tuple[str, ...] = (),
) -> Loaded:
    """Every key resolved through the five layers, with the record of which layer set each one.

    An override is spelled the way an environment variable is, a string the key's own type reads,
    so one conversion serves both and an override cannot be admitted by a route the environment is
    refused by. It sits above the environment because it is given for one run on purpose.

    The environment is required and never read from the process, because the machine is the one
    reader of the process and a host that built its machine by hand chose what it holds. The machine
    layer is the tables given, or the file at `machine_path`, or nothing when neither is named.
    """
    env = dict(environ)
    from_machine = dict(machine) if machine is not None else (read_machine_toml(machine_path) if machine_path else {})
    from_project = dict(project) if project is not None else (read_project_toml(root) if root else {})
    project_file = (root / PROJECT_FILE) if root else Path(PROJECT_FILE)
    project_text = project_file.read_text(encoding="utf-8") if root and project_file.is_file() else None
    refuse_off_scope(from_project, Scope.PROJECT, file=project_file, text=project_text)
    pairs = route(overrides)
    base = merge_tables(from_machine, from_project)
    env_and_overrides = {**env, **{BY_ID[key].environment: value for key, value in pairs.items()}}
    settings = from_mapping(Settings, base=base, prefixes=[ENV_PREFIX], environ=env_and_overrides)
    _require(settings)
    files = {
        Layer.MACHINE: machine_path,
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
    texts = {layer: current_text(path) if path else "" for layer, path in files.items()}
    rows: dict[str, tuple[LayerValue, ...]] = {}
    for key in KEYS:
        found = [LayerValue(layer=Layer.DEFAULT, value=json_value(key.default))]
        for layer, doc in ((Layer.MACHINE, machine), (Layer.PROJECT, project)):
            said = stated(doc, key.id)
            if said is not ABSENT:
                path = files.get(layer)
                found.append(
                    LayerValue(
                        layer=layer,
                        value=json_value(said),
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
        found[-1] = found[-1].model_copy(update={"value": json_value(value_of(settings, key.id))})
        rows[key.id] = tuple(found)
    return Layers(rows=rows)


ABSENT = object()
"""The answer to a lookup for a key a layer never stated, which None cannot be because None is a value."""


def stated(doc: Mapping[str, Any], dotted: str) -> object:
    """What one layer's document says about one key, or `ABSENT` when it says nothing."""
    found: object = doc
    for part in dotted.split("."):
        if not isinstance(found, Mapping) or part not in found:
            return ABSENT
        found = found[part]
    return found


json_value: Callable[[object], JsonValue] = functools.partial(to_jsonable_python, fallback=str)
"""One value as JSON carries it: a tuple as a list, an enum as its value, and anything else unknown as its text."""


HOME_VARIABLES = ("HOME", "USERPROFILE")
"""Where a machine's environment names its home directory, which a folder that starts with `~` is under."""


def machine_folder(loaded: Loaded, key: str, environ: Mapping[str, str]) -> Path | None:
    """The folder a machine key names, with `~` expanded to the machine's home, or None when it names none.

    A machine folder belongs to no project, so a relative one has nothing sensible to be relative to:
    read against each project it would put one folder inside every project, and read against the
    working directory it would move with the shell. Only an absolute path or one under `~` is read, and
    any other is refused naming the layer that set it, which for a machine file is the file itself.
    """
    named = str(value_of(loaded.settings, key))
    if not named:
        return None
    said = loaded.layers.winner(key)
    if said.layer is Layer.ENVIRONMENT:
        source = BY_ID[key].environment
    else:
        source = str(said.file) if said.file is not None else f"the {said.layer.value} layer"
    table, name = key.rsplit(".", 1)
    spelled = f"[{table}] {name} is {named!r} in {source}"
    location = Location(where=f"[{table}] {name}", file=said.file, line=said.line)
    if named == "~" or named.startswith(("~/", "~\\")):
        home = next((environ[variable] for variable in HOME_VARIABLES if environ.get(variable)), None)
        if home is None:
            raise InputError(
                f"{spelled}, which starts with ~, and this machine names no home directory to read it under.",
                hint="Write the folder as an absolute path.",
                location=location,
            )
        return Path(home, named[2:])
    if not Path(named).is_absolute():
        raise InputError(
            f"{spelled}, which is relative, so it would name a different folder for every project and every "
            "working directory.",
            hint=f"Write an absolute path or one that starts with ~, such as ~/{name.removesuffix('_dir')}.",
            location=location,
        )
    return Path(named)


def value_of(settings: Settings, dotted: str) -> object:
    """The value one dotted key holds in a settings tree."""
    return operator.attrgetter(dotted)(settings)


def effective(settings: Settings, name: str) -> object:
    """One input of a relation or a formula at its effective value, whether it is a key or a published number."""
    return value_of(settings, name) if name in BY_ID else NUMBERS_BY_ID[name].at(settings)


def _require(settings: Settings) -> None:
    """Every cross-table relation a key declares, enforced once the whole tree is built.

    A relation is a comparison an editor cannot check, because one side of it lives in another
    table or is a published number rather than a key, so the loader is the only place it can hold.
    """
    for key in KEYS:
        if key.requires is None:
            continue
        left, op, right = key.requires.split()
        if not COMPARISONS[op](_side(settings, left), _side(settings, right)):
            raise InputError(
                f"{key.id} must satisfy {key.requires}, and it does not.",
                hint=key.hazard,
            )


def _side(settings: Settings, token: str) -> float:
    """One side of a declared relation, which is a key, a published number or a literal."""
    if token in BY_ID or token in NUMBERS_BY_ID:
        return float(cast("float", effective(settings, token)))
    return float(token)


COMPARISONS = {">=": operator.ge, "<=": operator.le, ">": operator.gt, "<": operator.lt}
"""The four comparisons a declared relation may use."""


def not_a_key(key: str) -> InputError:
    """The one refusal of a name no settings key carries, with the nearest key when one is near."""
    return InputError(
        f"'{key}' is not a settings key.{did_you_mean(key, BY_ID)}",
        hint="Run `decktalk schema settings` for every key DeckTalk reads.",
    )


def key_named(key: str) -> Key:
    """The settings key called `key`, or the one refusal every reader of a key's name gives."""
    known = BY_ID.get(key)
    if known is None:
        raise not_a_key(key)
    return known


def parse_value(key: Key, text: str) -> object:
    """One value as a command line spells it, read as the key's own type and held to its range.

    A value that reaches `config set` and a value that reaches `--set` are the same string, so both
    are read here and both meet the same refusal.
    """
    return read_value(key.annotation, text, where=key.id, bounds=key.bounds, hazard=key.hazard, from_env=True)


def _scoped_key(key: str, scope: Scope, *, action: str, rerun: str) -> Key:
    """The key a write or a removal names, refused when no key has that name or it belongs in the other file."""
    known = key_named(key)
    if known.scope is not scope:
        raise InputError(
            f"'{key}' is {known.scope.value}-scoped, so it cannot be {action} the {scope.value} file.",
            hint=f"Run `{rerun} --scope {known.scope.value}`.",
        )
    return known


@dataclass(frozen=True)
class Edited:
    """One settings file's text with one key set in it, and what that key held before.

    It is text rather than a written file, because a fix that also edits lines of the same file
    stages both changes before either lands, and the whole file is judged once they are all made.
    """

    text: str
    value: object
    previous: object


def edit(text: str, key: str, value: str, *, scope: Scope, file: Path) -> Edited:
    """Set one key in the text of one settings file, keeping every comment the file already has.

    The key is refused when no key has that name, when it belongs in the other file, or when the
    value is not one the key takes. The document is edited rather than rewritten, because a person
    wrote the comments around the key and a writer that dumped a parsed tree would delete them the
    first time an agent changed a setting. The text that comes back is not yet validated as a whole,
    because a caller may have more changes to make to it first.
    """
    known = _scoped_key(key, scope, action="written to", rerun=f"decktalk config set {key} {value}")
    typed = parse_value(known, value)
    document = _document(text, file)
    previous = stated(document, key)
    _put(document, key.split("."), typed)
    return Edited(text=tomlkit.dumps(document), value=typed, previous=previous)


def write(
    path: Path,
    key: str,
    value: str,
    *,
    scope: Scope,
    environ: Mapping[str, str],
    dry_run: bool = False,
) -> ConfigSetResult:
    """Set one key in one file, through the whole loader, keeping every comment the file already has.

    The would-be file is built first and loaded whole, so a value that no run could use never lands
    and the refusal a caller meets is the loader's own, with its file, its line and its near name.
    The file is then replaced whole rather than written in place, so a write that fails leaves it as
    it was.

    `environ` is the machine's environment, which is the layer over the file that decides whether
    the value written is the value in force. The answer is `config set`'s own result, so the command
    renders what the library returns.
    """
    target = _target(path, scope)
    edited = edit(current_text(target), key, value, scope=scope, file=path)
    validate(edited.text, path, scope)
    if not dry_run:
        replace_all({target: edited.text})
    # A write that a higher layer shadows changes the file and not the run, so the result says so
    # rather than reporting a new value the next command will not use.
    tree = _in_force(path, scope, {key: edited.value}, environ)
    return ConfigSetResult(
        ok=True,
        written=() if dry_run else (path,),
        key=key,
        value=json_value(edited.value),
        previous=None if edited.previous is ABSENT else json_value(edited.previous),
        scope=scope,
        file=path,
        dry_run=dry_run,
        effective=json_value(value_of(tree.settings, key)),
        layer=tree.layers.winner(key).layer,
    )


def unset(path: Path, key: str, *more: str, scope: Scope, environ: Mapping[str, str]) -> ConfigUnsetResult:
    """Take one key, or several, out of one file, so the layer below each decides again.

    This is the writer's opposite and it is built the same way: the would-be file is loaded whole
    before a byte lands, so a removal that breaks a relation between two keys never reaches the
    disk, and the document is edited rather than rewritten so the comments a person wrote around the
    key survive. Several keys are one edit and one write, so a table is removed whole or not at all.
    A key the file never stated is taken out of nothing and the call says so, which is what lets an
    agent that cannot read the file call this twice. `previous`, `effective` and `layer` describe
    the first key.
    """
    keys = (key, *more)
    for one in keys:
        _scoped_key(one, scope, action="taken out of", rerun=f"decktalk config unset {one}")
    target = _target(path, scope)
    document = _document(current_text(target), path)
    stating = [one for one in keys if stated(document, one) is not ABSENT]
    previous = stated(document, key)
    for one in stating:
        _take(document, one.split("."))
    if stating:
        text = tomlkit.dumps(document)
        validate(text, path, scope)
        replace_all({target: text})
    tree = _in_force(path, scope, {}, environ)
    return ConfigUnsetResult(
        ok=True,
        written=(path,) if stating else (),
        keys=keys,
        previous=None if previous is ABSENT else json_value(previous),
        scope=scope,
        file=path,
        effective=json_value(value_of(tree.settings, key)),
        layer=tree.layers.winner(key).layer,
    )


def _target(path: Path, scope: Scope) -> Path:
    """The file a settings change replaces, which is `path` with every link followed.

    A link is followed so a machine file kept in a dotfiles repository stays where its owner keeps
    it. A project file that leads out of the project is refused, because a project someone else
    wrote would otherwise choose a file elsewhere on the machine for a fix or `config set` to write.
    A hard link needs no refusal, because the change replaces the file rather than writing into it,
    so the other name keeps its own contents.
    """
    target = path.resolve()
    if scope is Scope.PROJECT and not target.is_relative_to(path.parent.resolve()):
        raise InputError(
            f"{path.name} leads outside the project, so DeckTalk writes nothing through it.",
            hint=f"Replace the link at {path.name} with the file itself.",
            location=Location(where=path.name, file=Path(path.name)),
        )
    return target


def _document(text: str, file: Path) -> tomlkit.TOMLDocument:
    """A settings file parsed for editing, or the loader's own refusal when it is not valid TOML."""
    try:
        return tomlkit.parse(text)
    except ParseError as exc:
        raise InputError(
            f"{file.name} is not valid TOML: {exc}.",
            hint="Fix the line this message names, which is usually a quote or a bracket left open.",
            location=Location(where=file.name, file=file, line=exc.line),
        ) from exc


def _put(document: MutableMapping[str, Any], parts: list[str], value: object) -> None:
    """One key set in a parsed document, adding the tables it sits in when they are not there yet."""
    table = document
    for part in parts[:-1]:
        if part not in table:
            table[part] = tomlkit.table()
        table = cast("MutableMapping[str, Any]", table[part])
    table[parts[-1]] = value


def _take(document: MutableMapping[str, Any], parts: list[str]) -> None:
    """One key taken out of a parsed document, leaving the table it sat in where it was.

    A table that the removal empties stays, because a person's comments live around the table and a
    remover that deleted it would delete the sentences they wrote the first time they cleared a key.
    """
    table = document
    for part in parts[:-1]:
        table = cast("MutableMapping[str, Any]", table[part])
    del table[parts[-1]]


def validate(text: str, path: Path, scope: Scope) -> None:
    """The whole settings tree built on the would-be file, so a bad value never reaches the disk.

    A fix may also edit the lines of the file around a key, so the text is parsed here as well as
    loaded, and a line edit that breaks the TOML is refused as the loader would refuse it.
    """
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise InputError(
            f"{path.name} would not be valid TOML: {exc}.",
            hint="The change was not made, so the file is as it was.",
            location=Location(where=path.name, file=path, line=refused_line(exc)),
        ) from exc
    if scope is Scope.MACHINE:
        refuse_off_scope(data, Scope.MACHINE, file=path, text=text)
    load(
        machine=data if scope is Scope.MACHINE else {},
        project=data if scope is Scope.PROJECT else {},
        environ={},
    )


def _in_force(path: Path, scope: Scope, stated: Mapping[str, object], environ: Mapping[str, str]) -> Loaded:
    """The whole tree as it stands once a write or a removal has landed in the named file.

    Only the key the call touched is handed back to the loader, because no other key in that file
    decides this one: the layer under a settings file is the default, and the layers over it are the
    environment and the run's own overrides. A key is scoped to one file, so the file the call left
    alone states nothing about it either.
    """
    return load(
        machine=nested(stated) if scope is Scope.MACHINE else {},
        project=nested(stated) if scope is Scope.PROJECT else {},
        machine_path=path if scope is Scope.MACHINE else None,
        environ=environ,
    )


def nested(flat: Mapping[str, object]) -> dict[str, Any]:
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
    "AmbienceConfig",
    "AudioConfig",
    "DtspConfig",
    "EffectsConfig",
    "ElevenLabsConfig",
    "Layers",
    "MixConfig",
    "MotionConfig",
    "MusicConfig",
    "NarrationConfig",
    "OutputConfig",
    "ProviderTable",
    "RecordConfig",
    "Settings",
    "ScoreConfig",
    "ToolsConfig",
    "VerifyConfig",
    "VideoConfig",
    "VoiceConfig",
]
