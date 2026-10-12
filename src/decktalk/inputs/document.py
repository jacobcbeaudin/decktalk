"""The `decktalk.toml` document: the frozen tables that say what this presentation is.

    [project]                name, script, cues, build, language
    [[section]]              number, chapter, then page and scene, render_command, or clip
    [transition]             dips, dip_seconds, page_fades_in
    [mix]                    music, ambience, music_markers, effects and their levels
    [score]                  the prompts `decktalk score` generates from

Everything here changes per presentation. What a setting changes is tuning and lives in `settings`,
which is also where each voice's `[voices.NAME]` table lives, and secrets live only in `.env`.
 `[mix]` and `[score]` are
shared: this module reads the content half and the settings layer reads the settings, so neither
warns about the other's keys. Every value is
read through `tomlmap.Table`, so a bad file fails at load with the table, the key and the line
named, rather than deep inside ffmpeg.
"""

from __future__ import annotations

from dataclasses import MISSING, dataclass, field, fields
from pathlib import Path, PurePosixPath
from types import NoneType
from typing import Any, cast, get_args, get_type_hints

from decktalk.errors import InputError
from decktalk.page import Q
from decktalk.results import SectionKind, SoundKind, section_key
from decktalk.settings import BY_ID, PROJECT_FILE, Settings
from decktalk.tomlmap import Bounds, Key, declared, tune
from decktalk.tomlmap.read import Table
from decktalk.tomlmap.suggest import did_you_mean, unknown_key_message


@dataclass(frozen=True)
class ClipSection:
    """A section that is your own video clip, with its own audio.

    A missing clip plays a titled slate for `slate_seconds`. With `strict` that is an error,
    unless the section declares `optional`, which is how a project says the slate is the point.
    The speech inside the clip is captioned from the words file beside it, `<clip>.words.json`.
    `seamless` says the clip continues the previous section's picture, which verify checks.
    """

    number: int
    clip: str
    chapter: str = ""
    slate_seconds: float = tune(
        5.0,
        "How long the titled slate plays when the clip's file is not there.",
        unit="seconds",
        bounds=Bounds(gt=0, le=60),
        hazard="A slate has to last a frame to be in the film at all, and one past a minute is a hole in the film "
        "rather than a placeholder.",
    )
    optional: bool = False
    seamless: bool = False

    @property
    def key(self) -> str:
        return section_key(self.number)

    @property
    def is_clip(self) -> bool:
        return True


FREEZE_QUERY_KEYS = frozenset(key.value for key in (Q.CUES, Q.T0, Q.FREEZE, Q.AFTER, Q.BEFORE))
"""The query keys DeckTalk sets itself on a still, so a section's own params never override them."""


def hold_seconds_key() -> float:
    """The `hold_seconds` a page section and a render section both read, declared once."""
    return tune(
        0.0,
        "Seconds the page keeps playing after its narration, while the narration pauses for it.",
        unit="seconds",
        bounds=Bounds(ge=0, le=60),
        hazard="The narration pauses for the hold, so a hold past a minute is a minute of film with nothing said.",
    )


def lead_seconds_key() -> float | None:
    """The `lead_seconds` a page section and a render section both read, declared once."""
    return tune(
        None,
        "The silence before this section's first word, in place of `[narration] lead_seconds`.",
        unit="seconds",
        bounds=BY_ID["narration.lead_seconds"].bounds,
        see_also=("narration.lead_seconds",),
    )


def tail_seconds_key() -> float | None:
    """The `tail_seconds` a page section and a render section both read, declared once."""
    return tune(
        None,
        "The silence after this section's last word, in place of `[narration] tail_seconds`.",
        unit="seconds",
        bounds=BY_ID["narration.tail_seconds"].bounds,
        see_also=("narration.tail_seconds",),
    )


@dataclass(frozen=True)
class PageSection:
    """A section recorded from an HTML page, cut to the narration.

    `seamless` says the page opens on the previous section's last picture, so the cut
    into it should not show. verify compares the two frames.

    `lead_seconds` replaces `[narration] lead_seconds`, the silence in the narration before the
    section's first word, and `tail_seconds` replaces `[narration] tail_seconds`, the silence
    after its last. Both are placed when the takes are joined and never sent to the voice, so
    changing either never makes a take stale. `hold_seconds` keeps the page playing after its
    narration, and the narration pauses for it.
    """

    number: int
    page: str
    scene: str
    chapter: str = ""
    hold_seconds: float = hold_seconds_key()
    with_ambience: bool = False
    params: dict[str, str] = field(default_factory=dict)
    seamless: bool = False
    lead_seconds: float | None = lead_seconds_key()
    tail_seconds: float | None = tail_seconds_key()

    @property
    def key(self) -> str:
        return section_key(self.number)

    @property
    def is_clip(self) -> bool:
        return False

    @property
    def freeze_params(self) -> dict[str, str]:
        """The params a still of this section carries, which is every one DeckTalk does not set itself.

        A frozen frame and the poster both ask the page for a state rather than for the film, so they
        set `freeze`, `after` and `before` themselves and pass the author's own params through.
        """
        return {k: v for k, v in self.params.items() if k not in FREEZE_QUERY_KEYS}


@dataclass(frozen=True)
class RenderSection:
    """A section whose picture a command draws, narrated exactly as a page section is and naming no page.

    `record` hands `render_command` the section's words and cue seconds and takes the video it writes.
    The section has no page and no scene, so a key only a page reads is a note rather than a value
    nothing would ever play. `hold_seconds`, `lead_seconds`, `tail_seconds`, `with_ambience` and
    `seamless` mean what they mean on a page section.
    """

    number: int
    render_command: tuple[str, ...]
    chapter: str = ""
    hold_seconds: float = hold_seconds_key()
    with_ambience: bool = False
    seamless: bool = False
    lead_seconds: float | None = lead_seconds_key()
    tail_seconds: float | None = tail_seconds_key()

    @property
    def key(self) -> str:
        return section_key(self.number)

    @property
    def is_clip(self) -> bool:
        return False


Section = ClipSection | PageSection | RenderSection

RecordedSection = PageSection | RenderSection
"""A section `record` makes the picture of, from a page or from a command, which the narration runs through."""


ZERO_FADE_HAZARD = (
    "ffmpeg reads a fade of no length as its own default, which is about a second, so the shortest fade is one "
    "above zero."
)
"""Why every fade a project writes is refused at zero, which a fade filter treats as unset rather than as none."""

LEVEL_HAZARD = (
    "The narration plays at its own level, so a level above 0 dB plays this layer louder than the voice, and "
    "below -60 dB the loudness pass leaves nothing audible. Leave the file out for none."
)
"""Why a layer's level sits between -60 and 0 dB, which is the same for every layer the mix lays under the voice."""


@dataclass(frozen=True)
class Transition:
    """How one section is joined to the next."""

    dips: tuple[tuple[int, int], ...] | None = None  # None: dip at every cut
    dip_seconds: float = tune(
        0.15,
        "How long a dip to black lasts, rounded to whole frames so a fade never ends part way through one.",
        unit="seconds",
        bounds=Bounds(gt=0, le=1),
        hazard=f"{ZERO_FADE_HAZARD} Write `dips = []` for straight cuts. A dip longer than "
        "`verify.after_dip_seconds` is still dark on the frame the start check reads.",
        see_also=("verify.after_dip_seconds",),
    )
    page_fades_in: bool = True


@dataclass(frozen=True)
class MixEffect:
    """One sound file played at a cue, with the caption line a viewer reads when it plays."""

    file: str
    section: int
    cue: str
    db: float = tune(-16.0, "The level.", unit="dB", bounds=Bounds(ge=-60, le=0), hazard=LEVEL_HAZARD)
    offset_seconds: float = tune(
        0.0,
        "Seconds added to the cue time. This offset belongs to the sound and not to the cue.",
        unit="seconds",
        bounds=Bounds(ge=-10, le=10),
        hazard="An effect answers its cue, so a nudge past ten seconds plays it at a moment the cue does not name.",
    )
    caption: str = ""


@dataclass(frozen=True)
class Mix:
    """The content half of `[mix]`: the sound files the film plays and the level each one plays at."""

    music: str | None = None
    music_db: float = tune(-24.0, "The music level.", unit="dB", bounds=Bounds(ge=-60, le=0), hazard=LEVEL_HAZARD)
    music_duck_db: float = tune(
        -6.0,
        "The extra level change under each spoken span and each clip.",
        unit="dB",
        bounds=Bounds(ge=-40, le=0),
        hazard="A duck lowers the music under speech, so a value above zero would raise it there instead.",
    )
    music_fade_in_seconds: float = tune(
        2.0, "The fade in.", unit="seconds", bounds=Bounds(gt=0, le=30), hazard=ZERO_FADE_HAZARD
    )
    music_fade_out_seconds: float = tune(
        3.0, "The fade out.", unit="seconds", bounds=Bounds(gt=0, le=30), hazard=ZERO_FADE_HAZARD
    )
    music_markers: str | None = None
    ambience: str | None = None
    ambience_db: float = tune(-20.0, "The ambience level.", unit="dB", bounds=Bounds(ge=-60, le=0), hazard=LEVEL_HAZARD)
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
    duration_seconds: float | None = tune(
        None,
        "Length of this effect, in place of `[score.effects] duration_seconds`.",
        unit="seconds",
        bounds=BY_ID["score.effects.duration_seconds"].bounds,
    )
    prompt_influence: float | None = tune(
        None,
        "How closely this effect follows its prompt, in place of `[score.effects] prompt_influence`.",
        bounds=BY_ID["score.effects.prompt_influence"].bounds,
    )
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
RENDER_KEYS = frozenset(RenderSection.__dataclass_fields__)
KIND_KEYS = {SectionKind.PAGE: PAGE_KEYS, SectionKind.CLIP: CLIP_KEYS}
"""The keys each kind a misplaced key is named for reads, the page's first, which a note names that kind by."""
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
    def from_toml(cls, doc: dict[str, Any], *, default_name: str, text: str = "") -> Document:
        """Parse the whole document. An unknown table is an error, and an unknown key a note.

        `text` is the file as it was written, which is how a refused value names its line.
        """
        top = Table(doc, PROJECT_FILE, Path(PROJECT_FILE), text=text)
        known = TOP_TABLES | set(Settings.__dataclass_fields__)
        unknown = top.unknown(known)
        if unknown:
            raise InputError(
                f"{PROJECT_FILE}: unknown table(s) {unknown}. The known tables are {sorted(known)}."
                f"{did_you_mean(unknown[0], known)}"
            )
        project = top.inner(top.get_table("project") or {}, f"{PROJECT_FILE}: [project]", table="project")
        notes = project.note_unknown(PROJECT_KEYS)
        sections = parse_sections(top, notes)
        numbers = {s.number for s in sections}
        return cls(
            name=project.get_str("name", default_name),
            script=project.get_path("script", "script.md"),
            cues=project.get_path("cues", "cues.json"),
            build=project.get_path("build", "build"),
            language=project.get_str("language", "en"),
            sections=sections,
            transition=parse_transition(top, numbers, notes),
            mix=parse_mix(top, numbers, notes),
            score=parse_score(top, notes),
            notes=tuple(notes),
        )

    def section(self, number: int) -> Section | None:
        return next((s for s in self.sections if s.number == number), None)

    @property
    def recorded_sections(self) -> list[RecordedSection]:
        """Every section `record` makes the picture of, a page's or a command's, in section order."""
        return [s for s in self.sections if isinstance(s, (PageSection, RenderSection))]

    @property
    def page_sections(self) -> list[PageSection]:
        """Every section a page draws."""
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


PATH_KEYS = ("clip", "page", "file", "music", "music_markers", "ambience", "slate", "out")
"""The text keys of the project tables that name a project file, which `get_path` keeps inside the project."""


def fill[T](t: Table, cls: type[T], **given: object) -> T:
    """One dataclass read from a table, each field through the getter its type names and with the default it declares.

    A field in `given` is read by the caller, which is how a key with a rule of its own stays
    explicit, and a text field named in `PATH_KEYS` is read as a project path. A field `tune()`
    declared is held to the range it declares, so a value outside it is refused at its line rather
    than reaching ffmpeg.
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
        value = read(each.name, required=True) if missing else read(each.name, each.default)
        values[each.name] = t.held(each.name, value, each.metadata.get("bounds"), hazard=each.metadata.get("hazard"))
    return cls(**values)


def section_key_notes(t: Table, own: frozenset[str]) -> list[str]:
    """One note per key a section does not read among `own`, naming the section kind a misplaced key belongs to."""
    notes: list[str] = []
    for key in sorted(set(t.data) - own):
        kind = next((kind for kind, keys in KIND_KEYS.items() if key in keys), None)
        said = f"{t.where}: ignoring '{key}', which applies only to a {kind.value} section" if kind else None
        notes.append(said or unknown_key_message(key, own, t.where))
    return notes


def parse_section(top: Table, raw: dict[str, Any], index: int, notes: list[str]) -> Section:
    t = top.inner(raw, f"{PROJECT_FILE}: [[section]] #{index}", table="section", item=index)
    number = t.get_int("number", required=True)
    t.where = f"{PROJECT_FILE}: [[section]] number={number}"
    if "clip" in raw and "page" in raw:
        raise InputError(f"{t.where}: give either 'clip' or 'page', not both")
    if "render_command" in raw:
        return render_section(raw, t, number, notes)
    if "clip" in raw:
        notes += section_key_notes(t, CLIP_KEYS)
        return fill(t, ClipSection, number=number)
    if "page" not in raw:
        raise InputError(f"{t.where}: needs 'page' (an HTML file) or 'clip' (a video file)")
    notes += section_key_notes(t, PAGE_KEYS)
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
    params = {str(k): str(v) for k, v in params_raw.items()}
    return fill(t, PageSection, number=number, page=page, scene=str(scene), params=params)


def command_words(raw: object, where: str, key: str) -> tuple[str, ...]:
    """A command a project names, as its words, refused unless it is a list of words none of which is empty."""
    if not isinstance(raw, list) or not raw or not all(isinstance(word, str) and word for word in raw):
        raise InputError(f'{where}: \'{key}\' must be a list of words, such as ["{{python}}", "render/bars.py"]')
    return tuple(raw)


def render_section(raw: dict[str, Any], t: Table, number: int, notes: list[str]) -> RenderSection:
    """A narrated section whose picture a command draws, which names no page and no scene."""
    if "page" in raw or "clip" in raw:
        raise InputError(f"{t.where}: give one of 'page', 'clip' or 'render_command', not two")
    command = command_words(raw["render_command"], t.where, "render_command")
    notes += section_key_notes(t, RENDER_KEYS)
    return fill(t, RenderSection, number=number, render_command=command)


def parse_sections(top: Table, notes: list[str]) -> list[Section]:
    raw = top.get_tables("section")
    if not raw:
        raise InputError(f"{PROJECT_FILE}: no [[section]] tables. Add one per '## N.' section of the script.")
    sections = [parse_section(top, item, i, notes) for i, item in enumerate(raw, start=1)]
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


def parse_transition(top: Table, numbers: set[int], notes: list[str]) -> Transition:
    raw = top.get_table("transition")
    if raw is None:
        return Transition()
    t = top.inner(raw, f"{PROJECT_FILE}: [transition]", table="transition")
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


EFFECT_TABLE = SoundKind.EFFECT.value
"""The array of tables under `[mix]` that places a sound effect, named for the kind of sound it places and in the
singular like `[[section]]`."""

CONTENT_TABLES: dict[str, tuple[type, ...]] = {
    "section": (ClipSection, PageSection, RenderSection),
    "transition": (Transition,),
    "mix": (Mix,),
    f"mix.{EFFECT_TABLE}": (MixEffect,),
}
"""The tables of `decktalk.toml` whose numbers are project content, by the dotted name a refusal and the schema use.

An effect's own `[score.effects.NAME]` is left out, because its two numbers take the ranges of the
settings they replace, which the schema already publishes.
"""


def content_keys() -> tuple[Key, ...]:
    """Every number the project's own tables hold, with its default, its range and its reason, in table order.

    These are content rather than settings: no layer above the project file sets them, so they have
    no variable and no `--set`, and the schema publishes them beside the settings of the same file.
    """
    keys = (key for table, classes in CONTENT_TABLES.items() for cls in classes for key in declared(cls, table))
    return tuple({key.id: key for key in keys}.values())


def parse_mix(top: Table, numbers: set[int], notes: list[str]) -> Mix:
    raw = top.get_table("mix")
    if raw is None:
        return Mix()
    t = top.inner(raw, f"{PROJECT_FILE}: [mix]", table="mix")
    keys = set(Mix.__dataclass_fields__) - {"effects"} | {EFFECT_TABLE}
    notes += t.note_unknown(keys | tuning_keys("mix"), anywhere=BY_ID)
    effects: list[MixEffect] = []
    for i, item in enumerate(t.get_tables(EFFECT_TABLE), start=1):
        s = top.inner(item, f"{PROJECT_FILE}: [[mix.{EFFECT_TABLE}]] #{i}", table=f"mix.{EFFECT_TABLE}", item=i)
        notes += s.note_unknown(MixEffect.__dataclass_fields__)
        section = s.get_int("section", required=True)
        if section not in numbers:
            raise InputError(f"{s.where}: section {section} does not exist")
        effects.append(fill(s, MixEffect, section=section))
    return fill(t, Mix, effects=tuple(effects))


def parse_sound(top: Table, raw: dict[str, Any], name: str, notes: list[str]) -> SoundSpec:
    """One effect, which may set its own model, length and prompt influence over `[score.effects]`."""
    t = top.inner(raw, f"{PROJECT_FILE}: [score.effects.{name}]", table=f"score.effects.{name}")
    notes += t.note_unknown(SoundSpec.__dataclass_fields__)
    return fill(t, SoundSpec)


def parse_score(top: Table, notes: list[str]) -> Score:
    """The items `[score]` declares, leaving every key the settings layer reads to it.

    Each item's table holds its settings beside its prompt, so the ambience bed's and the music's
    settings are read once, by the settings layer, where an override can reach them, and an effect
    reads only what its own table sets for itself.
    """
    raw = top.get_table("score")
    if raw is None:
        return Score()
    t = top.inner(raw, f"{PROJECT_FILE}: [score]", table="score")
    notes += t.note_unknown(set(Score.__dataclass_fields__) | tuning_keys("score"), anywhere=BY_ID)
    amb_raw = t.get_table("ambience")
    ambience = None
    if amb_raw is not None:
        a = top.inner(amb_raw, f"{PROJECT_FILE}: [score.ambience]", table="score.ambience")
        settings = tuning_keys("score.ambience")
        notes += a.note_unknown({"prompt", "out"} | settings, anywhere=BY_ID)
        # The bed's own settings are read by the settings layer, so the ones an effect may set for
        # itself are left empty here and the rest, such as its rate, are no field of an item at all.
        ambience = fill(a, SoundSpec, **dict.fromkeys(settings & set(SoundSpec.__dataclass_fields__)))
    effects_raw = t.get_table("effects") or {}
    settings = tuning_keys("score.effects")
    named = {str(name) for name, item in effects_raw.items() if isinstance(item, dict)}
    e = top.inner(effects_raw, f"{PROJECT_FILE}: [score.effects]", table="score.effects")
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
        effects[name] = parse_sound(top, effects_raw[name], name, notes)
    music_raw = t.get_table("music")
    music = None
    if music_raw is not None:
        m = top.inner(music_raw, f"{PROJECT_FILE}: [score.music]", table="score.music")
        notes += m.note_unknown(set(MusicSpec.__dataclass_fields__) | tuning_keys("score.music"), anywhere=BY_ID)
        music = fill(m, MusicSpec)
    return Score(ambience=ambience, effects=effects, music=music)


def frame_dip(dip_seconds: float, fps: int) -> float:
    """The dip length quantized to whole frames, so a fade never ends part way through one."""
    if dip_seconds <= 0:
        return 0.0
    return round(max(round(dip_seconds * fps), 1) / fps, 4)
