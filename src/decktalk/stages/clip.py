"""Clips and word times taken from a built section.

    decktalk clip N --start S --end E --out media/x.mp4   a span of build/sections/NN.mp4 with the section's take
                                                          over the same span, and the words spoken inside it
    decktalk words [--only N] [--json]                   each spoken section's words, in seconds after it starts

Both read the section's own clock, the one `?words=` and `cue-times.json` use: 0 is the section start, which is
narration t=0 of its recording and the first frame of its sections/NN.mp4. A section's `lead_seconds` of
silence counts, so its first word starts after the lead.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..artifacts import Timeline, Word, write_words
from ..captions import display_words
from ..errors import ConfigError, MissingInputError
from ..jsonio import relative
from ..media import ffmpeg
from ..media.encode import Encoder
from ..model import PageSection, Project
from ..verdicts import Findings

log = logging.getLogger(__name__)

EDGE_FADE_SECONDS = 0.01  # The clip's audio fades in and out over this long, so a cut inside a word never clicks.
WORD_SLACK_SECONDS = 0.001  # A word that sits this close to an edge of the span still counts as inside it.


def _timeline(project: Project) -> Timeline:
    timeline = project.timeline()
    if timeline is None:
        raise MissingInputError(f"{project.timeline_path} not found. Run `decktalk narrate` first.")
    return timeline


@dataclass
class SectionWords:
    """One spoken section's words, in seconds after the section starts."""

    key: str
    title: str
    lead_seconds: float
    duration: float
    estimated: bool
    words: list[Word] = field(default_factory=list)  # as the voice returned them, with punctuation removed
    texts: list[str] = field(default_factory=list)  # the same words with the script's punctuation and case

    @property
    def section(self) -> int:
        return int(self.key)

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "section": self.section,
            "title": self.title,
            "lead_seconds": self.lead_seconds,
            "duration": self.duration,
            "estimated": self.estimated,
            "words": [
                {"word": w.word, "text": text, "start": w.start, "end": w.end}
                for w, text in zip(self.words, self.texts, strict=True)
            ],
        }


@dataclass
class WordsResult:
    """Every spoken section's words, in seconds after the section starts."""

    sections: list[SectionWords] = field(default_factory=list)

    @property
    def findings(self) -> Findings:
        """None. `words` reads the narration clock and judges nothing."""
        return Findings()

    def to_dict(self, root: Path) -> dict[str, Any]:
        return {"sections": [s.to_dict() for s in self.sections]}


def words(project: Project, only: list[int] | None = None) -> WordsResult:
    """Each spoken section's words from timeline.json, in seconds after the section starts.

    These are the times the recorder passes to a page as `?words=`, to three decimals. The script's
    spelling comes from takes.json, so each word also has its punctuation and case.
    """
    timeline = _timeline(project)
    keys = timeline.keys
    if only:
        wanted = {f"{n:02d}" for n in only}
        missing = sorted(wanted - set(keys))
        if missing:
            raise ConfigError(
                f"section(s) {[int(k) for k in missing]} have no words in {project.timeline_path.name}; "
                f"spoken sections are {[int(k) for k in keys]}"
            )
        keys = [k for k in keys if k in wanted]
    takes = project.takes()
    out: list[SectionWords] = []
    for key in keys:
        sec = timeline.sections[key]
        words = [Word(w.word, round(w.start - sec.start, 3), round(w.end - sec.start, 3)) for w in sec.words]
        entry = takes.sections.get(key) if takes else None
        shown = display_words(words, entry.spoken) if entry and entry.spoken else words
        out.append(
            SectionWords(
                key=key,
                title=sec.title,
                lead_seconds=sec.lead_seconds,
                duration=sec.duration,
                estimated=timeline.estimated,
                words=words,
                texts=[w.word for w in shown],
            )
        )
    return WordsResult(sections=out)


@dataclass
class ClipResult:
    """What `clip` wrote."""

    section: int
    video: Path
    words_file: Path
    start: float  # The first frame's time in the section, in seconds.
    end: float  # The time just after the last frame of the span, in seconds.
    first_frame: int
    last_frame: int
    hold_seconds: float
    duration: float  # The clip's length, with the hold.
    gain_db: float
    estimated: bool  # True when the words come from a build without voice.
    words: list[Word] = field(default_factory=list)  # in seconds after the clip starts, with the script's spelling
    cut_words: list[str] = field(default_factory=list)  # words the span cuts in two, left out of the words file

    @property
    def findings(self) -> Findings:
        """Uncertain: a word the span cuts in two, which the words file leaves out."""
        return Findings(uncertain=len(self.cut_words))

    def to_dict(self, root: Path) -> dict[str, Any]:
        return {
            "section": self.section,
            "video": relative(self.video, root),
            "words_file": relative(self.words_file, root),
            "start": self.start,
            "end": self.end,
            "first_frame": self.first_frame,
            "last_frame": self.last_frame,
            "hold_seconds": self.hold_seconds,
            "duration": self.duration,
            "gain_db": self.gain_db,
            "estimated": self.estimated,
            "word_count": len(self.words),
            "cut_words": list(self.cut_words),
        }


def clip(
    project: Project,
    number: int,
    *,
    start: float,
    end: float,
    out: Path | str,
    words_out: Path | str | None = None,
    gain_db: float = 0.0,
    hold_seconds: float = 0.0,
) -> ClipResult:
    """Cut a span of a built page section into a video file and a words file for a clip section.

    The picture is frames `round(start * fps)` up to `round(end * fps)` of build/sections/NN.mp4. The
    sound is the section's take over the same span, with `gain_db` applied and a 10 ms fade at each edge.
    The part of the span inside the section's `lead_seconds` is silence. `hold_seconds` holds the last
    frame in silence. The words file lists each word wholly inside the span, in seconds after the clip
    starts, with the script's punctuation and case. Relative paths are relative to the project.
    """
    sec = project.section(number)
    if sec is None:
        raise ConfigError(f"section {number} is not in decktalk.toml")
    if not isinstance(sec, PageSection):
        raise ConfigError(f"section {number} is a clip section; cut a clip from a page section")
    if start < 0 or end <= start:
        raise ConfigError(f"the span must start at 0 or later and end after it starts, got {start:g} to {end:g}")
    if hold_seconds < 0:
        raise ConfigError(f"--hold must be 0 or more, got {hold_seconds:g}")
    video = project.section_video(sec)
    if not video.exists():
        raise MissingInputError(f"section {number} has no section video at {video}. Run `decktalk assemble` first.")
    timeline = _timeline(project)
    tsec = timeline.sections.get(sec.key)
    takes = project.takes()
    entry = takes.sections.get(sec.key) if takes else None
    if tsec is None or entry is None:
        raise MissingInputError(f"section {number} has no narration yet. Run `decktalk narrate` first.")
    take = project.narration_dir / entry.file
    if not take.exists():
        raise MissingInputError(f"section {number}'s take is missing: {take}. Run `decktalk narrate` first.")

    v = project.settings.video
    fps = v.fps
    length = ffmpeg.probe_duration(video)
    if end > length + 0.5 / fps:
        raise ConfigError(f"the span ends at {end:g} s, but section {number}'s video is {length:g} s long")
    f0, f1 = round(start * fps), round(end * fps)
    if f1 <= f0:
        raise ConfigError(f"the span from {start:g} to {end:g} s holds no whole frame at {fps} fps")
    t0, t1 = f0 / fps, f1 / fps
    hold_frames = round(hold_seconds * fps)
    span = t1 - t0
    total = (f1 - f0 + hold_frames) / fps

    # The take starts after the section's lead, so a span that begins inside the lead starts with silence.
    lead = tsec.lead_seconds
    head = max(0.0, lead - t0)
    a0 = max(0.0, t0 - lead)
    # A span wholly inside the lead still reads a millisecond, so atrim outputs a frame.
    a1 = max(a0 + 0.001, t1 - lead)
    head_samples = round(head * v.sample_rate)
    fade_out_at = max(span - EDGE_FADE_SECONDS, 0.0)
    audio = (
        f"[1:a]aresample={v.sample_rate},atrim=start={a0:.6f}:end={a1:.6f},asetpts=PTS-STARTPTS,"
        f"adelay=delays={head_samples}S:all=1,volume={gain_db:g}dB,"
        f"afade=t=in:d={EDGE_FADE_SECONDS},afade=t=out:st={fade_out_at:.6f}:d={EDGE_FADE_SECONDS},"
        f"apad=whole_dur={total:.6f},atrim=duration={total:.6f}[a]"
    )
    # Frames pass through fps before the hold, or ffmpeg 7.0's tpad clones only two frames after a trim.
    picture = (
        f"[0:v]trim=start_frame={f0}:end_frame={f1},setpts=PTS-STARTPTS,fps={fps},"
        f"tpad=stop_mode=clone:stop={hold_frames}[v]"
    )
    dst = project.path(out)
    words_dst = project.path(words_out) if words_out is not None else dst.with_suffix(".words.json")
    if dst.resolve() == video.resolve():
        raise ConfigError(f"--out names the section video it reads: {video}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    enc = Encoder(v)
    ffmpeg.run(
        "-i", str(video), "-i", str(take),
        "-filter_complex", f"{picture};{audio}",
        "-map", "[v]", "-map", "[a]", "-r", str(fps), *enc.venc, *enc.aenc,
        "-t", f"{total:.6f}", "-movflags", "+faststart", str(dst),
    )  # fmt: skip

    words, cut = _clip_words(project, sec.key, t0, t1)
    for text in cut:
        log.warning("section %s: the span cuts the word %r in two, so the words file leaves it out", sec.key, text)
    if timeline.estimated:
        log.warning("section %s: the narration is a silent placeholder, so the word times are estimates", sec.key)
    write_words(words_dst, words)
    return ClipResult(
        section=number,
        video=dst,
        words_file=words_dst,
        start=round(t0, 3),
        end=round(t1, 3),
        first_frame=f0,
        last_frame=f1 - 1,
        hold_seconds=round(hold_frames / fps, 3),
        duration=round(total, 3),
        gain_db=gain_db,
        estimated=timeline.estimated,
        words=words,
        cut_words=cut,
    )


def _clip_words(project: Project, key: str, t0: float, t1: float) -> tuple[list[Word], list[str]]:
    """(the words wholly inside the span, in seconds after t0 with the script's spelling, the words it cuts)."""
    (section,) = [s for s in words(project, only=[int(key)]).sections if s.key == key]
    inside: list[Word] = []
    cut: list[str] = []
    for w, text in zip(section.words, section.texts, strict=True):
        if w.start >= t0 - WORD_SLACK_SECONDS and w.end <= t1 + WORD_SLACK_SECONDS:
            inside.append(Word(text, round(max(0.0, w.start - t0), 3), round(min(t1, w.end) - t0, 3)))
        elif w.start < t1 and w.end > t0:
            cut.append(text)
    return inside, cut
