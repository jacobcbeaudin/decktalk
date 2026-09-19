"""Everything a viewer receives beside the picture: captions, chapters, the transcript and the poster.

Captions carry the speech of every page section, the speech inside a clip that names a words file,
and one cue per cued sound that names a caption, so a viewer who cannot hear the film still reads
`[ball bounces]`. The chapters come from the section chapters, and consecutive sections that share
one chapter share one marker. The transcript is the media alternative: one page with a heading per
chapter, what was said, and the description of each reveal the page reported. The poster is a
lossless PNG of the film's opening slide with every one of its reveals fired, drawn by the page and
never taken from the mp4.

The final file is published atomically: everything is written under a work name and renamed once, so
a viewer never opens a half-written film.
"""

from __future__ import annotations

import logging
import shutil
import time
from collections.abc import Mapping
from pathlib import Path

from ...artifacts import Cut, Cuts, Takes, Word, read_words
from ...captions import (
    CaptionCue,
    Chapter,
    Said,
    TranscriptSection,
    caption_cues,
    display_words,
    write_srt,
    write_vtt,
)
from ...captions import write_chapters as write_chapter_file
from ...errors import ToolError
from ...media import ffmpeg
from ...media.browser import chromium, open_page, screenshot
from ...media.encode import iso_639_2
from ...media.origin import page_url
from ...model import ClipSection, PageSection, Project
from ...pipeline import SectionKind
from ...verdicts import Finding, Verdict
from .cut import RenderedSection, rendered_starts

log = logging.getLogger(__name__)

SOUND_CAPTION_SECONDS = 1.0  # A sound's caption is on screen for at least this long, as SC 1.2.2 expects.
POSTER_SETTLE_MS = 400  # Time the poster page gets to draw itself before the frame is taken.


# ---- captions ---------------------------------------------------------------------------------


def build_captions(
    project: Project, takes: Takes, t0: float | Mapping[str, float], texts: dict[str, str] | None = None
) -> list[CaptionCue]:
    """Cues for every spoken section, shifted to where its narration sits in the final file.

    `t0` is where narration t=0 sits in the final file. It may instead map each section key to its
    own offset, as `narration_offsets` gives when a clip between page sections pauses the narration.
    `texts` maps a section key to its spoken script text, which lends the captions their punctuation
    and case.
    """
    cues: list[CaptionCue] = []
    for key in takes.keys:
        shift = (t0.get(key, 0.0) if isinstance(t0, Mapping) else t0) + (takes.start(key) or 0.0)
        words = project.narration_words(key, takes.sections[key].words_file, at=shift)
        if texts and key in texts:
            words = display_words(words, texts[key])
        cues += caption_cues(words)
    return cues


def clip_captions(project: Project, rows: list[RenderedSection]) -> list[CaptionCue]:
    """Cues for the speech inside each clip section that names a words file.

    The words count from the clip's start and carry their own punctuation. A word that starts after
    the clip's picture ends is dropped. A slate, or a clip with no audio, gets no captions.
    """
    starts = rendered_starts(rows)
    cues: list[CaptionCue] = []
    for r in rows:
        sec = r.section
        if not isinstance(sec, ClipSection) or not sec.words or r.audio is None:
            continue
        path = project.path(sec.words)
        if not path.exists():
            log.warning(
                "section %s: the words file %s is missing, so the clip plays without captions", sec.key, sec.words
            )
            continue
        shift = starts[sec.key]
        words = [
            Word(w.word, round(shift + w.start, 3), round(shift + min(w.end, r.duration), 3))
            for w in read_words(path)
            if w.start < r.duration
        ]
        cues += caption_cues(words)
    return cues


def uncaptioned_sounds(project: Project) -> list[Finding]:
    """One row per cued sound that names no caption, which a viewer reading captions never learns about.

    The captions carry the whole soundtrack and not the dialogue alone, so a sound with no caption
    line is a hole in what a deaf viewer receives. It is uncertain, because a sound may be there to
    be felt rather than noticed, and `--strict` is how a project says every sound must be written down.
    """
    return [
        Finding(
            verdict=Verdict.NO_CAPTION,
            section=sfx.section,
            cue=sfx.cue,
            where=sfx.file,
            detail=(
                f"the sound {sfx.file} plays at cue {sfx.cue!r} and names no caption, "
                "so the captions never say it plays."
            ),
        )
        for sfx in project.mix.sfx
        if not sfx.caption
    ]


def sound_captions(project: Project, starts: dict[str, float]) -> list[CaptionCue]:
    """One cue per cued sound that names a caption, such as `[ball bounces]`.

    Captions carry the whole soundtrack and not only the dialogue, so a sound a viewer is meant to
    notice is written down where it plays. A sound whose cue is unresolved has nowhere to sit and is
    left out, as it is left out of the mix.
    """
    cue_times = project.cue_times()
    cues: list[CaptionCue] = []
    for sfx in project.mix.sfx:
        if not sfx.caption:
            continue
        key = f"{sfx.section:02d}"
        at = cue_times.get(key, sfx.cue)
        if key not in starts or at is None:
            continue
        start = round(starts[key] + at + sfx.offset, 3)
        text = sfx.caption if sfx.caption.startswith("[") else f"[{sfx.caption}]"
        cues.append(CaptionCue(start=start, end=round(start + SOUND_CAPTION_SECONDS, 3), lines=(text,)))
    return cues


def with_sound_captions(speech: list[CaptionCue], sounds: list[CaptionCue]) -> list[CaptionCue]:
    """Every caption a viewer reads, with each sound inside the cue it shares its moment with.

    Effects are cued to spoken words, so a sound landing under speech is the ordinary case. Two cues
    that overlap are drawn twice or dropped by most players, so a sound joins the cue it falls in as
    a line of its own. A sound that lands in the gap before a cue joins that cue too, because the gap
    is shorter than a caption may be read in, and only a sound with `SOUND_CAPTION_SECONDS` of room
    to itself keeps its own cue.
    """
    cues = sorted(speech, key=lambda c: c.start)
    for sound in sorted(sounds, key=lambda c: c.start):
        host = next((i for i, cue in enumerate(cues) if cue.start <= sound.start < cue.end), None)
        if host is not None:
            cue = cues[host]
            cues[host] = CaptionCue(start=cue.start, end=max(cue.end, sound.end), lines=(*cue.lines, *sound.lines))
            continue
        after = next((i for i, cue in enumerate(cues) if cue.start >= sound.start), len(cues))
        if after < len(cues) and cues[after].start - sound.start < SOUND_CAPTION_SECONDS:
            cue = cues[after]
            cues[after] = CaptionCue(start=cue.start, end=cue.end, lines=(*sound.lines, *cue.lines))
            continue
        cues.insert(after, sound)
    return one_at_a_time(cues)


def one_at_a_time(cues: list[CaptionCue]) -> list[CaptionCue]:
    """The same cues, with none running into the one after it and none left with no length at all.

    Two cues on screen at once are drawn twice or dropped, and a cue whose end is its start is
    dropped by every player, so a cue with no room left to it is not written rather than written as
    a line no one can read.
    """
    out: list[CaptionCue] = []
    for i, cue in enumerate(cues):
        limit = cues[i + 1].start if i + 1 < len(cues) else cue.end
        end = round(max(cue.start, min(cue.end, limit)), 3)
        if end <= cue.start:
            continue
        out.append(cue if end == cue.end else CaptionCue(start=cue.start, end=end, lines=cue.lines))
    return out


def caption_texts(project: Project, takes: Takes) -> dict[str, str]:
    """The spoken text per section key, which lends the captions their punctuation and case.

    The take index records the text each section was narrated from, so the captions match the audio
    even when the script has been edited since.
    """
    texts = {k: row.spoken for k, row in takes.sections.items() if row.spoken}
    if any(key not in texts for key in takes.keys):
        for seg in project.script_sections()[0]:
            texts.setdefault(seg.key, seg.spoken)
    return texts


def build_chapters(rows: list[RenderedSection], titles: dict[int, str]) -> list[Chapter]:
    """One chapter per section, where consecutive sections with the same chapter share one marker."""
    starts = rendered_starts(rows)
    chapters: list[Chapter] = []
    for r in rows:
        start = starts[r.section.key]
        title = titles[r.section.number]
        if chapters and chapters[-1].title == title:
            chapters[-1] = Chapter(start=chapters[-1].start, end=start + r.duration, title=title)
        else:
            chapters.append(Chapter(start=start, end=start + r.duration, title=title))
    return chapters


def mux_chapters(src: Path, chapters: Path, dst: Path, language: str) -> None:
    """Copy both streams into dst with the chapter markers, and tag each stream with the language.

    A player names the audio from that tag, and the transcript page carries the same language, so
    `[project] language` reaches everything a viewer picks from. The container takes the three-letter
    code, which `iso_639_2` gives from the project's BCP 47 tag.
    """
    tag = f"language={iso_639_2(language)}"
    ffmpeg.run(
        "-i", str(src), "-f", "ffmetadata", "-i", str(chapters),
        "-map", "0:v", "-map", "0:a", "-map_metadata", "1", "-map_chapters", "1",
        "-metadata:s:v:0", tag, "-metadata:s:a:0", tag,
        "-c", "copy", "-movflags", "+faststart", str(dst),
    )  # fmt: skip


# ---- the transcript ---------------------------------------------------------------------------


def described_cues(project: Project, key: str, at: float) -> tuple[tuple[float, str], ...]:
    """Each reveal of one section that the page described, as (second in the film, the description).

    The page records its own description beside each cue it ran, so a reveal a viewer cannot see is
    written down in the words its author chose rather than in a cue id.
    """
    recording_log = project.recording_log_of(key)
    if recording_log is None:
        return ()
    rows: list[tuple[float, str]] = []
    for entry in recording_log.cue_log:
        text = str(entry.get("describe") or "").strip()
        # The page writes `ran` as null for a cue it never ran, and the reveal is described where it was due.
        ran = entry.get("ran") if entry.get("ran") is not None else entry.get("due")
        if text and isinstance(ran, int | float):
            rows.append((round(at + float(ran), 3), text))
    return tuple(sorted(rows))


def clip_speech(project: Project, key: str) -> str:
    """What is spoken inside a clip section that names a words file, as one paragraph.

    The captions carry a clip's dialogue, so the page that calls itself the media alternative carries
    it too rather than leaving a viewer who reads the transcript with a silent gap.
    """
    section = next((s for s in project.clip_sections if s.key == key and s.words), None)
    path = project.path(section.words) if section and section.words else None
    if path is None or not path.exists():
        return ""
    return " ".join(w.word for w in read_words(path))


def cut_note(cut: Cut) -> str:
    """What plays in a section that spoke nothing, in one sentence, or nothing when it spoke."""
    if cut.substitute is not None:
        return f"A placeholder {cut.substitute.value} frame plays here."
    return f"A clip plays here: {cut.source}." if cut.kind is SectionKind.CLIP else ""


def transcript_sections(project: Project, cuts: Cuts, texts: dict[str, str]) -> list[TranscriptSection]:
    """One transcript entry per chapter, in the order they play, as the chapter markers group them.

    Consecutive sections that share a chapter share one heading, which is how the film's own markers
    group them, and each section's speech stays its own paragraph inside it.
    """
    out: list[TranscriptSection] = []
    for cut in cuts.sections:
        spoken = texts.get(cut.key, "") or clip_speech(project, cut.key)
        note = cut_note(cut)
        # One entry per section, in the order it plays, so a reader knows which gap a clip fills.
        said = (Said(text=spoken, note=False),) if spoken else ()
        said += (Said(text=note, note=True),) if note else ()
        described = described_cues(project, cut.key, cut.start)
        if out and out[-1].chapter == cut.chapter:
            last = out[-1]
            out[-1] = TranscriptSection(
                chapter=last.chapter,
                start=last.start,
                end=cut.end,
                said=last.said + said,
                describes=last.describes + described,
            )
            continue
        out.append(TranscriptSection(chapter=cut.chapter, start=cut.start, end=cut.end, said=said, describes=described))
    return out


# ---- the poster -------------------------------------------------------------------------------


def poster_query(catalog: list[dict] | None, section: PageSection) -> dict[str, str] | None:
    """The freeze query for a section's opening slide with every one of its reveals fired.

    A poster is the one picture that has to stand for the film, and a cue-driven slide before its
    first cue is an empty stage, so the slide is frozen in the state it ends in. The section's own
    params go with it, as they do on every other frozen frame, so a page whose look depends on one
    draws the picture the film opens on.
    """
    entry = next((c for c in (catalog or []) if str(c.get("scene")) == section.scene), None)
    slides = entry.get("slides") if isinstance(entry, dict) else None
    if not slides:
        return None
    return {**section.freeze_params, "slide": str(slides[0])}


def render_poster(project: Project, out: Path) -> Path | None:
    """The film's opening slide as a lossless PNG, drawn by the page rather than taken from the mp4.

    Nothing here may cost a film that is already written, so every failure is a warning and no poster.
    """
    section = next((s for s in project.sections if isinstance(s, PageSection)), None)
    if section is None:
        return None
    video = project.settings.video
    try:
        with chromium(project.settings.record.browser_path) as browser:
            page, _assets = open_page(browser, project.root, width=video.width, height=video.height)
            page.goto(page_url(section.page))
            catalog = page.evaluate("() => (window.__decktalk && window.__decktalk.catalog) || null")
            query = poster_query(catalog, section)
            if query is None:
                log.warning("no slide to draw the poster from on %s, so no poster is written", section.page)
                return None
            screenshot(page, page_url(section.page, query), out, settle_ms=POSTER_SETTLE_MS)
    except Exception as exc:
        log.warning("could not draw the poster (%s)", exc)
        return None
    return out


# ---- publishing -------------------------------------------------------------------------------


def publish(project: Project, work: Path, chapters: list[Chapter], paths: dict[str, Path]) -> Path | None:
    """Mux the chapters, move the work file into place atomically, and copy it when asked to.

    Nothing a viewer can open is written until the film is whole, because a rename is the only step
    another process can observe.
    """
    chaptered = project.out_dir / f".{project.name}.chapters.mp4"
    mux_chapters(work, paths["chapters"], chaptered, project.document.language)
    chaptered.replace(work)
    log.info("[chap] %d chapter(s) -> %s", len(chapters), paths["chapters"].name)
    if not work.exists() or work.stat().st_size == 0:
        raise ToolError("render produced no output")
    work.replace(project.final)  # atomic: a viewer never opens a half-written file
    if not project.settings.output.timestamped_copy:
        return None
    stamped = project.out_dir / f"{project.name}-{time.strftime('%Y%m%d-%H%M')}.mp4"
    shutil.copyfile(project.final, stamped)
    return stamped


def write_caption_files(paths: dict[str, Path], cues: list[CaptionCue], chapters: list[Chapter]) -> None:
    """The srt, the vtt and the ffmetadata chapters, in one place so nothing writes half of them."""
    write_srt(paths["srt"], cues)
    write_vtt(paths["vtt"], cues)
    write_chapter_file(paths["chapters"], chapters)
    log.info("[caps] %d cue(s) -> %s, %s", len(cues), paths["srt"].name, paths["vtt"].name)
