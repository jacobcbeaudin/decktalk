"""decktalk command line.

    decktalk init DIR                 scaffold a project (decktalk.toml, script, cues, deck with the runtime)
    decktalk setup                    fetch headless Chromium and ffmpeg (once per machine)
    decktalk doctor                   report what is installed
    decktalk narrate [--silent]       script.md -> build/audio (ElevenLabs, word timestamps, timeline)
    decktalk beats                    cues.json -> build/audio/beats.json
    decktalk soundscape               ambience, sfx, underscore (ElevenLabs)
    decktalk record                   pages -> build/rec/NN-scene.webm (Chromium)
    decktalk measure                  find narration t=0 in each recording
    decktalk check                    recording sanity (black / truncated)
    decktalk assemble                 ffmpeg -> build/out/<name>.mp4
    decktalk verify [SEC:CUE ...]     section starts, cuts, and every cue landing on the final mp4
    decktalk shots                    per-step screenshots, or frames from a playing section
    decktalk build [--silent]         narrate -> beats -> record -> measure -> check -> assemble -> verify
    decktalk status                   timeline and what is built
    decktalk runtime                  copy the packaged decktalk-runtime.js into the project

Every project command takes --project/-p DIR (default: DECKTALK_PROJECT, else the current
directory). The -v and -q flags go before or after the command name.
Tuning flags such as --preset override decktalk.toml and DECKTALK_* env for one run.
status, beats, check, verify and doctor take --json, --strict and --no-fail. They exit 1
on a certain finding, and on an uncertain one (a verdict ending in ?) only with --strict.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

from . import __version__, _report
from .errors import DeckTalkError
from .project import Project
from .verdicts import BLACK, OK, QUIET, SPEECH_AT_CUT, Findings, count

log = logging.getLogger("decktalk")

STRICT_HELP = "Also exit 1 on an uncertain verdict, the ones marked with a question mark."
NO_FAIL_HELP = "Exit 0 even when a check fails, for scripts that read the table or the JSON themselves."
JSON_HELP = "Print the result as one JSON object on stdout instead of the tables. Progress still goes to stderr."
ALLOW_UNKNOWN_HELP = "Continue when a cue id in cues.json appears nowhere in the page that plays it."


def _project(args: argparse.Namespace) -> Project:
    project = Project.load(getattr(args, "project", None))
    s = project.settings
    if getattr(args, "preset", None):
        s.video.preset = args.preset
    if getattr(args, "crf", None) is not None:
        s.video.crf = args.crf
    if getattr(args, "settle", None) is not None:
        s.record.settle_seconds = args.settle
    return project


def _only(values: list[int] | None) -> list[int] | None:
    return values or None


# ---- exit policy and output ------------------------------------------------------------


def _exit_for(findings: Findings, strict: bool, no_fail: bool) -> int:
    """The exit code of a read-only command, from what it found.

    A certain finding exits 1. An uncertain finding exits 1 only with `strict`. With
    `no_fail` the command exits 0 whatever it found. An error is not a finding, so it
    still exits 1 through main.
    """
    if no_fail:
        return 0
    if findings.certain or (strict and findings.uncertain):
        return 1
    return 0


def _finish(args: argparse.Namespace, findings: Findings, payload: dict[str, Any], table: Callable[[], str]) -> int:
    """Print the table, or the JSON envelope with the payload under the command's name, and return the exit code."""
    if args.json:
        doc = {
            "command": args.cmd,
            "version": __version__,
            "ok": _exit_for(findings, args.strict, no_fail=False) == 0,
            "findings": findings.to_dict(),
            args.cmd: payload,
        }
        print(json.dumps(doc, indent=2))
    else:
        print(table())
    return _exit_for(findings, args.strict, args.no_fail)


def _verify_findings(result: Any) -> Findings:
    """Every start, cut and cue verdict in a VerifyResult, tallied."""
    verdicts: Iterable[str] = [
        *(OK if s.ok else BLACK for s in result.starts),
        *(QUIET if c.ok else SPEECH_AT_CUT for c in result.cuts),
        *(c.verdict for c in result.cues),
    ]
    return count(verdicts)


# ---- commands ------------------------------------------------------------------------


def cmd_init(args: argparse.Namespace) -> int:
    from .scaffold import init

    target = init(Path(args.dir), name=args.name, force=args.force)
    print(f"created {target}")
    print("  decktalk.toml  the plan: sections -> pages or clips, voice, mix, soundscape")
    print("  script.md      the narration (## N. sections)")
    print("  cues.json      which spoken phrase each visual lands on")
    print("  deck/          index.html + decktalk-runtime.js (open index.html for the scene index)")
    print("  media/         your clips, b-roll, markers.json")
    print("next: cp .env.example .env  (ELEVENLABS_API_KEY, ELEVENLABS_VOICE_ID), then `decktalk build`")
    print("      or `decktalk build --silent` to render with placeholder narration and no API key")
    return 0


def cmd_setup(args: argparse.Namespace) -> int:
    from .scaffold import setup

    setup()
    print("setup complete")
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    from .scaffold import doctor

    rows = doctor()
    findings = Findings(certain=sum(not r.ok for r in rows))
    return _finish(
        args,
        findings,
        {"components": [r.to_dict() for r in rows]},
        lambda: "\n".join(f"{r.name:<9} {'ok     ' if r.ok else 'MISSING'} {r.detail}" for r in rows),
    )


def cmd_runtime(args: argparse.Namespace) -> int:
    from .scaffold import update_runtime

    for path, existed in update_runtime(_project(args).root):
        print(f"{'updated' if existed else 'wrote'} {path}")
    return 0


def cmd_narrate(args: argparse.Namespace) -> int:
    from .stages.narrate import narrate, script_segments

    project = _project(args)
    cfg = project.settings.narration
    if args.dry_run:
        _all, spoken = script_segments(project)
        targets = [s for s in spoken if not args.only or s.index in set(args.only)]
        for seg in targets:
            print(f"=== {seg.key} {seg.title}  -> {seg.filename}")
            print(seg.tts_text(cfg))
            print()
        print(f"voice: model={args.model or project.voice.model or cfg.model} {project.voice.api_settings()}")
        print(_report.segments_table(targets, cfg.words_per_minute))
        unfilled = sorted({p for s in targets for p in s.placeholders})
        if unfilled:
            print(f"\nnote: unfilled placeholders {unfilled}; fill them before the real run.")
        return 0
    result = narrate(
        project,
        only=_only(args.only),
        force=args.force,
        allow_placeholders=args.allow_placeholders,
        silent=args.silent,
        model=args.model,
    )
    print()
    print(_report.segments_table(result.segments, cfg.words_per_minute, result))
    print()
    print(_report.timeline_table(result.timeline))
    return 0


def cmd_beats(args: argparse.Namespace) -> int:
    from .stages.beats import resolve_beats

    project = _project(args)
    result = resolve_beats(project, allow_unknown=args.allow_unknown)
    unknown = 0 if args.allow_unknown else result.unknown
    # A section whose speech ends before its min_seconds is probably too short for its visuals.
    short = sum(
        1 for s in result.sections if not s.skipped and s.min_seconds is not None and s.speech_end < s.min_seconds
    )
    findings = Findings(certain=result.unresolved + unknown, uncertain=short)
    return _finish(args, findings, result.to_dict(project.root), lambda: _report.beats_table(result))


def cmd_soundscape(args: argparse.Namespace) -> int:
    from .stages.soundscape import soundscape

    print(_report.soundscape_table(soundscape(_project(args), only=args.only, force=args.force, dry_run=args.dry_run)))
    return 0


def cmd_record(args: argparse.Namespace) -> int:
    from .stages.record import record

    record(_project(args), only=_only(args.only), seconds=args.seconds, use_beats=not args.no_beats)
    return 0


def cmd_measure(args: argparse.Namespace) -> int:
    from .stages.measure import measure

    print(_report.leads_table(measure(_project(args), only=_only(args.only))))
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    from .stages.measure import check

    project = _project(args)
    rows = check(project, only=_only(args.only))
    findings = count(r.verdict for r in rows)
    payload = {"recordings": [r.to_dict(project.root) for r in rows]}
    return _finish(args, findings, payload, lambda: _report.checks_table(rows))


def cmd_assemble(args: argparse.Namespace) -> int:
    from .stages.assemble import assemble

    result = assemble(_project(args), nomix=args.nomix, loudnorm=not args.no_loudnorm, strict=args.strict)
    print(f"{result.final}  ({result.duration:.2f}s)")
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    from .stages.verify import verify

    project = _project(args)
    checks = [*args.checks, *(args.cue or [])]
    result = verify(project, checks=checks or None, only=_only(args.only))
    return _finish(args, _verify_findings(result), result.to_dict(project.root), lambda: _report.verify_table(result))


def cmd_shots(args: argparse.Namespace) -> int:
    if args.cue:
        # A cue freezes one step at the moment that cue fires, so it needs exactly one step.
        if not args.step or len(args.step) != 1 or args.section is not None:
            args.parser.error("--cue needs exactly one --step, and it does not combine with --section")
        from .stages.shots import shoot_steps

        shoot_steps(_project(args), args.page or None, args.step, cues=args.cue)
        return 0
    from .stages.shots import shoot

    shoot(_project(args), pages=args.page or None, steps=args.step or None, section=args.section, at=args.at or None)
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    from .status import status

    project = _project(args)
    report = status(project)
    # status reads what exists and judges nothing, so it has no findings of its own.
    return _finish(args, Findings(), report.to_dict(project.root), lambda: _report.status_table(report))


def cmd_build(args: argparse.Namespace) -> int:
    from .stages.build import build

    project = _project(args)
    wpm = project.settings.narration.words_per_minute

    def report(stage: str, result: Any) -> None:
        if stage == "narrate":
            print(_report.segments_table(result.segments, wpm, result))
            print(_report.timeline_table(result.timeline))
        elif stage == "beats":
            print(_report.beats_table(result))
        elif stage == "measure":
            print(_report.leads_table(result))
        elif stage == "check":
            print(_report.checks_table(result))
        elif stage == "verify":
            print(_report.verify_table(result))

    result = build(
        project,
        silent=args.silent,
        force=args.force,
        only=_only(args.only),
        nomix=args.nomix,
        loudnorm=not args.no_loudnorm,
        strict=args.strict,
        allow_unresolved=args.allow_unresolved,
        allow_unknown=args.allow_unknown,
        report=report,
    )
    if result.assembly:
        print(f"\nbuilt {result.assembly.final}")
    return 0 if result.ok else 1


# ---- parser -------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="decktalk", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--version", action="version", version=f"decktalk {__version__}")
    project_help = "project directory (default: DECKTALK_PROJECT, else the current directory)"
    verbose_help = "debug logging, including every ffmpeg command line"
    quiet_help = "warnings only"
    only_help = "only these section numbers (repeat the flag for several)"
    p.add_argument("--project", "-p", default=None, help=project_help)
    p.add_argument("-v", "--verbose", action="store_true", help=verbose_help)
    p.add_argument("-q", "--quiet", action="store_true", help=quiet_help)
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(sp: argparse.ArgumentParser) -> argparse.ArgumentParser:
        """Accept -v and -q after the command name too, as -p is below."""
        sp.add_argument("-v", "--verbose", action="store_true", default=argparse.SUPPRESS, help=verbose_help)
        sp.add_argument("-q", "--quiet", action="store_true", default=argparse.SUPPRESS, help=quiet_help)
        return sp

    def proj(sp: argparse.ArgumentParser) -> argparse.ArgumentParser:
        sp.add_argument("--project", "-p", default=argparse.SUPPRESS, help=project_help)
        return common(sp)

    def policy(sp: argparse.ArgumentParser) -> argparse.ArgumentParser:
        """The output and exit flags shared by the five read-only commands."""
        sp.add_argument("--json", action="store_true", help=JSON_HELP)
        sp.add_argument("--strict", action="store_true", help=STRICT_HELP)
        sp.add_argument("--no-fail", action="store_true", help=NO_FAIL_HELP)
        return sp

    def encoding(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--preset", help="x264 preset for this run (veryfast for drafts)")
        sp.add_argument("--crf", type=int, help="x264 quality for this run")

    s = common(sub.add_parser("init", help="scaffold a project directory"))
    s.add_argument("dir", help="the directory to create, with a working example deck")
    s.add_argument("--name", help="project name (default: directory name)")
    s.add_argument("--force", action="store_true", help="write into a non-empty directory")
    s.set_defaults(fn=cmd_init)

    common(sub.add_parser("setup", help="fetch Chromium and ffmpeg")).set_defaults(fn=cmd_setup)
    policy(common(sub.add_parser("doctor", help="report installed tools"))).set_defaults(fn=cmd_doctor)
    proj(sub.add_parser("runtime", help="copy the packaged runtime into the project")).set_defaults(fn=cmd_runtime)

    s = proj(sub.add_parser("narrate", help="synthesize narration with word timestamps"))
    s.add_argument("--only", type=int, action="append", help=only_help)
    s.add_argument("--force", action="store_true", help="ignore the text-hash cache")
    s.add_argument(
        "--allow-placeholders", action="store_true", help="synthesize a section that still has a [CAPITAL] placeholder"
    )
    s.add_argument("--dry-run", action="store_true", help="parse and print without any API call")
    s.add_argument("--silent", action="store_true", help="silent placeholders, no API key")
    s.add_argument("--model", help="ElevenLabs model for this run")
    s.set_defaults(fn=cmd_narrate)

    s = policy(proj(sub.add_parser("beats", help="resolve cue phrases to timestamps")))
    s.add_argument("--allow-unknown", action="store_true", help=ALLOW_UNKNOWN_HELP)
    s.set_defaults(fn=cmd_beats)

    s = proj(sub.add_parser("soundscape", help="generate ambience, sfx and underscore"))
    s.add_argument("--only", action="append", help="one item: ambience, music, or an effect name (repeat for several)")
    s.add_argument("--force", action="store_true", help="regenerate even if the file exists")
    s.add_argument("--dry-run", action="store_true", help="print every request without sending it")
    s.set_defaults(fn=cmd_soundscape)

    s = proj(sub.add_parser("record", help="record the pages with headless Chromium"))
    s.add_argument("--only", type=int, action="append", help=only_help)
    s.add_argument("--seconds", type=float, help="override every duration (smoke tests)")
    s.add_argument("--settle", type=float, help="seconds after load before the clock starts")
    s.add_argument("--no-beats", action="store_true", help="autoplay timing instead of ?beats=")
    s.set_defaults(fn=cmd_record)

    s = proj(sub.add_parser("measure", help="find narration t=0 in each recording"))
    s.add_argument("--only", type=int, action="append", help=only_help)
    s.set_defaults(fn=cmd_measure)

    s = policy(proj(sub.add_parser("check", help="recording sanity: duration and luma")))
    s.add_argument("--only", type=int, action="append", help=only_help)
    s.set_defaults(fn=cmd_check)

    s = proj(sub.add_parser("assemble", help="cut, mix and normalize the final mp4"))
    s.add_argument("--nomix", action="store_true", help="narration only: no beds, no effects")
    s.add_argument("--no-loudnorm", action="store_true", help="skip loudness normalization")
    s.add_argument(
        "--strict", action="store_true", help="fail on a missing clip or recording instead of substituting a slate"
    )
    encoding(s)
    s.set_defaults(fn=cmd_assemble)

    s = policy(proj(sub.add_parser("verify", help="check section starts, cuts and cue landings on the final mp4")))
    s.add_argument("checks", nargs="*", metavar="SECTION:CUE", help="cues to check (default: every cue in beats.json)")
    s.add_argument(
        "--cue", action="append", metavar="SECTION:CUE", help="one cue to check, added to any positional ones (repeat)"
    )
    s.add_argument("--only", type=int, action="append", help=only_help)
    s.set_defaults(fn=cmd_verify)

    s = proj(sub.add_parser("shots", help="screenshots per step, or frames from a playing section"))
    s.add_argument("--page", action="append", help="page file (default: every page in decktalk.toml)")
    s.add_argument("--step", action="append", help="only these step ids")
    s.add_argument("--cue", action="append", metavar="ID", help="freeze the one --step at this cue id (repeat)")
    s.add_argument("--section", type=int, help="play this section with its resolved cues")
    s.add_argument("--at", type=float, action="append", help="seconds after narration t=0 (with --section)")
    s.set_defaults(fn=cmd_shots, parser=s)

    policy(proj(sub.add_parser("status", help="what is built"))).set_defaults(fn=cmd_status)

    s = proj(sub.add_parser("build", help="run the whole pipeline"))
    s.add_argument("--silent", action="store_true", help="placeholder narration, no API key")
    s.add_argument("--force", action="store_true", help="re-synthesize every section")
    s.add_argument(
        "--only", type=int, action="append", help="re-record only these sections (repeat the flag for several)"
    )
    s.add_argument("--nomix", action="store_true", help="narration only: no beds, no effects")
    s.add_argument("--no-loudnorm", action="store_true", help="skip loudness normalization")
    s.add_argument(
        "--strict", action="store_true", help="fail on a missing clip or recording instead of substituting a slate"
    )
    s.add_argument("--allow-unresolved", action="store_true", help="build even if some cue phrases were not found")
    s.add_argument("--allow-unknown", action="store_true", help=ALLOW_UNKNOWN_HELP)
    encoding(s)
    s.set_defaults(fn=cmd_build)
    return p


def configure_logging(verbose: bool, quiet: bool) -> None:
    level = logging.DEBUG if verbose else logging.WARNING if quiet else logging.INFO
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(message)s" if not verbose else "%(name)s: %(message)s"))
    root = logging.getLogger("decktalk")
    root.handlers[:] = [handler]
    root.setLevel(level)
    root.propagate = False


def main(argv: list[str] | None = None) -> int:
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if reconfigure is not None:
        reconfigure(line_buffering=True)  # keep stdout and stderr in order
    args = build_parser().parse_args(argv)
    verbose = getattr(args, "verbose", False)
    configure_logging(verbose, getattr(args, "quiet", False))
    try:
        return int(args.fn(args) or 0)
    except DeckTalkError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        if verbose:
            raise
        print(f"error: {type(exc).__name__}: {exc} (add -v for the traceback)", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
