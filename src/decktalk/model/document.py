"""The `decktalk.toml` document: the frozen tables that say what this presentation is.

    [project]                name, script, cues, build
    [voice]                  the speech settings for this presentation
    [[section]]              number, chapter, then either page+scene or clip
    [transition]             dips, dip_seconds, page_fades_in
    [mix]                    music, ambience, music_markers, sfx, levels, loudness
    [soundscape]             prompts for `decktalk soundscape`

Everything here changes per presentation. What changes per machine is the tuning in
`settings.py`, and secrets live only in `.env`. Every value is read through `tomlmap.Table`, so a
bad file fails at load with the table and the key named, not deep inside ffmpeg.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from ..errors import ConfigError
from ..pipeline import SectionKind
from ..settings import PROJECT_FILE, Settings
from ..tomlmap import Table, unknown_key_message

log = logging.getLogger(__name__)


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
    """Speech settings. `model` may be overridden per project too."""

    provider: str = "elevenlabs"  # a registered SpeechProvider name
    model: str | None = None  # falls back to settings.narration.model
    stability: float = 0.55
    similarity_boost: float = 0.75
    style: float = 0.0
    speaker_boost: bool = True
    speed: float = 1.0
    price_per_1000_characters: float = 0.0  # What this project's plan charges, which `narrate --dry-run` prices.

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
    """One sound file played at a cue, with the caption line a viewer reads when it plays."""

    file: str
    section: int
    cue: str
    db: float = -16.0
    offset: float = 0.0
    caption: str = ""


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


# The fields of Document that [project] fills. Every other table's keys are the fields of its own class.
PROJECT_KEYS = frozenset({"name", "script", "cues", "build", "language"})
# The keys each kind of section reads, which are the fields of its own class and nothing else.
CLIP_KEYS = frozenset(ClipSection.__dataclass_fields__)
PAGE_KEYS = frozenset(PageSection.__dataclass_fields__)
TOP_TABLES = frozenset({"project", "voice", "section", "transition", "mix", "soundscape"})


@dataclass(frozen=True)
class Document:
    """One parsed and validated `decktalk.toml`, with every path still relative to the project."""

    name: str
    script: str
    cues: str
    build: str
    language: str
    sections: list[Section]
    voice: Voice
    transition: Transition
    mix: Mix
    soundscape: Soundscape

    @classmethod
    def from_toml(cls, doc: dict[str, Any], *, default_name: str) -> Document:
        """Parse the whole document. An unknown table is an error, and an unknown key a warning."""
        top = Table(doc, PROJECT_FILE)
        known = TOP_TABLES | set(Settings.__dataclass_fields__)
        unknown = top.unknown(known)
        if unknown:
            raise ConfigError(f"{PROJECT_FILE}: unknown table(s) {unknown}. The known tables are {sorted(known)}.")
        project = Table(top.get_table("project") or {}, f"{PROJECT_FILE}: [project]")
        project.warn_unknown(PROJECT_KEYS)
        sections = parse_sections(doc)
        numbers = {s.number for s in sections}
        return cls(
            name=project.get_str("name", default_name),
            script=project.get_str("script", "script.md"),
            cues=project.get_str("cues", "cues.json"),
            build=project.get_str("build", "build"),
            language=project.get_str("language", "en"),
            sections=sections,
            voice=parse_voice(doc),
            transition=parse_transition(doc, numbers),
            mix=parse_mix(doc, numbers),
            soundscape=parse_soundscape(doc),
        )

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
        """Each page file once, in section order."""
        seen: list[str] = []
        for s in self.page_sections:
            if s.page not in seen:
                seen.append(s.page)
        return seen


def warn_section_keys(t: Table, *, clip: bool) -> None:
    """Warn about keys a section does not read, and name the section kind a misplaced key belongs to."""
    own, other, kind = (CLIP_KEYS, PAGE_KEYS, SectionKind.PAGE) if clip else (PAGE_KEYS, CLIP_KEYS, SectionKind.CLIP)
    for key in sorted(set(t.data) - own):
        if key in other:
            log.warning("%s: ignoring '%s', which applies only to a %s section", t.where, key, kind.value)
        else:
            log.warning(unknown_key_message(key, own, t.where))


def parse_section(raw: dict[str, Any], index: int) -> Section:
    t = Table(raw, f"{PROJECT_FILE}: [[section]] #{index}")
    number = t.get_int("number", required=True)
    t.where = f"{PROJECT_FILE}: [[section]] number={number}"
    chapter = t.get_str("chapter", "")
    if "clip" in raw and "page" in raw:
        raise ConfigError(f"{t.where}: give either 'clip' or 'page', not both")
    if "clip" in raw:
        warn_section_keys(t, clip=True)
        return ClipSection(
            number=number,
            clip=t.get_str("clip", ""),
            chapter=chapter,
            slate_seconds=t.get_num("slate_seconds", 5.0),
            optional=t.get_bool("optional"),
            words=t.get_str("words"),
            seamless=t.get_bool("seamless"),
        )
    if "page" not in raw:
        raise ConfigError(f"{t.where}: needs 'page' (an HTML file) or 'clip' (a video file)")
    warn_section_keys(t, clip=False)
    params_raw = t.get_table("params") or {}
    scene = raw.get("scene", number)
    if isinstance(scene, bool) or not isinstance(scene, (int, str)):
        raise ConfigError(f"{t.where}: 'scene' must be a number or a string")
    for key in ("record_margin_seconds", "hold_seconds", "lead_seconds", "tail_seconds"):
        value = t.get_num(key)
        if value is not None and value < 0:
            raise ConfigError(f"{t.where}: '{key}' must be 0 or more, got {value:g}")
    return PageSection(
        number=number,
        page=t.get_str("page", ""),
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


def parse_sections(doc: dict[str, Any]) -> list[Section]:
    raw = Table(doc, PROJECT_FILE).get_tables("section")
    if not raw:
        raise ConfigError(f"{PROJECT_FILE}: no [[section]] tables. Add one per '## N.' section of the script.")
    sections = [parse_section(item, i + 1) for i, item in enumerate(raw)]
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


def parse_voice(doc: dict[str, Any]) -> Voice:
    raw = doc.get("voice")
    if raw is None:
        return Voice()
    t = Table(raw, f"{PROJECT_FILE}: [voice]")
    t.warn_unknown(Voice.__dataclass_fields__)
    return Voice(
        provider=t.get_str("provider", "elevenlabs"),
        model=t.get_str("model"),
        stability=t.get_num("stability", 0.55),
        similarity_boost=t.get_num("similarity_boost", 0.75),
        style=t.get_num("style", 0.0),
        speaker_boost=t.get_bool("speaker_boost", True),
        speed=t.get_num("speed", 1.0),
        price_per_1000_characters=t.get_num("price_per_1000_characters", 0.0),
    )


def parse_transition(doc: dict[str, Any], numbers: set[int]) -> Transition:
    raw = doc.get("transition")
    if raw is None:
        return Transition()
    t = Table(raw, f"{PROJECT_FILE}: [transition]")
    t.warn_unknown(Transition.__dataclass_fields__)
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


def parse_mix(doc: dict[str, Any], numbers: set[int]) -> Mix:
    raw = doc.get("mix")
    if raw is None:
        return Mix()
    t = Table(raw, f"{PROJECT_FILE}: [mix]")
    t.warn_unknown(Mix.__dataclass_fields__)
    ln = Table(t.get_table("loudness") or {}, f"{PROJECT_FILE}: [mix.loudness]")
    ln.warn_unknown(Loudness.__dataclass_fields__)
    sfx: list[Sfx] = []
    for i, item in enumerate(t.get_tables("sfx")):
        s = Table(item, f"{PROJECT_FILE}: [[mix.sfx]] #{i + 1}")
        s.warn_unknown(Sfx.__dataclass_fields__)
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
                caption=s.get_str("caption", ""),
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


def parse_sound(raw: dict[str, Any], where: str) -> SoundSpec:
    t = Table(raw, where)
    t.warn_unknown(SoundSpec.__dataclass_fields__)
    return SoundSpec(
        text=t.get_str("text", required=True),
        out=t.get_str("out"),
        duration_seconds=t.get_num("duration_seconds"),
        prompt_influence=t.get_num("prompt_influence"),
        model_id=t.get_str("model_id"),
    )


def parse_soundscape(doc: dict[str, Any]) -> Soundscape:
    raw = doc.get("soundscape")
    if raw is None:
        return Soundscape()
    t = Table(raw, f"{PROJECT_FILE}: [soundscape]")
    t.warn_unknown(Soundscape.__dataclass_fields__)
    amb_raw = t.get_table("ambience")
    sfx: dict[str, SoundSpec] = {}
    for name, item in (t.get_table("sfx") or {}).items():
        if not isinstance(item, dict):
            raise ConfigError(f"{PROJECT_FILE}: [soundscape.sfx.{name}] must be a table")
        sfx[str(name)] = parse_sound(item, f"{PROJECT_FILE}: [soundscape.sfx.{name}]")
    music_raw = t.get_table("music")
    music = None
    if music_raw is not None:
        m = Table(music_raw, f"{PROJECT_FILE}: [soundscape.music]")
        m.warn_unknown(MusicSpec.__dataclass_fields__)
        music = MusicSpec(
            prompt=m.get_str("prompt", required=True),
            seconds=m.get_int("seconds", 360),
            force_instrumental=m.get_bool("force_instrumental", True),
            out=m.get_str("out"),
            model_id=m.get_str("model_id"),
        )
    return Soundscape(
        ambience=parse_sound(amb_raw, f"{PROJECT_FILE}: [soundscape.ambience]") if amb_raw is not None else None,
        sfx=sfx,
        music=music,
    )
