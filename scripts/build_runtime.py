# /// script
# requires-python = ">=3.12"
# ///
"""Build the runtime bundles, contract.json and src/decktalk/page.py from the TypeScript sources.

    npm ci                                          # once, for the pinned esbuild, tsc and Biome
    uv run scripts/build_runtime.py --write         # type check, bundle, and write every artifact
    uv run scripts/build_runtime.py --check         # exit 1 if any committed artifact would change

The page contract lives once, in `src/decktalk/runtime/src/contract.ts`. This script is the only
thing that reads it: esbuild builds the contract as a CommonJS module, node prints `CONTRACT` as
JSON, and every artifact below is written from that JSON. No regular expression ever reads
TypeScript, so a contract that compiles is a contract Python can be generated from.

    src/decktalk/runtime/decktalk-runtime.js the bundle a deck loads
    src/decktalk/runtime/decktalk-probe.js   the bundle the recorder injects into every page
    src/decktalk/runtime/contract.json       the intermediate, committed so the rest is pure Python
    src/decktalk/page.py                     the vocabulary the library and the CLI read

Each artifact is written through the formatter that owns its language, the pinned Biome for
JavaScript and the project's ruff for Python, so a generated file is as clean as a written one and
`--check` compares two files that were made the same way.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
RUNTIME = ROOT / "src" / "decktalk" / "runtime"
SOURCE = RUNTIME / "src"
TSCONFIG = RUNTIME / "tsconfig.json"
BIN = ROOT / "node_modules" / ".bin"

CONTRACT_ENTRY = SOURCE / "contract.ts"
CONTRACT_JSON = RUNTIME / "contract.json"
PAGE_MODULE = ROOT / "src" / "decktalk" / "page.py"

# Every bundle the runtime ships, from the entry point that builds it. A module reaches a bundle
# only by being imported from one of these, which is what keeps the probe free of the runtime.
BUNDLES: dict[str, Path] = {
    "decktalk-runtime.js": SOURCE / "index.ts",
    "decktalk-probe.js": SOURCE / "probe" / "probe.ts",
}

# The browsers a bundle must run in are the ones Playwright drives and the ones an author previews
# in, so the output is the newest syntax level every current engine parses.
TARGET = "es2022"


class Stale(Exception):
    """A committed artifact differs from what the sources say it should be."""


def tool(name: str) -> Path:
    """The pinned executable `npm ci` installed, which is the only version this script will use."""
    path = BIN / name
    if not path.exists():
        raise SystemExit(f"{path.relative_to(ROOT)} is missing, so run `npm ci` first")
    return path


def run(cmd: list[str | Path], *, stdin: str | None = None) -> str:
    """One tool, with its output returned and its failure raised with everything it printed."""
    # uv runs this script in an environment of its own, and a nested `uv run` would warn about it.
    env = {key: value for key, value in os.environ.items() if key != "VIRTUAL_ENV"}
    done = subprocess.run(
        [str(part) for part in cmd],
        check=False,
        cwd=ROOT,
        env=env,
        input=stdin,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if done.returncode != 0:
        raise SystemExit(f"{cmd[0]} failed:\n{done.stdout}{done.stderr}")
    return done.stdout


def typecheck() -> None:
    """Every TypeScript source and every node test, checked against the contract's own types."""
    run([tool("tsc"), "--noEmit", "-p", TSCONFIG])


def bundle(entry: Path, out: Path) -> None:
    """One entry point as a formatted script, which is what a page loads and what git holds."""
    built = run(
        [
            tool("esbuild"),
            entry,
            "--bundle",
            "--format=iife",
            f"--target={TARGET}",
            "--charset=utf8",
            "--legal-comments=inline",
        ]
    )
    out.write_text(biome(built, out.name), encoding="utf-8")


def biome(text: str, name: str) -> str:
    """JavaScript through the pinned formatter, so a generated bundle is formatted like a written file."""
    return run([tool("biome"), "format", f"--stdin-file-path={name}"], stdin=text)


def ruff(text: str, name: str) -> str:
    """Python through the project's formatter, for the same reason and with the same settings."""
    return run(["uv", "run", "ruff", "format", f"--stdin-filename={name}", "-"], stdin=text)


def contract() -> dict[str, Any]:
    """The contract as JSON, printed by node from a CommonJS build of the one TypeScript module."""
    with tempfile.TemporaryDirectory() as tmp:
        built = Path(tmp) / "contract.cjs"
        run([tool("esbuild"), CONTRACT_ENTRY, "--bundle", "--format=cjs", f"--target={TARGET}", f"--outfile={built}"])
        printed = run(["node", "-e", f"process.stdout.write(JSON.stringify(require({str(built)!r}).CONTRACT))"])
    return json.loads(printed)


def contract_text(data: dict[str, Any]) -> str:
    """The committed intermediate, indented so a reviewer reads a diff of it line by line."""
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


# ---- the Python vocabulary ---------------------------------------------------------------------

PAGE_HEADER = '''"""The page contract as Python reads it: every attribute, every page code and every query key.

Generated by `scripts/build_runtime.py` from `src/decktalk/runtime/src/contract.ts`. Do not edit
this file, edit the contract and run the script.

An element on a slide has four moments and one value type. It arrives, it steps back, it comes to
the front, and it leaves, and each of those names a cue local to its slide, which `wire_id` joins
into the id `cues.json` carries. Everything else is either how a moment looks, which is a closed
word, or what a moment means, which is a sentence for the transcript.

This module is vocabulary and arithmetic and nothing else. It opens no file, reads no settings and
imports nothing from DeckTalk, so every layer above it may read the contract without a browser and
without a project.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict
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
    """One row of the attribute table, which is everything published about one knob.

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
    code: PageWarning | None
    span: float | None
    affects: tuple[Affects, ...]
    summary: str


'''

PAGE_FUNCTIONS = '''
def wire_id(slide: str, local: str) -> str:
    """The id `cues.json` carries for a moment, which is the slide it was written in and its local name."""
    return f"{slide}{WIRE_MARK}{local}"


def stagger_span(step: float, children: int, entrance: float) -> float:
    """The whole span a staggered container puts between its cue and the next one.

    The arithmetic is exact, which is why `PAGE_STAGGER_OVERRUN` is certain: the last child starts
    one step per earlier child after the cue and then plays its own entrance.
    """
    return step * (children - 1) + entrance if children > 0 else 0.0


def measurable(span: float) -> bool:
    """Whether a declared span is short enough for the cue it belongs to still to be measurable."""
    return span < MEASURABLE_SPAN_SECONDS


def scaled(span: float, scale: float) -> float:
    """A declared span under a reduced-motion render, clamped so no scaled span crosses the ceiling.

    The scale multiplies the declared span as well as the duration, so a project that slows its
    motion down cannot slow it past the point where its own cues stop being measurable.
    """
    return min(span * scale, MEASURABLE_SPAN_SECONDS - FRAME_STEP_MS / 1000)
'''

# A generated docstring is one sentence per member, and a sentence longer than this is written as
# adjacent string literals so the formatter never has to choose where to break it.
WRAP_AT = 108


def quoted(text: str, indent: str) -> str:
    """A Python string literal for one sentence, split across lines when it would not fit on one."""
    if len(text) + len(indent) + 2 <= WRAP_AT:
        return repr(text)
    parts: list[str] = []
    line = ""
    for word in text.split(" "):
        candidate = f"{line} {word}" if line else word
        if len(candidate) + len(indent) + 2 > WRAP_AT and line:
            parts.append(f"{line} ")
            line = word
        else:
            line = candidate
    parts.append(line)
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


def warnings_enum(codes: dict[str, Any]) -> str:
    """Every page code as one enum, in the shape `Verdict` already uses: a member, a sentence and a certainty."""
    lines = [
        "class PageWarning(Enum):",
        '    """One code whose subject is the page. `name` is the code, and `message` is the sentence it prints.',
        "",
        "    The message is a whole sentence carrying its own fix, written for the console and for a person.",
        "    Nothing parses it, and the documentation URL is derived from the code by whoever prints it.",
        '    """',
        "",
        "    message: str",
        "    certain: bool",
        "    raised_by: RaisedBy",
        "",
        "    def __init__(self, message: str, certain: bool, raised_by: RaisedBy) -> None:",
        "        self.message = message",
        "        self.certain = certain",
        "        self.raised_by = raised_by",
        "",
        "    def __repr__(self) -> str:",
        '        return f"{type(self).__name__}.{self.name}"',
        "",
    ]
    for code, row in codes.items():
        certain = row["certainty"] == "certain"
        lines.append(f"    {code} = (")
        lines.append(f"        {quoted(row['message'], '        ')},")
        lines.append(f"        {certain},")
        lines.append(f"        RaisedBy.{member_of(row['raisedBy'])},")
        lines.append("    )")
    return "\n".join(lines) + "\n"


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
        code = f"PageWarning.{row['code']}" if row["code"] else "None"
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
    raised = sorted({row["raisedBy"] for row in data["codes"].values()})
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
        "EXITS",
        "Effect",
        "FRAME_STEP_MS",
        "Kind",
        "MEASURABLE_SPAN_SECONDS",
        "MOMENTS",
        "ONSET_FIRST_FRAME_PERCENT",
        "PAIR_MARK",
        "PAIR_SEPARATOR",
        "Q",
        "QUERY",
        "REPORT",
        "Range",
        "RaisedBy",
        "SLIDE_ENTRANCES",
        "Subject",
        "WIRE_MARK",
        "WORD_STYLES",
        "PageWarning",
        "measurable",
        "scaled",
        "stagger_span",
        "wire_id",
    ]
    parts = [
        PAGE_HEADER,
        "__all__ = [\n" + "".join(f"    {name!r},\n" for name in sorted(exported)) + "]\n",
        f"CAPTURE_FPS = {data['captureFps']!r}\n"
        '"""The rate the recorder captures at, which is the rate Chromium paints a deck at."""\n',
        f"FRAME_STEP_MS = {data['frameStepMs']!r}\n"
        '"""One captured frame in milliseconds, which is the finest interval any page range may name."""\n',
        f"MEASURABLE_SPAN_SECONDS = {data['measurableSpanSeconds']!r}\n"
        '"""The longest motion a cue may still be playing, which is the unmeasurable threshold, the\n'
        'reduced-motion clamp and the stagger ceiling in one number."""\n',
        f"ONSET_FIRST_FRAME_PERCENT = {data['onsetFirstFramePercent']!r}\n"
        '"""The share of an entrance that must be drawn in its first captured frame."""\n',
        f"APPEAR_WORDS_MAX = {data['appearWordsMax']!r}\n"
        '"""The words `appear` may reveal one at a time before the line is longer than a cue can carry."""\n',
        f"BACK_OPACITY = {data['backOpacity']!r}\n"
        '"""The opacity a stepped-back element holds, which reads as secondary and stays readable."""\n',
        f'PAIR_SEPARATOR = {data["pairSeparator"]!r}\n"""What separates two moment pairs in one attribute value."""\n',
        f'PAIR_MARK = {data["pairMark"]!r}\n"""What separates a pair\'s moment from its value."""\n',
        f"WIRE_MARK = {data['wireMark']!r}\n"
        '"""What joins a slide id to a local moment name in the id `cues.json` carries."""\n',
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
            "What turning an attribute changes, published so an agent can tell a knob from a label.",
            [(member_of(word), word) for word in affects],
        ),
        word_enum(
            "RaisedBy",
            "Who raises a code. The runtime reports it from the page, and Python measures it after.",
            [(member_of(word), word) for word in raised],
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
        warnings_enum(data["codes"]),
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
        '"""Every attribute whose value is the local name of a cue, which is what joins the cue order."""\n',
        PAGE_FUNCTIONS,
    ]
    return ruff("\n".join(parts), PAGE_MODULE.name)


# ---- the code list both tracks land ------------------------------------------------------------


def code_block(data: dict[str, Any]) -> str:
    """The `PAGE_` members of the finding codes, as `findings.py` spells them, ready to paste."""
    return "\n".join(f"    {code} = {code!r}" for code in data["codes"])


def check_codes(data: dict[str, Any]) -> None:
    """The `PAGE_` half of the one `Code` enum, held equal to the registry that owns those sentences.

    The enum is written by hand, because generating it would be circular and would have one
    generator writing another module's file, so this is the check that keeps the two lists one list.
    """
    sys.path.insert(0, str(ROOT / "src"))
    try:
        from decktalk.findings import Code  # noqa: PLC0415  (absent until the models land)
    except ImportError:
        print("skipped the code list, because decktalk.findings is not there yet")
        return
    written = {name for name in Code.__members__ if name.startswith("PAGE_")}
    published = set(data["codes"])
    if written != published:
        missing = "\n".join(sorted(published - written)) or "none"
        extra = "\n".join(sorted(written - published)) or "none"
        raise Stale(
            f"findings.Code and the page contract disagree.\n"
            f"missing from Code:\n{missing}\nnot in the contract:\n{extra}\n"
            f"the block to paste:\n{code_block(data)}"
        )


# ---- writing and checking ------------------------------------------------------------------------


def first_difference(old: str, new: str) -> str:
    """The first line that differs between a committed artifact and a freshly built one."""
    was, now = old.splitlines(), new.splitlines()
    for number, (before, after) in enumerate(zip(was, now, strict=False), start=1):
        if before != after:
            return f"line {number}\n  committed: {before}\n  built:     {after}"
    return f"the committed file has {len(was)} lines and the built one has {len(now)}"


def compare(target: Path, built: str) -> None:
    """One artifact, held to what the sources say it should be."""
    if not target.exists():
        raise Stale(f"{target.relative_to(ROOT)} is missing")
    old = target.read_text(encoding="utf-8")
    if old != built:
        raise Stale(f"{target.relative_to(ROOT)} is stale at {first_difference(old, built)}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    action = ap.add_mutually_exclusive_group(required=True)
    action.add_argument("--write", action="store_true", help="type check, bundle, and write every artifact")
    action.add_argument("--check", action="store_true", help="exit 1 if any committed artifact would change")
    args = ap.parse_args()
    typecheck()
    data = contract()
    try:
        check_codes(data)
        if args.check:
            with tempfile.TemporaryDirectory() as tmp:
                for name, entry in BUNDLES.items():
                    built = Path(tmp) / name
                    bundle(entry, built)
                    compare(RUNTIME / name, built.read_text(encoding="utf-8"))
            compare(CONTRACT_JSON, contract_text(data))
            compare(PAGE_MODULE, page_module(data))
            print("the runtime, the contract and the page vocabulary are what the sources say")
            return 0
    except Stale as stale:
        print(stale)
        return 1
    for name, entry in BUNDLES.items():
        bundle(entry, RUNTIME / name)
        print(f"wrote {(RUNTIME / name).relative_to(ROOT)}")
    CONTRACT_JSON.write_text(contract_text(data), encoding="utf-8")
    print(f"wrote {CONTRACT_JSON.relative_to(ROOT)}")
    PAGE_MODULE.write_text(page_module(data), encoding="utf-8")
    print(f"wrote {PAGE_MODULE.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
