"""The `decktalk.toml` document: the frozen tables that say what this presentation is.

    [project]                name, script, cues, build, language
    [voice]                  which voice reads this presentation
    [[section]]              number, chapter, then either page and scene, or clip
    [transition]             dips, dip_seconds, page_fades_in
    [mix]                    music, ambience, music_markers, effects and their levels
    [soundscape]             the prompts `decktalk soundscape` generates from

Everything here changes per presentation. What a knob changes is tuning and lives in `settings.py`,
and secrets live only in `.env`. `[voice]` and `[mix]` are shared: this module reads the content
half and the settings layer reads the knobs, so neither warns about the other's keys. Every value is
read through `tomlmap.Table`, so a bad file fails at load with the table, the key and the line
named, rather than deep inside ffmpeg.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from decktalk.errors import InputError
from decktalk.results import SectionKind
from decktalk.settings import BY_ID, PROJECT_FILE, Settings
from decktalk.tomlmap import Table, unknown_key_message

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ClipSection:
    """A section that is your own video clip, with its own audio.

    A missing clip plays a titled slate for `slate_seconds`. With `strict` that is an error,
    unless the section declares `optional`, which is how a project says the slate is the point.
    `words` names a words
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


# The query keys DeckTalk's own runtime sets on a still, which a section's params may not name.
FREEZE_QUERY_KEYS = ("cues", "t0", "slide", "after", "before")


@dataclass(frozen=True)
class PageSection:
    """A section recorded from an HTML page, cut to the narration.

    `seamless` says the page opens on the previous section's last picture, so the cut
    into it should not show. verify compares the two frames.

    `lead_seconds` replaces `[narration] lead_seconds`, the silence in the narration before the
    section's first word, and `tail_seconds` replaces `[narration] tail_min_seconds`, the silence
    after its last. Both are placed when the takes are joined, not sent to the voice, so a cached
    take stays cached. `hold_seconds` holds the section's
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
    lead_seconds: float | None = None  # None uses [narration] lead_seconds.
    tail_seconds: float | None = None  # None uses [narration] tail_min_seconds.

    @property
    def key(self) -> str:
        return f"{self.number:02d}"

    @property
    def is_clip(self) -> bool:
        return False

    @property
    def freeze_params(self) -> dict[str, str]:
        """The params a still of this section carries, which is every one the runtime does not set itself.

        A frozen frame and the poster both ask the page for a state rather than for the film, so they
        set `slide`, `after` and `before` themselves and pass the author's own params through.
        """
        return {k: v for k, v in self.params.items() if k not in FREEZE_QUERY_KEYS}


Section = ClipSection | PageSection


@dataclass(frozen=True)
class Voice:
    """Which voice reads this presentation, which is content. How it reads is `[voice]` tuning."""

    provider: str = "elevenlabs"
    """The speech provider this project is read by, which is a name the machine's own map answers."""

    model: str | None = None
    """The provider model, or None to take the one `[narration] model` names."""


@dataclass(frozen=True)
class Transition:
    dips: tuple[tuple[int, int], ...] | None = None  # None: dip at every cut
    dip_seconds: float = 0.15
    page_fades_in: bool = True


@dataclass(frozen=True)
class MixEffect:
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
    effects: tuple[MixEffect, ...] = ()


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
    effects: dict[str, SoundSpec] = field(default_factory=dict)
    music: MusicSpec | None = None

    @property
    def empty(self) -> bool:
        return self.ambience is None and not self.effects and self.music is None


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
            raise InputError(f"{PROJECT_FILE}: unknown table(s) {unknown}. The known tables are {sorted(known)}.")
        project = Table(top.get_table("project") or {}, f"{PROJECT_FILE}: [project]")
        warn(project.note_unknown(PROJECT_KEYS))
        sections = parse_sections(doc)
        numbers = {s.number for s in sections}
        return cls(
            name=project.get_str("name", default_name),
            script=project.get_path("script", "script.md"),
            cues=project.get_path("cues", "cues.json"),
            build=project.get_path("build", "build"),
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
    def fade_flags(self) -> dict[str, tuple[bool, bool]]:
        """(fade_in, fade_out) per section key, from `[transition] dips` and `page_fades_in`."""
        pairs = {(a, b) for a, b in self.transition.dips} if self.transition.dips is not None else None
        flags: dict[str, tuple[bool, bool]] = {}
        for i, sec in enumerate(self.sections):
            prev_n = self.sections[i - 1].number if i > 0 else None
            next_n = self.sections[i + 1].number if i + 1 < len(self.sections) else None
            if pairs is None:
                dip_in, dip_out = prev_n is not None, next_n is not None
            else:
                dip_in = prev_n is not None and (prev_n, sec.number) in pairs
                dip_out = next_n is not None and (sec.number, next_n) in pairs
            flags[sec.key] = (dip_in and not (not sec.is_clip and self.transition.page_fades_in), dip_out)
        return flags

    @property
    def cut_summary(self) -> str:
        """What happens at the section cuts: straight cuts, or dips at some or all of them."""
        dips = sum(1 for _fade_in, fade_out in self.fade_flags.values() if fade_out)
        if dips == 0:
            return "straight cuts"
        if self.transition.dips is None:
            return "dips at every cut"
        return f"dips at {dips} cut{'s' if dips != 1 else ''}"

    @property
    def page_files(self) -> list[str]:
        """Each page file once, in section order."""
        seen: list[str] = []
        for s in self.page_sections:
            if s.page not in seen:
                seen.append(s.page)
        return seen


def tuning_keys(table: str) -> set[str]:
    """The keys of one shared table that the settings layer owns, so the document warns for neither.

    `[voice]` and `[mix]` each hold knobs beside the content this module parses, so a reader of one
    of them has to know both halves before it can call a key unknown.
    """
    return {key.id.rsplit(".", 1)[1] for key in BY_ID.values() if key.id.rsplit(".", 1)[0] == table}


def warn(notes: list[str]) -> None:
    """Say what the document parser found, which is where an ignored key reaches a person today."""
    for note in notes:
        log.warning(note)


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
        raise InputError(f"{t.where}: give either 'clip' or 'page', not both")
    if "clip" in raw:
        warn_section_keys(t, clip=True)
        return ClipSection(
            number=number,
            clip=t.get_path("clip", ""),
            chapter=chapter,
            slate_seconds=t.get_num("slate_seconds", 5.0),
            optional=t.get_bool("optional"),
            words=t.get_path("words"),
            seamless=t.get_bool("seamless"),
        )
    if "page" not in raw:
        raise InputError(f"{t.where}: needs 'page' (an HTML file) or 'clip' (a video file)")
    warn_section_keys(t, clip=False)
    params_raw = t.get_table("params") or {}
    scene = raw.get("scene", number)
    if isinstance(scene, bool) or not isinstance(scene, (int, str)):
        raise InputError(f"{t.where}: 'scene' must be a number or a string")
    for key in ("record_margin_seconds", "hold_seconds", "lead_seconds", "tail_seconds"):
        value = t.get_num(key)
        if value is not None and value < 0:
            raise InputError(f"{t.where}: '{key}' must be 0 or more, got {value:g}")
    return PageSection(
        number=number,
        page=t.get_path("page", ""),
        scene=str(scene),
        chapter=chapter,
        record_margin_seconds=t.get_num("record_margin_seconds", 0.3),
        hold_seconds=t.get_num("hold_seconds", 0.0),
        ambience=t.get_bool("ambience"),
        params={str(k): str(v) for k, v in params_raw.items()},
        seamless=t.get_bool("seamless"),
        lead_seconds=t.get_num("lead_seconds"),
        tail_seconds=t.get_num("tail_seconds"),
    )


def parse_sections(doc: dict[str, Any]) -> list[Section]:
    raw = Table(doc, PROJECT_FILE).get_tables("section")
    if not raw:
        raise InputError(f"{PROJECT_FILE}: no [[section]] tables. Add one per '## N.' section of the script.")
    sections = [parse_section(item, i + 1) for i, item in enumerate(raw)]
    numbers = [s.number for s in sections]
    dupes = sorted({n for n in numbers if numbers.count(n) > 1})
    if dupes:
        raise InputError(f"{PROJECT_FILE}: duplicate section number(s) {dupes}")
    sections.sort(key=lambda s: s.number)
    if sections[0].seamless:
        raise InputError(
            f"{PROJECT_FILE}: [[section]] number={sections[0].number}: seamless is set on the first section, "
            "which has no previous section"
        )
    return sections


def parse_voice(doc: dict[str, Any]) -> Voice:
    raw = doc.get("voice")
    if raw is None:
        return Voice()
    t = Table(raw, f"{PROJECT_FILE}: [voice]", table="voice")
    warn(t.note_unknown(set(Voice.__dataclass_fields__) | tuning_keys("voice")))
    return Voice(provider=t.get_str("provider", "elevenlabs"), model=t.get_str("model"))


def parse_transition(doc: dict[str, Any], numbers: set[int]) -> Transition:
    raw = doc.get("transition")
    if raw is None:
        return Transition()
    t = Table(raw, f"{PROJECT_FILE}: [transition]")
    warn(t.note_unknown(Transition.__dataclass_fields__))
    dips_raw = raw.get("dips")
    dips: tuple[tuple[int, int], ...] | None = None
    if dips_raw is not None:
        if not isinstance(dips_raw, list):
            raise InputError(f"{t.where}: 'dips' must be a list of [from, to] pairs")
        pairs: list[tuple[int, int]] = []
        for pair in dips_raw:
            if not (isinstance(pair, list) and len(pair) == 2 and all(isinstance(x, int) for x in pair)):
                raise InputError(f"{t.where}: dips entry {pair!r} is not a [from, to] pair of section numbers")
            if pair[0] not in numbers or pair[1] not in numbers:
                raise InputError(f"{t.where}: dips entry {pair!r} names a section that does not exist")
            pairs.append((pair[0], pair[1]))
        dips = tuple(pairs)
    return Transition(
        dips=dips, dip_seconds=t.get_num("dip_seconds", 0.15), page_fades_in=t.get_bool("page_fades_in", True)
    )


def parse_mix(doc: dict[str, Any], numbers: set[int]) -> Mix:
    raw = doc.get("mix")
    if raw is None:
        return Mix()
    t = Table(raw, f"{PROJECT_FILE}: [mix]", table="mix")
    warn(t.note_unknown(set(Mix.__dataclass_fields__) | tuning_keys("mix") | {"loudness"}))
    effects: list[MixEffect] = []
    for i, item in enumerate(t.get_tables("effects")):
        s = Table(item, f"{PROJECT_FILE}: [[mix.effects]] #{i + 1}")
        warn(s.note_unknown(MixEffect.__dataclass_fields__))
        section = s.get_int("section", required=True)
        if section not in numbers:
            raise InputError(f"{s.where}: section {section} does not exist")
        effects.append(
            MixEffect(
                file=s.get_path("file", required=True),
                section=section,
                cue=s.get_str("cue", required=True),
                db=s.get_num("db", -16.0),
                offset=s.get_num("offset", 0.0),
                caption=s.get_str("caption", ""),
            )
        )
    return Mix(
        music=t.get_path("music"),
        music_db=t.get_num("music_db", -24.0),
        music_duck_db=t.get_num("music_duck_db", -6.0),
        music_fade_in_seconds=t.get_num("music_fade_in_seconds", 2.0),
        music_fade_out_seconds=t.get_num("music_fade_out_seconds", 3.0),
        music_markers=t.get_path("music_markers"),
        ambience=t.get_path("ambience"),
        ambience_db=t.get_num("ambience_db", -20.0),
        slate=t.get_path("slate"),
        effects=tuple(effects),
    )


def parse_sound(raw: dict[str, Any], where: str) -> SoundSpec:
    t = Table(raw, where)
    warn(t.note_unknown(SoundSpec.__dataclass_fields__))
    return SoundSpec(
        text=t.get_str("text", required=True),
        out=t.get_path("out"),
        duration_seconds=t.get_num("duration_seconds"),
        prompt_influence=t.get_num("prompt_influence"),
        model_id=t.get_str("model_id"),
    )


def parse_soundscape(doc: dict[str, Any]) -> Soundscape:
    raw = doc.get("soundscape")
    if raw is None:
        return Soundscape()
    t = Table(raw, f"{PROJECT_FILE}: [soundscape]")
    warn(t.note_unknown(Soundscape.__dataclass_fields__))
    amb_raw = t.get_table("ambience")
    effects: dict[str, SoundSpec] = {}
    for name, item in (t.get_table("effects") or {}).items():
        if not isinstance(item, dict):
            raise InputError(f"{PROJECT_FILE}: [soundscape.effects.{name}] must be a table")
        effects[str(name)] = parse_sound(item, f"{PROJECT_FILE}: [soundscape.effects.{name}]")
    music_raw = t.get_table("music")
    music = None
    if music_raw is not None:
        m = Table(music_raw, f"{PROJECT_FILE}: [soundscape.music]")
        warn(m.note_unknown(MusicSpec.__dataclass_fields__))
        music = MusicSpec(
            prompt=m.get_str("prompt", required=True),
            seconds=m.get_int("seconds", 360),
            force_instrumental=m.get_bool("force_instrumental", True),
            out=m.get_path("out"),
            model_id=m.get_str("model_id"),
        )
    return Soundscape(
        ambience=parse_sound(amb_raw, f"{PROJECT_FILE}: [soundscape.ambience]") if amb_raw is not None else None,
        effects=effects,
        music=music,
    )


def frame_dip(dip_seconds: float, fps: int) -> float:
    """The dip length quantized to whole frames, so a fade never ends part way through one."""
    if dip_seconds <= 0:
        return 0.0
    return round(max(round(dip_seconds * fps), 1) / fps, 4)
