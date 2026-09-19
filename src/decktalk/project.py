"""A DeckTalk project: a directory with decktalk.toml, a script, cues, HTML pages and media.

    my-lesson/
      decktalk.toml      the document (below) plus optional tuning tables (settings.py)
      script.md          narration; "## N. Title" sections, [bracketed directions] unspoken
      cues.json          which spoken phrase each visual lands on
      deck/index.html    HTML scenes; decktalk-runtime.js gives them the ?cues= contract
      media/             your clips, b-roll, slate, markers.json
      .env               ELEVENLABS_API_KEY, ELEVENLABS_VOICE_ID (never committed)
      build/             everything generated (git-ignored)

decktalk.toml, the document:

    [project]                name, script, cues, build
    [voice]                  ElevenLabs voice settings for this presentation
    [[section]]              number, chapter, then either page+scene or clip
    [transition]             dips, dip_seconds, page_fades_in
    [mix]                    music, ambience, music_markers, sfx, levels, loudness
    [soundscape]             prompts for `decktalk soundscape`

Every path is relative to the project directory. Validation happens here, so a bad
file fails at load with the table and field named, not deep inside ffmpeg.
"""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .artifacts import CueTimes, Takes, Timeline, Word, read_words
from .errors import ConfigError
from .settings import PROJECT_FILE, Settings, load_settings, read_project_toml, settings_key_warnings
from .tomlmap import unknown_key_message, unknown_key_warnings

log = logging.getLogger(__name__)

# ---- document dataclasses ------------------------------------------------------


@dataclass(frozen=True)
class ClipSection:
    """A section that is your own video clip, with its own audio.

    A missing clip plays a titled slate for `slate_seconds`. With `strict` that is an error,
    unless the section is `optional`, as the scaffold's B-roll slot is. `words` names a words
    file of the speech inside the clip, in seconds after the clip starts, which the captions add.
    `seamless` says the clip continues the previous section's picture, which verify checks.
    """

    number: int
    clip: str
    chapter: str = ""
    slate_seconds: float = 5.0
    optional: bool = False
    words: str | None = None
    seamless: bool = False

    @property
    def key(self) -> str:
        return f"{self.number:02d}"

    @property
    def is_clip(self) -> bool:
        return True


@dataclass(frozen=True)
class PageSection:
    """A section recorded from an HTML page, cut to the narration.

    `seamless` says the page opens on the previous section's last picture, so the cut
    into it should not show. verify compares the two frames.

    `lead_seconds` is silence in the narration before the section's first word. It is added when
    the takes are joined, not sent to the voice, so a cached take stays cached. `tail_seconds`
    replaces `[narration] min_tail_seconds` for this section. `hold_seconds` holds the section's
    last frame after its narration, and the narration pauses for it.
    """

    number: int
    page: str
    scene: str
    chapter: str = ""
    record_margin_seconds: float = 0.3
    hold_seconds: float = 0.0
    ambience: bool = False
    params: dict[str, str] = field(default_factory=dict)
    seamless: bool = False
    lead_seconds: float = 0.0
    tail_seconds: float | None = None  # None uses [narration] min_tail_seconds.

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
class Loudness:
    target_lufs: float = -16.0
    true_peak_db: float = -1.5
    range_lu: float = 11.0


@dataclass(frozen=True)
class Sfx:
    file: str
    section: int
    cue: str
    db: float = -16.0
    offset: float = 0.0


@dataclass(frozen=True)
class Mix:
    music: str | None = None
    music_db: float = -24.0
    music_duck_db: float = -6.0
    music_fade_in_seconds: float = 2.0
    music_fade_out_seconds: float = 3.0
    music_markers: str | None = None
    ambience: str | None = None
    ambience_db: float = -20.0
    slate: str | None = None
    sfx: tuple[Sfx, ...] = ()
    loudness: Loudness = Loudness()


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
CLIP_KEYS = frozenset({"number", "chapter", "clip", "slate_seconds", "optional", "words", "seamless"})
PAGE_KEYS = frozenset(
    {
        "number",
        "chapter",
        "page",
        "scene",
        "record_margin_seconds",
        "hold_seconds",
        "lead_seconds",
        "tail_seconds",
        "ambience",
        "params",
        "seamless",
    }
)
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
    chapter = t.get_str("chapter", "")
    if "clip" in raw and "page" in raw:
        raise ConfigError(f"{where}: give either 'clip' or 'page', not both")
    if "clip" in raw:
        _warn_section_keys(t, clip=True)
        return ClipSection(
            number=number,
            clip=t.get_str("clip"),
            chapter=chapter,
            slate_seconds=t.get_num("slate_seconds", 5.0),
            optional=t.get_bool("optional"),
            words=t.get_str("words"),
            seamless=t.get_bool("seamless"),
        )
    if "page" not in raw:
        raise ConfigError(f"{where}: needs 'page' (an HTML file) or 'clip' (a video file)")
    _warn_section_keys(t, clip=False)
    params_raw = t.get_table("params") or {}
    scene = raw.get("scene", number)
    if isinstance(scene, bool) or not isinstance(scene, (int, str)):
        raise ConfigError(f"{where}: 'scene' must be a number or a string")
    for key in ("record_margin_seconds", "hold_seconds", "lead_seconds", "tail_seconds"):
        value = t.get_num(key)
        if value is not None and value < 0:
            raise ConfigError(f"{where}: '{key}' must be 0 or more, got {value:g}")
    return PageSection(
        number=number,
        page=t.get_str("page"),
        scene=str(scene),
        chapter=chapter,
        record_margin_seconds=t.get_num("record_margin_seconds", 0.3),
        hold_seconds=t.get_num("hold_seconds", 0.0),
        ambience=t.get_bool("ambience"),
        params={str(k): str(v) for k, v in params_raw.items()},
        seamless=t.get_bool("seamless"),
        lead_seconds=t.get_num("lead_seconds", 0.0),
        tail_seconds=t.get_num("tail_seconds"),
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
    sections.sort(key=lambda s: s.number)
    if sections[0].seamless:
        raise ConfigError(
            f"{PROJECT_FILE}: [[section]] number={sections[0].number}: seamless is set on the first section, "
            "which has no previous section"
        )
    return sections


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
    ln_raw = t.get_table("loudness") or {}
    ln = _Table(ln_raw, f"{PROJECT_FILE}: [mix.loudness]")
    ln.warn_unknown({"target_lufs", "true_peak_db", "range_lu"})
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
        music=t.get_str("music"),
        music_db=t.get_num("music_db", -24.0),
        music_duck_db=t.get_num("music_duck_db", -6.0),
        music_fade_in_seconds=t.get_num("music_fade_in_seconds", 2.0),
        music_fade_out_seconds=t.get_num("music_fade_out_seconds", 3.0),
        music_markers=t.get_str("music_markers"),
        ambience=t.get_str("ambience"),
        ambience_db=t.get_num("ambience_db", -20.0),
        slate=t.get_str("slate"),
        sfx=tuple(sfx),
        loudness=Loudness(
            target_lufs=ln.get_num("target_lufs", -16.0),
            true_peak_db=ln.get_num("true_peak_db", -1.5),
            range_lu=ln.get_num("range_lu", 11.0),
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
        return project

    # ---- paths -------------------------------------------------------------------
    def path(self, rel: str | Path) -> Path:
        p = Path(rel)
        return p if p.is_absolute() else self.root / p

    @property
    def narration_dir(self) -> Path:
        return self.build / "narration"

    @property
    def recordings_dir(self) -> Path:
        return self.build / "recordings"

    @property
    def out_dir(self) -> Path:
        return self.build / "out"

    @property
    def sections_dir(self) -> Path:
        return self.build / "sections"

    @property
    def screenshots_dir(self) -> Path:
        return self.build / "screenshots"

    @property
    def takes_path(self) -> Path:
        return self.narration_dir / "takes.json"

    @property
    def timeline_path(self) -> Path:
        return self.narration_dir / "timeline.json"

    @property
    def cue_times_path(self) -> Path:
        return self.build / "cue-times.json"

    @property
    def final(self) -> Path:
        return self.out_dir / f"{self.name}.mp4"

    @property
    def env_file(self) -> Path:
        return self.root / ".env"

    def recording(self, section: Section) -> Path:
        return self.recordings_dir / f"{section.key}.webm"

    def recording_log(self, section: Section) -> Path:
        return self.recordings_dir / f"{section.key}.json"

    def section_video(self, section: Section) -> Path:
        return self.sections_dir / f"{section.key}.mp4"

    def stray_section_videos(self) -> list[Path]:
        """Files in build/sections named like a section video whose section is not in decktalk.toml.

        A build before sections were renumbered or removed leaves such files behind.
        """
        if not self.sections_dir.is_dir():
            return []
        listed = {self.section_video(s).name for s in self.sections}
        return sorted(
            f for f in self.sections_dir.iterdir() if re.fullmatch(r"\d+\.mp4", f.name) and f.name not in listed
        )

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

    def lead_seconds(self, key: str) -> float:
        """Silence before the first word of the section with this two-digit key, rounded to whole milliseconds."""
        sec = self.section(int(key))
        return round(sec.lead_seconds, 3) if isinstance(sec, PageSection) else 0.0

    def section_words(self, key: str, words_file: str) -> list[Word]:
        """A take's words in seconds after its section starts, which is after the section's lead_seconds."""
        lead = self.lead_seconds(key)
        words = read_words(self.narration_dir / words_file)
        if not lead:
            return words
        return [Word(w.word, round(w.start + lead, 3), round(w.end + lead, 3)) for w in words]

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
    def takes(self) -> Takes | None:
        return Takes.load(self.takes_path)

    def timeline(self) -> Timeline | None:
        return Timeline.load(self.timeline_path)

    def cue_times(self) -> CueTimes:
        return CueTimes.load(self.cue_times_path)
