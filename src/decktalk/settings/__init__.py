"""Every setting DeckTalk publishes, with the range that is safe to turn it through.

One dataclass per table, composed into `Settings`. Each field is declared with `tune()`, which
carries everything any surface says about that key: what it changes, what its default is, the
range the loader enforces, the wider range its type would admit, its true unit, which findings it
moves, which file it belongs in, where its value is expected to come from, what a value at the
edge risks and which other key or published number it relates to. Nothing about a key is written
anywhere else, so the JSON Schema, the reference page, `config explain` and a finding that names
a setting are four renderings of one row.

A project key is set by four layers, lowest to highest: the default here, the project's
`decktalk.toml`, the environment variable named `DECKTALK_<TABLE>_<KEY>`, and a `--set
table.key=value` given for one run. A voice is a table the author names, `[voices.NAME]`, so its keys
are a family: one template per key, published under `voices.<name>`, which each declared voice fills
with its own name. A machine key is set by its default, the per-machine file, its
variable and `--set`. `load()` returns both the resolved tree and the record of which layer set each
key, because a caller that is told a value and not its layer cannot tell a deliberate choice from a
default.

Two rules keep the published range honest. The published range is the safe range: a bound is here
because a value past it deletes a check, corrupts the evidence a later stage measures or breaks a
tool, and the loader refuses outside it. A number that is not a setting is published too: every
derived expression and every named constant is in `numbers.NUMBERS` with its formula and its reason,
so a reader who cannot find a setting learns the number is deliberately not one.

    __init__.py   the tables, the key registry and the record of which layer set each key
    numbers.py    every number that is not a setting, with its formula and its reason
    layers.py     the five layers read, merged and held to their scopes, which is `load()`
    voices.py     each voice the project declares, resolved over its provider's table
    edit.py       one key written into or removed from a settings file
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field, fields, replace
from typing import get_type_hints

from pydantic import Field

from decktalk.errors import InputError
from decktalk.findings import Code, Model
from decktalk.results import LayerValue, Scope, SoundKind, Source
from decktalk.tomlmap import A_LUMA, A_PERCENT, A_SHARE, ENV_PREFIX, FAMILY, Bounds, Key, Nature, registry, tune
from decktalk.tomlmap.suggest import did_you_mean

PROJECT_FILE = "decktalk.toml"

X264_PRESETS = ("ultrafast", "superfast", "veryfast", "faster", "fast", "medium", "slow", "slower", "veryslow")
"""Truth: the words x264 accepts, so a wrong one fails at load rather than inside a render."""

COLOR_SCHEMES = ("light", "dark", "no-preference")
"""Truth: the values Chromium reports for `prefers-color-scheme`."""

VOICE_ID = r"^[A-Za-z0-9_-]*$"
"""Truth: the letters a provider's voice id is spelled in, and nothing that could leave a path segment."""

VOICE_NAME = r"^[a-z][a-z0-9]*$"
"""Truth: what a voice is called, so every name spells one shell variable and no two names spell the same one."""

VOICES_TABLE = "voices"
"""The table that holds one table per voice, each named by the voice's name."""

MEMBER = "<name>"
"""What stands for a voice's name in a key the family publishes, such as voices.<name>.id."""

PROVIDER_TABLES = ("elevenlabs", "dtsp")
"""The settings tables a shipped speech provider owns, each named after the provider, which a test holds to DECLARED."""

SOME_PATH = r"^.+$"
"""Truth: a path with at least one character in it, which is what a folder that must exist is named by."""

HEX_COLOR = r"^(#|0[xX])[0-9A-Fa-f]{6}$"
"""Truth: a colour written as six hex digits after the prefix a stylesheet or ffmpeg reads, and nothing else."""

PAGE_POLICIES = ("trusted", "untrusted")
"""The two ways `record` treats a page: as the author's own work, or as a stranger's that may be hostile."""

CAPTURE_SWITCHES = ("disable-frame-rate-limit", "disable-gpu-vsync")
"""Truth: the Chromium switches `record` may launch with, each named as Chromium spells it without its dashes.

They are a closed set because each one is held to recording the same frames as a launch without it,
and a free switch would be a launch nobody held to that.
"""

# The three constants a setting's bounds or relations read. `settings.numbers.NUMBERS` publishes each
# one with its unit and its reason, so the reason is written there alone.
BLOCK_PX = 8
GUARD_FRAMES = 1.5
CLICK_LEVEL_DBFS = -24.0


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
    fps: int = tune(
        25,
        "Frame rate of every recording and of the final mp4. The recorder steps the section clock one frame "
        "of this rate at a time, so every frame of a film is the page at its own second.",
        unit="frames per second",
        bounds=Bounds(enum=(25, 30, 50, 60)),
        requires="video.fps >= SLOWEST_FPS",
        see_also=("SLOWEST_FPS",),
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

    voice: str = tune(
        "narrator",
        "The default voice: the voice that reads every section no voice direction in the script names, and every "
        "turn of a section that names none. It names a [voices.NAME] table, and with no such table written it is a "
        "voice at every default.",
        bounds=Bounds(pattern=VOICE_NAME),
        see_also=("narration.turn_gap_seconds",),
    )
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
        "How many more times `narrate` asks again when the voice provider or the aligner answers that it is busy or "
        "failed.",
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
    turn_gap_seconds: float = tune(
        0.3,
        "Silence between two turns of a section when no timed pause is written where the voice changes. A "
        "[pause N] written there replaces it, and neither is part of either turn's take.",
        unit="seconds",
        bounds=Bounds(ge=0, le=5),
        see_also=("narration.lead_seconds", "narration.tail_seconds"),
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
        "machine's take store, `store_dir`, second, and a take this project voices or finds there is written here.",
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
        "The take store: a folder outside every project where this machine keeps a second copy of every voiced "
        "take it makes and its provider words, each named by its digest. A take is looked for "
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
    context_characters: int = tune(
        1500,
        "Characters of each neighbour section sent with a request, for continuous prosody: the end of the turn "
        "before and the start of the turn after. 0 sends no neighbouring text, so each turn is sent on its own.",
        unit="characters",
        bounds=Bounds(ge=0, le=5000),
    )
    timeout_seconds: int = tune(
        180,
        "Seconds before a speech or alignment request times out.",
        unit="seconds",
        bounds=Bounds(ge=10, le=1800),
        nature=Nature.APPARATUS,
    )
    store_wait_seconds: int = tune(
        900,
        "Seconds a run waits while another run on this machine voices the same take into the take store, before "
        "it is refused as locked. Keep it above one whole take request, `timeout_seconds` times `retries` plus "
        "one, with the waits between them. The default covers that at the default `timeout_seconds` and "
        "`retries`: four attempts of 180 seconds and three waits of at most 30 seconds, 810 seconds.",
        unit="seconds",
        bounds=Bounds(ge=1, le=36000),
        scope=Scope.MACHINE,
        nature=Nature.APPARATUS,
        hazard=(
            "Below the length of one take request, a run is refused while another run's healthy request is still "
            "being answered."
        ),
        see_also=("narration.store_dir", "narration.timeout_seconds", "narration.retries"),
    )
    sound_end_noise_dbfs: float = tune(
        -35.0,
        "Level under which the tail of a take counts as silence, which is where the take's sound ends.",
        unit="dBFS",
        bounds=Bounds(ge=-60, le=-25),
        decides=(Code.CUT_SPEECH, Code.TAKE_WORD_CUT),
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
        decides=(Code.CUT_SPEECH, Code.TAKE_WORD_CUT),
        see_also=("narration.sound_end_noise_dbfs",),
    )


@dataclass(frozen=True)
class RecordConfig:
    """These keys tune how `record` opens each page section in headless Chromium and steps it frame by frame."""

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
            "Each recording steps its own Chromium and its own encode, so a number above the machine's share "
            "makes every section wait on the others and the run slower rather than faster."
        ),
    )
    capture_switches: tuple[str, ...] = tune(
        CAPTURE_SWITCHES,
        "Chromium switches `record` launches its browser with, which stop it holding its frames to the display's "
        "refresh rate, so a recorded frame takes about half the time. A recording with them decodes to the same "
        "frames as one without them. An empty list launches Chromium with none.",
        bounds=Bounds(items=Bounds(enum=CAPTURE_SWITCHES)),
        scope=Scope.MACHINE,
        nature=Nature.APPARATUS,
        hazard=(
            "With the switches no frame waits for a display refresh, so each recording keeps more of the CPU "
            "busy, and a `record.concurrency` set by hand above the machine's share makes the sections wait on "
            "each other."
        ),
        see_also=("record.concurrency",),
    )
    frame_wait_seconds: float = tune(
        15.0,
        "Longest `record` waits for a page to draw one stepped frame, with every font it shows loaded, every "
        "image decoded and the step laid out and painted, before it fails the section rather than capture "
        "the frame half drawn.",
        unit="seconds",
        bounds=Bounds(gt=0, le=120),
        scope=Scope.MACHINE,
        nature=Nature.APPARATUS,
        hazard=(
            "A wait too short for this machine fails sections whose pages are only slow to fetch a font or "
            "decode an image. It never shortens a recording that succeeds, because a page that has drawn "
            "its frame is captured at once."
        ),
    )
    skip_still: bool = tune(
        True,
        "Write a frame again rather than capture it when the page reports that nothing it draws changed "
        "since the frame before, which is most frames of a narrated slide, and write every frame before the "
        "next change the page reports without stepping the page to each. The frames are the same either "
        "way, so it changes how long `record` takes and nothing it writes.",
        nature=Nature.APPARATUS,
        hazard=(
            "A page that changes its picture outside what DeckTalk knows of, such as through its style "
            "sheet's object model or inside a shadow root, records the frame before until something it "
            "knows of changes. Such a page draws from a frame hook or declares a loop, or turns this off."
        ),
        see_also=("record.skip_audit_every",),
    )
    skip_audit_every: int = tune(
        0,
        "Step to and capture every this many frames `record` writes again anyway, and stop the section if "
        "one differs from the frame before. Zero audits none, which is the fast path, and the test suite "
        "sets it to prove the frames written again.",
        unit="frames",
        bounds=Bounds(ge=0, le=10_000),
        scope=Scope.MACHINE,
        nature=Nature.APPARATUS,
        hazard=(
            "Every audited frame costs one step and one capture, so one audits every skipped frame and saves no time."
        ),
        see_also=("record.skip_still",),
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
        decides=(Code.CUE_OFF, Code.MOTION_OFF, Code.MOTION_NO_EDGE),
        hazard=(
            "A viewer sees a reveal land late at about a fifth of a second, so above roughly 200 "
            "milliseconds the limit passes films whose pictures visibly miss their words."
        ),
        see_also=("verify.av_offset_max_ms", "verify.reference_lead_seconds"),
    )
    motion_floor_percent: float = tune(
        0.5,
        "How much of a moving element's own area must change from one frame to the next for verify to "
        "read it as moving, which is where a motion's departure and arrival are found.",
        unit="percent",
        bounds=Bounds(ge=0.05, le=20),
        typed=A_PERCENT,
        decides=(Code.MOTION_OFF, Code.MOTION_NO_EDGE),
        hazard=(
            "Above a few percent the slow last frames of a firm arrival read as still, so its arrival is "
            "measured early and a motion that landed on its word fails."
        ),
        evidence=(
            "Calibration: on the motion prototype a firm arrival measured on its cue frame at half a percent "
            "at both the full and the quarter size."
        ),
    )
    motion_mask_max_percent: float = tune(
        40.0,
        "The most of the frame verify may mask out of a cue's measurement before it says it measured less "
        "of the picture than usual.",
        unit="percent",
        bounds=Bounds(ge=5, le=90),
        typed=A_PERCENT,
        decides=(Code.MOTION_MASK_LARGE,),
        hazard=(
            "Near the top of the range a cue is judged on a sliver of the frame, so a reveal under a large "
            "declared motion passes on very little evidence."
        ),
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
    """These keys are the three every voice has: which provider reads its turns, in which voice and at what pace.

    Each voice is one `[voices.NAME]` table. Everything a provider is particular about, its own fields,
    its model and its rate, is in that provider's own table, and a voice may state any of that table's
    chosen keys for itself alone, so changing `provider` never sends one vendor's fields or model to another.
    """

    provider: str = tune(
        "elevenlabs",
        "The speech provider that reads this voice's turns, which is a name the machine's own map answers. An "
        "unregistered name loads, and fails only when a run asks it for a take, which a run that does not "
        "spend never does. DeckTalk ships `elevenlabs`, the "
        "cloud voice whose own keys are `[elevenlabs]`, and `dtsp`, a provider served by a local server, whose "
        "own keys are `[dtsp]`.",
    )
    id: str = tune(
        "",
        "The voice that reads this voice's turns, as the provider names it. It is a published name rather than a "
        "credential, so it is committed with the project, and it is one of the inputs every take is named by. "
        "`DECKTALK_VOICES_<NAME>_ID` overrides it for anyone who keeps it out of the file.",
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
    "The script, and for a provider that bills the API key, travels to whatever host this names, so only the "
    "machine names it and a project file that sets it is refused."
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
        "The ElevenLabs model every ElevenLabs voice reads with, unless its own `[voices.NAME]` table names another.",
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
        "Whether ElevenLabs applies the boost it calls use_speaker_boost to the take.",
    )
    dollars_per_1000_characters: float = tune(
        0.0,
        "What this project's ElevenLabs plan charges in US dollars per thousand characters of speech.",
        unit="US dollars per 1000 characters",
        bounds=Bounds(ge=0, le=100),
        source=Source.STATED,
        evidence="the plan page of the account whose key this project uses",
        hazard=(
            "It is zero until somebody states it, and `--max-cost` refuses a run while the price is still "
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
    """These keys are the `dtsp` provider's own: where its local server listens, the model it reads with, and
    how long the server has to say which aligner it serves.

    `dtsp` speaks the DeckTalk speech protocol to `decktalk-voice`, a separate local server that is
    not part of this repository. The same server times the words of a take whose voice sent none. It
    needs no key, bills nothing, and `base_url` is the machine's to set.
    """

    base_url: str = tune(
        "http://127.0.0.1:9361",
        "Base URL of decktalk-voice, the local server that reads the script for `dtsp` and times the words of a "
        "take that came without them. Port 9361 is assigned to nothing at IANA and listed in no /etc/services, so "
        "no other service is expected to answer there.",
        scope=Scope.MACHINE,
        nature=Nature.APPARATUS,
        hazard=BASE_URL_HAZARD,
    )
    handshake_seconds: int = tune(
        5,
        "Seconds the local server has to say which aligner it serves, asked at most once per run and only when a "
        "take has no aligned words kept. It is short because a server that is up answers at once, so a port held "
        "by a process that never answers costs this wait and not `[narration] timeout_seconds`.",
        unit="seconds",
        bounds=Bounds(ge=1, le=60),
        scope=Scope.MACHINE,
        nature=Nature.APPARATUS,
        see_also=("dtsp.base_url", "narration.timeout_seconds"),
    )
    model: str = tune(
        "kokoro-82m",
        "The model the local server reads every `dtsp` voice with, as the server names it, unless the voice's own "
        "`[voices.NAME]` table names another.",
    )


SOUND_PRICE_HAZARD = (
    "It is zero until somebody states it, and `--max-cost` refuses a score while any rate it buys at is "
    "still the default, because DeckTalk would otherwise be capping a spend against a number it invented."
)
"""Why a sound rate nobody stated refuses a cap, said once for the three tables that state one."""


def sound_rate(kind: SoundKind) -> float:
    """The rate of one kind of sound, which each of the three tables states the same way for its own audio."""
    return tune(
        0.0,
        f"What this project's sound plan charges in US dollars per minute of {kind.value} audio, which is priced "
        "by the second of audio asked for.",
        unit="US dollars per minute of audio",
        bounds=Bounds(ge=0, le=100),
        source=Source.STATED,
        evidence="the plan page of the account whose key buys the sound",
        hazard=SOUND_PRICE_HAZARD,
        see_also=("score.provider",),
    )


@dataclass(frozen=True)
class AmbienceConfig:
    """The settings half of `[score.ambience]`: how the ambience bed is asked for. Its prompt and file are content."""

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
    dollars_per_minute: float = sound_rate(SoundKind.AMBIENCE)


@dataclass(frozen=True)
class EffectConfig:
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
    dollars_per_minute: float = sound_rate(SoundKind.EFFECT)


@dataclass(frozen=True)
class MusicConfig:
    """The settings half of `[score.music]`: how the music is asked for and joined. Its prompt is content."""

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
    chunk_max_seconds: int = tune(
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
    dollars_per_minute: float = sound_rate(SoundKind.MUSIC)


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
    output_format: str = tune(
        "mp3_44100_128",
        "Audio format the music, the ambience and the effects are asked for in. A take's format is its "
        "provider's own, such as `[elevenlabs] output_format`.",
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
    effects: EffectConfig = field(default_factory=EffectConfig)


type ProviderTable = ElevenLabsConfig | DtspConfig
"""The table a speech provider DeckTalk ships owns, one per adapter in the closed set.

Every one carries the provider's own fields and its default `model`, and its adapter declares which
key holds its base URL and which states its rate, so a reader above the speech layer asks the
adapter's declaration and never names a vendor.
"""


@dataclass(frozen=True)
class OutputConfig:
    """These keys switch the files a build writes beside the final mp4."""

    timestamped_copy: bool = tune(False, "Write a second copy of the final mp4 named with the date and time.")
    caption_voices: bool = tune(
        False,
        "Name the voice of every caption in a section spoken in more than one voice: a WebVTT voice span such as "
        "`<v narrator>` in the .vtt, and the name before the line, such as `narrator: `, in the .srt. Off, the "
        "captions carry the words alone, and the transcript names the voices of a dialogue either way.",
    )


@dataclass(frozen=True)
class EventsConfig:
    """These keys bound the events files a run writes under `build/events/`."""

    keep_runs: int = tune(
        20,
        "How many runs of event files `build/events/` keeps before the oldest is deleted.",
        unit="runs",
        bounds=Bounds(ge=1, le=1000),
    )
    max_bytes: int = tune(
        8_388_608,
        "Bytes one run's events file may reach before progress lines, download lines and debug and info lines "
        "are left out of it. Warnings and errors are left out once it reaches twice this. Lines about the run, "
        "its stages, its sections, its findings and its costs are always kept.",
        unit="bytes",
        bounds=Bounds(ge=65_536, le=1_073_741_824),
        scope=Scope.MACHINE,
        nature=Nature.APPARATUS,
        see_also=("events.keep_runs",),
        hazard="A file with no bound grows with every tool call a long film makes, on a disk a host shares.",
    )


@dataclass(frozen=True)
class ToolsConfig:
    """These keys name the tools DeckTalk drives, for a machine that supplies its own."""

    chromium: str = tune(
        "",
        "Chromium executable that `record`, `check` and `storyboard` drive. It is empty for the build DeckTalk "
        "fetches itself, and a path here is what a managed machine sets: a named executable that will not launch "
        "is never a reason to download another.",
        unit="path",
        scope=Scope.MACHINE,
        nature=Nature.APPARATUS,
    )
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
class Voice:
    """One voice of the project resolved: its name, its own keys, and its provider's table with its own values over it.

    A voice of a provider DeckTalk ships reads every key of that provider's table it does not state
    itself, and `table` already holds its own values in their place, so a reader asks the table and
    never merges anything. A provider a host registered owns no table, so its voice has its own three
    keys alone.
    """

    name: str
    own: VoiceConfig
    table: ProviderTable | None
    """Its provider's table with this voice's own values over it, or None for a provider a host registered."""
    stated: tuple[str, ...] = ()
    """The keys of its provider's table this voice states itself, in name order, which explain renders as its own
    rows."""

    def value(self, key: str) -> object:
        """The value this voice reads for one key: its own key, or its provider's table's key with its own over it."""
        if key in OWN_KEYS:
            return getattr(self.own, key)
        if self.table is None or key not in {f.name for f in fields(self.table)}:
            raise KeyError(f"{VOICES_TABLE}.{self.name}.{key}")
        return getattr(self.table, key)


OWN_KEYS = tuple(f.name for f in fields(VoiceConfig))
"""The keys every voice has of its own, whatever its provider, in the order the family publishes them."""


@dataclass(frozen=True)
class Settings:
    """Every setting with its default, in the order the reference and the schema publish them."""

    video: VideoConfig = field(default_factory=VideoConfig)
    narration: NarrationConfig = field(default_factory=NarrationConfig)
    record: RecordConfig = field(default_factory=RecordConfig)
    audio: AudioConfig = field(default_factory=AudioConfig)
    verify: VerifyConfig = field(default_factory=VerifyConfig)
    voices: Mapping[str, Voice] = field(default_factory=dict, metadata={FAMILY: VOICES_TABLE})
    """Every voice the project declares, the default first, which the loader resolves and nothing reads before it.

    It is a plain mapping rather than a read-only view so the tree stays a tree of plain values, which a
    digest over the whole settings reads field by field. The tree is frozen and no reader writes it.
    """
    mix: MixConfig = field(default_factory=MixConfig)
    motion: MotionConfig = field(default_factory=MotionConfig)
    elevenlabs: ElevenLabsConfig = field(default_factory=ElevenLabsConfig)
    dtsp: DtspConfig = field(default_factory=DtspConfig)
    score: ScoreConfig = field(default_factory=ScoreConfig)
    output: OutputConfig = field(default_factory=OutputConfig)
    events: EventsConfig = field(default_factory=EventsConfig)
    tools: ToolsConfig = field(default_factory=ToolsConfig)


def provider_tables() -> dict[str, type[ElevenLabsConfig | DtspConfig]]:
    """Every table a shipped speech provider owns, by its name, read off the fields of `Settings`."""
    hints = get_type_hints(Settings)
    return {table: hints[table] for table in PROVIDER_TABLES}


def voice_keys() -> tuple[Key, ...]:
    """The family's template keys: a voice's own three, then each chosen project key a provider table declares.

    A provider table's key that is project-scoped and chosen describes how its voices sound, so any
    voice may state it for itself. The rate is stated rather than chosen and the base URL is the
    machine's, so neither is a key a voice holds. A key two tables declare, such as `model`, is one
    template, which a test holds to one type and one range.
    """
    prefix = f"{VOICES_TABLE}.{MEMBER}"
    own = [replace(key, id=f"{prefix}.{key.name}") for key in registry(VoiceConfig)]
    inherited: dict[str, list[Key]] = {}
    for table, cls in provider_tables().items():
        for key in registry(cls, prefix=(table,)):
            if key.scope is Scope.PROJECT and key.source is Source.CHOSEN:
                inherited.setdefault(key.name, []).append(key)
    for name, keys in inherited.items():
        tables = " or ".join(key.table for key in keys)
        first = keys[0]
        own.append(
            Key(
                id=f"{prefix}.{name}",
                description=f"This voice's own {name}, over [{tables}] {name}, which every voice of that provider "
                "reads unless it sets its own.",
                default=None,
                annotation=first.annotation,
                bounds=first.bounds,
                unit=first.unit,
                hazard=first.hazard,
                see_also=tuple(key.id for key in keys),
            )
        )
    return tuple(own)


STATIC_KEYS: tuple[Key, ...] = registry(Settings)
"""Every key of a fixed table, in declaration order, which is every key but the voices' family."""

TEMPLATES: tuple[Key, ...] = voice_keys()
"""The voices' family, one template per key any voice may hold, each id spelled with `MEMBER` for the name."""

KEYS: tuple[Key, ...] = STATIC_KEYS + TEMPLATES
"""Every settings key in declaration order, the family's templates last, which is the one list every surface renders."""

BY_ID: dict[str, Key] = {key.id: key for key in KEYS}
"""Every key by the dotted name a diagnostic prints, `config set` takes and `--set` spells, a template by its own."""


def family(key: Key) -> bool:
    """Whether a key belongs to the voices' family, as a template or as one voice's member."""
    return key.table.split(".")[0] == VOICES_TABLE


def key_of(dotted: str) -> Key | None:
    """The key a dotted name spells: a fixed key, a template, one voice's member key, or None for none of these.

    A member key is its template with the voice's name in the id, so it carries the template's whole
    record and its own variable, such as `DECKTALK_VOICES_GUEST_ID` for `voices.guest.id`.
    """
    known = BY_ID.get(dotted)
    if known is not None:
        return known
    table, _, rest = dotted.partition(".")
    name, _, key = rest.partition(".")
    if table != VOICES_TABLE or not re.fullmatch(VOICE_NAME, name):
        return None
    template = BY_ID.get(f"{VOICES_TABLE}.{MEMBER}.{key}")
    return None if template is None else replace(template, id=dotted)


def member_keys(voice: Voice) -> tuple[Key, ...]:
    """Every key one voice holds, in the family's order: its own three, then each key of its provider's table."""
    held = set(OWN_KEYS) | ({f.name for f in fields(voice.table)} if voice.table is not None else set())
    return tuple(
        replace(template, id=f"{VOICES_TABLE}.{voice.name}.{template.name}")
        for template in TEMPLATES
        if template.name in held
    )


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

MACHINE_FILE_VARIABLE = f"{ENV_PREFIX}MACHINE_FILE"
"""The variable that names a per-machine settings file other than the standard one."""

PROJECT_VARIABLE = f"{ENV_PREFIX}PROJECT"
"""The variable that names the project when a caller names none, read only through the machine."""

STANDALONE_ENV = frozenset((PROJECT_VARIABLE, MACHINE_FILE_VARIABLE))
"""The two variables DeckTalk reads that name no key. Every other DECKTALK_ name is a key or a typo."""


def not_a_key(key: str) -> InputError:
    """The one refusal of a name no settings key carries, with the nearest key when one is near."""
    return InputError(
        f"'{key}' is not a settings key.{did_you_mean(key, BY_ID)}",
        hint="Run `decktalk schema setting` for every key DeckTalk reads.",
    )


def key_named(key: str) -> Key:
    """The settings key called `key`, a voice's key included, or the one refusal every reader of a key's name gives."""
    known = key_of(key)
    if known is None:
        raise not_a_key(key)
    return known


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

    @property
    def keys(self) -> tuple[Key, ...]:
        """Every key these settings hold: the fixed keys, then every key of every voice, the default voice first."""
        return STATIC_KEYS + tuple(key for voice in self.settings.voices.values() for key in member_keys(voice))


__all__ = [
    "AmbienceConfig",
    "AudioConfig",
    "DtspConfig",
    "EffectConfig",
    "ElevenLabsConfig",
    "EventsConfig",
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
    "Voice",
    "VoiceConfig",
]
