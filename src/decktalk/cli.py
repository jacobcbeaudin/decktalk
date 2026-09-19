"""decktalk command line.

    decktalk init DIR                 scaffold a project (decktalk.toml, script, cues, deck with the runtime)
    decktalk install                  fetch headless Chromium and ffmpeg (once per machine)
    decktalk doctor                   report what is installed
    decktalk narrate [--no-voice]     script.md -> build/narration (ElevenLabs, word timestamps, timeline)
    decktalk narrate --dry-run        what a voiced run would send, cache, or move (--json)
    decktalk align                    cues.json -> build/cue-times.json
    decktalk preflight                takes, cues and frozen reveals before a voiced build: no credits, no recording
    decktalk soundscape               ambience, sfx, music (ElevenLabs)
    decktalk record                   pages -> build/recordings/NN.webm (Chromium)
    decktalk measure                  find narration t=0 in each recording
    decktalk check                    recording sanity (black / truncated)
    decktalk assemble                 ffmpeg -> build/out/<name>.mp4
    decktalk verify [SECTION:CUE ...] section starts, cuts, and every cue landing on the final mp4
    decktalk screenshots              one PNG per slide, per cue of one slide, or per second of a playing section
    decktalk words [--json]           each spoken section's words, in seconds after the section starts
    decktalk clip N --start S --end E --out FILE
                                      a span of a built section and its narration -> a clip and its words file
    decktalk build [--no-voice]       narrate -> align -> record -> measure -> check -> assemble -> verify
    decktalk status                   timeline and what is built

Every project command takes --project/-p DIR (default: DECKTALK_PROJECT, else the current
directory). The -v and -q flags go before or after the command name.
Tuning flags such as --preset override decktalk.toml and DECKTALK_* env for one run.
Six read-only commands print JSON with --json. The five that judge, align, preflight, check, verify
and doctor, take --exit-zero, and the four that can end a verdict in ? also take --strict. They exit
1 on a certain finding, and on an uncertain one only with --strict.
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
from .verdicts import Findings, Verdict

log = logging.getLogger("decktalk")

STRICT_HELP = "also exit 1 on an uncertain verdict, the ones marked with a question mark"
EXIT_ZERO_HELP = "exit 0 even when a check fails, for a caller that reads the table or the JSON itself"
JSON_HELP = "print the result as one JSON object on stdout instead of the tables (progress stays on stderr)"
ALLOW_UNKNOWN_HELP = "continue when a cue id in cues.json appears nowhere in the page that plays it"
BUILD_STRICT_HELP = (
    "fail on a missing clip or recording instead of substituting a slate "
    "(a clip section with optional = true still plays its slate), "
    "and on a recording that measure has not read since it was recorded"
)


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


def _exit_for(findings: Findings, strict: bool, exit_zero: bool) -> int:
    """The exit code of a read-only command, from what it found.

    A certain finding exits 1. An uncertain finding exits 1 only with `strict`. With
    `exit_zero` the command exits 0 whatever it found. An error is not a finding, so it
    still exits 1 through main.
    """
    if exit_zero:
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
            "ok": _exit_for(findings, args.strict, exit_zero=False) == 0,
            "findings": findings.to_dict(),
            args.cmd: payload,
        }
        print(json.dumps(doc, indent=2))
    else:
        print(table())
    return _exit_for(findings, args.strict, args.exit_zero)


def _verify_findings(result: Any) -> Findings:
    """Every start, cut and cue verdict in a VerifyResult, tallied."""
    verdicts: Iterable[Verdict | None] = [
        *(s.verdict for s in result.starts),
        *(c.verdict for c in result.cuts),
        *(c.verdict for c in result.seams),
        *(c.verdict for c in result.cues),
    ]
    return Findings.of(verdicts)


# ---- commands ------------------------------------------------------------------------


def cmd_init(args: argparse.Namespace) -> int:
    from .scaffold import init

    target = init(Path(args.dir), name=args.name, force=args.force)
    print(f"created {target}")
    print("  decktalk.toml  the project file: sections -> pages or clips, voice, mix, soundscape")
    print("  script.md      the narration (## N. sections)")
    print("  cues.json      which spoken phrase each visual lands on")
    print("  deck/          index.html, lesson.html, decktalk-runtime.js (open a page for its scene index)")
    print("  media/         your clips, b-roll, markers.json")
    print("next: cp .env.example .env  (ELEVENLABS_API_KEY, ELEVENLABS_VOICE_ID), then `decktalk build`")
    print("      or `decktalk build --no-voice` to render with placeholder narration and no API key")
    return 0


def cmd_install(args: argparse.Namespace) -> int:
    from .scaffold import install

    install()
    print("install complete")
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    from .scaffold import doctor

    rows = doctor()
    findings = Findings(certain=sum(1 for r in rows if not r.ok))
    return _finish(
        args,
        findings,
        {"components": [r.to_dict() for r in rows]},
        lambda: "\n".join(f"{r.name:<9} {'ok     ' if r.ok else 'MISSING'} {r.detail}" for r in rows),
    )


def cmd_narrate(args: argparse.Namespace) -> int:
    from .stages.narrate import narrate, narration_plan, plan_totals, script_segments

    if args.json and not args.dry_run:
        args.parser.error("--json needs --dry-run")
    project = _project(args)
    cfg = project.settings.narration
    if args.dry_run:
        _all, spoken = script_segments(project)
        targets = [s for s in spoken if not args.only or s.index in set(args.only)]
        model = args.model or project.voice.model or cfg.model
        plans, note = narration_plan(project, targets, model=model, force=args.force)
        if args.json:
            payload = {
                "voice": {"provider": project.voice.provider, "model": model, "settings": project.voice.api_settings()},
                "note": note,
                "sections": [{**p.to_dict(cfg), "text": p.segment.tts_text(cfg)} for p in plans],
                "totals": plan_totals(plans, cfg),
            }
            doc = {"command": args.cmd, "version": __version__, "ok": True, "findings": Findings().to_dict()}
            print(json.dumps({**doc, args.cmd: payload}, indent=2))
            return 0
        for seg in targets:
            print(f"=== {seg.key} {seg.title}  -> {seg.filename}")
            print(seg.tts_text(cfg))
            print()
        print(f"voice: model={model} {project.voice.api_settings()}")
        print(_report.segments_table(targets, cfg.words_per_minute))
        print()
        print(_report.plan_table(plans, cfg, note))
        unfilled = sorted({p for s in targets for p in s.placeholders})
        if unfilled:
            print(f"\nnote: unfilled placeholders {unfilled}; fill them before the real run.")
        return 0
    result = narrate(
        project,
        only=_only(args.only),
        force=args.force,
        allow_placeholders=args.allow_placeholders,
        silent=args.no_voice,
        model=args.model,
    )
    print()
    print(_report.segments_table(result.segments, cfg.words_per_minute, result))
    print()
    print(_report.timeline_table(result.timeline))
    return 0


def cmd_align(args: argparse.Namespace) -> int:
    from .stages.align import UnknownCueError, align

    project = _project(args)
    try:
        result = align(project, allow_unknown_cues=args.allow_unknown_cues)
    except UnknownCueError as err:
        # cue-times.json is already written, so report the result like any other finding
        # instead of stopping before the table or the JSON is printed.
        result = err.result
    unknown = 0 if args.allow_unknown_cues else result.unknown
    # A section whose speech ends before its min_seconds is probably too short for its visuals.
    short = sum(
        1 for s in result.sections if not s.skipped and s.min_seconds is not None and s.speech_end < s.min_seconds
    )
    findings = Findings(certain=result.unresolved + unknown, uncertain=short)
    return _finish(args, findings, result.to_dict(project.root), lambda: _report.align_table(result))


def cmd_preflight(args: argparse.Namespace) -> int:
    from .stages.preflight import preflight

    project = _project(args)
    result = preflight(project, only=_only(args.only), frames=not args.no_frames, model=args.model)
    findings = result.findings(allow_unknown_cues=args.allow_unknown_cues)
    return _finish(args, findings, result.to_dict(project.root), lambda: _report.preflight_table(result))


def cmd_soundscape(args: argparse.Namespace) -> int:
    from .stages.soundscape import soundscape

    print(
        _report.soundscape_table(
            soundscape(_project(args), only=args.names or None, force=args.force, dry_run=args.dry_run)
        )
    )
    return 0


def cmd_record(args: argparse.Namespace) -> int:
    from .stages.record import record

    record(_project(args), only=_only(args.only), seconds=args.seconds, use_cues=not args.no_cues)
    return 0


def cmd_measure(args: argparse.Namespace) -> int:
    from .stages.measure import measure

    print(_report.leads_table(measure(_project(args), only=_only(args.only))))
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    from .stages.measure import check

    project = _project(args)
    rows = check(project, only=_only(args.only))
    findings = Findings.of(v for r in rows for v in r.verdicts)
    payload = {"recordings": [r.to_dict(project.root) for r in rows]}
    return _finish(args, findings, payload, lambda: _report.checks_table(rows))


def cmd_assemble(args: argparse.Namespace) -> int:
    from .stages.assemble import assemble

    result = assemble(
        _project(args), soundscape=not args.no_soundscape, loudness=not args.no_loudness, strict=args.strict
    )
    print(f"{result.final}  ({result.duration:.2f}s)")
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    from .stages.verify import verify

    project = _project(args)
    result = verify(project, checks=list(args.checks) or None, only=_only(args.only))
    return _finish(args, _verify_findings(result), result.to_dict(project.root), lambda: _report.verify_table(result))


def cmd_screenshots(args: argparse.Namespace) -> int:
    if args.after:
        # A cue freezes one slide at the moment that cue fires, so it needs exactly one slide.
        if not args.slide or len(args.slide) != 1 or args.section is not None:
            args.parser.error("--after needs exactly one --slide, and it does not combine with --section")
        from .stages.screenshots import screenshot_slides

        screenshot_slides(_project(args), args.page or None, args.slide, cues=args.after)
        return 0
    from .stages.screenshots import screenshots

    screenshots(
        _project(args), pages=args.page or None, slides=args.slide or None, section=args.section, at=args.at or None
    )
    return 0


def cmd_words(args: argparse.Namespace) -> int:
    from .stages.clip import words

    project = _project(args)
    sections = words(project, only=_only(args.only))
    if args.json:
        payload = {"sections": [s.to_dict() for s in sections]}
        doc = {"command": args.cmd, "version": __version__, "ok": True, "findings": Findings().to_dict()}
        print(json.dumps({**doc, args.cmd: payload}, indent=2))
        return 0
    print(_report.words_table(sections))
    return 0


def cmd_clip(args: argparse.Namespace) -> int:
    from .stages.clip import clip
    from .status import relpath

    project = _project(args)
    result = clip(
        project,
        args.section,
        start=args.start,
        end=args.end,
        out=args.out,
        words_out=args.words,
        gain_db=args.gain,
        hold_seconds=args.hold,
    )
    video, words = (relpath(p, project.root) for p in (result.video, result.words_file))
    source = f"sections/{args.section:02d}.mp4"
    print(
        f"wrote {video}  ({result.duration:.2f}s: frames {result.first_frame} to {result.last_frame} of {source}, "
        f"{result.start:.2f} to {result.end:.2f}s, hold {result.hold_seconds:g}s, gain {result.gain_db:+g} dB)"
    )
    print(f"wrote {words}  ({len(result.words)} words)")
    print(f'use it in a clip section: clip = "{video}" and words = "{words}"')
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
        elif stage == "align":
            print(_report.align_table(result))
        elif stage == "measure":
            print(_report.leads_table(result))
        elif stage == "check":
            print(_report.checks_table(result))
        elif stage == "verify":
            print(_report.verify_table(result))

    result = build(
        project,
        silent=args.no_voice,
        force=args.force,
        only=_only(args.only),
        soundscape=not args.no_soundscape,
        loudness=not args.no_loudness,
        strict=args.strict,
        allow_unresolved_cues=args.allow_unresolved_cues,
        allow_unknown_cues=args.allow_unknown_cues,
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

    def policy(sp: argparse.ArgumentParser, *, strict: bool = True, exit_zero: bool = True) -> argparse.ArgumentParser:
        """The output and exit flags of a read-only command, for the findings that command can make.

        A command that can never report an uncertain finding takes no `--strict`, and one that
        reports no finding at all also takes no `--exit-zero`, because a flag that cannot change an
        outcome is a promise the command breaks. Each command still exits through one policy, so
        the defaults stand in for the flags it does not offer.
        """
        sp.add_argument("--json", action="store_true", help=JSON_HELP)
        if strict:
            sp.add_argument("--strict", action="store_true", help=STRICT_HELP)
        else:
            sp.set_defaults(strict=False)
        if exit_zero:
            sp.add_argument("--exit-zero", action="store_true", help=EXIT_ZERO_HELP)
        else:
            sp.set_defaults(exit_zero=False)
        return sp

    def encoding(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--preset", help="x264 preset for this run (veryfast for drafts)")
        sp.add_argument("--crf", type=int, help="x264 quality for this run")

    s = common(sub.add_parser("init", help="scaffold a project directory"))
    s.add_argument("dir", help="the directory to create, with a working example deck")
    s.add_argument("--name", help="project name (default: directory name)")
    s.add_argument("--force", action="store_true", help="write into a non-empty directory")
    s.set_defaults(fn=cmd_init)

    common(sub.add_parser("install", help="fetch headless Chromium and ffmpeg once per machine")).set_defaults(
        fn=cmd_install
    )
    policy(common(sub.add_parser("doctor", help="report installed tools")), strict=False).set_defaults(fn=cmd_doctor)

    s = proj(sub.add_parser("narrate", help="synthesize narration with word timestamps"))
    s.add_argument("--only", type=int, action="append", metavar="N", help=only_help)
    s.add_argument(
        "--force", action="store_true", help="ignore the text-hash cache, and let --no-voice replace voiced takes"
    )
    s.add_argument(
        "--allow-placeholders", action="store_true", help="synthesize a section that still has a [CAPITAL] placeholder"
    )
    s.add_argument(
        "--dry-run",
        action="store_true",
        help="print each section's text and what a voiced run would voice, cache, or move, without any API call",
    )
    s.add_argument("--json", action="store_true", help="with --dry-run, print the plan as one JSON object on stdout")
    s.add_argument("--no-voice", action="store_true", help="placeholder narration with no API key and no spend")
    s.add_argument("--model", help="ElevenLabs model for this run")
    s.set_defaults(fn=cmd_narrate, parser=s)

    s = policy(
        proj(sub.add_parser("align", help="resolve each cue phrase in cues.json to a second on its section clock"))
    )
    s.add_argument("--allow-unknown-cues", action="store_true", help=ALLOW_UNKNOWN_HELP)
    s.set_defaults(fn=cmd_align)

    s = policy(proj(sub.add_parser("preflight", help="plan the takes, resolve the cues, and estimate every reveal")))
    s.add_argument("--only", type=int, action="append", metavar="N", help=only_help)
    s.add_argument(
        "--no-frames", action="store_true", help="skip the frozen frames, so no browser starts and nothing is written"
    )
    s.add_argument("--model", help="speech model to check the narration cache for")
    s.add_argument("--allow-unknown-cues", action="store_true", help=ALLOW_UNKNOWN_HELP)
    s.set_defaults(fn=cmd_preflight)

    s = proj(sub.add_parser("soundscape", help="generate ambience, sfx and music"))
    s.add_argument("names", nargs="*", metavar="NAME", help="only these items: ambience, music, or an effect name")
    s.add_argument("--force", action="store_true", help="regenerate even if the file exists")
    s.add_argument("--dry-run", action="store_true", help="print every request without sending it")
    s.set_defaults(fn=cmd_soundscape)

    s = proj(sub.add_parser("record", help="record the pages with headless Chromium"))
    s.add_argument("--only", type=int, action="append", metavar="N", help=only_help)
    s.add_argument("--seconds", type=float, help="override every duration (smoke tests)")
    s.add_argument("--settle", type=float, help="seconds after load before the clock starts")
    s.add_argument("--no-cues", action="store_true", help="preview timing instead of ?cues=")
    s.set_defaults(fn=cmd_record)

    s = proj(sub.add_parser("measure", help="find narration t=0 in each recording"))
    s.add_argument("--only", type=int, action="append", metavar="N", help=only_help)
    s.set_defaults(fn=cmd_measure)

    s = policy(proj(sub.add_parser("check", help="recording sanity: duration and luma")))
    s.add_argument("--only", type=int, action="append", metavar="N", help=only_help)
    s.set_defaults(fn=cmd_check)

    s = proj(sub.add_parser("assemble", help="cut, mix and normalize the final mp4"))
    s.add_argument("--no-soundscape", action="store_true", help="narration only: no music, no ambience, no effects")
    s.add_argument("--no-loudness", action="store_true", help="skip loudness normalization")
    s.add_argument("--strict", action="store_true", help=BUILD_STRICT_HELP)
    encoding(s)
    s.set_defaults(fn=cmd_assemble)

    s = policy(proj(sub.add_parser("verify", help="check section starts, cuts and cue landings on the final mp4")))
    s.add_argument(
        "checks", nargs="*", metavar="SECTION:CUE", help="cues to check (default: every cue in cue-times.json)"
    )
    s.add_argument("--only", type=int, action="append", metavar="N", help=only_help)
    s.set_defaults(fn=cmd_verify)

    s = proj(
        sub.add_parser(
            "screenshots", help="one PNG per slide, per cue of one slide, or per second of a playing section"
        )
    )
    s.add_argument("--page", action="append", help="page file (default: every page in decktalk.toml)")
    s.add_argument("--slide", action="append", metavar="ID", help="only these slide ids")
    s.add_argument("--after", action="append", metavar="ID", help="freeze the one --slide at this cue id (repeat)")
    s.add_argument("--section", type=int, help="play this section with its resolved cues")
    s.add_argument("--at", type=float, action="append", help="seconds after narration t=0 (with --section)")
    s.set_defaults(fn=cmd_screenshots, parser=s)

    s = proj(sub.add_parser("words", help="each spoken section's words, in seconds after the section starts"))
    s.add_argument("--only", type=int, action="append", metavar="N", help=only_help)
    s.add_argument("--json", action="store_true", help=JSON_HELP)
    s.set_defaults(fn=cmd_words)

    s = proj(sub.add_parser("clip", help="cut a span of a built section and its narration into a clip"))
    s.add_argument("section", type=int, help="the page section to cut from")
    s.add_argument("--start", type=float, required=True, metavar="SECONDS", help="start, after the section starts")
    s.add_argument("--end", type=float, required=True, metavar="SECONDS", help="end, after the section starts")
    s.add_argument("--out", required=True, help="the clip file, relative to the project, such as media/open.mp4")
    s.add_argument("--words", help="the words file (default: the clip's path with .words.json)")
    s.add_argument("--gain", type=float, default=0.0, metavar="DB", help="gain on the clip's sound, in dB")
    s.add_argument("--hold", type=float, default=0.0, metavar="SECONDS", help="hold the last frame this long, silent")
    encoding(s)
    s.set_defaults(fn=cmd_clip)

    policy(proj(sub.add_parser("status", help="what is built")), strict=False, exit_zero=False).set_defaults(
        fn=cmd_status
    )

    s = proj(sub.add_parser("build", help="run the whole pipeline"))
    s.add_argument("--no-voice", action="store_true", help="placeholder narration with no API key and no spend")
    s.add_argument(
        "--force", action="store_true", help="re-synthesize every section, and let --no-voice replace voiced takes"
    )
    s.add_argument(
        "--only",
        type=int,
        action="append",
        metavar="N",
        help="re-record only these sections (repeat the flag for several)",
    )
    s.add_argument("--no-soundscape", action="store_true", help="narration only: no music, no ambience, no effects")
    s.add_argument("--no-loudness", action="store_true", help="skip loudness normalization")
    s.add_argument("--strict", action="store_true", help=BUILD_STRICT_HELP)
    s.add_argument("--allow-unresolved-cues", action="store_true", help="build even if some cue phrases were not found")
    s.add_argument("--allow-unknown-cues", action="store_true", help=ALLOW_UNKNOWN_HELP)
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
