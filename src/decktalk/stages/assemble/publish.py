"""Everything a viewer receives beside the picture: captions, chapters, the transcript and the poster.

Captions carry the speech of every page section, the speech inside a clip with a words file beside it,
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
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path

from decktalk.artifacts import ClipWords, Placement, Placements, SpokenWord, Takes, Unreadable, words_beside
from decktalk.captions import (
    CAPTION_MAX_CHARS,
    CaptionCue,
    Chapter,
    Said,
    TranscriptSection,
    caption_cues,
    chapters_text,
    display_words,
    srt_text,
    transcript_html,
    voice_label,
    vtt_text,
)
from decktalk.errors import DeckTalkError, ToolError
from decktalk.events import Level
from decktalk.files import replace_all
from decktalk.inputs import ClipSection, Inputs, PageSection
from decktalk.machine.run import Run
from decktalk.media import browser, ffmpeg, pages
from decktalk.media.encode import iso_639_2
from decktalk.media.origin import page_url
from decktalk.media.pagereport import MeasuredScene
from decktalk.page import SECOND_DIGITS, Q
from decktalk.pagescan import scene_entry, slide_cues
from decktalk.results import SectionKind, Substitute, section_key
from decktalk.stages.assemble.cut import Rendered, rendered_starts
from decktalk.stages.assemble.mix import effect_second
from decktalk.stages.storyboard import open_project_page

SOUND_CAPTION_SECONDS = 1.0
"""Calibration: how long a sound's caption stays on screen, which is what SC 1.2.2 expects of one."""


POSTER_MARK = "poster"
"""What a poster's still is keyed under before its scene, so it never shares a key with a frozen state."""

STAMP_FORMAT = "%Y%m%d-%H%M"
"""How a timestamped copy is named, which is the date and the minute the copy was taken."""


# ---- captions ---------------------------------------------------------------------------------


def build_captions(inputs: Inputs, narrated: Mapping[int, Sequence[SpokenTurn]]) -> list[CaptionCue]:
    """Cues for every turn of every spoken section, `narrated_turns` placing each where the finished film says it.

    Each turn is captioned on its own, so a caption never spans two turns, the way it never spans two
    sections. Every caption knows the voice and the turn that speak it, which a page drawing the
    captioned copy places it by. The files carry the words alone unless `[output] caption_voices` is
    on, and then every caption of a section spoken in more than one voice is named. The .srt writes
    that name before the line, so those turns are wrapped short enough that the name and the line still
    fit in `CAPTION_MAX_CHARS`.
    """
    named = inputs.settings.output.caption_voices
    cues: list[CaptionCue] = []
    for turns in narrated.values():
        dialogue = named and in_dialogue(turn.voice for turn in turns)
        for turn in turns:
            room = CAPTION_MAX_CHARS - len(voice_label(turn.voice)) if dialogue else CAPTION_MAX_CHARS
            cues += [
                replace(cue, voice=turn.voice, turn=turn.turn, named=dialogue)
                for cue in caption_cues(list(turn.words), max_chars=room)
            ]
    return cues


def narrated_turns(inputs: Inputs, takes: Takes, offsets: Mapping[int, float]) -> dict[int, list[SpokenTurn]]:
    """Every turn of every spoken section, keyed by its section, its words where the finished film says them.

    `offsets` is what `inputs.timeline.narration_offsets` gives, which is what to add to a time in
    the joined narration to place it in the film. A turn's words take the punctuation and the case of
    the text its take index row says it was narrated from, so they match the audio even when the
    script has been edited since it was voiced.
    """
    out: dict[int, list[SpokenTurn]] = {}
    placed = takes.placed
    for number in takes.numbers:
        shift = offsets.get(number, 0.0) + placed[number].start
        turns = inputs.turn_words(number)
        out[number] = []
        for turn in turns:
            words = list(turn.words.shifted(shift))
            shown = display_words(words, turn.spoken) if turn.spoken else words
            said = tuple(
                SpokenWord(word=w.word, start=w.start, end=w.end, voice=turn.voice, turn=turn.turn) for w in shown
            )
            out[number].append(SpokenTurn(voice=turn.voice, turn=turn.turn, words=said))
    return out


@dataclass(frozen=True)
class SpokenTurn:
    """One turn of the narration as the film shows it: who says it, and its words on the film's clock."""

    voice: str
    turn: int
    words: tuple[SpokenWord, ...]


def inner_words(found: ClipWords, shift: float, until: float) -> tuple[SpokenWord, ...]:
    """The words of a clip played from `shift` on the film's clock, as far as `until` seconds of it play.

    A word that starts once the clip has stopped playing is not heard, and one that runs past it ends there.
    """
    return tuple(
        word.model_copy(
            update={
                "start": round(shift + word.start, SECOND_DIGITS),
                "end": round(shift + min(word.end, until), SECOND_DIGITS),
            }
        )
        for word in found.words
        if word.start < until
    )


def spoken_cues(words: Sequence[SpokenWord]) -> list[CaptionCue]:
    """Captions for the words inside one clip, one run per voice and turn, each knowing who says it.

    A clip's captions never name a voice, so a page drawing the captioned copy places them by voice
    and turn and nothing it draws names anyone.
    """
    cues: list[CaptionCue] = []
    for (voice, turn), run in itertools.groupby(words, key=lambda word: (word.voice, word.turn)):
        cues += [replace(cue, voice=voice, turn=turn) for cue in caption_cues(list(run))]
    return cues


def clip_words(inputs: Inputs, run: Run, rows: list[Rendered]) -> list[tuple[SpokenWord, ...]]:
    """The words inside every clip the film plays with sound, one list per clip, on the film's clock.

    Every clip's words are the file beside it, `<clip>.words.json`, which `decktalk clip` writes
    beside every clip it cuts and every build writes beside its film: a clip section's from its
    start, and a pane clip's from the pane's cue for as long as the clip plays there. A slate, a clip
    with no audio and a clip with no words file beside it add nothing.
    """
    starts = rendered_starts(rows)
    found: list[tuple[SpokenWord, ...]] = []
    for row in rows:
        section = row.section
        if isinstance(section, ClipSection):
            words = inputs.clip_words(section) if row.audio is not None else None
            if words is not None:
                found.append(inner_words(words, starts[row.number], row.seconds))
        elif isinstance(section, PageSection):
            found += _pane_words(run, row, starts[row.number])
    return [words for words in found if words]


def _pane_words(run: Run, row: Rendered, start: float) -> list[tuple[SpokenWord, ...]]:
    """The words of every pane clip one page section played with sound, over the span the mix plays its sound."""
    found: list[tuple[SpokenWord, ...]] = []
    for play in row.panes:
        if not play.has_audio:
            continue
        try:
            words = ClipWords.parse(words_beside(play.path))
        except Unreadable as unread:
            run.note(
                f"Section {row.number} plays the pane clip {play.clip}, and {unread}, so its speech has no captions.",
                level=Level.WARNING,
            )
            continue
        if words is not None:
            found.append(inner_words(words, start + play.start, play.seconds))
    return found


def clip_captions(inputs: Inputs, run: Run, rows: list[Rendered]) -> list[CaptionCue]:
    """Cues for the speech inside every clip the film plays, a clip section's and a pane clip's alike."""
    return [cue for words in clip_words(inputs, run, rows) for cue in spoken_cues(words)]


def film_words(narrated: Mapping[int, Sequence[SpokenTurn]], inside: Sequence[Sequence[SpokenWord]]) -> ClipWords:
    """Every word the film says, the narration's and every clip's, in the order they start, on the film's clock.

    This is what the film carries beside itself as `<name>.words.json`, so a pane that plays it in
    another film captions its speech there as this film captions it.
    """
    words = [word for turns in narrated.values() for turn in turns for word in turn.words]
    words += [word for clip in inside for word in clip]
    return ClipWords(words=tuple(sorted(words, key=lambda word: word.start)))


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
            cues[host] = replace(_joined(cue, after=sound.lines), end=max(cue.end, sound.end))
            continue
        after = next((i for i, cue in enumerate(cues) if cue.start >= sound.start), len(cues))
        if after < len(cues) and cues[after].start - sound.start < SOUND_CAPTION_SECONDS:
            cues[after] = _joined(cues[after], before=sound.lines)
            continue
        cues.insert(after, sound)
    return one_at_a_time(cues)


def _joined(cue: CaptionCue, *, before: tuple[str, ...] = (), after: tuple[str, ...] = ()) -> CaptionCue:
    """The cue with a sound's lines before or after its own, still knowing which lines its voice speaks."""
    first, end = cue.speech
    spoken = (first + len(before), end + len(before)) if cue.voice is not None else None
    return replace(cue, lines=(*before, *cue.lines, *after), spoken=spoken)


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
        out.append(cue if end == cue.end else replace(cue, end=end))
    return out


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

    The rows sort on the second alone, and the sort is stable, so cues that share a second keep the
    order the runtime wrote them in, which is one sentence per cue in document order.
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


def placement_note(placement: Placement) -> str:
    """What plays in a section that spoke nothing, in one sentence, or nothing when it spoke."""
    if placement.substitute is not None:
        return "A slate plays here." if placement.substitute is Substitute.SLATE else "A black frame plays here."
    return f"A clip plays here: {placement.source.as_posix()}." if placement.kind is SectionKind.CLIP else ""


def dialogue_turns(takes: Takes) -> dict[int, dict[int, Said]]:
    """Each section spoken in more than one voice, as one paragraph per turn, by turn, that names its voice.

    A reader who cannot hear the film cannot tell two voices apart from the words alone, so a dialogue
    names who says each line. A section in one voice, however many turns it has, is read as one
    paragraph and names nobody, which is how it read before a project could have two voices.
    """
    out: dict[int, dict[int, Said]] = {}
    for number in takes.numbers:
        rows = takes.turns_of(number)
        if in_dialogue(row.voice for row in rows):
            out[number] = {row.turn: Said(text=row.spoken, voice=row.voice) for row in rows if row.spoken}
    return out


def in_dialogue(voices: Iterable[str]) -> bool:
    """Whether the turns of one section are read in more than one voice, which is when a reader is told who."""
    return len(set(voices)) > 1


def transcript_sections(
    inputs: Inputs,
    placements: Placements,
    takes: Takes,
    *,
    narrated: Mapping[int, Sequence[SpokenTurn]] | None = None,
    inside: Sequence[Sequence[SpokenWord]] = (),
) -> list[TranscriptSection]:
    """One transcript entry per chapter, in the order they play, as the chapter markers group them.

    Consecutive sections that share a chapter share one heading, which is how the film's own markers
    group them, and each section's speech, as its take index rows say it was narrated, stays its own
    paragraph inside it. A section in more than one voice, which `dialogue_turns` gives, is one
    paragraph per turn instead, each naming its voice.

    `inside` is the speech inside every clip the film plays, a clip section's or a pane clip's, on
    the film's clock as `clip_words` reads it for the captions. Each clip's is one paragraph in the
    section it starts in, naming nobody, and every paragraph of a section is in the order the film
    plays it, a turn by its first word in `narrated` and a clip by its own first word.
    """
    out: list[TranscriptSection] = []
    turns = dialogue_turns(takes)
    heard = _Heard(narrated or {}, inside)
    for chapter, grouped in itertools.groupby(placements.sections, key=lambda placement: placement.chapter):
        run = list(grouped)
        said = tuple(one for placement in run for one in _said(placement, takes, turns, heard))
        shown = tuple(one for placement in run for one in described_cues(inputs, placement.section, placement.start))
        out.append(TranscriptSection(chapter=chapter, start=run[0].start, end=run[-1].end, said=said, describes=shown))
    return out


@dataclass(frozen=True)
class _Heard:
    """When the film plays each turn of its narration, and the speech inside its clips, on the film's clock."""

    narrated: Mapping[int, Sequence[SpokenTurn]]
    inside: Sequence[Sequence[SpokenWord]]

    def turn_start(self, section: int, turn: int) -> float | None:
        """The second one turn of one section starts at, or None when it has no words on the film's clock."""
        found = next((one for one in self.narrated.get(section, ()) if one.turn == turn and one.words), None)
        return found.words[0].start if found is not None else None

    def clips_in(self, placement: Placement) -> list[tuple[float, Said]]:
        """Every clip's speech that starts inside this section, as one paragraph each that names nobody."""
        return [
            (words[0].start, Said(text=" ".join(word.word for word in words)))
            for words in self.inside
            if words and placement.start <= words[0].start < placement.end
        ]


def _said(
    placement: Placement, takes: Takes, turns: Mapping[int, Mapping[int, Said]], heard: _Heard
) -> tuple[Said, ...]:
    """What one section contributes to its chapter's text: its speech, its turns and its clips' speech in the
    order the film plays them, then the note on what plays."""
    number = placement.section

    def at(turn: int) -> float:
        start = heard.turn_start(number, turn)
        return placement.start if start is None else start

    if number in turns:
        spoken = [(at(turn), said) for turn, said in turns[number].items()]
    else:
        text = " ".join(row.spoken for row in takes.turns_of(number) if row.spoken)
        spoken = [(at(1), Said(text=text))] if text else []
    played = sorted(spoken + heard.clips_in(placement), key=lambda piece: piece[0])
    note = placement_note(placement)
    return tuple(one for _at, one in played) + ((Said(text=note, note=True),) if note else ())


# ---- the poster -------------------------------------------------------------------------------


def poster_query(catalog: tuple[MeasuredScene, ...], section: PageSection) -> dict[Q, str] | None:
    """The freeze query for a section's opening slide with every one of its reveals already fired.

    A poster is the one picture that has to stand for the film, and a cue-driven slide before its
    first cue is an empty canvas, so the slide is frozen in the state it ends in.
    """
    slides = slide_cues(scene_entry(catalog, section.scene))
    return None if not slides else {Q.FREEZE: next(iter(slides))}


def render_poster(inputs: Inputs, run: Run, out: Path) -> Path | None:
    """The film's opening slide as a lossless PNG, drawn by the page rather than taken from the mp4.

    A poster drawn before from the same page, the same settings and the same loaded files is read
    back from the frames the project keeps, so an unchanged build opens no browser for it. Nothing
    here may cost a film that is already written, so every refusal the media layer raises is one
    sentence on the stream and no poster.
    """
    section = next(iter(inputs.document.drawn_sections), None)
    if section is None:
        return None
    key = inputs.still_key(section.page, POSTER_MARK, section.scene, documents=inputs.documents())
    kept = inputs.stills.find(key)
    if kept is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(kept, out)
        return out
    try:
        record = inputs.settings.record
        with browser.chromium(inputs.settings.tools.chromium, policy=record.page_policy, spend=run.spend) as chrome:
            page, assets = open_project_page(chrome, inputs)
            pages.load(page, page_url(section.page))
            pages.await_ready(page)
            query = poster_query(pages.read_report(page, out.stem).catalog, section)
            if query is None:
                run.note(f"{section.page} declares no slide for scene {section.scene}, so no poster is written.",
                         level=Level.WARNING)  # fmt: skip
                return None
            pages.screenshot(page, page_url(section.page, query), out)
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
    chaptered = inputs.workspace.work_file("chapters.mp4")
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
    stamped = inputs.workspace.stamped_film(time.strftime(STAMP_FORMAT))
    shutil.copyfile(inputs.workspace.film, stamped)
    return stamped


def write_transcript_page(
    inputs: Inputs,
    path: Path,
    placements: Placements,
    takes: Takes,
    *,
    narrated: Mapping[int, Sequence[SpokenTurn]],
    inside: Sequence[Sequence[SpokenWord]],
) -> None:
    """The media alternative: one page with a heading per chapter, the speech, who says it, the speech inside
    every clip and every reveal."""
    sections = transcript_sections(inputs, placements, takes, narrated=narrated, inside=inside)
    replace_all({path: transcript_html(inputs.workspace.name, sections, language=inputs.document.language)})


__all__ = [
    "SOUND_CAPTION_SECONDS",
    "build_captions",
    "build_chapters",
    "SpokenTurn",
    "clip_captions",
    "clip_words",
    "film_words",
    "inner_words",
    "narrated_turns",
    "spoken_cues",
    "dialogue_turns",
    "in_dialogue",
    "placement_note",
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
