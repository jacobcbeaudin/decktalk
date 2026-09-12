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
    decktalk verify [SEC:CUE ...]     section starts (+ cue landings) on the final mp4
    decktalk shots                    per-step screenshots, or frames from a playing section
    decktalk build [--silent]         narrate -> beats -> record -> measure -> check -> assemble -> verify
    decktalk status                   timeline and what is built
    decktalk runtime                  copy the packaged decktalk-runtime.js into the project

Every project command takes --project/-p DIR (default: the current directory).
Tuning flags such as --preset override decktalk.toml and DECKTALK_* env for one run.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Any

from . import __version__, _report
from .errors import DeckTalkError
from .project import ClipSection, Project

log = logging.getLogger("decktalk")


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
    for name, ok, detail in rows:
        print(f"{name:<9} {'ok     ' if ok else 'MISSING'} {detail}")
    return 0 if all(ok for _n, ok, _d in rows) else 1


def cmd_runtime(args: argparse.Namespace) -> int:
    from .scaffold import update_runtime

    for path in update_runtime(_project(args).root):
        print(f"updated {path}")
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

    print(_report.beats_table(resolve_beats(_project(args))))
    return 0


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

    rows = check(_project(args), only=_only(args.only))
    print(_report.checks_table(rows))
    return 1 if args.strict and any(not r.ok for r in rows) else 0


def cmd_assemble(args: argparse.Namespace) -> int:
    from .stages.assemble import assemble

    result = assemble(_project(args), nomix=args.nomix, loudnorm=not args.no_loudnorm, strict=args.strict)
    print(f"{result.final}  ({result.duration:.2f}s)")
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    from .stages.verify import verify

    result = verify(_project(args), checks=args.checks or None)
    print(_report.verify_table(result))
    return 0 if result.ok else 1


def cmd_shots(args: argparse.Namespace) -> int:
    from .stages.shots import shoot

    shoot(_project(args), pages=args.page or None, steps=args.step or None, section=args.section, at=args.at or None)
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    project = _project(args)
    print(f"project  {project.root}  (name: {project.name})")
    print(f"script   {project.script.relative_to(project.root)}  {'ok' if project.script.exists() else 'MISSING'}")
    print(f"cues     {project.cues.relative_to(project.root)}  {'ok' if project.cues.exists() else 'none'}")
    for sec in project.sections:
        what = f"clip {sec.clip}" if isinstance(sec, ClipSection) else f"{sec.page}?scene={sec.scene}"
        rec = project.recording(sec).exists()
        cut = project.section_video(sec).exists()
        print(f"  {sec.key}  {what:<40} {'rec ' if rec else '    '}{'cut' if cut else ''}")
    tl = project.timeline()
    print(_report.timeline_table(tl) if tl else "timeline none (run `decktalk narrate`)")
    beats = project.beats()
    print(
        f"beats    {len(beats.sections)} section(s) with resolved cues"
        if beats.sections
        else "beats    none (run `decktalk beats`)"
    )
    if project.final.exists():
        from .media.ffmpeg import probe_duration

        print(f"final    {project.final.relative_to(project.root)}  {_report.mmss(probe_duration(project.final))}")
    else:
        print("final    not built")
    return 0


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
    p.add_argument("--project", "-p", default=None, help="project directory (default: .)")
    p.add_argument("-v", "--verbose", action="store_true", help="debug logging (ffmpeg command lines)")
    p.add_argument("-q", "--quiet", action="store_true", help="warnings only")
    sub = p.add_subparsers(dest="cmd", required=True)

    def proj(sp: argparse.ArgumentParser) -> argparse.ArgumentParser:
        sp.add_argument("--project", "-p", default=argparse.SUPPRESS, help="project directory (default: .)")
        return sp

    def encoding(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--preset", help="x264 preset for this run (veryfast for drafts)")
        sp.add_argument("--crf", type=int, help="x264 quality for this run")

    s = sub.add_parser("init", help="scaffold a project directory")
    s.add_argument("dir")
    s.add_argument("--name", help="project name (default: directory name)")
    s.add_argument("--force", action="store_true", help="write into a non-empty directory")
    s.set_defaults(fn=cmd_init)

    sub.add_parser("setup", help="fetch Chromium and ffmpeg").set_defaults(fn=cmd_setup)
    sub.add_parser("doctor", help="report installed tools").set_defaults(fn=cmd_doctor)
    proj(sub.add_parser("runtime", help="copy the packaged runtime into the project")).set_defaults(fn=cmd_runtime)

    s = proj(sub.add_parser("narrate", help="synthesize narration with word timestamps"))
    s.add_argument("--only", type=int, action="append", help="section number(s)")
    s.add_argument("--force", action="store_true", help="ignore the text-hash cache")
    s.add_argument("--allow-placeholders", action="store_true")
    s.add_argument("--dry-run", action="store_true", help="parse and print; no API calls")
    s.add_argument("--silent", action="store_true", help="silent placeholders, no API key")
    s.add_argument("--model", help="ElevenLabs model for this run")
    s.set_defaults(fn=cmd_narrate)

    proj(sub.add_parser("beats", help="resolve cue phrases to timestamps")).set_defaults(fn=cmd_beats)

    s = proj(sub.add_parser("soundscape", help="generate ambience, sfx and underscore"))
    s.add_argument("--only", action="append", help="ambience | music | <sfx name>")
    s.add_argument("--force", action="store_true")
    s.add_argument("--dry-run", action="store_true")
    s.set_defaults(fn=cmd_soundscape)

    s = proj(sub.add_parser("record", help="record the pages with headless Chromium"))
    s.add_argument("--only", type=int, action="append")
    s.add_argument("--seconds", type=float, help="override every duration (smoke tests)")
    s.add_argument("--settle", type=float, help="seconds after load before the clock starts")
    s.add_argument("--no-beats", action="store_true", help="autoplay timing instead of ?beats=")
    s.set_defaults(fn=cmd_record)

    s = proj(sub.add_parser("measure", help="find narration t=0 in each recording"))
    s.add_argument("--only", type=int, action="append")
    s.set_defaults(fn=cmd_measure)

    s = proj(sub.add_parser("check", help="recording sanity: duration and luma"))
    s.add_argument("--only", type=int, action="append")
    s.add_argument("--strict", action="store_true", help="exit 1 on a suspect recording")
    s.set_defaults(fn=cmd_check)

    s = proj(sub.add_parser("assemble", help="cut, mix and normalize the final mp4"))
    s.add_argument("--nomix", action="store_true", help="narration only: no beds, no sfx")
    s.add_argument("--no-loudnorm", action="store_true")
    s.add_argument("--strict", action="store_true", help="fail on a missing clip or recording")
    encoding(s)
    s.set_defaults(fn=cmd_assemble)

    s = proj(sub.add_parser("verify", help="check section starts and cue landings on the final mp4"))
    s.add_argument("checks", nargs="*", help="SECTION:CUE ...")
    s.set_defaults(fn=cmd_verify)

    s = proj(sub.add_parser("shots", help="screenshots per step, or frames from a playing section"))
    s.add_argument("--page", action="append", help="page file (default: every page in decktalk.toml)")
    s.add_argument("--step", action="append", help="only these step ids")
    s.add_argument("--section", type=int, help="play this section with its resolved cues")
    s.add_argument("--at", type=float, action="append", help="seconds after narration t=0 (with --section)")
    s.set_defaults(fn=cmd_shots)

    proj(sub.add_parser("status", help="what is built")).set_defaults(fn=cmd_status)

    s = proj(sub.add_parser("build", help="run the whole pipeline"))
    s.add_argument("--silent", action="store_true", help="placeholder narration, no API key")
    s.add_argument("--force", action="store_true", help="re-synthesize every section")
    s.add_argument("--only", type=int, action="append", help="re-record only these sections")
    s.add_argument("--nomix", action="store_true")
    s.add_argument("--no-loudnorm", action="store_true")
    s.add_argument("--strict", action="store_true")
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
    configure_logging(args.verbose, args.quiet)
    try:
        return int(args.fn(args) or 0)
    except DeckTalkError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
