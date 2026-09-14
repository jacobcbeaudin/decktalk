"""A DeckTalk project: a directory with decktalk.toml, a script, cues, HTML pages and media.

    my-lesson/
      decktalk.toml      the document (below) plus optional tuning tables (config.py)
      script.md          narration; "## N. Title" sections, [bracketed directions] unspoken
      cues.json          which spoken phrase each visual lands on
      deck/index.html    HTML scenes; decktalk-runtime.js gives them the ?beats= contract
      media/             your clips, b-roll, slate, markers.json
      .env               ELEVENLABS_API_KEY, ELEVENLABS_VOICE_ID (never committed)
      build/             everything generated (git-ignored)

decktalk.toml, the document:

    [project]                name, script, cues, build
    [voice]                  ElevenLabs voice settings for this presentation
    [[section]]              number, title, then either page+scene or clip
    [transition]             dips, dip_seconds, page_fades_in
    [mix]                    underscore, ambience, markers, sfx, levels, loudnorm
    [soundscape]             prompts for `decktalk soundscape`

Every path is relative to the project directory. Validation happens here, so a bad
file fails at load with the table and field named, not deep inside ffmpeg.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .artifacts import Beats, Manifest, Timeline
from .config import (
    PROJECT_FILE,
    Settings,
    load_settings,
    read_project_toml,
    settings_key_warnings,
    unknown_key_message,
    unknown_key_warnings,
)
from .errors import ConfigError

log = logging.getLogger(__name__)

# ---- document dataclasses ------------------------------------------------------


@dataclass(frozen=True)
class ClipSection:
    """A section that is your own video clip, with its own audio.

    A missing clip plays a titled slate for `slate_seconds`. With `strict` that is an error,
    unless the section is `optional`, as the scaffold's B-roll slot is.
    """

    number: int
    clip: str
    title: str = ""
    slate_seconds: float = 5.0
    optional: bool = False

    @property
    def key(self) -> str:
        return f"{self.number:02d}"

    @property
    def is_clip(self) -> bool:
        return True


@dataclass(frozen=True)
class PageSection:
    """A section recorded from an HTML page, cut to the narration."""

    number: int
    page: str
    scene: str
    title: str = ""
    extra_seconds: float = 0.3
    hold_seconds: float = 0.0
    ambience: bool = False
    params: dict[str, str] = field(default_factory=dict)

    @property
    def key(self) -> str:
        return f"{self.number:02d}"

    @property
    def is_clip(self) -> bool:
        return False


Section = ClipSection | PageSection


@dataclass(frozen=True)
class Voice:
    """ElevenLabs voice settings. `model` may be overridden per project too."""

    provider: str = "elevenlabs"  # a registered SpeechProvider name
    model: str | None = None  # falls back to settings.narration.model
    stability: float = 0.55
    similarity_boost: float = 0.75
    style: float = 0.0
    speaker_boost: bool = True
    speed: float = 1.0

    def api_settings(self) -> dict[str, Any]:
        return {
            "stability": self.stability,
            "similarity_boost": self.similarity_boost,
            "style": self.style,
            "use_speaker_boost": self.speaker_boost,
            "speed": self.speed,
        }


@dataclass(frozen=True)
class Transition:
    dips: tuple[tuple[int, int], ...] | None = None  # None: dip at every cut
    dip_seconds: float = 0.15
    page_fades_in: bool = True


@dataclass(frozen=True)
class Loudnorm:
    i: float = -16.0
    tp: float = -1.5
    lra: float = 11.0


@dataclass(frozen=True)
class Sfx:
    file: str
    section: int
    cue: str
    db: float = -16.0
    offset: float = 0.0


@dataclass(frozen=True)
class Mix:
    underscore: str | None = None
    underscore_db: float = -24.0
    underscore_duck_db: float = -6.0
    underscore_fade_in: float = 2.0
    underscore_fade_out: float = 3.0
    markers: str | None = None
    ambience: str | None = None
    ambience_db: float = -20.0
    slate: str | None = None
    sfx: tuple[Sfx, ...] = ()
    loudnorm: Loudnorm = Loudnorm()


@dataclass(frozen=True)
class SoundSpec:
    text: str
    out: str | None = None
    duration_seconds: float | None = None
    prompt_influence: float | None = None
    model_id: str | None = None


@dataclass(frozen=True)
class MusicSpec:
    prompt: str
    seconds: int = 360
    force_instrumental: bool = True
    out: str | None = None
    model_id: str | None = None


@dataclass(frozen=True)
class Soundscape:
    ambience: SoundSpec | None = None
    sfx: dict[str, SoundSpec] = field(default_factory=dict)
    music: MusicSpec | None = None

    @property
    def empty(self) -> bool:
        return self.ambience is None and not self.sfx and self.music is None


# ---- parsing --------------------------------------------------------------------


class _Table:
    """Typed access to one TOML table with error messages that name the location."""

    def __init__(self, data: dict[str, Any], where: str) -> None:
        self.data = data
        self.where = where

    def _get(self, key: str, kind: type | tuple[type, ...], default: Any, required: bool) -> Any:
        if key not in self.data:
            if required:
                raise ConfigError(f"{self.where}: missing required key '{key}'")
            return default
        value = self.data[key]
        if isinstance(value, bool) and kind in (int, float, (int, float)):
            raise ConfigError(f"{self.where}: '{key}' must be a number, got a boolean")
        if not isinstance(value, kind):
            names = kind.__name__ if isinstance(kind, type) else " or ".join(k.__name__ for k in kind)
            raise ConfigError(f"{self.where}: '{key}' must be {names}, got {type(value).__name__}")
        return value

    def get_str(self, key: str, default: str | None = None, *, required: bool = False) -> Any:
        return self._get(key, str, default, required)

    def get_num(self, key: str, default: float | None = None, *, required: bool = False) -> Any:
        v = self._get(key, (int, float), default, required)
        return None if v is None else float(v)

    def get_int(self, key: str, default: int | None = None, *, required: bool = False) -> Any:
        return self._get(key, int, default, required)

    def get_bool(self, key: str, default: bool = False) -> bool:
        return bool(self._get(key, bool, default, False))

    def get_table(self, key: str) -> dict[str, Any] | None:
        return self._get(key, dict, None, False)

    def get_tables(self, key: str) -> list[dict[str, Any]]:
        items = self._get(key, list, [], False)
        for i, item in enumerate(items):
            if not isinstance(item, dict):
                raise ConfigError(f"{self.where}: [[{key}]] #{i + 1} must be a table")
        return items

    def unknown(self, known: set[str]) -> list[str]:
        return sorted(set(self.data) - known)

    def warn_unknown(self, known: Iterable[str]) -> None:
        """Log a warning for every key this table does not read. DeckTalk ignores such a key."""
        for message in unknown_key_warnings(self.data, known, self.where):
            log.warning(message)


VOICE_KEYS = frozenset({"provider", "model", "stability", "similarity_boost", "style", "speaker_boost", "speed"})
CLIP_KEYS = frozenset({"number", "title", "clip", "slate_seconds", "optional"})
PAGE_KEYS = frozenset({"number", "title", "page", "scene", "extra_seconds", "hold_seconds", "ambience", "params"})
SOUND_KEYS = frozenset({"text", "out", "duration_seconds", "prompt_influence", "model_id"})


def _warn_section_keys(t: _Table, *, clip: bool) -> None:
    """Warn about keys a section does not read, and name the section kind a misplaced key belongs to."""
    own, other, kind = (CLIP_KEYS, PAGE_KEYS, "page") if clip else (PAGE_KEYS, CLIP_KEYS, "clip")
    for key in sorted(set(t.data) - own):
        if key in other:
            log.warning("%s: ignoring '%s', which applies only to a %s section", t.where, key, kind)
        else:
            log.warning(unknown_key_message(key, own, t.where))


def _parse_section(raw: dict[str, Any], index: int) -> Section:
    t = _Table(raw, f"{PROJECT_FILE}: [[section]] #{index}")
    number = t.get_int("number", required=True)
    where = f"{PROJECT_FILE}: [[section]] number={number}"
    t.where = where
    title = t.get_str("title", "")
    if "clip" in raw and "page" in raw:
        raise ConfigError(f"{where}: give either 'clip' or 'page', not both")
    if "clip" in raw:
        _warn_section_keys(t, clip=True)
        return ClipSection(
            number=number,
            clip=t.get_str("clip"),
            title=title,
            slate_seconds=t.get_num("slate_seconds", 5.0),
            optional=t.get_bool("optional"),
        )
    if "page" not in raw:
        raise ConfigError(f"{where}: needs 'page' (an HTML file) or 'clip' (a video file)")
    _warn_section_keys(t, clip=False)
    params_raw = t.get_table("params") or {}
    scene = raw.get("scene", number)
    if isinstance(scene, bool) or not isinstance(scene, (int, str)):
        raise ConfigError(f"{where}: 'scene' must be a number or a string")
    return PageSection(
        number=number,
        page=t.get_str("page"),
        scene=str(scene),
        title=title,
        extra_seconds=t.get_num("extra_seconds", 0.3),
        hold_seconds=t.get_num("hold_seconds", 0.0),
        ambience=t.get_bool("ambience"),
        params={str(k): str(v) for k, v in params_raw.items()},
    )


def _parse_sections(doc: dict[str, Any]) -> list[Section]:
    raw = _Table(doc, PROJECT_FILE).get_tables("section")
    if not raw:
        raise ConfigError(f"{PROJECT_FILE}: no [[section]] tables; add one per '## N.' section of the script")
    sections = [_parse_section(item, i + 1) for i, item in enumerate(raw)]
    numbers = [s.number for s in sections]
    dupes = sorted({n for n in numbers if numbers.count(n) > 1})
    if dupes:
        raise ConfigError(f"{PROJECT_FILE}: duplicate section number(s) {dupes}")
    return sorted(sections, key=lambda s: s.number)


def _parse_voice(doc: dict[str, Any]) -> Voice:
    raw = doc.get("voice")
    if raw is None:
        return Voice()
    t = _Table(raw, f"{PROJECT_FILE}: [voice]")
    t.warn_unknown(VOICE_KEYS)
    return Voice(
        provider=t.get_str("provider", "elevenlabs"),
        model=t.get_str("model"),
        stability=t.get_num("stability", 0.55),
        similarity_boost=t.get_num("similarity_boost", 0.75),
        style=t.get_num("style", 0.0),
        speaker_boost=t.get_bool("speaker_boost", True),
        speed=t.get_num("speed", 1.0),
    )


def _parse_transition(doc: dict[str, Any], numbers: set[int]) -> Transition:
    raw = doc.get("transition")
    if raw is None:
        return Transition()
    t = _Table(raw, f"{PROJECT_FILE}: [transition]")
    t.warn_unknown({"dips", "dip_seconds", "page_fades_in"})
    dips_raw = raw.get("dips")
    dips: tuple[tuple[int, int], ...] | None = None
    if dips_raw is not None:
        if not isinstance(dips_raw, list):
            raise ConfigError(f"{t.where}: 'dips' must be a list of [from, to] pairs")
        pairs: list[tuple[int, int]] = []
        for pair in dips_raw:
            if not (isinstance(pair, list) and len(pair) == 2 and all(isinstance(x, int) for x in pair)):
                raise ConfigError(f"{t.where}: dips entry {pair!r} is not a [from, to] pair of section numbers")
            if pair[0] not in numbers or pair[1] not in numbers:
                raise ConfigError(f"{t.where}: dips entry {pair!r} names a section that does not exist")
            pairs.append((pair[0], pair[1]))
        dips = tuple(pairs)
    return Transition(
        dips=dips, dip_seconds=t.get_num("dip_seconds", 0.15), page_fades_in=t.get_bool("page_fades_in", True)
    )


def _parse_mix(doc: dict[str, Any], numbers: set[int]) -> Mix:
    raw = doc.get("mix")
    if raw is None:
        return Mix()
    t = _Table(raw, f"{PROJECT_FILE}: [mix]")
    t.warn_unknown(Mix.__dataclass_fields__)
    ln_raw = t.get_table("loudnorm") or {}
    ln = _Table(ln_raw, f"{PROJECT_FILE}: [mix.loudnorm]")
    ln.warn_unknown({"I", "TP", "LRA", "i", "tp", "lra"})
    sfx: list[Sfx] = []
    for i, item in enumerate(t.get_tables("sfx")):
        s = _Table(item, f"{PROJECT_FILE}: [[mix.sfx]] #{i + 1}")
        s.warn_unknown({"file", "section", "cue", "db", "offset"})
        section = s.get_int("section", required=True)
        if section not in numbers:
            raise ConfigError(f"{s.where}: section {section} does not exist")
        sfx.append(
            Sfx(
                file=s.get_str("file", required=True),
                section=section,
                cue=s.get_str("cue", required=True),
                db=s.get_num("db", -16.0),
                offset=s.get_num("offset", 0.0),
            )
        )
    return Mix(
        underscore=t.get_str("underscore"),
        underscore_db=t.get_num("underscore_db", -24.0),
        underscore_duck_db=t.get_num("underscore_duck_db", -6.0),
        underscore_fade_in=t.get_num("underscore_fade_in", 2.0),
        underscore_fade_out=t.get_num("underscore_fade_out", 3.0),
        markers=t.get_str("markers"),
        ambience=t.get_str("ambience"),
        ambience_db=t.get_num("ambience_db", -20.0),
        slate=t.get_str("slate"),
        sfx=tuple(sfx),
        loudnorm=Loudnorm(
            i=ln.get_num("I", ln.get_num("i", -16.0)),
            tp=ln.get_num("TP", ln.get_num("tp", -1.5)),
            lra=ln.get_num("LRA", ln.get_num("lra", 11.0)),
        ),
    )


def _parse_sound(raw: dict[str, Any], where: str) -> SoundSpec:
    t = _Table(raw, where)
    t.warn_unknown(SOUND_KEYS)
    return SoundSpec(
        text=t.get_str("text", required=True),
        out=t.get_str("out"),
        duration_seconds=t.get_num("duration_seconds"),
        prompt_influence=t.get_num("prompt_influence"),
        model_id=t.get_str("model_id"),
    )


def _parse_soundscape(doc: dict[str, Any]) -> Soundscape:
    raw = doc.get("soundscape")
    if raw is None:
        return Soundscape()
    t = _Table(raw, f"{PROJECT_FILE}: [soundscape]")
    t.warn_unknown({"ambience", "sfx", "music"})
    amb_raw = t.get_table("ambience")
    sfx_raw = t.get_table("sfx") or {}
    music_raw = t.get_table("music")
    sfx = {}
    for name, item in sfx_raw.items():
        if not isinstance(item, dict):
            raise ConfigError(f"{PROJECT_FILE}: [soundscape.sfx.{name}] must be a table")
        sfx[str(name)] = _parse_sound(item, f"{PROJECT_FILE}: [soundscape.sfx.{name}]")
    music = None
    if music_raw is not None:
        m = _Table(music_raw, f"{PROJECT_FILE}: [soundscape.music]")
        m.warn_unknown({"prompt", "seconds", "force_instrumental", "out", "model_id"})
        music = MusicSpec(
            prompt=m.get_str("prompt", required=True),
            seconds=m.get_int("seconds", 360),
            force_instrumental=m.get_bool("force_instrumental", True),
            out=m.get_str("out"),
            model_id=m.get_str("model_id"),
        )
    return Soundscape(
        ambience=_parse_sound(amb_raw, f"{PROJECT_FILE}: [soundscape.ambience]") if amb_raw is not None else None,
        sfx=sfx,
        music=music,
    )


def load_dotenv(path: Path) -> dict[str, str]:
    """Tiny .env reader: KEY=value, optional quotes, # comments, export prefix."""
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export ") :]
        key, _, value = line.partition("=")
        value = value.strip()
        if value[:1] in "\"'" and value.count(value[0]) >= 2:
            quote = value[0]
            value = value[1 : value.index(quote, 1)]  # quoted: take the inside, ignore a trailing comment
        else:
            value = value.split(" #", 1)[0].strip()
        values[key.strip()] = value
    return values


# ---- the project ----------------------------------------------------------------


@dataclass
class Project:
    """A loaded, validated project. Build with Project.load(directory)."""

    root: Path
    name: str
    script: Path
    cues: Path
    build: Path
    sections: list[Section]
    voice: Voice
    transition: Transition
    mix: Mix
    soundscape: Soundscape
    settings: Settings

    @classmethod
    def load(cls, where: Path | str | None = None, *, environ: dict[str, str] | None = None) -> Project:
        root = Path(where or os.environ.get("DECKTALK_PROJECT") or ".").resolve()
        if root.is_file() and root.name == PROJECT_FILE:
            root = root.parent
        if not (root / PROJECT_FILE).exists():
            raise ConfigError(
                f"{root / PROJECT_FILE} not found. Run from a project directory, pass --project DIR, "
                "or create one with `decktalk init DIR`."
            )
        doc = read_project_toml(root)
        return cls.from_toml(root, doc, environ=environ)

    @classmethod
    def from_toml(cls, root: Path, doc: dict[str, Any], *, environ: dict[str, str] | None = None) -> Project:
        root = root.resolve()
        top = _Table(doc, PROJECT_FILE)
        known = {"project", "voice", "section", "transition", "mix", "soundscape"} | set(Settings.__dataclass_fields__)
        unknown = top.unknown(known)
        if unknown:
            raise ConfigError(f"{PROJECT_FILE}: unknown table(s) {unknown}; known: {sorted(known)}")
        proj = _Table(top.get_table("project") or {}, f"{PROJECT_FILE}: [project]")
        proj.warn_unknown({"name", "script", "cues", "build"})
        sections = _parse_sections(doc)
        numbers = {s.number for s in sections}
        project = cls(
            root=root,
            name=proj.get_str("name", root.name),
            script=root / proj.get_str("script", "script.md"),
            cues=root / proj.get_str("cues", "cues.json"),
            build=root / proj.get_str("build", "build"),
            sections=sections,
            voice=_parse_voice(doc),
            transition=_parse_transition(doc, numbers),
            mix=_parse_mix(doc, numbers),
            soundscape=_parse_soundscape(doc),
            settings=load_settings(root, toml=doc, environ=environ),
        )
        for message in settings_key_warnings(doc, PROJECT_FILE):
            log.warning(message)
        project._check_holds()
        return project

    def _check_holds(self) -> None:
        pages = [s for s in self.sections if isinstance(s, PageSection)]
        holders = [s for s in pages if s.hold_seconds > 0]
        if holders and (len(holders) > 1 or holders[0] is not pages[-1]):
            raise ConfigError(
                f"{PROJECT_FILE}: hold_seconds is allowed only on the last page section ({pages[-1].number}); "
                "the narration is continuous, so holding earlier would push every later visual off its words"
            )

    # ---- paths -------------------------------------------------------------------
    def path(self, rel: str | Path) -> Path:
        p = Path(rel)
        return p if p.is_absolute() else self.root / p

    @property
    def audio_dir(self) -> Path:
        return self.build / "audio"

    @property
    def rec_dir(self) -> Path:
        return self.build / "rec"

    @property
    def out_dir(self) -> Path:
        return self.build / "out"

    @property
    def shots_dir(self) -> Path:
        return self.build / "shots"

    @property
    def manifest_path(self) -> Path:
        return self.audio_dir / "manifest.json"

    @property
    def timeline_path(self) -> Path:
        return self.audio_dir / "timeline.json"

    @property
    def beats_path(self) -> Path:
        return self.audio_dir / "beats.json"

    @property
    def final(self) -> Path:
        return self.out_dir / f"{self.name}.mp4"

    @property
    def env_file(self) -> Path:
        return self.root / ".env"

    def recording(self, section: Section) -> Path:
        return self.rec_dir / f"{section.key}-scene.webm"

    def section_video(self, section: Section) -> Path:
        return self.out_dir / f"{section.key}-section.mp4"

    # ---- secrets ---------------------------------------------------------------------
    def env(self, key: str) -> str:
        value = os.environ.get(key) or load_dotenv(self.env_file).get(key, "")
        return "" if value.startswith("<") else value

    def require_env(self, *keys: str) -> list[str]:
        values = [self.env(k) for k in keys]
        missing = [k for k, v in zip(keys, values, strict=True) if not v]
        if missing:
            raise ConfigError(
                f"{', '.join(missing)} not set. Put them in {self.env_file} (see .env.example) "
                "or export them. Keys are never printed."
            )
        return values

    # ---- sections --------------------------------------------------------------------
    def section(self, number: int) -> Section | None:
        return next((s for s in self.sections if s.number == number), None)

    @property
    def page_sections(self) -> list[PageSection]:
        return [s for s in self.sections if isinstance(s, PageSection)]

    @property
    def clip_sections(self) -> list[ClipSection]:
        return [s for s in self.sections if isinstance(s, ClipSection)]

    @property
    def clip_numbers(self) -> set[int]:
        return {s.number for s in self.clip_sections}

    @property
    def page_files(self) -> list[str]:
        seen: list[str] = []
        for s in self.page_sections:
            if s.page not in seen:
                seen.append(s.page)
        return seen

    # ---- artifacts -------------------------------------------------------------------
    def manifest(self) -> Manifest | None:
        return Manifest.load(self.manifest_path)

    def timeline(self) -> Timeline | None:
        return Timeline.load(self.timeline_path)

    def beats(self) -> Beats:
        return Beats.load(self.beats_path)
