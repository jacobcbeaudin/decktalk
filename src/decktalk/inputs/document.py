"""The `decktalk.toml` document: the frozen tables that say what this presentation is.

    [project]                name, script, cues, build, language
    [[section]]              number, chapter, then either page and scene, or clip
    [transition]             dips, dip_seconds, page_fades_in
    [mix]                    music, ambience, music_markers, effects and their levels
    [score]                  the prompts `decktalk score` generates from

Everything here changes per presentation. What a setting changes is tuning and lives in `settings.py`,
which is also where `[voice]` lives, and secrets live only in `.env`. `[mix]` and `[score]` are
shared: this module reads the content half and the settings layer reads the settings, so neither
warns about the other's keys. Every value is
read through `tomlmap.Table`, so a bad file fails at load with the table, the key and the line
named, rather than deep inside ffmpeg.
"""

from __future__ import annotations

from dataclasses import MISSING, dataclass, field, fields
from pathlib import PurePosixPath
from types import NoneType
from typing import Any, cast, get_args, get_type_hints

from decktalk.errors import InputError
from decktalk.results import SectionKind, section_key
from decktalk.settings import BY_ID, PROJECT_FILE, Settings
from decktalk.tomlmap import Table, unknown_key_message


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
        return section_key(self.number)

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
    section's first word, and `tail_seconds` replaces `[narration] tail_seconds`, the silence
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
    tail_seconds: float | None = None  # None uses [narration] tail_seconds.

    @property
    def key(self) -> str:
        return section_key(self.number)

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
    offset_seconds: float = 0.0
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
    """One sound to ask for: its prompt and its file, and what an effect sets for itself.

    `model`, `duration_seconds` and `prompt_influence` are None for the settings of
    `[score.effects]` or `[score.ambience]`, which the ambience bed always reads, because
    its own table is where those settings live.
    """

    prompt: str
    out: str | None = None
    duration_seconds: float | None = None
    prompt_influence: float | None = None
    model: str | None = None


@dataclass(frozen=True)
class MusicSpec:
    """The music to ask for: its prompt, whether it is instrumental, and its file.

    How long it is and which model makes it are the settings of `[score.music]`.
    """

    prompt: str
    force_instrumental: bool = True
    out: str | None = None


@dataclass(frozen=True)
class Score:
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
TOP_TABLES = frozenset({"project", "section", "transition", "mix", "score"})


@dataclass(frozen=True)
class Document:
    """One parsed and validated `decktalk.toml`, with every path still relative to the project."""

    name: str
    script: str
    cues: str
    build: str
    language: str
    sections: list[Section]
    transition: Transition
    mix: Mix
    score: Score
    notes: tuple[str, ...] = ()
    """One sentence per key the parser read past, which a run reports rather than a log line nobody sees."""

    @classmethod
    def from_toml(cls, doc: dict[str, Any], *, default_name: str) -> Document:
        """Parse the whole document. An unknown table is an error, and an unknown key a note."""
        top = Table(doc, PROJECT_FILE)
        known = TOP_TABLES | set(Settings.__dataclass_fields__)
        unknown = top.unknown(known)
        if unknown:
            raise InputError(f"{PROJECT_FILE}: unknown table(s) {unknown}. The known tables are {sorted(known)}.")
        project = Table(top.get_table("project") or {}, f"{PROJECT_FILE}: [project]")
        notes = project.note_unknown(PROJECT_KEYS)
        sections = parse_sections(doc, notes)
        numbers = {s.number for s in sections}
        return cls(
            name=project.get_str("name", default_name),
            script=project.get_path("script", "script.md"),
            cues=project.get_path("cues", "cues.json"),
            build=project.get_path("build", "build"),
            language=project.get_str("language", "en"),
            sections=sections,
            transition=parse_transition(doc, numbers, notes),
            mix=parse_mix(doc, numbers, notes),
            score=parse_score(doc, notes),
            notes=tuple(notes),
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
    def page_files(self) -> list[str]:
        """Each page file once, in section order."""
        return list(dict.fromkeys(s.page for s in self.page_sections))


def tuning_keys(table: str) -> set[str]:
    """The keys of one shared table that the settings layer owns, so the document warns for neither.

    `[mix]` and every table of `[score]` hold settings beside the content this module parses, so
    a reader of one of them has to know both halves before it can call a key unknown.
    """
    return {key.name for key in BY_ID.values() if key.table == table}


PATH_KEYS = ("clip", "words", "page", "file", "music", "music_markers", "ambience", "slate", "out")
"""The text keys of the project tables that name a project file, which `get_path` keeps inside the project."""


def fill[T](t: Table, cls: type[T], **given: object) -> T:
    """One dataclass read from a table, each field through the getter its type names and with the default it declares.

    A field in `given` is read by the caller, which is how a key with a rule of its own stays
    explicit, and a text field named in `PATH_KEYS` is read as a project path.
    """
    hints = get_type_hints(cls)
    readers: dict[type, Any] = {str: t.get_str, float: t.get_num, int: t.get_int, bool: t.get_bool}
    values = dict(given)
    for each in fields(cast("Any", cls)):
        if each.name in values:
            continue
        kind = next(arg for arg in get_args(hints[each.name]) or (hints[each.name],) if arg is not NoneType)
        read = t.get_path if kind is str and each.name in PATH_KEYS else readers[kind]
        missing = each.default is MISSING
        values[each.name] = read(each.name, required=True) if missing else read(each.name, each.default)
    return cls(**values)


def section_key_notes(t: Table, *, clip: bool) -> list[str]:
    """One note per key a section does not read, naming the section kind a misplaced key belongs to."""
    own, other, kind = (CLIP_KEYS, PAGE_KEYS, SectionKind.PAGE) if clip else (PAGE_KEYS, CLIP_KEYS, SectionKind.CLIP)
    return [
        f"{t.where}: ignoring '{key}', which applies only to a {kind.value} section"
        if key in other
        else unknown_key_message(key, own, t.where)
        for key in sorted(set(t.data) - own)
    ]


def parse_section(raw: dict[str, Any], index: int, notes: list[str]) -> Section:
    t = Table(raw, f"{PROJECT_FILE}: [[section]] #{index}")
    number = t.get_int("number", required=True)
    t.where = f"{PROJECT_FILE}: [[section]] number={number}"
    if "clip" in raw and "page" in raw:
        raise InputError(f"{t.where}: give either 'clip' or 'page', not both")
    if "clip" in raw:
        notes += section_key_notes(t, clip=True)
        return fill(t, ClipSection, number=number)
    if "page" not in raw:
        raise InputError(f"{t.where}: needs 'page' (an HTML file) or 'clip' (a video file)")
    notes += section_key_notes(t, clip=False)
    page = t.get_path("page", "")
    # The origin serves a page's whole directory, so a page at the root would be handed the
    # script, the cue file, the build directory and everything else the project holds.
    if PurePosixPath(page.replace("\\", "/")).parent == PurePosixPath("."):
        raise InputError(
            f"{t.where}: 'page' sits at the project root, and a page is served with its whole directory.",
            hint="Move the page into a directory of its own, such as deck/index.html, and name that path.",
        )
    params_raw = t.get_table("params") or {}
    scene = raw.get("scene", number)
    if isinstance(scene, bool) or not isinstance(scene, (int, str)):
        raise InputError(f"{t.where}: 'scene' must be a number or a string")
    for key in ("record_margin_seconds", "hold_seconds", "lead_seconds", "tail_seconds"):
        value = t.get_num(key)
        if value is not None and value < 0:
            raise InputError(f"{t.where}: '{key}' must be 0 or more, got {value:g}")
    params = {str(k): str(v) for k, v in params_raw.items()}
    return fill(t, PageSection, number=number, page=page, scene=str(scene), params=params)


def parse_sections(doc: dict[str, Any], notes: list[str]) -> list[Section]:
    raw = Table(doc, PROJECT_FILE).get_tables("section")
    if not raw:
        raise InputError(f"{PROJECT_FILE}: no [[section]] tables. Add one per '## N.' section of the script.")
    sections = [parse_section(item, i + 1, notes) for i, item in enumerate(raw)]
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


def parse_transition(doc: dict[str, Any], numbers: set[int], notes: list[str]) -> Transition:
    raw = doc.get("transition")
    if raw is None:
        return Transition()
    t = Table(raw, f"{PROJECT_FILE}: [transition]")
    notes += t.note_unknown(Transition.__dataclass_fields__)
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
    return fill(t, Transition, dips=dips)


def parse_mix(doc: dict[str, Any], numbers: set[int], notes: list[str]) -> Mix:
    raw = doc.get("mix")
    if raw is None:
        return Mix()
    t = Table(raw, f"{PROJECT_FILE}: [mix]", table="mix")
    notes += t.note_unknown(set(Mix.__dataclass_fields__) | tuning_keys("mix"), anywhere=BY_ID)
    effects: list[MixEffect] = []
    for i, item in enumerate(t.get_tables("effects")):
        s = Table(item, f"{PROJECT_FILE}: [[mix.effects]] #{i + 1}")
        notes += s.note_unknown(MixEffect.__dataclass_fields__)
        section = s.get_int("section", required=True)
        if section not in numbers:
            raise InputError(f"{s.where}: section {section} does not exist")
        effects.append(fill(s, MixEffect, section=section))
    return fill(t, Mix, effects=tuple(effects))


def parse_sound(raw: dict[str, Any], where: str, notes: list[str]) -> SoundSpec:
    """One effect, which may set its own model, length and prompt influence over `[score.effects]`."""
    t = Table(raw, where)
    notes += t.note_unknown(SoundSpec.__dataclass_fields__)
    return fill(t, SoundSpec)


def parse_score(doc: dict[str, Any], notes: list[str]) -> Score:
    """The items `[score]` declares, leaving every key the settings layer reads to it.

    Each item's table holds its settings beside its prompt, so the ambience bed's and the music's
    settings are read once, by the settings layer, where an override can reach them, and an effect
    reads only what its own table sets for itself.
    """
    raw = doc.get("score")
    if raw is None:
        return Score()
    t = Table(raw, f"{PROJECT_FILE}: [score]", table="score")
    notes += t.note_unknown(set(Score.__dataclass_fields__) | tuning_keys("score"), anywhere=BY_ID)
    amb_raw = t.get_table("ambience")
    ambience = None
    if amb_raw is not None:
        a = Table(amb_raw, f"{PROJECT_FILE}: [score.ambience]", table="score.ambience")
        settings = tuning_keys("score.ambience")
        notes += a.note_unknown({"prompt", "out"} | settings, anywhere=BY_ID)
        # The bed's own settings are read by the settings layer, so the ones an effect may set for
        # itself are left empty here and the rest, such as its rate, are no field of an item at all.
        ambience = fill(a, SoundSpec, **dict.fromkeys(settings & set(SoundSpec.__dataclass_fields__)))
    effects_raw = t.get_table("effects") or {}
    settings = tuning_keys("score.effects")
    named = {str(name) for name, item in effects_raw.items() if isinstance(item, dict)}
    e = Table(effects_raw, f"{PROJECT_FILE}: [score.effects]", table="score.effects")
    notes += e.note_unknown(settings | named, anywhere=BY_ID)
    effects: dict[str, SoundSpec] = {}
    for name in sorted(named, key=list(effects_raw).index):
        if name in settings:
            # The settings layer reads this name as the setting every effect shares, so an effect
            # called by it would be read as that setting rather than as a sound.
            raise InputError(
                f"{PROJECT_FILE}: [score.effects.{name}] is named after a setting of [score.effects].",
                hint=f"Give the effect another name. Every effect shares the settings {', '.join(sorted(settings))}.",
            )
        effects[name] = parse_sound(effects_raw[name], f"{PROJECT_FILE}: [score.effects.{name}]", notes)
    music_raw = t.get_table("music")
    music = None
    if music_raw is not None:
        m = Table(music_raw, f"{PROJECT_FILE}: [score.music]", table="score.music")
        notes += m.note_unknown(set(MusicSpec.__dataclass_fields__) | tuning_keys("score.music"), anywhere=BY_ID)
        music = fill(m, MusicSpec)
    return Score(ambience=ambience, effects=effects, music=music)


def frame_dip(dip_seconds: float, fps: int) -> float:
    """The dip length quantized to whole frames, so a fade never ends part way through one."""
    if dip_seconds <= 0:
        return 0.0
    return round(max(round(dip_seconds * fps), 1) / fps, 4)
