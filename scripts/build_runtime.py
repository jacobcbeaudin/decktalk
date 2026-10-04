"""Build the runtime bundles, contract.json and src/decktalk/page.py from the TypeScript sources.

    npm ci                                          # once, for the pinned esbuild, tsc and Biome
    uv run scripts/build_runtime.py --write         # type check, bundle, and write every artifact
    uv run scripts/build_runtime.py --check         # exit 1 if any committed artifact would change

The page contract lives once, in `src/decktalk/runtime/src/contract.ts`. This script is the only
thing that reads it: esbuild builds the contract as a CommonJS module, node prints `CONTRACT` as
JSON, and every artifact below is written from that JSON. No regular expression ever reads
TypeScript, so a contract that compiles is a contract Python can be generated from.

    src/decktalk/runtime/decktalk-runtime.js the bundle a deck loads, whose first line names its version
    src/decktalk/runtime/decktalk-probe.js   the bundle the recorder injects into every page
    src/decktalk/runtime/contract.json       the intermediate, committed so the rest is pure Python
    src/decktalk/page.py                     the vocabulary the library and the CLI read

Each artifact is written through the formatter that owns its language, the pinned Biome for
JavaScript and the project's ruff for Python, so a generated file is as clean as a written one and
`--check` compares two files that were made the same way.
"""

from __future__ import annotations

import json
import sys
import tempfile
import textwrap
import tomllib
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

import generated

ROOT = Path(__file__).resolve().parent.parent
RUNTIME = ROOT / "src" / "decktalk" / "runtime"
SOURCE = RUNTIME / "src"
TSCONFIG = RUNTIME / "tsconfig.json"
BIN = ROOT / "node_modules" / ".bin"

PYPROJECT = ROOT / "pyproject.toml"
CONTRACT_ENTRY = SOURCE / "contract.ts"
CONTRACT_JSON = RUNTIME / "contract.json"
PAGE_MODULE = ROOT / "src" / "decktalk" / "page.py"

# Every bundle the runtime ships, from the entry point that builds it. A module reaches a bundle
# only by being imported from one of these, which is what keeps the probe free of the runtime.
BUNDLES: dict[str, Path] = {
    "decktalk-runtime.js": SOURCE / "index.ts",
    "decktalk-probe.js": SOURCE / "probe" / "probe.ts",
}

# The bundle a page loads, which is the one that opens with the banner naming its version. The probe
# is injected by the engine that recorded the page, so its version is never in question.
BANNERED = "decktalk-runtime.js"

# The first line of the page's bundle, filled from the contract's mark and the engine version, so a
# person who opens the file the origin served can read which engine shipped it.
BANNER = "/*! {mark} {version} */"

# The browsers a bundle must run in are the ones Playwright drives and the ones an author previews
# in, so the output is the newest syntax level every current engine parses.
TARGET = "es2022"


def tool(name: str) -> Path:
    """The pinned executable `npm ci` installed, which is the only version this script will use."""
    path = BIN / name
    if not path.exists():
        raise SystemExit(f"{path.relative_to(ROOT)} is missing, so run `npm ci` first")
    return path


def typecheck() -> None:
    """Every TypeScript source and every node test, checked against the contract's own types."""
    generated.command([tool("tsc"), "--noEmit", "-p", TSCONFIG])


def engine_version() -> str:
    """The version the engine is released as, which release-please writes into the project file."""
    return tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["project"]["version"]


def banner(data: dict[str, Any]) -> str:
    """The first line of the page's bundle, which names the engine version that shipped it."""
    return BANNER.format(mark=data["runtimeMark"], version=engine_version())


def bundle(entry: Path, name: str, first_line: str | None = None) -> str:
    """One entry point as a formatted script, which is what a page loads and what git holds."""
    cmd: list[str | Path] = [
        tool("esbuild"),
        entry,
        "--bundle",
        "--format=iife",
        f"--target={TARGET}",
        "--charset=utf8",
        "--legal-comments=inline",
    ]
    if first_line is not None:
        cmd.append(f"--banner:js={first_line}")
    return biome(generated.command(cmd), name)


def biome(text: str, name: str) -> str:
    """JavaScript through the pinned formatter, so a generated bundle is formatted like a written file."""
    return generated.command([tool("biome"), "format", f"--stdin-file-path={name}"], stdin=text)


def contract() -> dict[str, Any]:
    """The contract as JSON, printed by node from a CommonJS build of the one TypeScript module."""
    with tempfile.TemporaryDirectory() as tmp:
        built = Path(tmp) / "contract.cjs"
        generated.command(
            [tool("esbuild"), CONTRACT_ENTRY, "--bundle", "--format=cjs", f"--target={TARGET}", f"--outfile={built}"]
        )
        printed = generated.command(
            ["node", "-e", f"process.stdout.write(JSON.stringify(require({str(built)!r}).CONTRACT))"]
        )
    return json.loads(printed)


def contract_text(data: dict[str, Any]) -> str:
    """The committed intermediate, indented so a reviewer reads a diff of it line by line."""
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


# ---- the Python vocabulary ---------------------------------------------------------------------

PAGE_HEADER = '''"""The page contract as Python reads it: every attribute, the code that judges it and every query key.

Generated by `scripts/build_runtime.py` from `src/decktalk/runtime/src/contract.ts`. Do not edit
this file, edit the contract and run the script.

An element on a slide has four moments and one value type. It arrives, it steps back, it comes to
the front, and it leaves, and each of those writes a cue name, which means something inside its own
slide alone and which `CUE_MARK` joins to the slide's id in the cue id `cues.json` carries.
Everything else is either how a moment looks, which is a closed word, or what a moment means, which
is a sentence for the transcript.

This module is vocabulary and arithmetic and nothing else. It opens no file, reads no settings and
imports only the finding codes, so every layer above it may read the contract without a browser
and without a project. An attribute names the code that judges it, and that code's sentence, its
severity and who raises it live once, in `findings.Code`.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict

from decktalk.findings import Code
'''

PAGE_MODELS = '''

class Frozen(BaseModel):
    """A published record of the contract, which nothing may edit after the module is imported."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class Range(Frozen):
    """The values a number attribute admits, in the unit the tool can see."""

    min: float
    max: float
    step: float
    unit: str


class Effect(Frozen):
    """One closed word of a style set, with the motion it puts between its cue and the next one."""

    seconds: float
    lift_pixels: int = 0
    overshoot_percent: float = 0


class AttrSpec(Frozen):
    """One row of the attribute table, which is everything published about one attribute.

    `span` is the seconds of motion the row puts between its cue and the next one. A number is the
    span the row declares on its own, zero is a row that never moves a pixel, and `None` is a row
    whose span is read from the page or from a closed word set. Every span is bounded by
    `MEASURABLE_SPAN_SECONDS`, whichever of the three it is.
    """

    name: Attr
    on: tuple[Subject, ...]
    kind: Kind
    values: tuple[str, ...]
    default: str | None
    range: Range | None
    code: Code | None
    span: float | None
    affects: tuple[Affects, ...]
    summary: str


'''

PAGE_FUNCTIONS = '''
def stagger_span(step: float, children: int, entrance: float) -> float:
    """The whole span a staggered container puts between its cue and the next one.

    The arithmetic is exact, which is why `PAGE_STAGGER_OVERRUN` is an error: the last child starts
    one step per earlier child after the cue and then plays its own entrance.
    """
    return step * (children - 1) + entrance if children > 0 else 0.0


def measurable(span: float) -> bool:
    """Whether a declared span is short enough for the cue it belongs to still to be measurable."""
    return span < MEASURABLE_SPAN_SECONDS

'''

# A generated docstring is one sentence per member, and a sentence longer than this is written as
# adjacent string literals so the formatter never has to choose where to break it.
WRAP_AT = 108


def quoted(text: str, indent: str) -> str:
    """A Python string literal for one sentence, split across lines when it would not fit on one."""
    width = WRAP_AT - len(indent) - 2  # the two quotes around the literal
    if len(text) <= width:
        return repr(text)
    *lines, last = textwrap.wrap(text, width, break_long_words=False, break_on_hyphens=False)
    parts = [*(f"{line} " for line in lines), last]
    if "".join(parts) != text:
        raise SystemExit(f"{text!r} holds whitespace other than single spaces, which a wrapped literal would lose.")
    body = f"\n{indent}    ".join(repr(part) for part in parts)
    return f"(\n{indent}    {body}\n{indent})"


def word_enum(name: str, doc: str, words: Iterable[tuple[str, str]]) -> str:
    """One closed word set as a plain enum whose value is the word itself."""
    lines = [f"class {name}(Enum):", f'    """{doc}"""', ""]
    lines += [f"    {member} = {value!r}" for member, value in words]
    return "\n".join(lines) + "\n"


def snake(name: str) -> str:
    """A camel-cased field of the contract as the Python name for the same thing."""
    return "".join(f"_{ch.lower()}" if ch.isupper() else ch for ch in name)


def member_of(word: str) -> str:
    """The enum member name a lower-case word takes, which is the word in capitals with no punctuation."""
    return word.replace("-", "_").upper()


def attr_member(name: str) -> str:
    """The enum member name an attribute takes, which drops the prefix every attribute shares."""
    return member_of(name.removeprefix("data-"))


def effects(name: str, doc: str, rows: dict[str, Any]) -> str:
    """One closed style set as a mapping from its word to the motion that word declares."""
    lines = [f"{name}: dict[str, Effect] = {{"]
    for word, row in rows.items():
        fields = ", ".join(f"{snake(key)}={value!r}" for key, value in row.items())
        lines.append(f'    "{word}": Effect({fields}),')
    lines.append("}")
    lines.append(f'"""{doc}"""')
    return "\n".join(lines) + "\n"


def attr_table(attrs: dict[str, Any]) -> str:
    """The attribute table, in the order an author reaches for the rows."""
    lines = ["ATTRS: dict[Attr, AttrSpec] = {"]
    for name, row in attrs.items():
        lines.append(f"    Attr.{attr_member(name)}: AttrSpec(")
        lines.append(f"        name=Attr.{attr_member(name)},")
        lines.append(f"        on=({', '.join(f'Subject.{member_of(s)}' for s in row['on'])},),")
        lines.append(f"        kind=Kind.{member_of(row['kind'])},")
        lines.append(f"        values={tuple(row['values'])!r},")
        lines.append(f"        default={row['default']!r},")
        span = row["range"]
        if span is None:
            lines.append("        range=None,")
        else:
            args = ", ".join(f"{key}={value!r}" for key, value in span.items())
            lines.append(f"        range=Range({args}),")
        code = f"Code.{row['code']}" if row["code"] else "None"
        lines.append(f"        code={code},")
        lines.append(f"        span={row['span']!r},")
        lines.append(f"        affects=({', '.join(f'Affects.{member_of(a)}' for a in row['affects'])},),")
        lines.append(f"        summary={quoted(row['summary'], '        ')},")
        lines.append("    ),")
    lines.append("}")
    lines.append('"""Every attribute of the contract, in the order an author reaches for them."""')
    return "\n".join(lines) + "\n"


def sentences(name: str, doc: str, rows: dict[str, str], key: Callable[[str], str] = repr) -> str:
    """A mapping from a published key to the one sentence that key's meaning lives in."""
    lines = [f"{name} = {{"]
    for word, sentence in rows.items():
        lines.append(f"    {key(word)}: {quoted(sentence, '    ')},")
    lines.append("}")
    lines.append(f'"""{doc}"""')
    return "\n".join(lines) + "\n"


def page_module(data: dict[str, Any]) -> str:
    """The whole of `src/decktalk/page.py`, written from the contract and formatted by ruff."""
    attrs = data["attrs"]
    subjects = sorted({subject for row in attrs.values() for subject in row["on"]})
    kinds = sorted({row["kind"] for row in attrs.values()})
    affects = sorted({item for row in attrs.values() for item in row["affects"]})
    moments = [name for name, row in attrs.items() if row["kind"] == "moment"]
    exported = [
        "APPEAR_WORDS_MAX",
        "ATTENTION",
        "ATTRS",
        "Affects",
        "Attr",
        "AttrSpec",
        "BACK_OPACITY",
        "CAPTURE_FPS",
        "COUNTS",
        "ENTRANCES",
        "EXEMPT",
        "ENGINE_PATH",
        "EXITS",
        "Effect",
        "FRAME_STEP_MS",
        "Kind",
        "LIST_SEPARATOR",
        "MEASURABLE_SPAN_SECONDS",
        "MOTION_SCALE_PROPERTY",
        "MOMENTS",
        "ONSET_FIRST_FRAME_PERCENT",
        "PAIR_MARK",
        "PAIR_SEPARATOR",
        "PLAYABLE_SPAN_SECONDS",
        "PREVIEW_CUE_TIMES",
        "Q",
        "QUERY",
        "REPORT",
        "Range",
        "SECOND_DIGITS",
        "SLIDE_ENTRANCES",
        "T0_SIGNAL",
        "DONE_ATTR",
        "Subject",
        "TIME_MARK",
        "CUE_MARK",
        "WORD_STYLES",
        "measurable",
        "stagger_span",
    ]
    parts = [
        PAGE_HEADER,
        "__all__ = [\n" + "".join(f"    {name!r},\n" for name in sorted(exported)) + "]\n",
        f"SECOND_DIGITS = {data['secondDigits']!r}\n"
        '"""Truth: a second is written to the millisecond, which is finer than any frame a recording holds."""\n',
        f"CAPTURE_FPS = {data['captureFps']!r}\n"
        '"""The rate the recorder captures at, which is the rate Chromium paints a deck at."""\n',
        f"FRAME_STEP_MS = {data['frameStepMs']!r}\n"
        '"""One captured frame in milliseconds, which is the finest interval any page range may name."""\n',
        f"MEASURABLE_SPAN_SECONDS = {data['measurableSpanSeconds']!r}\n"
        '"""The longest motion a cue may still be playing, which is the unmeasurable threshold, the\n'
        'reduced-motion clamp and the stagger ceiling in one number."""\n',
        f"PLAYABLE_SPAN_SECONDS = {data['playableSpanSeconds']!r}\n"
        '"""The longest motion the page plays, one captured frame under the ceiling, which every declared\n'
        'span sits at or below and which a reduced-motion render is clamped to."""\n',
        f"ONSET_FIRST_FRAME_PERCENT = {data['onsetFirstFramePercent']!r}\n"
        '"""The share of an entrance that must be drawn in its first captured frame."""\n',
        f"APPEAR_WORDS_MAX = {data['appearWordsMax']!r}\n"
        '"""The words `appear` may reveal one at a time before the line is longer than a cue can carry."""\n',
        f"BACK_OPACITY = {data['backOpacity']!r}\n"
        '"""The opacity a stepped-back element holds, which reads as secondary and stays readable."""\n',
        f'PAIR_SEPARATOR = {data["pairSeparator"]!r}\n"""What separates two cue pairs in one attribute value."""\n',
        f'PAIR_MARK = {data["pairMark"]!r}\n"""What separates a pair\'s cue from its value."""\n',
        f"CUE_MARK = {data['cueMark']!r}\n"
        '"""What joins a slide id to a cue name in the cue id `cues.json` carries."""\n',
        f"TIME_MARK = {data['timeMark']!r}\n"
        '"""What joins a cue\'s cue id, or a spoken word, to its second in the query a recorded page reads."""\n',
        f'LIST_SEPARATOR = {data["listSeparator"]!r}\n"""What separates two entries of that query."""\n',
        f"T0_SIGNAL = {data['t0Signal']!r}\n"
        '"""What `t0` says when the recorder starts the page clock on its own signal rather than at a second."""\n',
        f"DONE_ATTR = {data['doneAttr']!r}\n"
        '"""The attribute the runtime sets on the body once the page has drawn everything its URL asked for."""\n',
        f"ENGINE_PATH = {data['enginePath']!r}\n"
        '"""The path the engine answers itself under every origin, which holds the runtime and KaTeX."""\n',
        f"PREVIEW_CUE_TIMES = {data['previewCueTimes']!r}\n"
        '"""The path a previewed page asks its origin for, which answers with the last run\'s cue times."""\n',
        f"MOTION_SCALE_PROPERTY = {data['motionScaleProperty']!r}\n"
        '"""The custom property on the root element that carries `motion.scale` into a page."""\n',
        word_enum(
            "Subject",
            "What an attribute is written on. A container is an element whose children carry moments.",
            [(member_of(word), word) for word in subjects],
        ),
        word_enum(
            "Kind",
            "What kind of value an attribute takes, which is what a reader parses it as.",
            [(member_of(word), word) for word in kinds],
        ),
        word_enum(
            "Affects",
            "What turning an attribute changes, published so an agent can tell a control from a label.",
            [(member_of(word), word) for word in affects],
        ),
        word_enum(
            "Attr",
            "Every attribute name the contract defines. The value is the attribute as an author writes it.",
            [(attr_member(name), name) for name in attrs],
        ),
        word_enum(
            "Q",
            "Every query key a DeckTalk page reads, which is the whole vocabulary of a page URL.",
            [(member_of(word), word) for word in data["query"]],
        ),
        PAGE_MODELS,
        effects(
            "ENTRANCES",
            "How long each entrance plays and how far outside its resting box it travels.",
            data["entrances"],
        ),
        effects("EXITS", "How long each exit plays.", data["exits"]),
        effects(
            "SLIDE_ENTRANCES",
            "How a slide replaces the one before it, and how long that takes.",
            data["slideEntrances"],
        ),
        effects(
            "WORD_STYLES",
            "How a line is shown on the voice, and how long one word takes to arrive.",
            data["wordStyles"],
        ),
        effects("COUNTS", "Which number in the text counts up from zero, and how long the count runs.", data["counts"]),
        effects("ATTENTION", "How long a step back and a return to the front play.", data["attention"]),
        attr_table(attrs),
        sentences(
            "EXEMPT",
            "The rows that survive without a code, and the sentence that says why each one is allowed to.",
            data["exempt"],
            key=lambda name: f"Attr.{attr_member(name)}",
        ),
        sentences(
            "QUERY", "What each query key asks the page for.", data["query"], key=lambda word: f"Q.{member_of(word)}"
        ),
        sentences(
            "REPORT",
            "Every field the probe's one report call answers with, and what a reader does with it.",
            data["report"],
        ),
        "MOMENTS: tuple[Attr, ...] = (" + "".join(f"Attr.{attr_member(name)}, " for name in moments).rstrip() + ")\n"
        '"""Every attribute whose value is a cue name, which is what joins the cue order."""\n',
        PAGE_FUNCTIONS,
    ]
    return generated.ruff("\n".join(parts), PAGE_MODULE)


# ---- the page codes Python and the contract both name ------------------------------------------


def code_block(data: dict[str, Any]) -> str:
    """The page contract's members of the finding codes, as `findings.py` spells them, ready to paste."""
    return "\n".join(f"    {code} = {code!r}" for code in data["codes"])


def check_codes(data: dict[str, Any]) -> None:
    """The `PAGE_` half of the one `Code` enum, held equal to the registry that owns those sentences.

    The enum is written by hand, because generating it would be circular and would have one
    generator writing another module's file, so this is the check that keeps the two lists one list.
    """
    # The package is imported here alone, because every other step of this script reads TypeScript and
    # JSON, and a module-level import would make the runtime depend on the Python it generates.
    from decktalk.findings import CONTRACT_SUBJECTS, Code  # noqa: PLC0415

    written = {code.name for code in Code if code.subject in CONTRACT_SUBJECTS}
    published = set(data["codes"])
    if written != published:
        missing = "\n".join(sorted(published - written)) or "none"
        extra = "\n".join(sorted(written - published)) or "none"
        raise SystemExit(
            f"findings.Code and the page contract disagree.\n"
            f"missing from Code:\n{missing}\nnot in the contract:\n{extra}\n"
            f"the block to paste:\n{code_block(data)}"
        )


# ---- the command line ----------------------------------------------------------------------------


def documents() -> dict[Path, str]:
    """Every artifact, built from the sources after the type check and the code list hold."""
    typecheck()
    data = contract()
    check_codes(data)
    files = {
        RUNTIME / name: bundle(entry, name, banner(data) if name == BANNERED else None)
        for name, entry in BUNDLES.items()
    }
    return files | {CONTRACT_JSON: contract_text(data), PAGE_MODULE: page_module(data)}


if __name__ == "__main__":
    sys.exit(generated.run(documents))
