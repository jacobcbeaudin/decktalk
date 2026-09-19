"""The commands an author runs around a build: what is there, what it would do, and what it cuts.

`status` and `words` only read. `preflight` rehearses a voiced build and spends nothing.
`screenshots`, `soundscape` and `clip` write files, and each lists what it wrote. `serve` is the
one command that does not end: it holds the local origin open until the author stops it.
"""

from __future__ import annotations

import logging
import sys

from ..jsonio import relative
from ..media.origin import open_server, reachable_warning, served_urls
from ..stages.clip import clip as cut_span
from ..stages.clip import words as read_words
from ..stages.preflight import preflight as rehearse
from ..stages.screenshots import screenshots as capture_pngs
from ..stages.soundscape import soundscape as generate
from ..status import status as read_status
from . import options as opt
from . import output
from .envelope import Outcome, of, wrote
from .options import load_project

log = logging.getLogger(__name__)


def status(opts: opt.StatusOptions) -> Outcome:
    project = load_project(opts)
    result = read_status(project)
    cut = sum(1 for section in result.sections if section.cut)
    summary = {"sections": len(result.sections), "cut": cut, "final": result.final_exists}
    return of(result, project.root, summary, [], output.status_table(result))


# ---- before a build --------------------------------------------------------------------


def preflight(opts: opt.PreflightOptions) -> Outcome:
    project = load_project(opts)
    result = rehearse(
        project,
        only=opts.only or None,
        frames=not opts.no_frames,
        model=opts.model,
        allow_unknown_cues=opts.allow_unknown_cues,
    )
    summary = {"sections": len(result.takes), "cues": len(result.cues), "unresolved": result.align.unresolved}
    # Every frozen frame a rehearsal rendered, because the frames are what a reader opens next.
    frames = [f for cue in result.cues for f in (cue.before, cue.after)]
    frames += [f for seam in result.seams for f in (seam.last, seam.first)]
    written = wrote(project.root, *frames)
    return of(result, project.root, summary, written, output.preflight_table(result))


def words(opts: opt.WordsOptions) -> Outcome:
    project = load_project(opts)
    result = read_words(project, only=opts.only or None)
    summary = {"sections": len(result.sections), "words": sum(len(s.words) for s in result.sections)}
    return of(result, project.root, summary, [], output.words_table(result.sections))


def screenshots(opts: opt.ScreenshotsOptions) -> Outcome:
    project = load_project(opts)
    result = capture_pngs(
        project,
        pages=opts.page or None,
        slides=opts.slide or None,
        section=opts.section,
        at=opts.at or None,
        cues=opts.after or None,
    )
    written = wrote(project.root, *result.paths)
    return of(result, project.root, {"files": len(result.files)}, written, output.file_list(written))


def soundscape(opts: opt.SoundscapeOptions) -> Outcome:
    project = load_project(opts)
    result = generate(project, only=opts.names or None, force=opts.force, dry_run=opts.dry_run)
    made = [item for item in result.items if item.status == "generated"]
    summary = {"items": len(result.items), "generated": len(made)}
    written = wrote(project.root, *(item.out for item in made))
    return of(result, project.root, summary, written, output.soundscape_table(result.items))


def clip(opts: opt.ClipOptions) -> Outcome:
    project = load_project(opts)
    result = cut_span(
        project,
        opts.section,
        start=opts.start,
        end=opts.end,
        out=opts.out,
        words_out=opts.words,
        gain_db=opts.gain,
        hold_seconds=opts.hold,
    )
    written = [relative(p, project.root) for p in (result.video, result.words_file)]
    summary = {"seconds": round(result.duration, 2), "words": len(result.words)}
    return of(result, project.root, summary, written, output.clip_note(result, *written))


def serve(opts: opt.ServeOptions) -> Outcome:
    """Hold the local origin open on the project directory until the author stops the command.

    The envelope is printed before the server blocks, because a caller that started `serve` to open
    a page needs the address while the server is still running rather than after it is stopped.
    """
    import webbrowser

    project = load_project(opts)
    server = open_server(project.root, opts.host, opts.port)
    urls = served_urls(server, project.page_files or ["index.html"])
    warning = reachable_warning(server)
    if warning:
        print(f"warning: {warning}", file=sys.stderr)

    def hold() -> None:
        if opts.open:
            webbrowser.open(urls[0])
        log.info("serving %s; press Ctrl-C to stop", project.root)
        with server:
            server.serve_forever()

    return Outcome(payload={"urls": urls}, summary={"urls": len(urls)}, text="\n".join(urls), after=hold)
