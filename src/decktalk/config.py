"""Tool tuning: one dataclass per concern, composed into Settings.

Three layers, lowest to highest precedence:

  1. the defaults below
  2. tables of the same names in the project's decktalk.toml ([video], [narration], ...)
  3. DECKTALK_<SECTION>_<FIELD> environment variables, e.g. DECKTALK_VIDEO_PRESET=veryfast

Content that changes per presentation (sections, voice, mix levels, soundscape prompts)
is the project document, not tuning; see project.py. Secrets live only in .env.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ._env import from_env
from .errors import ConfigError

PROJECT_FILE = "decktalk.toml"
ENV_PREFIX = "decktalk"


@dataclass
class VideoConfig:
    """Frame size and encoding of every recording and of the final mp4."""

    width: int = 1920
    height: int = 1080
    fps: int = 30
    preset: str = "medium"  # x264 preset; veryfast for drafts
    crf: int = 18
    audio_bitrate: str = "192k"
    sample_rate: int = 44100
    channels: int = 2
    slate_color: str = "0x0e1116"  # plain frame when a slate cannot be rendered


@dataclass
class NarrationConfig:
    """How the script is turned into audio."""

    model: str = "eleven_multilingual_v2"
    output_format: str = "mp3_44100_128"
    mp3_bitrate: str = "128k"
    words_per_minute: int = 140  # pacing estimate shown in tables
    silent_words_per_minute: int = 150  # --silent placeholder pacing
    lead_break_seconds: float = 0.7  # opens the first spoken section
    direction_break_seconds: float = 0.7  # pause where a bracketed direction sat
    tail_break_seconds: float = 0.35  # requested at the end of every section
    min_tail_seconds: float = 0.35  # guaranteed silence after the last word
    tail_slack_seconds: float = 0.05  # extra padding added when the tail is short
    context_chars: int = 1500  # previous_text / next_text sent for prosody continuity
    timeout_seconds: int = 180


@dataclass
class RecordConfig:
    """Headless Chromium recording."""

    settle_seconds: float = 0.5  # after load, before narration t=0
    min_lead_seconds: float = 1.5  # t=0 never comes sooner than this after the recorder starts
    color_scheme: str = "light"
    shot_settle_ms: int = 400  # wait before a review screenshot


@dataclass
class AlignConfig:
    """Finding narration t=0 in a recording, and the recording sanity check."""

    scan_seconds: float = 4.0
    fallback_first_paint_seconds: float = 1.1
    magenta_luma_min: float = 70
    magenta_luma_max: float = 140
    magenta_chroma_min: float = 165
    painted_ymax: float = 60  # something is drawn
    painted_yavg_max: float = 120  # and it is not a white flash
    black_ymax: float = 40  # check: a frame darker than this is "black"
    truncated_slack_seconds: float = 0.5


@dataclass
class AudioConfig:
    """Mix mechanics. Levels are per project (decktalk.toml [mix])."""

    duck_ramp_seconds: float = 0.5
    ambience_ramp_seconds: float = 1.0
    ambience_pad_seconds: float = 0.5
    marker_mute_ramp_seconds: float = 0.04
    marker_boost_ramp_seconds: float = 0.3
    limiter: float = 0.95


@dataclass
class VerifyConfig:
    """Checks on the assembled mp4."""

    after_dip_seconds: float = 0.2
    lead_seconds: float = 0.1  # the reference frame sits this long before the cue
    probe_delays: tuple[float, ...] = (0.7, 1.5)  # seconds after the cue; the later one catches slow reveals
    diff_level: int = 40  # luma steps a pixel must change to count
    min_changed_percent: float = 0.1  # share of the frame the best probe must change
    min_margin_percent: float = 0.1  # and by how much it must beat the control span
    visible_ymax: float = 60
    probe_width: int = 480
    probe_height: int = 270


@dataclass
class ElevenLabsConfig:
    api_base: str = "https://api.elevenlabs.io/v1"
    sound_model: str = "eleven_text_to_sound_v2"
    music_model: str = "music_v2"
    music_bitrate: str = "192k"
    max_music_chunk_seconds: int = 300
    music_crossfade_seconds: int = 2
    ambience_seconds: float = 25.0
    ambience_prompt_influence: float = 0.3
    sfx_seconds: float = 0.5
    sfx_prompt_influence: float = 0.5
    timeout_seconds: int = 600


@dataclass
class Settings:
    """Every tunable, with defaults. Loaded by load_settings()."""

    video: VideoConfig = field(default_factory=VideoConfig)
    narration: NarrationConfig = field(default_factory=NarrationConfig)
    record: RecordConfig = field(default_factory=RecordConfig)
    align: AlignConfig = field(default_factory=AlignConfig)
    audio: AudioConfig = field(default_factory=AudioConfig)
    verify: VerifyConfig = field(default_factory=VerifyConfig)
    elevenlabs: ElevenLabsConfig = field(default_factory=ElevenLabsConfig)


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
) -> Settings:
    """Settings for a project directory: defaults, then its decktalk.toml, then env."""
    base = toml if toml is not None else (read_project_toml(root) if root else {})
    try:
        return from_env(Settings, prefixes=[ENV_PREFIX], base=base, environ=environ)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"invalid tuning value: {exc}") from exc
