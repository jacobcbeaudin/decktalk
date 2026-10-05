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
table.key=value` given for one run. A machine key is set by its default, the per-machine file, its
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
    edit.py       one key written into or removed from a settings file
"""

from __future__ import annotations

from dataclasses import dataclass, field

from pydantic import Field

from decktalk.errors import InputError
from decktalk.findings import Code, Model
from decktalk.results import LayerValue, Scope, SoundKind
from decktalk.tomlmap import A_LUMA, A_PERCENT, A_SHARE, ENV_PREFIX, Bounds, Key, Nature, Source, registry, tune
from decktalk.tomlmap.suggest import did_you_mean

PROJECT_FILE = "decktalk.toml"

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

# The four constants a setting's bounds or relations read. `settings.numbers.NUMBERS` publishes each
# one with its unit and its reason, so the reason is written there alone.
BLOCK_PX = 8
GUARD_FRAMES = 1.5
REPORT_FRAME_GAP_MS = 100
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
        "Frame rate of the final mp4. The recorder's own rate is measured and is not a setting.",
        unit="frames per second",
        bounds=Bounds(enum=(25, 30, 50, 60)),
        requires="video.fps >= CAPTURE_FPS",
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
    """These keys tune the headless Chromium recording and how the start of the section clock is found in it."""

    settle_seconds: float = tune(
        0.5,
        "Shortest wait after the page is ready and before the start of the section clock.",
        unit="seconds",
        bounds=Bounds(ge=0, le=30),
    )
    cover_min_seconds: float = tune(
        1.5,
        "Shortest time from the start of the recorder to the start of the section clock.",
        unit="seconds",
        bounds=Bounds(ge=0, le=30),
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
    cover_min_luma: float = tune(
        70.0,
        "A cover frame has an average luma above this.",
        unit="luma",
        bounds=Bounds(ge=40, le=120),
        typed=A_LUMA,
        nature=Nature.APPARATUS,
    )
    cover_max_luma: float = tune(
        140.0,
        "A cover frame has an average luma below this.",
        unit="luma",
        bounds=Bounds(ge=80, le=200),
        typed=A_LUMA,
        nature=Nature.APPARATUS,
    )
    cover_min_chroma: float = tune(
        165.0,
        "A cover frame has an average U and an average V above this.",
        unit="chroma",
        bounds=Bounds(ge=128, le=255),
        typed=A_LUMA,
        nature=Nature.APPARATUS,
    )
    painted_peak_min_luma: float = tune(
        60.0,
        "A painted frame has a brightest luma above this.",
        unit="luma",
        bounds=Bounds(ge=1, le=200),
        typed=A_LUMA,
    )
    painted_mean_max_luma: float = tune(
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
        "Longest gap between two presented frames after the start of the section clock. A longer gap makes "
        "`record` try again.",
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
        see_also=("record.painted_peak_min_luma",),
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
        "unregistered name loads, and fails only when a run asks it for a take, which a run that does not "
        "spend never does. DeckTalk ships `elevenlabs`, the "
        "cloud voice whose own keys are `[elevenlabs]`, and `dtsp`, a provider served by a local server, whose "
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
    """These keys are the `dtsp` provider's own: where its local server listens and the model it reads with.

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
class Settings:
    """Every setting with its default, in the order the reference and the schema publish them."""

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
    events: EventsConfig = field(default_factory=EventsConfig)
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
    """The settings key called `key`, or the one refusal every reader of a key's name gives."""
    known = BY_ID.get(key)
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
    "VoiceConfig",
]
