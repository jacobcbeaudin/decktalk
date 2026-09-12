"""decktalk command line.

    decktalk init DIR                 scaffold a project (script, scenes, cues, deck with the runtime)
    decktalk setup                    fetch headless Chromium and ffmpeg (once per machine)
    decktalk doctor                   report what is installed
    decktalk narrate [--silent]       script.md -> build/audio (ElevenLabs, word timestamps, timeline)
    decktalk beats                    cues.json -> build/audio/beats.json
    decktalk soundscape               ambience, sfx, underscore (ElevenLabs)
    decktalk broll --prompt "..."     text-to-video clip -> media/broll/
    decktalk record                   pages -> build/rec/NN-scene.webm (Playwright)
    decktalk measure                  find narration t=0 in each recording
    decktalk check                    recording sanity (black / truncated)
    decktalk assemble                 ffmpeg -> build/out/<name>.mp4
    decktalk verify [SEC:CUE ...]     section starts (+ cue landings) on the final mp4
    decktalk shots                    per-step screenshots, or frames from a playing section
    decktalk build [--silent]         narrate -> beats -> record -> measure -> check -> assemble -> verify
    decktalk status                   timeline and what is built
    decktalk runtime                  copy the packaged decktalk-runtime.js into the project

Every project command takes --project/-p DIR (default: the current directory).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__
from .project import Project
from .tools import fmt_mmss


def _project(args: argparse.Namespace) -> Project:
    return Project.load(getattr(args, "project", None))


def _only(values: list[int] | None) -> list[int] | None:
    return values or None


def cmd_init(args: argparse.Namespace) -> int:
    from .scaffold import init

    return init(Path(args.dir), name=args.name, force=args.force)


def cmd_setup(args: argparse.Namespace) -> int:
    from .scaffold import setup

    return setup()


def cmd_doctor(args: argparse.Namespace) -> int:
    from .scaffold import doctor

    return doctor()


def cmd_runtime(args: argparse.Namespace) -> int:
    from .scaffold import update_runtime

    return update_runtime(_project(args).root)


def cmd_narrate(args: argparse.Namespace) -> int:
    from .narrate import narrate

    return narrate(
        _project(args),
        only=_only(args.only),
        force=args.force,
        allow_placeholders=args.allow_placeholders,
        dry_run=args.dry_run,
        silent=args.silent,
        model=args.model,
    )


def cmd_beats(args: argparse.Namespace) -> int:
    from .beats import beats

    return beats(_project(args))


def cmd_soundscape(args: argparse.Namespace) -> int:
    from .soundscape import soundscape

    return soundscape(_project(args), only=args.only, force=args.force, dry_run=args.dry_run)


def cmd_broll(args: argparse.Namespace) -> int:
    from .broll import BrollRequest, broll

    req = BrollRequest(
        prompt=args.prompt,
        negative=args.negative,
        duration=args.duration,
        resolution=args.resolution,
        aspect=args.aspect,
        poll=args.poll,
        timeout=args.timeout,
    )
    if args.model:
        req.model = args.model
    if args.fal_model:
        req.fal_model = args.fal_model
    return broll(
        _project(args),
        req,
        name=args.name,
        out_dir=Path(args.out) if args.out else None,
        takes=args.takes,
        provider=args.provider,
        dry_run=args.dry_run,
    )


def cmd_record(args: argparse.Namespace) -> int:
    from .record import record

    return record(
        _project(args), only=_only(args.only), seconds=args.seconds, settle=args.settle, no_beats=args.no_beats
    )


def cmd_measure(args: argparse.Namespace) -> int:
    from .measure import measure

    return measure(_project(args), only=_only(args.only))


def cmd_check(args: argparse.Namespace) -> int:
    from .measure import check

    bad = check(_project(args), only=_only(args.only))
    return 1 if bad and args.strict else 0


def cmd_assemble(args: argparse.Namespace) -> int:
    from .assemble import assemble

    return assemble(
        _project(args),
        nomix=args.nomix,
        no_loudnorm=args.no_loudnorm,
        strict=args.strict,
        preset=args.preset,
        crf=args.crf,
    )


def cmd_verify(args: argparse.Namespace) -> int:
    from .verify import verify

    return verify(_project(args), checks=args.checks or None)


def cmd_shots(args: argparse.Namespace) -> int:
    from .shots import shots

    return shots(
        _project(args), pages=args.page or None, steps=args.step or None, section=args.section, at=args.at or None
    )


def cmd_status(args: argparse.Namespace) -> int:
    project = _project(args)
    print(f"project  {project.root}  (name: {project.name})")
    print(f"script   {project.script.relative_to(project.root)}  {'ok' if project.script.exists() else 'MISSING'}")
    print(f"cues     {project.cues.relative_to(project.root)}  {'ok' if project.cues.exists() else 'none'}")
    for sec in project.sections:
        what = f"clip {sec.data['video']}" if sec.is_video else f"{sec.file}?scene={sec.scene}"
        rec = (project.rec_dir / f"{sec.key}-scene.webm").exists()
        out = (project.out_dir / f"{sec.key}-section.mp4").exists()
        marks = ("rec " if rec else "    ") + ("cut" if out else "   ")
        print(f"  {sec.key}  {what:<40} {marks}")
    tl = project.timeline_data()
    if tl:
        from .narrate import print_timeline

        print_timeline(tl)
    else:
        print("timeline none (run `decktalk narrate`)")
    beats = project.beats_data()
    print(f"beats    {len(beats)} section(s) with resolved cues" if beats else "beats    none (run `decktalk beats`)")
    if project.final.exists():
        from .tools import ffprobe_duration

        print(f"final    {project.final.relative_to(project.root)}  {fmt_mmss(ffprobe_duration(project.final))}")
    else:
        print("final    not built")
    return 0


def cmd_build(args: argparse.Namespace) -> int:
    from .assemble import assemble
    from .beats import beats
    from .measure import check, measure
    from .narrate import narrate
    from .record import record
    from .verify import verify

    project = _project(args)
    steps = [
        ("narrate", lambda: narrate(project, silent=args.silent, force=args.force, model=args.model)),
        ("beats", lambda: beats(project)),
        ("record", lambda: record(project, only=_only(args.only))),
        ("measure", lambda: measure(project, only=_only(args.only))),
        ("check", lambda: check(project, only=_only(args.only)) and 0),
        (
            "assemble",
            lambda: assemble(
                project, nomix=args.nomix, no_loudnorm=args.no_loudnorm, strict=args.strict, preset=args.preset
            ),
        ),
        ("verify", lambda: verify(project)),
    ]
    for name, fn in steps:
        print(f"\n===== {name} =====")
        rc = fn()
        if rc:
            print(f"\nbuild stopped at {name} (exit {rc})", file=sys.stderr)
            return rc
    print(f"\nbuilt {project.final}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="decktalk", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--version", action="version", version=f"decktalk {__version__}")
    p.add_argument("--project", "-p", default=None, help="project directory (default: .)")
    sub = p.add_subparsers(dest="cmd", required=True)

    def proj(sp: argparse.ArgumentParser) -> argparse.ArgumentParser:
        sp.add_argument("--project", "-p", default=argparse.SUPPRESS, help="project directory (default: .)")
        return sp

    s = sub.add_parser("init", help="scaffold a project directory")
    s.add_argument("dir")
    s.add_argument("--name", help="output name (default: directory name)")
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
    s.add_argument("--model", default="eleven_multilingual_v2")
    s.set_defaults(fn=cmd_narrate)

    proj(sub.add_parser("beats", help="resolve cue phrases to timestamps")).set_defaults(fn=cmd_beats)

    s = proj(sub.add_parser("soundscape", help="generate ambience, sfx and underscore"))
    s.add_argument("--only", action="append", help="ambience | music | <sfx name>")
    s.add_argument("--force", action="store_true")
    s.add_argument("--dry-run", action="store_true")
    s.set_defaults(fn=cmd_soundscape)

    s = proj(sub.add_parser("broll", help="generate a text-to-video clip"))
    s.add_argument("--prompt", required=True)
    s.add_argument("--name", default="broll", help="clip name (media/broll/<name>.mp4)")
    s.add_argument("--negative", default="")
    s.add_argument("--takes", type=int, default=1)
    s.add_argument("--out", help="output directory (default media/broll)")
    s.add_argument("--provider", choices=["auto", "gemini", "fal"], default="auto")
    s.add_argument("--model", help="Gemini Veo model id")
    s.add_argument("--fal-model", help="fal.ai model id")
    s.add_argument("--duration", type=int, default=8)
    s.add_argument("--resolution", default="1080p", choices=["720p", "1080p", "4k"])
    s.add_argument("--aspect", default="16:9", choices=["16:9", "9:16"])
    s.add_argument("--poll", type=int, default=10)
    s.add_argument("--timeout", type=int, default=900)
    s.add_argument("--dry-run", action="store_true")
    s.set_defaults(fn=cmd_broll)

    s = proj(sub.add_parser("record", help="record the pages with headless Chromium"))
    s.add_argument("--only", type=int, action="append")
    s.add_argument("--seconds", type=float, help="override every duration (smoke tests)")
    s.add_argument("--settle", type=float, default=0.5, help="seconds after load before the clock starts")
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
    s.add_argument("--preset", help="x264 preset (default medium; veryfast for drafts)")
    s.add_argument("--crf", type=int)
    s.set_defaults(fn=cmd_assemble)

    s = proj(sub.add_parser("verify", help="check section starts and cue landings on the final mp4"))
    s.add_argument("checks", nargs="*", help="SECTION:CUE ...")
    s.set_defaults(fn=cmd_verify)

    s = proj(sub.add_parser("shots", help="screenshots per step, or frames from a playing section"))
    s.add_argument("--page", action="append", help="page file (default: every page in scenes.json)")
    s.add_argument("--step", action="append", help="only these step ids")
    s.add_argument("--section", type=int, help="play this section with its resolved cues")
    s.add_argument("--at", type=float, action="append", help="seconds after narration t=0 (with --section)")
    s.set_defaults(fn=cmd_shots)

    proj(sub.add_parser("status", help="what is built")).set_defaults(fn=cmd_status)

    s = proj(sub.add_parser("build", help="run the whole pipeline"))
    s.add_argument("--silent", action="store_true", help="placeholder narration, no API key")
    s.add_argument("--force", action="store_true", help="re-synthesize every section")
    s.add_argument("--model", default="eleven_multilingual_v2")
    s.add_argument("--only", type=int, action="append", help="re-record only these sections")
    s.add_argument("--nomix", action="store_true")
    s.add_argument("--no-loudnorm", action="store_true")
    s.add_argument("--strict", action="store_true")
    s.add_argument("--preset", help="x264 preset (veryfast for drafts)")
    s.set_defaults(fn=cmd_build)
    return p


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(line_buffering=True)  # keep stdout and stderr in order
    except (AttributeError, ValueError):
        pass
    args = build_parser().parse_args(argv)
    try:
        return int(args.fn(args) or 0)
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
