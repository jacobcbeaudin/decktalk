"""The command table, the shared flag groups, and the parser both are built into.

One table names every command, so the parser, the help epilog, the generated reference page and the
dispatch table can never disagree about what exists. A command declares which shared groups it takes
and adds its own flags in one function, and nothing about a command is written twice.

The shared groups are the project directory, the machine output, the exit policy and the encoder
override. `--json` is global: every command prints one envelope with it, on the root parser and
after the command name alike. `--exit-zero` is offered by every command that can report a
finding, and `--strict` only by a command that can report an uncertain one, because a flag that
cannot change an outcome is a promise the command breaks. Every argument rule lives here rather than
in a handler, so a caller learns it from `--help` and a test can read it from the parser.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from ..pipeline import Stage
from ..scaffold import listed_names
from .options import (
    AlignOptions,
    AssembleOptions,
    BuildOptions,
    ClipOptions,
    DoctorOptions,
    InitOptions,
    InstallOptions,
    NarrateOptions,
    Options,
    PreflightOptions,
    RecordOptions,
    ScreenshotsOptions,
    ServeOptions,
    SoundscapeOptions,
    StatusOptions,
    VerifyOptions,
    WordsOptions,
)

DESCRIPTION = (
    "Every picture lands on its word. DeckTalk turns a markdown script, HTML slides and your voice "
    "into one narrated mp4."
)

PROJECT_HELP = "project directory (default: DECKTALK_PROJECT, else the current directory)"
VERBOSE_HELP = "debug logging on stderr, including every ffmpeg command line and the traceback of a bug"
QUIET_HELP = "warnings and errors only on stderr"
JSON_HELP = "print one JSON envelope on stdout and nothing else there, with every log line on stderr"
STRICT_HELP = "an uncertain finding fails as a certain one does"
EXIT_ZERO_HELP = "exit 0 whatever was found, for a caller that reads the envelope itself"
ONLY_HELP = "only these sections: 3, 3,5 or 7-9, and the flag repeats"
ALLOW_UNKNOWN_HELP = "continue when a cue id in cues.json appears nowhere in the page that plays it"
FORCE_HELP = "ignore the cache, and let --no-voice replace voiced takes"
NO_VOICE_HELP = "placeholder narration with no API key and no spend"


class UsageError(Exception):
    """A command line the parser refused, carrying the sentence it printed.

    argparse would exit the process here. Raising instead lets one caller print the usage envelope
    under `--json` and the same sentence without it, so exit code 2 is readable either way.
    """


class Parser(argparse.ArgumentParser):
    """An `ArgumentParser` that raises its refusals rather than exiting where no caller can see."""

    def error(self, message: str) -> Any:
        self.print_usage(sys.stderr)
        raise UsageError(message)


def sections(text: str) -> list[int]:
    """`3`, `3,5` and `7-9` as section numbers, so one spelling works on every command that has it."""
    out: list[int] = []
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        first, _, last = part.partition("-")
        try:
            lo, hi = int(first), int(last or first)
        except ValueError:
            raise argparse.ArgumentTypeError(f"{part!r} is not a section number, a list or a range") from None
        if hi < lo:
            raise argparse.ArgumentTypeError(f"{part!r} counts backwards")
        out += range(lo, hi + 1)
    return out


STAGE_WORDS = ", ".join(member.value for member in Stage)
"""The five stages in run order, as `--help` and a refusal print them."""


def stage(text: str) -> Stage:
    """The stage a word names, for `--from` and `--to`, refusing a word that names none."""
    try:
        return Stage(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{text!r} is not a stage: {STAGE_WORDS}") from None


class Sections(argparse.Action):
    """Gather every `--only` into one sorted list, so a range and a repeat compose."""

    def __call__(
        self,
        parser: argparse.ArgumentParser,
        namespace: argparse.Namespace,
        values: Any,
        option_string: str | None = None,
    ) -> None:
        gathered = set(getattr(namespace, self.dest, None) or [])
        gathered.update(values or [])
        setattr(namespace, self.dest, sorted(gathered))


def only_flag(sp: argparse.ArgumentParser, help_text: str = ONLY_HELP) -> None:
    sp.add_argument("--only", type=sections, action=Sections, default=None, metavar="N", help=help_text)


# ---- each command's own flags ----------------------------------------------------------


def init_flags(sp: argparse.ArgumentParser) -> None:
    sp.add_argument("dir", help="the directory to create, with a working starter deck")
    sp.add_argument("--name", help="project name (default: the directory name)")
    sp.add_argument("--force", action="store_true", help="write into a non-empty directory")
    examples = listed_names()
    sp.add_argument("--example", metavar="NAME", help=f"write a packaged example instead of the starter: {examples}")
    sp.add_argument("--no-skills", action="store_true", help="do not write the packaged skills into the project")


def serve_flags(sp: argparse.ArgumentParser) -> None:
    sp.add_argument("--host", default="127.0.0.1", help="the address to bind (default: 127.0.0.1)")
    sp.add_argument("--port", type=int, default=0, help="the port to bind (default: any free port)")
    sp.add_argument("--open", action="store_true", help="open the first page in your browser")


def doctor_flags(sp: argparse.ArgumentParser) -> None:
    sp.add_argument("--report", action="store_true", help="print a pasteable environment block, with no secrets")


def narrate_flags(sp: argparse.ArgumentParser) -> None:
    only_flag(sp)
    sp.add_argument("--force", action="store_true", help=FORCE_HELP)
    sp.add_argument(
        "--allow-placeholders", action="store_true", help="synthesize a section that still has a [CAPITAL] placeholder"
    )
    sp.add_argument("--dry-run", action="store_true", help="print what a voiced run would send and spend, and stop")
    sp.add_argument("--no-voice", action="store_true", help=NO_VOICE_HELP)
    sp.add_argument("--model", help="speech model for this run")


def align_flags(sp: argparse.ArgumentParser) -> None:
    sp.add_argument("--allow-unknown-cues", action="store_true", help=ALLOW_UNKNOWN_HELP)


def preflight_flags(sp: argparse.ArgumentParser) -> None:
    only_flag(sp)
    sp.add_argument("--no-frames", action="store_true", help="skip the frozen frames, so no browser starts")
    sp.add_argument("--model", help="speech model to check the narration cache for")
    sp.add_argument("--allow-unknown-cues", action="store_true", help=ALLOW_UNKNOWN_HELP)


def soundscape_flags(sp: argparse.ArgumentParser) -> None:
    sp.add_argument("names", nargs="*", metavar="NAME", help="only these items: ambience, music, or an effect name")
    sp.add_argument("--force", action="store_true", help="regenerate even if the file exists")
    sp.add_argument("--dry-run", action="store_true", help="print every request without sending it")


def record_flags(sp: argparse.ArgumentParser) -> None:
    only_flag(sp)
    sp.add_argument("--seconds", type=float, help="override every duration (smoke tests)")
    sp.add_argument("--settle", type=float, help="seconds after load before the clock starts")
    sp.add_argument("--no-cues", action="store_true", help="preview timing instead of ?cues=")


def assemble_flags(sp: argparse.ArgumentParser) -> None:
    sp.add_argument("--no-soundscape", action="store_true", help="narration only: no music, no ambience, no effects")
    sp.add_argument("--no-loudness", action="store_true", help="skip loudness normalization")
    encode_flags(sp)


def verify_flags(sp: argparse.ArgumentParser) -> None:
    sp.add_argument("checks", nargs="*", metavar="SECTION:CUE", help="cues to check (default: every resolved cue)")
    only_flag(sp)


def screenshots_flags(sp: argparse.ArgumentParser) -> None:
    sp.add_argument("--page", action="append", help="page file (default: every page in decktalk.toml)")
    sp.add_argument("--slide", action="append", metavar="ID", help="only these slide ids")
    sp.add_argument("--after", action="append", metavar="ID", help="freeze the one --slide at this cue id (repeats)")
    sp.add_argument("--before", action="append", metavar="ID", help="freeze the one --slide just before this cue id")
    sp.add_argument("--section", type=int, help="play this section with its resolved cues")
    sp.add_argument("--at", type=float, action="append", help="seconds after narration t=0 (with --section)")


def clip_flags(sp: argparse.ArgumentParser) -> None:
    sp.add_argument("section", type=int, help="the page section to cut from")
    sp.add_argument("--start", type=float, required=True, metavar="SECONDS", help="start, after the section starts")
    sp.add_argument("--end", type=float, required=True, metavar="SECONDS", help="end, after the section starts")
    sp.add_argument("--out", required=True, help="the clip file, relative to the project, such as media/open.mp4")
    sp.add_argument("--words", help="the words file (default: the clip's path with .words.json)")
    sp.add_argument("--gain", type=float, default=0.0, metavar="DB", help="gain on the clip's sound, in dB")
    sp.add_argument("--hold", type=float, default=0.0, metavar="SECONDS", help="hold the last frame this long, silent")
    encode_flags(sp)


def build_flags(sp: argparse.ArgumentParser) -> None:
    sp.add_argument("--no-voice", action="store_true", help=NO_VOICE_HELP)
    sp.add_argument("--force", action="store_true", help=FORCE_HELP)
    only_flag(sp, "re-record only these sections: 3, 3,5 or 7-9, and the flag repeats")
    sp.add_argument("--no-soundscape", action="store_true", help="narration only: no music, no ambience, no effects")
    sp.add_argument("--no-loudness", action="store_true", help="skip loudness normalization")
    sp.add_argument("--allow-unresolved-cues", action="store_true", help="build even if a cue phrase was not found")
    sp.add_argument("--allow-unknown-cues", action="store_true", help=ALLOW_UNKNOWN_HELP)
    sp.add_argument("--progress", metavar="PATH", help="the progress log (default: build/progress.jsonl)")
    sp.add_argument("--from", dest="from_stage", type=stage, metavar="STAGE",
                    help=f"start at this stage, inclusive: {STAGE_WORDS}")  # fmt: skip
    sp.add_argument("--to", dest="to_stage", type=stage, metavar="STAGE",
                    help="stop after this stage, inclusive")  # fmt: skip
    sp.add_argument("--dry-run", action="store_true", help="print the stages the run would execute, and stop")
    encode_flags(sp)


def encode_flags(sp: argparse.ArgumentParser) -> None:
    sp.add_argument("--preset", help="x264 preset for this run (veryfast for drafts)")
    sp.add_argument("--crf", type=int, help="x264 quality for this run")


# ---- the table -------------------------------------------------------------------------


@dataclass(frozen=True)
class Command:
    """One command: what it does, the shared groups it takes, and the flags of its own."""

    name: str
    purpose: str
    options: type[Options]
    flags: Callable[[argparse.ArgumentParser], None] | None = None
    group: str = "the pipeline"
    project: bool = True
    strict: bool = False  # it can report an uncertain finding
    exit_zero: bool = False  # it can report a finding at all


COMMANDS: tuple[Command, ...] = (
    Command("init", "create a project: decktalk.toml, script.md, cues.json, a deck page and the runtime",
            InitOptions, init_flags, group="one machine", project=False),
    Command("install", "fetch headless Chromium and ffmpeg up front, with Chromium's Linux libraries",
            InstallOptions, group="one machine", project=False),
    Command("doctor", "report what is installed and which build a run would use",
            DoctorOptions, doctor_flags, group="one machine", project=False, exit_zero=True),
    Command("status",
            "report what the four input files say, what is built, what disagrees and whether a build runs",
            StatusOptions, group="before a build", exit_zero=True),
    Command("preflight", "estimate the takes, resolve the cues and freeze every reveal, spending nothing",
            PreflightOptions, preflight_flags, group="before a build", strict=True, exit_zero=True),
    Command("words", "print each spoken section's words, in seconds after the section starts",
            WordsOptions, only_flag, group="before a build"),
    Command("screenshots", "write one PNG per slide, per cue of one slide, or per second of a playing section",
            ScreenshotsOptions, screenshots_flags, group="before a build"),
    Command("soundscape", "generate the ambience, the sound effects and the music",
            SoundscapeOptions, soundscape_flags, group="before a build"),
    Command("clip", "cut a span of a built section and its narration into a clip file and its words",
            ClipOptions, clip_flags, group="before a build", strict=True, exit_zero=True),
    Command("serve", "serve the project on the local origin, so a page loads over http",
            ServeOptions, serve_flags, group="before a build"),
    Command(Stage.NARRATE.value, "turn script.md into one take per section with its word timestamps",
            NarrateOptions, narrate_flags),
    Command(Stage.ALIGN.value, "resolve each cue phrase in cues.json to a second on its section clock",
            AlignOptions, align_flags, strict=True, exit_zero=True),
    Command(Stage.RECORD.value,
            "record each page section with headless Chromium, find narration t=0 and check the frames",
            RecordOptions, record_flags, strict=True, exit_zero=True),
    Command(Stage.ASSEMBLE.value, "cut, mix and normalize the sections into the final mp4 and its captions",
            AssembleOptions, assemble_flags, strict=True, exit_zero=True),
    Command(Stage.VERIFY.value, "read the final mp4 and report every section start, cut, seam and cue landing",
            VerifyOptions, verify_flags, strict=True, exit_zero=True),
    Command("build", "run every stage in order, writing progress as it goes",
            BuildOptions, build_flags, group="the whole run", strict=True, exit_zero=True),
)  # fmt: skip

BY_NAME: dict[str, Command] = {c.name: c for c in COMMANDS}


def epilog() -> str:
    """The first help screen, generated from the table, so it can never list a command that is gone."""
    width = max(len(c.name) for c in COMMANDS) + 2
    lines: list[str] = []
    for group in dict.fromkeys(c.group for c in COMMANDS):
        lines.append(f"\n{group}:")
        lines += [f"  decktalk {c.name:<{width}}{c.purpose}" for c in COMMANDS if c.group == group]
    lines.append("\nEvery command takes --json and prints one envelope. Logs go to stderr, results to stdout.")
    lines.append("A run exits 0 when it found nothing, 1 on a finding, 2 on a usage error, 3 when DeckTalk")
    lines.append("itself could not run, and 130 when it was interrupted.")
    return "\n".join(lines)


def _shared(sp: argparse.ArgumentParser, command: Command) -> None:
    """The groups a command declares, added after its own flags so they read last in --help."""
    sp.add_argument("-v", "--verbose", action="store_true", default=argparse.SUPPRESS, help=VERBOSE_HELP)
    sp.add_argument("-q", "--quiet", action="store_true", default=argparse.SUPPRESS, help=QUIET_HELP)
    sp.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help=JSON_HELP)
    if command.project:
        sp.add_argument("--project", "-p", default=argparse.SUPPRESS, help=PROJECT_HELP)
    if command.strict:
        sp.add_argument("--strict", action="store_true", help=STRICT_HELP)
    else:
        sp.set_defaults(strict=False)
    if command.exit_zero:
        sp.add_argument("--exit-zero", action="store_true", help=EXIT_ZERO_HELP)
    else:
        sp.set_defaults(exit_zero=False)


def _build(version: str = "") -> tuple[argparse.ArgumentParser, Any]:
    p = Parser(
        prog="decktalk",
        description=DESCRIPTION,
        epilog=epilog(),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--version", action="version", version=f"decktalk {version}".strip())
    p.add_argument("--project", "-p", default=None, help=PROJECT_HELP)
    p.add_argument("-v", "--verbose", action="store_true", help=VERBOSE_HELP)
    p.add_argument("-q", "--quiet", action="store_true", help=QUIET_HELP)
    p.add_argument("--json", action="store_true", help=JSON_HELP)
    sub = p.add_subparsers(dest="cmd", required=True, metavar="<command>")
    for command in COMMANDS:
        # Each command is added without `help`, so argparse prints no command list beside the epilog.
        sp = sub.add_parser(command.name, description=command.purpose)
        if command.flags:
            command.flags(sp)
        _shared(sp, command)
    return p, sub


def build_parser(version: str = "") -> argparse.ArgumentParser:
    """The whole command line. `--version` prints the installed version the caller passes in."""
    parser, _ = _build(version)
    return parser


def command_parsers() -> dict[str, argparse.ArgumentParser]:
    """Each command's own parser, for the generated reference and the tests that read the table."""
    _, sub = _build()
    return dict(sub.choices)


def validate(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    """The argument rules a single flag cannot state, checked here so `--help` and a test can see them."""
    if args.cmd == "build" and args.from_stage and args.to_stage:
        # A backwards pair is a command line the caller can fix, so it is a usage error rather than
        # an error that tells the caller to stop.
        if not Stage.span(args.from_stage, args.to_stage):
            first, last = args.from_stage.value, args.to_stage.value
            parser.error(f"the stage {first} comes after the stage {last}, so this run is empty")
    if args.cmd == "screenshots" and (args.after or args.before):
        # A cue freezes one slide at the moment that cue fires, so it needs exactly one slide.
        named = "--after" if args.after else "--before"
        if not args.slide or len(args.slide) != 1 or args.section is not None:
            parser.error(f"{named} needs exactly one --slide, and it does not combine with --section")


def parse(argv: Sequence[str] | None, version: str = "") -> argparse.Namespace:
    """The parsed command line, with every argument rule applied."""
    parser = build_parser(version)
    args = parser.parse_args(argv)
    validate(parser, args)
    return args
