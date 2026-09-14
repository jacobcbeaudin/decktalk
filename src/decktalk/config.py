"""Tool tuning: one dataclass per concern, composed into Settings.

Four layers, lowest to highest precedence:

  1. the defaults below
  2. the same tables in the user's own decktalk.toml, one per machine (see user_config_path)
  3. tables of the same names in the project's decktalk.toml ([video], [narration], ...)
  4. DECKTALK_<TABLE>_<FIELD> environment variables, e.g. DECKTALK_VIDEO_PRESET=veryfast

Content that changes per presentation (sections, voice, mix levels, soundscape prompts)
is the project document, not tuning; see project.py. Secrets live only in .env.
"""

from __future__ import annotations

import difflib
import logging
import os
import sys
import tomllib
from collections.abc import Iterable
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

from ._env import from_env
from .errors import ConfigError

log = logging.getLogger(__name__)

PROJECT_FILE = "decktalk.toml"
ENV_PREFIX = "decktalk"


@dataclass
class VideoConfig:
    """These keys set the frame size and the encoding of every recording and of the final mp4."""

    width: int = 1920  # Frame width in pixels, for every recording and the final mp4.
    height: int = 1080  # Frame height in pixels, for every recording and the final mp4.
    fps: int = 25  # Frames per second.
    # Chromium records at 25 fps, so 25 avoids a pulldown that duplicates every sixth frame.
    preset: str = "medium"  # x264 preset. Use veryfast for drafts.
    crf: int = 18  # x264 quality. A lower value gives higher quality and a larger file.
    audio_bitrate: str = "192k"  # AAC bitrate of the final mp4.
    sample_rate: int = 48000  # Audio sample rate of the final mp4. Every audio input is resampled to it once.
    channels: int = 2  # Audio channels of the final mp4.
    slate_color: str = "0x0e1116"  # Color of the plain frame that plays when a slate image cannot be rendered.


@dataclass
class NarrationConfig:
    """These keys govern how the script is turned into audio."""

    model: str = "eleven_multilingual_v2"  # Speech model. `[voice] model` and `narrate --model` override it.
    output_format: str = "mp3_44100_128"  # Audio format that the speech provider returns.
    # It is part of the narration cache key.
    mp3_bitrate: str = "128k"  # Bitrate of the mp3 files that DeckTalk writes.
    # They include click tracks and `narration.mp3`.
    words_per_minute: int = 140  # Pacing of the estimated length in the `narrate` table.
    silent_words_per_minute: int = 150  # Pacing of the click track in a silent build.
    lead_break_seconds: float = 0.7  # Silence before the first spoken section.
    direction_break_seconds: float = 0.7  # Seconds that each beat adds to the length of a silent build's section.
    min_tail_seconds: float = 0.7  # Shortest silence after the last word of a section, so a cut never falls on speech.
    tail_slack_seconds: float = 0.05  # Extra silence added when `narrate` pads a short tail.
    context_chars: int = 1500  # Characters of each neighbor section sent with a request, for continuous prosody.
    timeout_seconds: int = 180  # Seconds before a speech request times out.


@dataclass
class RecordConfig:
    """These keys tune the headless Chromium recording."""

    settle_seconds: float = 0.5  # Shortest wait after the page is ready and before narration t=0.
    # `record --settle` overrides it.
    min_lead_seconds: float = 1.5  # Shortest time from the start of the recorder to narration t=0.
    color_scheme: str = "light"  # Color scheme that Chromium reports to the page, such as `light` or `dark`.
    retries: int = 2  # How many more times `record` records a section whose frames stalled.
    shot_settle_ms: int = 400  # Milliseconds that `decktalk shots` waits before each step screenshot.


@dataclass
class AlignConfig:
    """These keys tune how narration t=0 is found in a recording, and the recording sanity check."""

    scan_seconds: float = 4.0  # Seconds at the start of each recording that `measure` scans for the magenta cover.
    fallback_first_paint_seconds: float = 1.1  # Guessed first paint if `measure` finds no cover and no painted frame.
    # `measure` adds `settle_seconds` to it.
    magenta_luma_min: float = 70  # A cover frame has an average luma above this.
    magenta_luma_max: float = 140  # A cover frame has an average luma below this.
    magenta_chroma_min: float = 165  # A cover frame has an average U and an average V above this.
    painted_ymax: float = 60  # A painted frame has a brightest luma above this.
    painted_yavg_max: float = 120  # A painted frame has an average luma below this, so a white flash does not count.
    black_ymax: float = 40  # `check` reports `BLACK?` when the brightest luma of the middle frame is below this.
    truncated_slack_seconds: float = 0.5  # Allowed shortfall of a recording against its requested length.
    # A larger shortfall makes `check` report `TRUNCATED`.
    stall_ms: int = 150  # Longest frame gap after narration t=0, in milliseconds.
    # A longer gap makes `record` try again and `check` report `STALLED`.


@dataclass
class AudioConfig:
    """These keys set the mix mechanics. Levels live in `[mix]` in `decktalk.toml`."""

    duck_ramp_seconds: float = 0.5  # Ramp of the underscore duck at each edge of a spoken span or a clip.
    ambience_ramp_seconds: float = 1.0  # Ramp of the ambience bed at each edge of its span.
    ambience_pad_seconds: float = 0.5  # Seconds that the ambience bed extends past each edge of its section.
    marker_mute_ramp_seconds: float = 0.04  # Ramp into and out of a marker's mute.
    marker_boost_ramp_seconds: float = 0.3  # Ramp into and out of a marker's swell.


@dataclass
class VerifyConfig:
    """These keys tune the checks on the assembled mp4."""

    after_dip_seconds: float = 0.2  # Seconds after a section start to the frame that the start check reads.
    lead_seconds: float = 0.1  # Reference lead.
    # It has an effect only above (`max_offset_frames` + 1.5) / `fps`, which is 0.14 s at the defaults.
    probe_delays: tuple[float, ...] = (0.7, 1.5)  # Seconds after the cue time for each probe.
    # The later probe catches a slow reveal.
    diff_level: int = 40  # Luma difference that a pixel must exceed to count as changed, for probes and control shares.
    min_changed_percent: float = 0.1  # Smallest changed share, in percent, that the reported probe needs.
    min_margin_percent: float = 0.1  # Smallest margin, in percentage points, that the reported probe needs.
    onset_percent: float = 0.01  # Rise in changed share from one frame to the next that marks the onset.
    # It is in percentage points. 0.01 is about 13 pixels.
    onset_diff_level: int = 12  # Luma difference that a pixel must exceed to count as changed, for the onset scan only.
    click_search_seconds: float = 0.25  # Seconds on each side of the cued word's start that the click search covers.
    max_offset_frames: int = 2  # Offset limit, in frames. The onset can sit this far from the cue time, early or late.
    max_av_frames: int = 3  # a/v limit, in frames, early or late.
    # It is wider because the click carries encoding jitter too.
    visible_ymax: float = 60  # The start check reports `BLACK` when the brightest luma of its frame is at most this.
    cut_window_seconds: float = 0.15  # Seconds of narration before each cut that the cut check measures.
    cut_max_db: float = -40.0  # Loudest RMS level of the cut window, in dBFS, that passes the cut check.
    probe_width: int = 480  # Width in pixels that frames are scaled to before a comparison.
    probe_height: int = 270  # Height in pixels that frames are scaled to before a comparison.
    block_width: int = 240  # Width in pixels of the block-averaged copy of each frame that confirms an onset.
    # Each pixel then averages an 8 by 8 block of a 1080p frame, the size of an H.264 transform block. The
    # encoder can shift a few pixels of a still picture by up to about 15 levels in the frames just before a
    # change. The average cancels that ringing, so a frame is the onset only when a block also changed.
    block_height: int = 135  # Height in pixels of the block-averaged copy of each frame that confirms an onset.


@dataclass
class ElevenLabsConfig:
    """These keys point at the ElevenLabs API and size its soundscape requests."""

    api_base: str = "https://api.elevenlabs.io/v1"  # Base URL of the ElevenLabs API.
    sound_model: str = "eleven_text_to_sound_v2"  # Model for ambience and sound effect requests.
    # A soundscape table can name its own.
    music_model: str = "music_v2"  # Model for music requests.
    # A soundscape table can name its own.
    music_bitrate: str = "192k"  # Bitrate of the music file that `soundscape` joins from its chunks.
    max_music_chunk_seconds: int = 300  # Longest music request. Longer music is requested in chunks.
    music_crossfade_seconds: int = 2  # Crossfade between two music chunks.
    ambience_seconds: float = 25.0  # Length of a generated ambience bed.
    # A soundscape table can set `duration_seconds` instead.
    ambience_prompt_influence: float = 0.3  # Prompt influence of an ambience request.
    # A soundscape table can set its own.
    sfx_seconds: float = 0.5  # Length of a generated sound effect.
    # A soundscape table can set `duration_seconds` instead.
    sfx_prompt_influence: float = 0.5  # Prompt influence of a sound effect request.
    # A soundscape table can set its own.
    timeout_seconds: int = 600  # Seconds before a sound or music request times out.


@dataclass
class Settings:
    """Every tunable with its default. load_settings() builds it."""

    video: VideoConfig = field(default_factory=VideoConfig)
    narration: NarrationConfig = field(default_factory=NarrationConfig)
    record: RecordConfig = field(default_factory=RecordConfig)
    align: AlignConfig = field(default_factory=AlignConfig)
    audio: AudioConfig = field(default_factory=AudioConfig)
    verify: VerifyConfig = field(default_factory=VerifyConfig)
    elevenlabs: ElevenLabsConfig = field(default_factory=ElevenLabsConfig)


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
        raise ConfigError(f"{path}: {unknown} do not belong in a user settings file; only {sorted(allowed)} do")
    for message in settings_key_warnings(data, str(path)):
        log.warning(message)
    return data


def unknown_key_message(key: str, known: Iterable[str], where: str) -> str:
    """The warning for one key that DeckTalk does not read, with the closest known key when one is near."""
    close = difflib.get_close_matches(key, sorted(known), n=1)
    hint = f" (did you mean '{close[0]}'?)" if close else ""
    return f"{where}: ignoring unknown key '{key}'{hint}"


def unknown_key_warnings(table: dict[str, Any], known: Iterable[str], where: str) -> list[str]:
    """One warning per key in `table` that is not in `known`. An unknown key is ignored, not an error."""
    known = set(known)
    return [unknown_key_message(key, known, where) for key in sorted(set(table) - known)]


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
    try:
        return from_env(Settings, prefixes=[ENV_PREFIX], base=base, environ=environ)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"invalid tuning value: {exc}") from exc
