"""The captioned copy: the film with its captions drawn into the picture by the project's own page.

    build/final/<name>-captioned.mp4   the clean film with every caption laid over it

A place that cannot load a caption file still needs the captions, so `[output] captioned` asks for a
second film beside the clean one. Each caption is drawn by the page that plays where it shows, in a
band the runtime lays out over a clear frame, so the deck's own fonts and styles set it, and the page
places it by the voice and the turn that say it without the caption naming anyone. A caption over a
clip section, or over a section a command renders, is drawn by the film's first drawn page.

Each caption is drawn once as a PNG with an alpha channel and kept among the stills under everything
it is drawn from, so an unchanged caption opens no browser. The stills are laid over the clean film's
picture in one encode, each on every frame from the first at or after its start up to the first at
or after its end, which are the frames a player shows that caption on. The sound, the chapters and
the language tags are copied from the clean film untouched, so the two films differ in the band alone.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

from decktalk.artifacts import Placements
from decktalk.captions import CaptionCue
from decktalk.errors import InputError
from decktalk.inputs import Inputs, PageSection
from decktalk.machine.run import Run
from decktalk.media import browser, ffmpeg, pages
from decktalk.media.encode import Encoder
from decktalk.media.frames import span_indices
from decktalk.media.origin import page_url
from decktalk.page import Q
from decktalk.stages.storyboard import open_project_page

CAPTION_MARK = "caption"
"""What a caption's still is keyed under before the caption itself, so it never shares a key with a frozen state."""

DRAWN = "1"
"""What `?caption=` says to ask a page for its caption band alone."""

MICROSECONDS = 1_000_000
"""Truth: the concat demuxer counts time in microseconds, so a duration written in them is read exactly."""


@dataclass(frozen=True)
class Shown:
    """One caption as the picture shows it: the page that draws it, what it says, and the frames it covers."""

    page: str
    caption: dict[str, object]
    first: int
    after: int


def drawing_page(inputs: Inputs, placements: Placements, at: float) -> str | None:
    """The page that draws a caption shown at second `at`: the page of the section playing then, else the first."""
    drawn = inputs.document.drawn_sections
    playing = next((p for p in placements.sections if p.start <= at < p.end), None)
    section = inputs.document.section(playing.section) if playing is not None else None
    if isinstance(section, PageSection) and not section.rendered:
        return section.page
    return drawn[0].page if drawn else None


def page_caption(cue: CaptionCue) -> dict[str, object]:
    """What the page is handed for one caption: its lines, who says them and which lines are spoken."""
    first, after = cue.speech
    return {"lines": list(cue.lines), "voice": cue.voice, "turn": cue.turn, "spoken": [first, after]}


def shown(inputs: Inputs, placements: Placements, cues: Sequence[CaptionCue], fps: int, frames: int) -> list[Shown]:
    """Every caption that covers at least one of the film's `frames`, with the page that draws it, in order.

    A film with captions and no page to draw them is refused, because the copy the project asked for
    could not show any of them.
    """
    out: list[Shown] = []
    for cue in cues:
        covered = span_indices(cue.start, cue.end, Fraction(fps), frames)
        if not covered:
            continue
        first, after = covered.start, covered.stop
        page = drawing_page(inputs, placements, cue.start)
        if page is None:
            raise InputError(
                "[output] captioned asks for a captioned copy, and no section plays a page to draw its captions.",
                hint="Add a section that plays a page, or turn [output] captioned off.",
            )
        out.append(Shown(page=page, caption=page_caption(cue), first=first, after=after))
    return out


def caption_stills(inputs: Inputs, run: Run, captions: Sequence[Shown]) -> list[Path]:
    """One kept PNG per caption, drawn by its page over a clear frame, or read back when nothing it is drawn from moved.

    One browser draws every caption that is not kept, and each page is opened once for every caption
    it draws.
    """
    documents = inputs.documents()
    keys = [
        inputs.still_key(one.page, CAPTION_MARK, json.dumps(one.caption, sort_keys=True), documents=documents)
        for one in captions
    ]
    kept: dict[str, Path] = {}
    for key in dict.fromkeys(keys):
        if (hit := inputs.stills.find(key)) is not None:
            kept[key] = hit
    unkept = ((key, one) for key, one in zip(keys, captions, strict=True) if key not in kept)
    missing = sorted(unkept, key=lambda item: item[1].page)
    if missing:
        drawing = inputs.workspace.work_file("caption.png")
        record = inputs.settings.record
        try:
            with browser.chromium(inputs.settings.tools.chromium, policy=record.page_policy, spend=run.spend) as chrome:
                page, assets = open_project_page(chrome, inputs)
                opened: str | None = None
                for key, one in missing:
                    if key in kept:
                        continue
                    if one.page != opened:
                        pages.load(page, page_url(one.page, {Q.CAPTION: DRAWN}))
                        pages.await_ready(page)
                        opened = one.page
                    pages.caption_frame(page, one.caption, drawing)
                    kept[key] = inputs.stills.keep(key, drawing, assets.paths)
        finally:
            drawing.unlink(missing_ok=True)
    return [kept[key] for key in keys]


def layer_listing(stills: Sequence[Path], captions: Sequence[Shown], clear: Path, frames: int, fps: int) -> str:
    """The concat list that plays each still on its caption's frames and the clear frame on every other.

    Each duration is the difference of two frame boundaries written in whole microseconds, so the
    boundaries add up exactly and a still starts on the frame its caption does. The demuxer gives the
    last entry no duration of its own, so the clear frame is named once more after it.
    """

    def at(frame: int) -> int:
        return round(frame * MICROSECONDS / fps)

    lines: list[str] = []
    shown_to = 0
    for still, one in zip(stills, captions, strict=True):
        if one.first > shown_to:
            lines += [ffmpeg.concat_line(clear), f"duration {at(one.first) - at(shown_to)}us\n"]
        lines += [ffmpeg.concat_line(still), f"duration {at(one.after) - at(one.first)}us\n"]
        shown_to = one.after
    if frames > shown_to:
        lines += [ffmpeg.concat_line(clear), f"duration {at(frames) - at(shown_to)}us\n"]
    lines.append(ffmpeg.concat_line(clear))
    return "".join(lines)


def write_captioned(inputs: Inputs, run: Run, cues: Sequence[CaptionCue], placements: Placements) -> Path:
    """Lay every caption over the clean film's picture and publish the copy in one rename."""
    video = inputs.settings.video
    enc = Encoder(video, inputs.settings.audio)
    film = inputs.workspace.film
    frames = round(sum(p.end - p.start for p in placements.sections) * video.fps)
    captions = shown(inputs, placements, cues, video.fps, frames)
    stills = caption_stills(inputs, run, captions)
    clear = inputs.workspace.work_file("clear.png")
    listing = inputs.workspace.work_file("captions.txt")
    work = inputs.workspace.work_file("captioned.mp4")
    try:
        ffmpeg.run(
            "-f", "lavfi", "-i", f"color=c=black@0:s={video.width}x{video.height},format=rgba",
            "-frames:v", "1", str(clear),
        )  # fmt: skip
        listing.write_text(layer_listing(stills, captions, clear, frames, video.fps), encoding="utf-8")
        band = f"[1:v]fps={video.fps},format=rgba[band]"
        chain = f"{band};[0:v][band]overlay=0:0:eof_action=pass:format=yuv420,{enc.tag}[v]"
        ffmpeg.run(
            *ffmpeg.source(film), *ffmpeg.concat_source(listing, ffmpeg.LAYERED_FORMATS),
            "-filter_complex", chain, "-map", "[v]", "-map", "0:a", "-frames:v", str(frames),
            *enc.venc, "-c:a", "copy", "-map_metadata", "0", "-map_chapters", "0",
            "-movflags", "+faststart", str(work),
        )  # fmt: skip
        work.replace(inputs.workspace.captioned_film)
    finally:
        for path in (clear, listing, work):
            path.unlink(missing_ok=True)
    return inputs.workspace.captioned_film


def remove_captioned(inputs: Inputs) -> None:
    """Remove a captioned copy an earlier build left, so a project that turned the copy off never ships a stale one."""
    inputs.workspace.captioned_film.unlink(missing_ok=True)


__all__ = [
    "CAPTION_MARK",
    "Shown",
    "caption_stills",
    "drawing_page",
    "layer_listing",
    "page_caption",
    "remove_captioned",
    "shown",
    "write_captioned",
]
