"""`cues.json` read against the catalog the page itself published, and the fixes that reconcile them.

A cue and the moment it fires are one thing written in two files, so each is read against the other.
The page's side of it is the catalog the runtime publishes, which names every slide of a scene and
every moment its elements declare, already qualified into the wire id `cues.json` carries. It is
read from the catalog and never from a regex over the markup, because `data-steps`, `data-class` and
`data-owns` all declare moments no text scan can see.

A moment with no row is `CUE_MISSING`, whose fix adds the row and leaves the phrase for the author,
because the phrase is their line and not DeckTalk's. A row no page declares is `CUE_UNKNOWN`, which
names the row rather than deleting it, and when one row's phrase survives beside exactly one
undeclared moment the two are read as a rename and the fix changes the id alone.

Nothing here opens a browser or reads a project, so `cue` calls it with the catalogs the recordings
left on disk and `check` calls it with the catalogs it read live, and both reach the same verdicts.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from decktalk.findings import Applicability, Code, Edit, EditFix, Finding, Location
from decktalk.inputs.cues import CuedSection
from decktalk.inputs.document import PageSection
from decktalk.inputs.paths import relative
from decktalk.media.pagereport import SceneCatalog
from decktalk.pagescan import Measured
from decktalk.pipeline import Stage
from decktalk.stages import judge

CUES_FIELD = "cues"
"""What the catalog entry calls the map of the cues each slide of a scene declares."""

JSON_INDENT = 2
"""How a scaffolded `cues.json` is indented, which keeps a diff of one readable in a terminal."""

EMPTY_PHRASE = ""
"""What a scaffolded row leaves in `on`, because the phrase a cue lands on is the author's own line."""

SECTION_KEY = '"{number}"'
"""How `cues.json` keys one section, which is its number in `decktalk.toml` written as a string."""

CUES_KEY = re.compile(r'^(?P<indent>\s*)"cues"\s*:\s*\[')
"""The line a section's cue array opens on, which is where a scaffolded row is written."""

SECTIONS_KEY = re.compile(r'^(?P<indent>\s*)"sections"\s*:\s*\{')
"""The line the sections object opens on, which is where a whole new section block is written."""


def scene_cues(entry: SceneCatalog) -> tuple[str, ...]:
    """Every wire id one scene declares, in the order the catalog names them and without repeats.

    A moment reaches the catalog twice, once as the attribute of the element that draws it and once
    in the scene's own cue map, and the two agree. Both are read because a scene whose cues are
    served by a handler alone declares them in the map and on no element.
    """
    found = [wire for row in measured_rows(entry) for wire in row.moments.values() if wire]
    return tuple(dict.fromkeys(found + _listed(entry)))


def measured_rows(entry: SceneCatalog) -> list[Measured]:
    """Every element the probe measured on one scene, as the rows `pagescan` judges.

    The catalog speaks the page's own shapes and `pagescan` speaks the contract's, so this is the
    one place the two sit beside each other.
    """
    return [
        Measured(
            attrs=dict(row.attrs),
            moments=dict(row.moments),
            text=row.text,
            box=(int(row.box.x), int(row.box.y), int(row.box.w), int(row.box.h)),
        )
        for slide in entry.elements.values()
        for row in slide
    ]


def _listed(entry: SceneCatalog) -> list[str]:
    """The wire ids the scene's own cue map names, which is a map of slide to ids or a plain list."""
    listed = (entry.model_extra or {}).get(CUES_FIELD)
    if isinstance(listed, Mapping):
        return [str(wire) for ids in listed.values() for wire in _ids(ids)]
    return _ids(listed)


def _ids(given: object) -> list[str]:
    """One list of wire ids as the page wrote it, which is nothing at all when it wrote something else."""
    if isinstance(given, str | bytes) or not isinstance(given, Sequence):
        return []
    return [str(wire) for wire in given]


def declared_cues(
    catalogs: Mapping[str, Sequence[SceneCatalog]],
    sections: Iterable[PageSection],
) -> dict[int, tuple[str, ...]]:
    """Every wire id the scene each section plays declares, by section number.

    `catalogs` maps each page to the scenes it published. A section whose page published no catalog,
    or whose scene is not in it, is left out rather than mapped to nothing, so a caller can tell a
    scene that declares no moment from a page nobody has opened.
    """
    out: dict[int, tuple[str, ...]] = {}
    for section in sections:
        entries = catalogs.get(section.page)
        if entries is None:
            continue
        entry = next((one for one in entries if str(one.scene) == str(section.scene)), None)
        if entry is not None:
            out[section.number] = scene_cues(entry)
    return out


def cue_findings(
    declared: Mapping[int, Sequence[str]],
    cued: Sequence[CuedSection],
    *,
    cues_path: Path,
    root: Path,
    stage: Stage | None = None,
    allow_unknown: bool = False,
) -> list[Finding]:
    """Every moment with no row and every row no page declares, each with the fix that reconciles it.

    The edits are worked out one after another against the file as each earlier fix would leave it,
    because a line-addressed edit into a file another edit has already grown would otherwise land in
    the wrong place.
    """
    where = relative(cues_path, root)
    listed = {block.number: block for block in cued}
    if not cues_path.is_file():
        return _create_findings(declared, where=where, stage=stage)
    text = cues_path.read_text(encoding="utf-8")
    renames = _renames(declared, listed)
    found: list[Finding] = []
    for number in sorted(declared):
        missing = [wire for wire in declared[number] if wire not in _rows_of(listed, number)]
        scaffold = [wire for wire in missing if wire not in renames.get(number, {}).values()]
        if not missing:
            continue
        fix, text = _scaffold_fix(text, number, scaffold, where=where)
        found.append(_missing_finding(number, missing, renames.get(number, {}), where=where, stage=stage, fix=fix))
    if allow_unknown:
        return found
    for number in sorted(listed):
        if number not in declared:
            continue
        for row in listed[number].cues:
            if row.cue in declared[number]:
                continue
            fix, text = _rename_fix(text, row.cue, renames.get(number, {}).get(row.cue), where=where)
            found.append(_unknown_finding(number, row.cue, renames.get(number, {}).get(row.cue), where, stage, fix))
    return found


# ---- the judgements ---------------------------------------------------------------------------


def _missing_finding(
    number: int,
    missing: Sequence[str],
    renamed: Mapping[str, str],
    *,
    where: Path,
    stage: Stage | None,
    fix: EditFix | None,
) -> Finding:
    """One judgement per section, naming every moment of it that no row gives a second to."""
    named = ", ".join(missing)
    paired = {new: old for old, new in renamed.items()}
    also = (
        f" The row {next(iter(paired.values()))} looks like {next(iter(paired))} renamed, so its own fix resolves it."
        if paired
        else ""
    )
    return judge(
        Code.CUE_MISSING,
        f"section {number} declares {len(missing)} moment(s) that {where.as_posix()} does not list, which is "
        f"{named}, so nothing gives them a second.{also}",
        Location(where=where.as_posix(), file=where, section=number),
        stage=stage,
        fix=fix,
    )


def _unknown_finding(
    number: int,
    wire: str,
    renamed: str | None,
    where: Path,
    stage: Stage | None,
    fix: EditFix | None,
) -> Finding:
    """One judgement per row whose moment no page declares, which names the row and never removes it."""
    looks = f" It looks like {renamed} renamed, because its phrase is the one that survives." if renamed else ""
    return judge(
        Code.CUE_UNKNOWN,
        f"{where.as_posix()} lists {wire} in section {number} and no slide that section plays declares it, "
        f"so nothing plays it.{looks}",
        Location(where=wire, file=where, section=number, cue=wire),
        stage=stage,
        fix=fix,
    )


def _renames(declared: Mapping[int, Sequence[str]], listed: Mapping[int, CuedSection]) -> dict[int, dict[str, str]]:
    """Each section's one stale row against the one moment it was plainly renamed into.

    A rename is only ever read where it is unambiguous, which is one row nothing declares beside one
    moment nothing lists in the same section. The phrase is what survives a rename, so the row that
    carries it is the row the author already wrote for that moment.
    """
    out: dict[int, dict[str, str]] = {}
    for number, wires in declared.items():
        block = listed.get(number)
        if block is None:
            continue
        stale = [row for row in block.cues if row.cue not in wires and row.on]
        gained = [wire for wire in wires if wire not in {row.cue for row in block.cues}]
        if len(stale) == 1 and len(gained) == 1:
            out[number] = {stale[0].cue: gained[0]}
    return out


def _rows_of(listed: Mapping[int, CuedSection], number: int) -> set[str]:
    """Every wire id `cues.json` lists for one section, which is empty when it holds no block for it."""
    block = listed.get(number)
    return {row.cue for row in block.cues} if block else set()


# ---- the fixes --------------------------------------------------------------------------------


@dataclass(frozen=True)
class Placement:
    """Where one section's new rows go in the file as it stands, and how they have to be written there."""

    line: int
    replaces: str | None
    text: str


def _create_findings(declared: Mapping[int, Sequence[str]], *, where: Path, stage: Stage | None) -> list[Finding]:
    """The one judgement for a project with no cue file at all, whose fix writes the whole of one."""
    moments = sorted((number, wire) for number, wires in declared.items() for wire in wires)
    if not moments:
        return []
    document = {
        "sections": {
            str(number): {"cues": [{"cue": wire, "on": EMPTY_PHRASE} for wire in sorted(declared[number])]}
            for number in sorted(declared)
            if declared[number]
        }
    }
    fix = EditFix(
        title=(
            f"Write {where.as_posix()} with a row for each of the {len(moments)} moment(s) the deck declares, "
            "each waiting for the phrase you write in its `on`."
        ),
        applicability=Applicability.SAFE,
        edits=(Edit(file=where, line=1, old=None, new=json.dumps(document, indent=JSON_INDENT)),),
    )
    return [
        judge(
            Code.CUE_MISSING,
            f"the deck declares {len(moments)} moment(s) and there is no {where.as_posix()}, so nothing gives "
            "any of them a second.",
            Location(where=where.as_posix(), file=where),
            stage=stage,
            fix=fix,
        )
    ]


def _scaffold_fix(text: str, number: int, wires: Sequence[str], *, where: Path) -> tuple[EditFix | None, str]:
    """(the fix that adds this section's rows, the file as that fix would leave it).

    A fix only ever adds, so running it twice adds nothing the second time: the rows it wrote are
    declared and listed by then, and no moment of the section is missing any more.
    """
    if not wires:
        return None, text
    placement = _place(text, number, wires)
    if placement is None:
        return _by_hand(number, wires, where=where), text
    edit = Edit(file=where, line=placement.line, old=placement.replaces, new=placement.text)
    fix = EditFix(
        title=(
            f"Add {len(wires)} row(s) to section {number} of {where.as_posix()}, each waiting for the phrase "
            "you write in its `on`."
        ),
        applicability=Applicability.SAFE,
        edits=(edit,),
    )
    return fix, _as_applied(text, edit)


def _by_hand(number: int, wires: Sequence[str], *, where: Path) -> EditFix:
    """The fix for a file this module cannot place a row in, which is a change only a person can make.

    A hand-written `cues.json` may be laid out any way its author likes, and an edit that guessed
    where a row goes would be worse than an edit nobody made.
    """
    rows = ", ".join(json.dumps({"cue": wire, "on": EMPTY_PHRASE}) for wire in wires)
    return EditFix(
        title=f"Add these row(s) to section {number} of {where.as_posix()}: {rows}",
        applicability=Applicability.DISPLAY,
        edits=(Edit(file=where, line=1, old=None, new=rows),),
    )


def _place(text: str, number: int, wires: Sequence[str]) -> Placement | None:
    """Where this section's rows go in the file, or None when its cue array cannot be found.

    A cue array written over several lines is grown by inserting the new rows after the line it
    opens on, each with the comma that keeps the array valid. An array that opens and closes on one
    line is rewritten as the whole line, because there is no line inside it to insert before.
    """
    lines = text.splitlines()
    start = _section_line(lines, number)
    if start is None:
        return _new_block(lines, number, wires)
    for offset, line in enumerate(lines[start:], start=start):
        match = CUES_KEY.match(line)
        if not match:
            continue
        indent = match.group("indent") + "  "
        rows = [json.dumps({"cue": wire, "on": EMPTY_PHRASE}) for wire in wires]
        if "]" in line:
            inner = line[line.index("[") + 1 : line.rindex("]")].strip()
            kept = [*rows, inner] if inner else rows
            body = f",\n{indent}".join(kept)
            return Placement(
                line=offset + 1,
                replaces=line,
                text=f'{match.group("indent")}"cues": [\n{indent}{body}\n{match.group("indent")}]',
            )
        empty = _closes_next(lines, offset)
        joined = f",\n{indent}".join(rows)
        return Placement(line=offset + 2, replaces=None, text=f"{indent}{joined}" + ("" if empty else ","))
    return None


def _new_block(lines: Sequence[str], number: int, wires: Sequence[str]) -> Placement | None:
    """A whole block for a section the file holds none for, written as the first member of `sections`.

    It goes first rather than in number order because the member order of a JSON object says
    nothing, and because the line the object opens on does not move when another block is written
    before it, so two sections scaffolded by one call cannot land on top of each other.
    """
    found = ((index, SECTIONS_KEY.match(line)) for index, line in enumerate(lines))
    opened, match = next(((index, one) for index, one in found if one is not None), (None, None))
    if opened is None or match is None:
        return None
    indent = match.group("indent") + "  "
    rows = f",\n{indent}    ".join(json.dumps({"cue": wire, "on": EMPTY_PHRASE}) for wire in wires)
    after = next((line.strip() for line in lines[opened + 1 :] if line.strip()), "")
    comma = "" if after.startswith("}") else ","
    block = f'{indent}"{number}": {{\n{indent}  "cues": [\n{indent}    {rows}\n{indent}  ]\n{indent}}}{comma}'
    return Placement(line=opened + 2, replaces=None, text=block)


def _section_line(lines: Sequence[str], number: int) -> int | None:
    """The line one section's block opens on, or None when the file holds no block for it."""
    key = SECTION_KEY.format(number=number)
    return next((index for index, line in enumerate(lines) if line.lstrip().startswith(f"{key}:")), None)


def _closes_next(lines: Sequence[str], opened: int) -> bool:
    """Whether the cue array that opens on this line holds nothing, so no row needs a comma after it."""
    after = next((line.strip() for line in lines[opened + 1 :] if line.strip()), "")
    return after.startswith("]")


def _rename_fix(text: str, wire: str, renamed: str | None, *, where: Path) -> tuple[EditFix | None, str]:
    """(the fix that renames one stale row, the file as it would leave it), or none when nothing renamed it.

    It is unsafe rather than safe, because it rewrites an id the author typed and a pairing read
    from one surviving phrase is a reading rather than a fact.
    """
    if renamed is None:
        return None, text
    lines = text.splitlines()
    found = next((index for index, line in enumerate(lines) if f'"{wire}"' in line), None)
    if found is None:
        return None, text
    edit = Edit(file=where, line=found + 1, old=lines[found], new=lines[found].replace(f'"{wire}"', f'"{renamed}"'))
    fix = EditFix(
        title=f"Rename the cue {wire} to {renamed} in {where.as_posix()}, keeping the phrase it already waits for.",
        applicability=Applicability.UNSAFE,
        edits=(edit,),
    )
    return fix, _as_applied(text, edit)


def _as_applied(text: str, edit: Edit) -> str:
    """The file as this edit would leave it, which is how the next edit's line is worked out.

    It is the applier's own arithmetic, held here so that two fixes offered by one call cannot both
    be written against the same original line and land one on top of the other.
    """
    lines = text.splitlines(keepends=True)
    index = (edit.line or 1) - 1
    lines[index : index + (1 if edit.old is not None else 0)] = [edit.new + "\n"] if edit.new else []
    return "".join(lines)


__all__ = [
    "CUES_FIELD",
    "EMPTY_PHRASE",
    "Placement",
    "cue_findings",
    "declared_cues",
    "measured_rows",
    "scene_cues",
]
