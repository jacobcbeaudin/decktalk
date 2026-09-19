"""Tool tuning: one dataclass per concern, composed into Settings.

Every key DeckTalk tunes is a field below, with its type, its default and the comment the
generated reference prints, so a key is written once and read from here by the loader, by the
unknown-key warning and by `scripts/build_settings_reference.py`. Four layers set a key here, lowest to
highest: the default below, the same table in the user's own decktalk.toml (see
user_config_path), the same table in the project's decktalk.toml, and the environment variable
DECKTALK_<TABLE>_<FIELD>, such as DECKTALK_VIDEO_PRESET=veryfast. A command-line flag overrides
the result of those four for one run, which the generated reference counts as a fifth layer.

Content that changes per presentation (sections, voice, mix levels, soundscape prompts) is the
project document rather than tuning, and model/document.py holds it. Secrets live only in .env.
"""

from __future__ import annotations

import logging
import os
import sys
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

from .errors import ConfigError
from .tomlmap import (
    A_LUMA,
    A_PERCENT,
    A_SHARE,
    ABOVE_ZERO,
    NOT_NEGATIVE,
    Check,
    env_names,
    from_mapping,
    unknown_key_message,
    unknown_key_warnings,
)

log = logging.getLogger(__name__)

PROJECT_FILE = "decktalk.toml"
ENV_PREFIX = "decktalk"
# The words x264 and Chromium accept, so a wrong one fails at load rather than inside a render.
X264_PRESETS = ("ultrafast", "superfast", "veryfast", "faster", "fast", "medium", "slow", "slower", "veryslow")
COLOR_SCHEMES = ("light", "dark", "no-preference")


def tune[T](default: T, doc: str, check: Check | None = None) -> T:
    """One tuning key: its default, the sentence the reference prints, and the rule its value obeys.

    The sentence lives beside the default, so `scripts/build_settings_reference.py` reads the
    whole reference from these fields and the page can never drift from the code. A key whose
    value would break a run outside a range carries that range as a `Check`, which `tomlmap`
    applies at load, so a bad number is a configuration error naming its table and its key rather
    than a traceback from ffmpeg.
    """
    return field(default=default, metadata={"doc": doc, "check": check})


@dataclass(frozen=True)
class VideoConfig:
    """These keys set the frame size and the encoding of every recording and of the final mp4."""

    width: int = tune(1920, "Frame width in pixels, for every recording and the final mp4.", ABOVE_ZERO)
    height: int = tune(1080, "Frame height in pixels, for every recording and the final mp4.", ABOVE_ZERO)
    fps: int = tune(
        25,
        "Frames per second. Chromium records at 25 fps, so 25 avoids a pulldown that duplicates every sixth frame.",
        ABOVE_ZERO,
    )
    preset: str = tune(
        "medium",
        "x264 preset. Use veryfast for drafts.",
        Check(lambda v: v in X264_PRESETS, f"must be one of {', '.join(X264_PRESETS)}"),
    )
    crf: int = tune(
        18,
        "x264 quality. A lower value gives higher quality and a larger file.",
        Check(lambda v: 0 <= v <= 51, "must be an x264 quality between 0 and 51"),
    )
    audio_bitrate: str = tune("192k", "AAC bitrate of the final mp4.")
    sample_rate: int = tune(
        48000, "Audio sample rate of the final mp4. Every audio input is resampled to it once.", ABOVE_ZERO
    )
    channels: int = tune(2, "Audio channels of the final mp4.", ABOVE_ZERO)
    slate_color: str = tune("0x0e1116", "Color of the plain frame that plays when a slate image cannot be rendered.")


@dataclass(frozen=True)
class NarrationConfig:
    """These keys govern how the script is turned into audio."""

    model: str = tune("eleven_multilingual_v2", "Speech model. `[voice] model` and `narrate --model` override it.")
    output_format: str = tune(
        "mp3_44100_128", "Audio format that the speech provider returns. It is part of the narration cache key."
    )
    mp3_bitrate: str = tune(
        "128k", "Bitrate of the mp3 files that DeckTalk writes. They include click tracks and `narration.mp3`."
    )
    words_per_minute: int = tune(140, "Pacing of the estimated length in the `narrate` table.", ABOVE_ZERO)
    silent_words_per_minute: int = tune(150, "Pacing of the click track in a build without voice.", ABOVE_ZERO)
    opening_silence_seconds: float = tune(0.7, "Silence before the first spoken section.", NOT_NEGATIVE)
    silent_beat_seconds: float = tune(
        0.7, "Seconds each beat adds to a section's length in a build without voice.", NOT_NEGATIVE
    )
    min_tail_seconds: float = tune(
        0.7,
        "Shortest silence after the last word of a section, so a cut never falls on speech.",
        NOT_NEGATIVE,
    )
    tail_slack_seconds: float = tune(0.05, "Extra silence added when `narrate` pads a short tail.", NOT_NEGATIVE)
    cache_dir: str = tune(
        "",
        "Directory that holds the takes, their words files and the take index. It is empty for "
        "`build/narration/` inside the project, and a path here keeps the voiced takes when `build/` is deleted.",
    )
    context_chars: int = tune(
        1500, "Characters of each neighbor section sent with a request, for continuous prosody.", NOT_NEGATIVE
    )
    timeout_seconds: int = tune(180, "Seconds before a speech request times out.", ABOVE_ZERO)


@dataclass(frozen=True)
class RecordConfig:
    """These keys tune the headless Chromium recording, how narration t=0 is found in it, and its sanity check."""

    settle_seconds: float = tune(
        0.5,
        "Shortest wait after the page is ready and before narration t=0. `record --settle` overrides it.",
        NOT_NEGATIVE,
    )
    min_cover_seconds: float = tune(1.5, "Shortest time from the start of the recorder to narration t=0.", NOT_NEGATIVE)
    browser_path: str = tune(
        "",
        "Chromium executable that `record` drives. It is empty for the build that `decktalk install` fetched, "
        "and a path here is what a managed machine sets.",
    )
    color_scheme: str = tune(
        "light",
        "Color scheme that Chromium reports to the page.",
        Check(lambda v: v in COLOR_SCHEMES, f"must be one of {', '.join(COLOR_SCHEMES)}"),
    )
    retries: int = tune(2, "How many more times `record` records a section whose frames stalled.", NOT_NEGATIVE)
    screenshot_settle_ms: int = tune(
        400,
        "Milliseconds that `decktalk screenshots` waits before each slide screenshot.",
        NOT_NEGATIVE,
    )
    cover_scan_seconds: float = tune(
        4.0,
        "Seconds at the start of each recording that `measure` scans for the magenta cover.",
        NOT_NEGATIVE,
    )
    fallback_first_paint_seconds: float = tune(
        1.1,
        "Guessed first paint if `measure` finds no cover and no painted frame. `measure` adds `settle_seconds` to it.",
        NOT_NEGATIVE,
    )
    cover_luma_min: float = tune(70, "A cover frame has an average luma above this.", A_LUMA)
    cover_luma_max: float = tune(140, "A cover frame has an average luma below this.", A_LUMA)
    cover_chroma_min: float = tune(165, "A cover frame has an average U and an average V above this.", A_LUMA)
    painted_ymax: float = tune(60, "A painted frame has a brightest luma above this.", A_LUMA)
    painted_yavg_max: float = tune(
        120,
        "A painted frame has an average luma below this, so a white flash does not count.",
        A_LUMA,
    )
    black_ymax: float = tune(
        40, "`check` reports `BLACK?` when the brightest luma of the middle frame is below this.", A_LUMA
    )
    truncated_slack_seconds: float = tune(
        0.5,
        "Allowed shortfall of a recording against its requested length. A larger shortfall makes `check` "
        "report `TRUNCATED`.",
        NOT_NEGATIVE,
    )
    stall_ms: int = tune(
        150,
        "Longest frame gap after narration t=0, in milliseconds. A longer gap makes `record` try again and "
        "`check` report `STALLED`.",
        ABOVE_ZERO,
    )


@dataclass(frozen=True)
class AudioConfig:
    """These keys set the mix mechanics. Levels live in `[mix]` in `decktalk.toml`."""

    duck_ramp_seconds: float = tune(
        0.5, "Ramp of the music duck at each edge of a spoken span or a clip.", NOT_NEGATIVE
    )
    ambience_ramp_seconds: float = tune(1.0, "Ramp of the ambience bed at each edge of its span.", NOT_NEGATIVE)
    ambience_pad_seconds: float = tune(
        0.5, "Seconds that the ambience bed extends past each edge of its section.", NOT_NEGATIVE
    )
    marker_mute_ramp_seconds: float = tune(0.04, "Ramp into and out of a marker's mute.", NOT_NEGATIVE)
    marker_boost_ramp_seconds: float = tune(0.3, "Ramp into and out of a marker's swell.", NOT_NEGATIVE)


@dataclass(frozen=True)
class VerifyConfig:
    """These keys tune the checks on the assembled mp4."""

    after_dip_seconds: float = tune(
        0.2, "Seconds after a section start to the frame that the start check reads.", NOT_NEGATIVE
    )
    reference_lead_seconds: float = tune(
        0.1,
        "Reference lead. It has an effect only above (`max_offset_frames` + 1.5) / `fps`, which is 0.14 s at "
        "the defaults.",
        NOT_NEGATIVE,
    )
    probe_delays: tuple[float, ...] = tune(
        (0.7, 1.5),
        "Seconds after the cue time for each probe. The later probe catches a slow reveal.",
        Check(lambda v: bool(v) and all(x > 0 for x in v), "must be one or more delays above zero"),
    )
    diff_level: int = tune(
        40,
        "Luma difference that a pixel must exceed to count as changed, for probes and control shares.",
        A_LUMA,
    )
    min_changed_percent: float = tune(
        0.1, "Smallest changed share, in percent, that the reported probe needs.", A_PERCENT
    )
    min_margin_percent: float = tune(
        0.1, "Smallest margin, in percentage points, that the reported probe needs.", A_PERCENT
    )
    thin_change_factor: float = tune(
        3.0,
        "A passing cue whose changed share or margin is below this many times its floor reads `THIN CHANGE?`, "
        "an uncertain finding. 1 turns the warning off.",
        ABOVE_ZERO,
    )
    onset_percent: float = tune(
        0.01,
        "Rise in changed share from one frame to the next that marks the onset. It is in percentage points. "
        "0.01 is about 13 pixels.",
        A_PERCENT,
    )
    onset_diff_level: int = tune(
        12,
        "Luma difference that a pixel must exceed to count as changed, for the onset scan only.",
        A_LUMA,
    )
    click_search_seconds: float = tune(
        0.25,
        "Seconds on each side of the cued word's start that the click search covers.",
        NOT_NEGATIVE,
    )
    max_offset_frames: int = tune(
        2,
        "Offset limit, in frames. The onset can sit this far from the cue time, early or late.",
        NOT_NEGATIVE,
    )
    max_av_frames: int = tune(
        3,
        "a/v limit, in frames, early or late. It is wider because the click carries encoding jitter too.",
        NOT_NEGATIVE,
    )
    visible_ymax: float = tune(
        60,
        "The start check reports `BLACK` when the brightest luma of its frame is at most this.",
        A_LUMA,
    )
    cut_window_seconds: float = tune(
        0.15, "Seconds of narration before each cut that the cut check measures.", NOT_NEGATIVE
    )
    cut_max_db: float = tune(-40.0, "Loudest RMS level of the cut window, in dBFS, that passes the cut check.")
    max_pop_percent: float = tune(
        0.1,
        "Largest changed share, in percent, across the cut into a section that sets `seamless`. A larger "
        "share reads `POP AT CUT`.",
        A_PERCENT,
    )
    probe_width: int = tune(480, "Width in pixels that frames are scaled to before a comparison.", ABOVE_ZERO)
    probe_height: int = tune(270, "Height in pixels that frames are scaled to before a comparison.", ABOVE_ZERO)
    block_width: int = tune(
        240,
        "Width in pixels of the block-averaged copy of each frame that confirms an onset. Each pixel then "
        "averages an 8 by 8 block of a 1080p frame, the size of an H.264 transform block. The encoder can "
        "shift a few pixels of a still picture by up to about 15 levels in the frames just before a change. "
        "The average cancels that ringing, so a frame is the onset only when a block also changed.",
        ABOVE_ZERO,
    )
    block_height: int = tune(
        135, "Height in pixels of the block-averaged copy of each frame that confirms an onset.", ABOVE_ZERO
    )


@dataclass(frozen=True)
class ElevenLabsConfig:
    """These keys point at the ElevenLabs API and size its soundscape requests."""

    api_base: str = tune("https://api.elevenlabs.io/v1", "Base URL of the ElevenLabs API.")
    sound_model: str = tune(
        "eleven_text_to_sound_v2", "Model for ambience and sound effect requests. A soundscape table can name its own."
    )
    music_model: str = tune("music_v2", "Model for music requests. A soundscape table can name its own.")
    music_bitrate: str = tune("192k", "Bitrate of the music file that `soundscape` joins from its chunks.")
    max_music_chunk_seconds: int = tune(300, "Longest music request. Longer music is requested in chunks.", ABOVE_ZERO)
    music_crossfade_seconds: int = tune(2, "Crossfade between two music chunks.", NOT_NEGATIVE)
    ambience_seconds: float = tune(
        25.0,
        "Length of a generated ambience bed. A soundscape table can set `duration_seconds` instead.",
        ABOVE_ZERO,
    )
    ambience_prompt_influence: float = tune(
        0.3,
        "Prompt influence of an ambience request. A soundscape table can set its own.",
        A_SHARE,
    )
    sfx_seconds: float = tune(
        0.5,
        "Length of a generated sound effect. A soundscape table can set `duration_seconds` instead.",
        ABOVE_ZERO,
    )
    sfx_prompt_influence: float = tune(
        0.5,
        "Prompt influence of a sound effect request. A soundscape table can set its own.",
        A_SHARE,
    )
    timeout_seconds: int = tune(600, "Seconds before a sound or music request times out.", ABOVE_ZERO)


@dataclass(frozen=True)
class OutputConfig:
    """These keys switch the files a build writes beside the final mp4."""

    timestamped_copy: bool = tune(False, "Write a second copy of the final mp4 named with the date and time.")


@dataclass(frozen=True)
class Settings:
    """Every tunable with its default. load_settings() builds it."""

    video: VideoConfig = field(default_factory=VideoConfig)
    narration: NarrationConfig = field(default_factory=NarrationConfig)
    record: RecordConfig = field(default_factory=RecordConfig)
    audio: AudioConfig = field(default_factory=AudioConfig)
    verify: VerifyConfig = field(default_factory=VerifyConfig)
    elevenlabs: ElevenLabsConfig = field(default_factory=ElevenLabsConfig)
    output: OutputConfig = field(default_factory=OutputConfig)


# The variables DeckTalk reads that name no tuning key. Every other DECKTALK_ name is a tuning key.
STANDALONE_ENV = frozenset(
    "DECKTALK_PROJECT DECKTALK_CONFIG DECKTALK_CACHE_DIR DECKTALK_FFMPEG DECKTALK_FFPROBE "
    "DECKTALK_ALLOW_ANY_API_BASE".split()
)


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


def user_config_path() -> Path:
    """The per-machine settings file. DECKTALK_CONFIG overrides the standard location."""
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


def read_user_toml(path: Path | None = None) -> dict[str, Any]:
    """The user's tuning tables, or {} when the file is absent. Only settings tables are allowed there."""
    path = path or user_config_path()
    if not path.exists():
        return {}
    try:
        with path.open("rb") as fh:
            data = tomllib.load(fh)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path}: {exc}") from exc
    allowed = {f.name for f in fields(Settings)}
    unknown = sorted(set(data) - allowed)
    if unknown:
        raise ConfigError(f"{path}: {unknown} do not belong in a user settings file. Only {sorted(allowed)} do.")
    for message in settings_key_warnings(data, str(path)):
        log.warning(message)
    return data


def settings_key_warnings(doc: dict[str, Any], where: str) -> list[str]:
    """Warnings for unknown keys inside the tuning tables, such as [video] or [verify], of a parsed file."""
    defaults = Settings()
    out: list[str] = []
    for f in fields(Settings):
        table = doc.get(f.name)
        if isinstance(table, dict):
            known = {g.name for g in fields(getattr(defaults, f.name))}
            out += unknown_key_warnings(table, known, f"{where}: [{f.name}]")
    return out


def merge_tables(base: dict[str, Any], over: dict[str, Any]) -> dict[str, Any]:
    """`over` on top of `base`, one level deep, which is how settings tables nest."""
    out = {k: dict(v) if isinstance(v, dict) else v for k, v in base.items()}
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k].update(v)
        else:
            out[k] = v
    return out


def read_project_toml(root: Path) -> dict[str, Any]:
    """Parsed decktalk.toml, or {} when the file is absent. Malformed TOML is a ConfigError."""
    path = root / PROJECT_FILE
    if not path.exists():
        return {}
    try:
        with path.open("rb") as fh:
            data = tomllib.load(fh)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path}: {exc}") from exc
    return data


def load_settings(
    root: Path | None = None,
    *,
    toml: dict[str, Any] | None = None,
    environ: dict[str, str] | None = None,
    user: dict[str, Any] | None = None,
) -> Settings:
    """Settings for a project directory: defaults, the user's file, the project's decktalk.toml, then env."""
    project = toml if toml is not None else (read_project_toml(root) if root else {})
    machine = user if user is not None else read_user_toml()
    base = merge_tables(machine, project)
    for message in env_warnings(environ):
        log.warning(message)
    try:
        return from_mapping(Settings, base=base, prefixes=[ENV_PREFIX], environ=environ)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"invalid tuning value: {exc}") from exc
