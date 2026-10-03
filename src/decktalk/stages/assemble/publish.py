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

import itertools
import shutil
import time
from collections.abc import Mapping
from pathlib import Path

from decktalk.artifacts import Cut, Cuts, Takes, Words
from decktalk.captions import (
    CaptionCue,
    Chapter,
    Said,
    TranscriptSection,
    caption_cues,
    chapters_text,
    display_words,
    srt_text,
    transcript_html,
    vtt_text,
)
from decktalk.errors import DeckTalkError, ToolError
from decktalk.events import Level
from decktalk.files import replace_all
from decktalk.inputs import ClipSection, Inputs, PageSection
from decktalk.machine import Run
from decktalk.media import browser, ffmpeg
from decktalk.media.encode import iso_639_2
from decktalk.media.origin import page_url
from decktalk.media.pagereport import MeasuredScene
from decktalk.page import SECOND_DIGITS, Q
from decktalk.pagescan import scene_entry, slide_cues
from decktalk.results import SectionKind, Word, section_key
from decktalk.stages.assemble.cut import Rendered, rendered_starts
from decktalk.stages.assemble.mix import effect_second
from decktalk.stages.storyboard import open_project_page

SOUND_CAPTION_SECONDS = 1.0
"""Calibration: how long a sound's caption stays on screen, which is what SC 1.2.2 expects of one."""


WORK_MARK = "."
"""What a work file's name opens with, so nothing a viewer can open is written until the film is whole."""

POSTER_MARK = "poster"
"""What a poster's still is keyed under before its scene, so it never shares a key with a frozen state."""

STAMP_FORMAT = "%Y%m%d-%H%M"
"""How a timestamped copy is named, which is the date and the minute the copy was taken."""


# ---- captions ---------------------------------------------------------------------------------


def build_captions(inputs: Inputs, takes: Takes, offsets: Mapping[int, float], texts: Mapping[int, str]) -> list[
    CaptionCue
]:  # fmt: skip
    """Cues for every spoken section, shifted to where its narration sits in the finished film.

    `offsets` is what `inputs.timeline.narration_offsets` gives, which is what to add to a time in
    the joined narration to place it in the film. `texts` lends each section's captions the
    punctuation and the case the script wrote.
    """
    cues: list[CaptionCue] = []
    for take in takes.sections:
        shift = offsets.get(take.section, 0.0) + (takes.start(take.section) or 0.0)
        words = list(Words(words=inputs.words(take.section, take.hash)).shifted(shift))
        text = texts.get(take.section)
        cues += caption_cues(display_words(words, text) if text else words)
    return cues


def clip_captions(inputs: Inputs, run: Run, rows: list[Rendered]) -> list[CaptionCue]:
    """Cues for the speech inside each clip section that names a words file.

    The words count from the clip's own start and carry their own punctuation. A word that starts
    after the clip's picture ends is dropped. A slate, and a clip with no audio, get no captions.
    """
    starts = rendered_starts(rows)
    cues: list[CaptionCue] = []
    for row in rows:
        section = row.section
        if not isinstance(section, ClipSection) or not section.words or row.audio is None:
            continue
        found = inputs.clip_words(section)
        if found is None:
            run.note(
                f"{section.words} is not there, so section {section.number} plays with no captions.",
                level=Level.WARNING,
            )
            continue
        shift = starts[row.number]
        inside = [
            Word(
                word=word.word,
                start=round(shift + word.start, SECOND_DIGITS),
                end=round(shift + min(word.end, row.seconds), SECOND_DIGITS),
            )
            for word in found.words
            if word.start < row.seconds
        ]
        cues += caption_cues(inside)
    return cues


def uncaptioned_sounds(inputs: Inputs, run: Run) -> None:
    """Say which cued sound names no caption, which a viewer reading captions never learns about.

    The captions carry the whole soundtrack and not the dialogue alone, so a sound with no caption
    line is a hole in what a deaf viewer receives. The frozen code list holds no code for it, so it
    is one sentence on the stream rather than a judgement wearing a code that means something else.
    """
    for effect in inputs.document.mix.effects:
        if effect.caption:
            continue
        run.note(
            f"The sound {effect.file} plays at cue {effect.cue!r} in section {effect.section} and names no "
            "caption, so the captions never say it plays.",
            level=Level.WARNING,
        )


def sound_captions(inputs: Inputs, starts: Mapping[int, float]) -> list[CaptionCue]:
    """One cue per cued sound that names a caption, such as `[ball bounces]`.

    A sound whose cue is unresolved has nowhere to sit and is left out, as it is left out of the mix.
    """
    cue_times = inputs.cue_times()
    cues: list[CaptionCue] = []
    for effect in inputs.document.mix.effects:
        if not effect.caption:
            continue
        at = effect_second(effect, cue_times, starts)
        if at is None:
            continue
        start = round(at, SECOND_DIGITS)
        text = effect.caption if effect.caption.startswith("[") else f"[{effect.caption}]"
        cues.append(CaptionCue(start=start, end=round(start + SOUND_CAPTION_SECONDS, SECOND_DIGITS), lines=(text,)))
    return cues


def with_sound_captions(speech: list[CaptionCue], sounds: list[CaptionCue]) -> list[CaptionCue]:
    """Every caption a viewer reads, with each sound inside the cue it shares its moment with.

    Effects are cued to spoken words, so a sound landing under speech is the ordinary case. Two cues
    that overlap are drawn twice or dropped by most players, so a sound joins the cue it falls in as
    a line of its own. A sound that lands in the gap before a cue joins that cue too, because the
    gap is shorter than a caption may be read in, and only a sound with `SOUND_CAPTION_SECONDS` of
    room to itself keeps a cue of its own.
    """
    cues = sorted(speech, key=lambda cue: cue.start)
    for sound in sorted(sounds, key=lambda cue: cue.start):
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
    a line nobody can read.
    """
    out: list[CaptionCue] = []
    for index, cue in enumerate(cues):
        limit = cues[index + 1].start if index + 1 < len(cues) else cue.end
        end = round(max(cue.start, min(cue.end, limit)), SECOND_DIGITS)
        if end <= cue.start:
            continue
        out.append(cue if end == cue.end else CaptionCue(start=cue.start, end=end, lines=cue.lines))
    return out


def caption_texts(inputs: Inputs, takes: Takes) -> dict[int, str]:
    """The spoken text per section, which lends the captions their punctuation and their case.

    The take index records the text each section was narrated from, so the captions match the audio
    even when the script has been edited since it was voiced.
    """
    texts = {take.section: take.spoken for take in takes.sections if take.spoken}
    if any(take.section not in texts for take in takes.sections):
        for segment in inputs.script():
            texts.setdefault(segment.index, segment.spoken)
    return texts


def build_chapters(rows: list[Rendered], titles: Mapping[int, str]) -> list[Chapter]:
    """One chapter per section, where consecutive sections with the same title share one marker."""
    starts = rendered_starts(rows)
    grouped = (list(run) for _title, run in itertools.groupby(rows, key=lambda row: titles[row.number]))
    return [
        Chapter(start=starts[run[0].number], end=starts[run[-1].number] + run[-1].seconds, title=titles[run[0].number])
        for run in grouped
    ]


def write_caption_files(paths: Mapping[str, Path], cues: list[CaptionCue], chapters: list[Chapter]) -> None:
    """The SubRip, the WebVTT and the ffmetadata chapters, so nothing ever writes half of them."""
    replace_all(
        {paths["srt"]: srt_text(cues), paths["vtt"]: vtt_text(cues), paths["chapters"]: chapters_text(chapters)}
    )


def mux_chapters(src: Path, chapters: Path, dst: Path, language: str) -> None:
    """Copy both streams into `dst` with the chapter markers, and tag each stream with the language.

    A player names the audio from that tag and the transcript page carries the same language, so
    `[project] language` reaches everything a viewer picks from. The container takes the three-letter
    code, which `iso_639_2` gives from the project's own BCP 47 tag.
    """
    tag = f"language={iso_639_2(language)}"
    ffmpeg.run(
        "-i", str(src), "-f", "ffmetadata", "-i", str(chapters),
        "-map", "0:v", "-map", "0:a", "-map_metadata", "1", "-map_chapters", "1",
        "-metadata:s:v:0", tag, "-metadata:s:a:0", tag,
        "-c", "copy", "-movflags", "+faststart", str(dst),
    )  # fmt: skip


# ---- the transcript ---------------------------------------------------------------------------


def described_cues(inputs: Inputs, section: int, at: float) -> tuple[tuple[float, str], ...]:
    """Each reveal of one section that the page described, as (second in the film, the description).

    The page records its own description beside each cue it ran, so a reveal a viewer cannot see is
    written down in the words its author chose rather than as a cue id.

    The rows sort on the second alone. Sorting on the sentence as well broke a tie on its first
    letter, which printed a step back before the arrival it belongs to, and the runtime now composes
    one sentence per cue in document order, so the order the page gave them in is already right.
    """
    log = inputs.recording_log(section_key(section))
    if log is None:
        return ()
    rows = [
        (round(at + cue.ran, SECOND_DIGITS), cue.describe.strip())
        for cue in log.recording.report.cues
        if cue.describe and cue.describe.strip()
    ]
    return tuple(sorted(rows, key=lambda row: row[0]))


def clip_speech(inputs: Inputs, section: int) -> str:
    """What is spoken inside a clip section that names a words file, as one paragraph.

    The captions carry a clip's dialogue, so the page that calls itself the media alternative carries
    it too rather than leaving a viewer who reads the transcript with a silent gap.
    """
    found = next(
        (s for s in inputs.document.clip_sections if s.number == section and s.words),
        None,
    )
    if found is None or not found.words:
        return ""
    words = inputs.clip_words(found)
    return "" if words is None else " ".join(word.word for word in words.words)


def cut_note(cut: Cut) -> str:
    """What plays in a section that spoke nothing, in one sentence, or nothing when it spoke."""
    if cut.substitute is not None:
        return f"A placeholder {cut.substitute.value} frame plays here."
    return f"A clip plays here: {cut.source.as_posix()}." if cut.kind is SectionKind.CLIP else ""


def transcript_sections(inputs: Inputs, cuts: Cuts, texts: Mapping[int, str]) -> list[TranscriptSection]:
    """One transcript entry per chapter, in the order they play, as the chapter markers group them.

    Consecutive sections that share a chapter share one heading, which is how the film's own markers
    group them, and each section's speech stays its own paragraph inside it.
    """
    out: list[TranscriptSection] = []
    for chapter, grouped in itertools.groupby(cuts.sections, key=lambda cut: cut.chapter):
        run = list(grouped)
        said = tuple(one for cut in run for one in _said(inputs, cut, texts))
        shown = tuple(one for cut in run for one in described_cues(inputs, cut.section, cut.start))
        out.append(TranscriptSection(chapter=chapter, start=run[0].start, end=run[-1].end, said=said, describes=shown))
    return out


def _said(inputs: Inputs, cut: Cut, texts: Mapping[int, str]) -> tuple[Said, ...]:
    """What one section contributes to its chapter's text: its speech, then the note on what plays."""
    spoken = texts.get(cut.section, "") or clip_speech(inputs, cut.section)
    note = cut_note(cut)
    return ((Said(text=spoken, note=False),) if spoken else ()) + ((Said(text=note, note=True),) if note else ())


# ---- the poster -------------------------------------------------------------------------------


def poster_query(catalog: tuple[MeasuredScene, ...], section: PageSection) -> dict[Q, str] | None:
    """The freeze query for a section's opening slide with every one of its reveals already fired.

    A poster is the one picture that has to stand for the film, and a cue-driven slide before its
    first cue is an empty stage, so the slide is frozen in the state it ends in.
    """
    slides = slide_cues(scene_entry(catalog, section.scene))
    return None if not slides else {Q.SLIDE: next(iter(slides))}


def render_poster(inputs: Inputs, run: Run, out: Path) -> Path | None:
    """The film's opening slide as a lossless PNG, drawn by the page rather than taken from the mp4.

    A poster drawn before from the same page, the same settings and the same loaded files is read
    back from the frames the project keeps, so an unchanged build opens no browser for it. Nothing
    here may cost a film that is already written, so every refusal the media layer raises is one
    sentence on the stream and no poster.
    """
    section = next((s for s in inputs.document.sections if isinstance(s, PageSection)), None)
    if section is None:
        return None
    key = inputs.still_key(section.page, POSTER_MARK, section.scene, documents=inputs.documents())
    kept = inputs.stills.find(key)
    if kept is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(kept, out)
        return out
    try:
        with browser.chromium(inputs.settings.record.browser_path, policy=inputs.settings.record.page_policy) as chrome:
            page, assets = open_project_page(chrome, inputs)
            page.goto(page_url(section.page))
            browser.await_ready(page)
            query = poster_query(browser.read_report(page, out.stem).catalog, section)
            if query is None:
                run.note(f"{section.page} declares no slide for scene {section.scene}, so no poster is written.",
                         level=Level.WARNING)  # fmt: skip
                return None
            browser.screenshot(page, page_url(section.page, query), out)
            inputs.stills.keep(key, out, assets.paths)
    except DeckTalkError as refused:
        run.note(f"The poster could not be drawn ({refused}), so the film is published without one.",
                 level=Level.WARNING)  # fmt: skip
        return None
    return out


# ---- publishing -------------------------------------------------------------------------------


def publish(inputs: Inputs, work: Path, paths: Mapping[str, Path]) -> Path | None:
    """Mux the chapters, move the work file into place in one step, and copy it when asked to.

    Nothing a viewer can open is written until the film is whole, because a rename is the only step
    another process can observe.
    """
    final_dir = inputs.workspace.final_dir
    name = inputs.workspace.name
    chaptered = final_dir / f"{WORK_MARK}{name}.chapters.mp4"
    mux_chapters(work, paths["chapters"], chaptered, inputs.document.language)
    chaptered.replace(work)
    if not work.exists() or work.stat().st_size == 0:
        raise ToolError(
            "the render produced no output, so there is no film to publish.",
            hint="Run the build again with --events to see which pass failed.",
        )
    work.replace(inputs.workspace.film)
    if not inputs.settings.output.timestamped_copy:
        return None
    stamped = final_dir / f"{name}-{time.strftime(STAMP_FORMAT)}.mp4"
    shutil.copyfile(inputs.workspace.film, stamped)
    return stamped


def write_transcript_page(inputs: Inputs, path: Path, cuts: Cuts, texts: Mapping[int, str]) -> None:
    """The media alternative: one page with a heading per chapter, the speech and every reveal."""
    sections = transcript_sections(inputs, cuts, texts)
    replace_all({path: transcript_html(inputs.workspace.name, sections, language=inputs.document.language)})


__all__ = [
    "SOUND_CAPTION_SECONDS",
    "WORK_MARK",
    "build_captions",
    "build_chapters",
    "caption_texts",
    "clip_captions",
    "clip_speech",
    "cut_note",
    "described_cues",
    "mux_chapters",
    "one_at_a_time",
    "poster_query",
    "publish",
    "render_poster",
    "sound_captions",
    "transcript_sections",
    "uncaptioned_sounds",
    "with_sound_captions",
    "write_caption_files",
    "write_transcript_page",
]
