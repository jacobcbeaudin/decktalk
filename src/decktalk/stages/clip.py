"""A span of one built section, cut into its own file with its own sound and its own words.

    decktalk clip 3 --start 4 --end 9 --out media/answer.mp4

The picture is a run of whole frames of `build/sections/NN.mp4` and the sound is that section's take
over the same span, so a clip is a piece of the film rather than a second render of it. The words
file beside it lists every word wholly inside the span, in seconds after the clip starts, which is
what a `[[section]]` playing this clip needs to caption it.

The clock is the section's own: zero is where the section starts, and the silence a section's lead
puts before its first word is part of the span like anything else. It is read through `words`, so
the two commands can never disagree about where a word sits.

One noun names a short video file whoever made it, so a clip an author supplies and a clip DeckTalk
cuts are the same kind of thing under the same name.
"""

from __future__ import annotations

from pathlib import Path

from decktalk.artifacts import WORDS_SUFFIX, Take, Takes, Words
from decktalk.errors import InputError
from decktalk.events import Level
from decktalk.inputs import Inputs, PageSection
from decktalk.inputs.paths import at
from decktalk.machine import Run
from decktalk.media import ffmpeg
from decktalk.media.encode import Encoder
from decktalk.pipeline import Artifact
from decktalk.results import ClipResult, SectionWords, Word
from decktalk.stages import SECOND_DIGITS
from decktalk.stages.words import section_words

FRAME_SLACK = 0.5
"""Truth: half a frame, which is the most a span may pass the last frame by and still name it."""

EDGE_FADE_SECONDS = 0.01
"""Calibration: how long the clip's sound fades at each edge, which is short enough not to be heard as a fade.

A span cut inside a word ends on a sample that is not zero, and a cut straight to silence there is a
click. Ten milliseconds is under the shortest sound a listener can place, so the fade removes the
click without softening the word.
"""

WORD_SLACK_SECONDS = 0.001
"""Truth: how far outside the span a word may sit and still count as inside it, which is one millisecond.

A span is rounded to whole frames and a word time is rounded to milliseconds, so a word that ends
exactly where the span does can land a rounding step past it and would otherwise be dropped.
"""

LEAST_AUDIO_SECONDS = 0.001
"""Truth: the shortest stretch of a take the trim may ask for, so a span wholly inside the lead still reads.

A trim whose end is its start outputs no samples at all, and the filter chain that follows it then
has nothing to pad, so the clip would carry no sound track rather than a silent one.
"""


def clip(
    inputs: Inputs,
    run: Run,
    *,
    section: int,
    start: float,
    end: float,
    out: Path,
    gain_db: float = 0.0,
    hold_seconds: float = 0.0,
) -> ClipResult:
    """Cut the span from `start` to `end` of one built page section into `out`, with its words beside it.

    The span is rounded outward to whole frames, because a clip that began mid-frame would play its
    first frame twice. `hold_seconds` holds the last frame in silence after the span, which is how a
    clip ends on the picture it made rather than on the next thing the film did.
    """
    played = _page_section(inputs, section)
    video = _section_video(inputs, played)
    take, source = _take_of(inputs, section)
    settings = inputs.settings.video
    fps = settings.output_fps
    span = _span(inputs, video, start=start, end=end, hold_seconds=hold_seconds, fps=fps)
    film = _out_path(inputs, out, video)
    words_file = film.with_name(film.stem + WORDS_SUFFIX)

    _render(inputs, video, source, film, span=span, lead=take.lead_seconds, gain_db=gain_db, fps=fps)
    inside, cut = _clip_words(inputs, section, span.first_seconds, span.last_seconds)
    for word in cut:
        run.note(
            f"The span from {span.first_seconds:g}s to {span.last_seconds:g}s cuts the word {word!r} in two, "
            "so the words file leaves it out.",
            level=Level.WARNING,
        )
    if not take.voiced:
        run.note(
            f"Section {section} carries placeholder narration, so every word time in this clip is an estimate.",
            level=Level.WARNING,
        )
    run.wrote(film)
    run.wrote(Words(words=inside).write(words_file))
    return run.result(
        ClipResult,
        section=section,
        film=inputs.relative(film),
        words=inputs.relative(words_file),
        start=span.first_seconds,
        end=span.last_seconds,
        seconds=span.total_seconds,
        hold_seconds=span.hold_seconds,
        gain_db=gain_db,
        estimated=not take.voiced,
    )


class _Span:
    """The whole frames one clip plays, and the silence it holds after them."""

    def __init__(self, first: int, last: int, hold: int, fps: int) -> None:
        self.first = first
        self.last = last
        self.hold = hold
        self.fps = fps

    @property
    def first_seconds(self) -> float:
        """Where the clip's first frame sits in its section, in seconds."""
        return round(self.first / self.fps, SECOND_DIGITS)

    @property
    def last_seconds(self) -> float:
        """Where the clip's last frame ends in its section, which is where the next frame would start."""
        return round(self.last / self.fps, SECOND_DIGITS)

    @property
    def hold_seconds(self) -> float:
        """How long the last frame is held after the span, rounded to the frames it really holds."""
        return round(self.hold / self.fps, SECOND_DIGITS)

    @property
    def total_seconds(self) -> float:
        """How long the clip runs, which is its span and its hold together."""
        return round((self.last - self.first + self.hold) / self.fps, SECOND_DIGITS)


def _page_section(inputs: Inputs, number: int) -> PageSection:
    """The page section this clip is cut from, or a refusal naming what that section really is."""
    found = inputs.document.section(number)
    if found is None:
        raise InputError(
            f"section {number} is not in {inputs.relative(inputs.root / 'decktalk.toml')}.",
            hint=f"The sections are {sorted(section.number for section in inputs.document.sections)}.",
        )
    if not isinstance(found, PageSection):
        raise InputError(
            f"section {number} plays a clip the project already has, so there is nothing to cut out of it.",
            hint="Cut a clip from a page section, which is one DeckTalk recorded.",
        )
    return found


def _section_video(inputs: Inputs, section: PageSection) -> Path:
    """The cut of one section, or a refusal naming the command that makes it."""
    video = inputs.workspace.section_video(section.key)
    if not video.is_file():
        raise InputError(
            f"section {section.number} has no cut at {inputs.relative(video)}.",
            hint="Run `decktalk assemble` first.",
            location=at(video, inputs.root, section=section.number),
        )
    return video


def _take_of(inputs: Inputs, number: int) -> tuple[Take, Path]:
    """One section's take and the file that holds it, or a refusal naming the command that makes it."""
    takes = Takes.require(inputs.workspace.takes_path, Artifact.TAKES)
    take = takes.of(number)
    if take is None:
        raise InputError(
            f"section {number} has no take in {inputs.relative(inputs.workspace.takes_path)}.",
            hint="Run `decktalk narrate`, or `decktalk narrate --no-voice` to spend nothing.",
            location=at(inputs.workspace.takes_path, inputs.root, section=number),
        )
    source = inputs.workspace.takes_dir / take.file
    if not source.is_file():
        raise InputError(
            f"section {number} names the take {take.file}, which is not on disk.",
            hint="Run `decktalk narrate` again to write it.",
            location=at(source, inputs.root, section=number),
        )
    return take, source


def _span(inputs: Inputs, video: Path, *, start: float, end: float, hold_seconds: float, fps: int) -> _Span:
    """The whole frames the span names, refusing every span that names none."""
    if start < 0 or end <= start:
        raise InputError(
            f"the span runs from {start:g}s to {end:g}s, which starts before zero or ends before it starts.",
            hint="Write --start at 0 or later and --end after it.",
        )
    if hold_seconds < 0:
        raise InputError(
            f"--hold is {hold_seconds:g}s, which is less than no time at all.",
            hint="Write --hold 0 or more.",
        )
    length = ffmpeg.probe_duration(video)
    if end > length + FRAME_SLACK / fps:
        raise InputError(
            f"the span ends at {end:g}s and {inputs.relative(video)} runs for {length:g}s.",
            hint=f"Write --end {length:g} or earlier.",
            location=at(video, inputs.root),
        )
    first, last = round(start * fps), round(end * fps)
    if last <= first:
        raise InputError(
            f"the span from {start:g}s to {end:g}s holds no whole frame at {fps} frames per second.",
            hint=f"Make the span at least {1 / fps:.2f}s long.",
        )
    return _Span(first, last, round(hold_seconds * fps), fps)


def _out_path(inputs: Inputs, out: Path, video: Path) -> Path:
    """Where the clip is written, refusing a name that is the section cut it reads."""
    film = inputs.path(out)
    if film.resolve() == video.resolve():
        raise InputError(
            f"--out names {inputs.relative(video)}, which is the section cut this clip is read from.",
            hint="Write the clip somewhere else, such as under media/.",
            location=at(video, inputs.root),
        )
    film.parent.mkdir(parents=True, exist_ok=True)
    return film


def _render(
    inputs: Inputs, video: Path, source: Path, film: Path, *, span: _Span, lead: float, gain_db: float, fps: int
) -> None:
    """Cut the picture and the sound of one span into one file, in a single pass.

    The picture passes through the frame rate filter before the hold, because ffmpeg clones only two
    frames after a trim otherwise, and the sound is the take moved to where the span starts so that
    a span opening inside the section's lead opens on the silence the film has there.
    """
    settings = inputs.settings.video
    head = max(0.0, lead - span.first_seconds)
    begins = max(0.0, span.first_seconds - lead)
    ends = max(begins + LEAST_AUDIO_SECONDS, span.last_seconds - lead)
    played = span.last_seconds - span.first_seconds
    fade_out_at = max(played - EDGE_FADE_SECONDS, 0.0)
    total = span.total_seconds
    sound = (
        f"[1:a]aresample={settings.sample_rate},atrim=start={begins:.6f}:end={ends:.6f},asetpts=PTS-STARTPTS,"
        f"adelay=delays={round(head * settings.sample_rate)}S:all=1,volume={gain_db:g}dB,"
        f"afade=t=in:d={EDGE_FADE_SECONDS},afade=t=out:st={fade_out_at:.6f}:d={EDGE_FADE_SECONDS},"
        f"apad=whole_dur={total:.6f},atrim=duration={total:.6f}[a]"
    )
    picture = (
        f"[0:v]trim=start_frame={span.first}:end_frame={span.last},setpts=PTS-STARTPTS,fps={fps},"
        f"tpad=stop_mode=clone:stop={span.hold}[v]"
    )
    encoder = Encoder(settings)
    ffmpeg.run(
        "-i", str(video), "-i", str(source),
        "-filter_complex", f"{picture};{sound}",
        "-map", "[v]", "-map", "[a]", "-r", str(fps), *encoder.venc, *encoder.aenc,
        "-t", f"{total:.6f}", "-movflags", "+faststart", str(film),
    )  # fmt: skip


def _clip_words(inputs: Inputs, number: int, first: float, last: float) -> tuple[tuple[Word, ...], tuple[str, ...]]:
    """(the words wholly inside the span, in seconds after it starts, the words the span cuts in two)."""
    said: SectionWords | None = section_words(inputs, number)
    if said is None:
        return (), ()
    inside: list[Word] = []
    cut: list[str] = []
    for word in said.words:
        if word.start >= first - WORD_SLACK_SECONDS and word.end <= last + WORD_SLACK_SECONDS:
            inside.append(
                Word(
                    word=word.word,
                    start=round(max(0.0, word.start - first), SECOND_DIGITS),
                    end=round(min(last, word.end) - first, SECOND_DIGITS),
                )
            )
        elif word.start < last and word.end > first:
            cut.append(word.word)
    return tuple(inside), tuple(cut)


__all__ = ["EDGE_FADE_SECONDS", "clip"]
