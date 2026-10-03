"""The whole soundtrack as one ffmpeg filter graph, one `MixInput` per layer.

A silent anchor of the picture's length fixes the duration, the narration plays under the page
sections, each clip's own audio lands at its section start, the music is ducked under speech and
shaped by `markers.json`, an ambience bed sits under the sections that ask for one, and each sound
effect lands on its resolved cue. A clip between two page sections, or a hold, pauses the
narration, so the track plays in the runs `inputs.timeline` splits it into, each from where its
first section starts.

One function per layer, because the graph used to be one body whose comments were the names these
functions now carry. A layer that cannot be laid says so through the run and lays nothing, since a
soundtrack missing its music is still a soundtrack and a film that stopped for one is not.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from decktalk.artifacts import CueTimes, Takes
from decktalk.errors import InputError
from decktalk.events import Level
from decktalk.findings import Code, Location, judge
from decktalk.inputs import Inputs, PageSection
from decktalk.inputs.cues import SECTION_END, SECTION_START, Spoken
from decktalk.inputs.document import MixEffect
from decktalk.inputs.markers import Marker
from decktalk.inputs.timeline import narration_offsets, narration_runs
from decktalk.machine import Run
from decktalk.media import ffmpeg
from decktalk.media.audio import gain
from decktalk.media.encode import Encoder
from decktalk.page import MILLISECONDS, SECOND_DIGITS
from decktalk.pipeline import Artifact, Stage
from decktalk.stages.assemble.cut import Rendered, concat, rendered_starts

CLIP_FADE_SECONDS = 0.02
"""Truth: half a frame of fade at each edge of a clip's own audio, so a cut into it never clicks."""


ONCE = "once"
"""An input read from its start, once, such as the narration track or one sound effect."""

LOOP = "loop"
"""An input repeated until the picture ends, such as a music bed or an ambience bed."""

LAVFI = "lavfi"
"""A generated input, which is the silent anchor that fixes the length of the mix."""


@dataclass(frozen=True)
class MixInput:
    """One input of the soundtrack: how ffmpeg reads it, and what it reads."""

    mode: str
    path: str

    def args(self, total: float) -> list[str]:
        """The ffmpeg input arguments for this layer, in the order the filter graph indexes them.

        A file layer is a sound the project names or the soundscape wrote, so it opens through
        `ffmpeg.source`, which reads that one file and follows no name inside it. The generated
        anchor is the one input that is not a file.
        """
        if self.mode == LOOP:
            return ["-stream_loop", "-1", *ffmpeg.source(self.path)]
        if self.mode == LAVFI:
            return ["-f", "lavfi", "-t", f"{total:.3f}", "-i", self.path]
        return ffmpeg.source(self.path)


@dataclass(frozen=True)
class MixPlan:
    """The whole soundtrack: its inputs, the filter graph over them, and how long it runs."""

    inputs: tuple[MixInput, ...]
    filter: str
    total: float


@dataclass
class Chain:
    """The graph being built, so a layer adds its input and its label without counting indexes.

    ffmpeg indexes the audio inputs in the order they are given on the command line, and the picture
    is input zero, so an index handed out here and an argument list built from the same list are the
    one thing that keeps the graph and the command in step.
    """

    sample_rate: int
    inputs: list[MixInput] = field(default_factory=list)
    fragments: list[str] = field(default_factory=list)
    labels: list[str] = field(default_factory=list)

    @property
    def format(self) -> str:
        """What every layer is resampled and laid out to before it is mixed with the others."""
        return f"aresample={self.sample_rate},aformat=channel_layouts=stereo"

    def add(self, mode: str, path: str) -> int:
        """Take one more input and give back the index the filter graph names it by."""
        self.inputs.append(MixInput(mode=mode, path=path))
        return len(self.inputs)

    def layer(self, index: int, filters: str, label: str) -> None:
        """Add one finished layer of the mix, as its filters and the label the final mix reads."""
        self.fragments.append(f"[{index}:a]{self.format}{filters}[{label}]")
        self.labels.append(f"[{label}]")

    def mixed(self, total: float) -> MixPlan:
        """The plan, with every layer summed into one track as long as the picture."""
        summed = "".join(self.labels) + f"amix=inputs={len(self.labels)}:duration=first:normalize=0[a]"
        return MixPlan(inputs=tuple(self.inputs), filter=";".join([*self.fragments, summed]), total=total)


def delay(seconds: float) -> str:
    """Where one layer starts in the film, in the milliseconds `adelay` takes."""
    return f"adelay={round(seconds * MILLISECONDS)}:all=1"


type Span = tuple[float, float]
"""One stretch of the film, as the second it starts and the second it ends."""


def second(seconds: float) -> str:
    """A moment as the graph writes it, to the millisecond, which is the moment ffmpeg reads."""
    return f"{seconds:.{SECOND_DIGITS}f}"


def ramp_expr(start: float, end: float, ramp: float) -> str:
    """Zero outside the span, one inside it, with a straight ramp of `ramp` seconds at both edges.

    A ramp of no length is a step, because dividing by it gives ffmpeg 0/0 on a frame that starts
    exactly at an edge, and that NaN silences the frame.
    """
    if ramp == 0:
        return f"gt(t,{second(start)})*lt(t,{second(end)})"
    return f"min(1,max(0,(t-{second(start)})/{ramp}))*min(1,max(0,({second(end)}-t)/{ramp}))"


def max_expr(terms: list[str]) -> str:
    """The largest of several ramps at each moment, halved at each level so it nests log2 of them deep."""
    if len(terms) <= 1:
        return terms[0] if terms else "0"
    half = len(terms) // 2
    return f"max({max_expr(terms[:half])},{max_expr(terms[half:])})"


def ramp_groups(spans: list[Span]) -> list[list[Span]]:
    """The spans in the order they start, joined wherever one starts before the group before it has ended.

    A ramp is zero at and outside the edges of its span, so two groups that do not overlap are never
    both above zero at one moment. Spans are compared as the graph writes them, to the millisecond.
    """
    groups: list[list[Span]] = []
    reach = 0.0
    for span in sorted(spans, key=lambda span: float(second(span[0]))):
        start, end = (float(second(edge)) for edge in span)
        if groups and start < reach:
            groups[-1].append(span)
            reach = max(reach, end)
        else:
            groups.append([span])
            reach = end
    return groups


def ramps_expr(spans: list[Span], ramp: float) -> str:
    """The largest ramp over every span at each moment, as a balanced search tree on t.

    Each leaf is the ramps of one group, and each branch asks whether t is before the first start
    of its later half, so a moment evaluates log2(groups) comparisons and the ramps of one group. It
    equals the largest of every ramp at every moment, because only the group that t falls among can
    be above zero, and it nests log2 of the spans deep where one fold of them nests once per span,
    which ffmpeg refuses at a hundred.
    """
    groups = ramp_groups(spans)

    def tree(lo: int, hi: int) -> str:
        if hi - lo == 1:
            return max_expr([ramp_expr(start, end, ramp) for start, end in groups[lo]])
        mid = (lo + hi) // 2
        return f"if(lt(t,{second(groups[mid][0][0])}),{tree(lo, mid)},{tree(mid, hi)})"

    return tree(0, len(groups)) if groups else "0"


def encode_soundtrack(inputs: Inputs, src: Path, dst: Path, *, filters: str | None = None) -> None:
    """Copy the picture and encode the mixed soundtrack to the delivery codec, once, through `filters` when given.

    Both the loudness pass and the path a build takes when that pass is skipped end here, so the
    soundtrack meets the delivery encoder exactly once rather than never or twice.
    """
    shaped = ("-af", filters) if filters else ()
    ffmpeg.run(
        "-i", str(src), "-map", "0:v", "-map", "0:a", "-c:v", "copy",
        *shaped, *Encoder(inputs.settings.video, inputs.settings.audio).aenc, "-movflags", "+faststart", str(dst),
    )  # fmt: skip


def resolve_marker_time(marker: Marker, starts: Mapping[int, float], takes: Takes, inputs: Inputs) -> float | None:
    """Where one marker falls in the finished film, or None when its phrase is not in the narration.

    A marker resolves exactly as a cue does, against the words of its own section, which already sit
    after that section's lead.
    """
    if marker.section not in starts:
        return None
    if marker.on == SECTION_START:
        return starts[marker.section] + marker.offset
    take = takes.of(marker.section)
    if take is None:
        return None
    words = inputs.words(marker.section, take.hash)
    if marker.on == SECTION_END:
        return starts[marker.section] + words[-1].end + marker.offset if words else None
    found = Spoken.of(words).find(marker.on, marker.occurrence, marker.case_sensitive)
    return None if found is None else starts[marker.section] + words[found].start + marker.offset


def speech_spans(rows: list[Rendered], takes: Takes, starts: Mapping[int, float]) -> list[Span]:
    """Every span of the film that carries speech, which is what the music ducks under.

    A spoken section speaks from where it starts until its last word, or until it ends when it says
    nothing at all, and a clip speaks for the whole of its own length because its dialogue is its own.
    """
    offsets = narration_offsets([row.section for row in rows], takes, starts)
    spans: list[Span] = []
    for take in takes.sections:
        offset = offsets[take.section]
        said = takes.speech_end(take.section)
        until = said if said is not None else takes.end(take.section)
        spans.append((offset + (takes.start(take.section) or 0.0), offset + (until or 0.0)))
    spans += [(starts[row.number], starts[row.number] + row.seconds) for row in rows if row.section.is_clip]
    return spans


def _anchor(chain: Chain) -> None:
    """A silence as long as the picture, which fixes the length of the mix."""
    chain.layer(chain.add(LAVFI, f"anullsrc=r={chain.sample_rate}:cl=stereo"), "", "anchor")


def _narration(chain: Chain, inputs: Inputs, rows: list[Rendered], takes: Takes, starts: Mapping[int, float]) -> None:
    """The joined narration, laid under the page sections in the runs the timeline splits it into."""
    track = str(inputs.workspace.narration_path)
    runs = narration_runs([row.section for row in rows], takes, starts)
    if len(runs) <= 1:
        at = runs[0].at if runs else 0.0
        chain.layer(chain.add(ONCE, track), f",{delay(at)}", "narr")
        return
    # A clip between two page sections, or a hold, pauses the narration, so each unbroken run of
    # page sections plays its own stretch of the track from where its first section starts.
    for number, run in enumerate(runs):
        trim = f"atrim=start={run.start:.3f}" + ("" if run.end is None else f":end={run.end:.3f}")
        chain.layer(chain.add(ONCE, track), f",{trim},asetpts=PTS-STARTPTS,{delay(run.at)}", f"narr{number}")


def _clip_audio(chain: Chain, rows: list[Rendered], starts: Mapping[int, float]) -> None:
    """Each clip's own sound, at its section start, trimmed to its picture and faded at both ends."""
    for row in rows:
        if row.audio is None:
            continue
        fade_out_at = max(row.seconds - CLIP_FADE_SECONDS, 0)
        chain.layer(
            chain.add(ONCE, str(row.audio)),
            f",atrim=duration={row.seconds:.3f},asetpts=PTS-STARTPTS,"
            f"afade=t=in:d={CLIP_FADE_SECONDS},afade=t=out:st={fade_out_at:.3f}:d={CLIP_FADE_SECONDS},"
            f"{delay(starts[row.number])}",
            f"clip{row.key}",
        )


def _music_shape(inputs: Inputs, run: Run, takes: Takes, starts: Mapping[int, float],
                 speech: list[Span]) -> list[str]:  # fmt: skip
    """The volume factors the music plays under: the duck under speech, and the swells the markers ask for."""
    mix = inputs.document.mix
    ramps = inputs.settings.mix
    ducked = gain(mix.music_duck_db)
    factors = [f"(1-{1 - ducked:.5f}*{ramps_expr(speech, ramps.duck_ramp_seconds)})"]
    if not mix.music_markers:
        return factors
    markers = inputs.markers()
    if markers is None:
        run.note(f"{mix.music_markers} is not there, so the music plays with no structure.", level=Level.WARNING)
        return factors
    for note in markers.notes:
        run.note(note, level=Level.WARNING)
    boosts: list[Span] = []
    mutes: list[Span] = []
    for marker in markers.markers:
        at = resolve_marker_time(marker, starts, takes, inputs)
        if at is None:
            run.note(f"The marker {marker.name!r} is unresolved, so it shapes nothing.", level=Level.WARNING)
            continue
        if marker.mute_seconds > 0:
            mutes.append((at, at + marker.mute_seconds))
        swell = at + marker.mute_seconds
        boosts.append((swell, swell + markers.boost_seconds))
    if boosts:
        factors.append(f"(1+{gain(markers.boost_db) - 1:.5f}*{ramps_expr(boosts, ramps.marker_boost_ramp_seconds)})")
    if mutes:
        factors.append(f"(1-{ramps_expr(mutes, ramps.marker_mute_ramp_seconds)})")
    return factors


def _music(chain: Chain, inputs: Inputs, run: Run, takes: Takes, starts: Mapping[int, float],
           speech: list[Span], total: float) -> None:  # fmt: skip
    """The music bed, ducked under every span that carries speech and shaped by the markers."""
    mix = inputs.document.mix
    if not mix.music:
        return
    path = inputs.path(mix.music)
    if not path.exists():
        _missing_sound(inputs, run, mix.music, "music", Artifact.SOUNDSCAPE.next_step)
        return
    volume = f"{gain(mix.music_db):.5f}*" + "*".join(_music_shape(inputs, run, takes, starts, speech))
    chain.layer(
        chain.add(LOOP, str(path)),
        f",atrim=duration={total:.3f},asetpts=PTS-STARTPTS,volume='{volume}':eval=frame,"
        f"afade=t=in:d={mix.music_fade_in_seconds},"
        f"afade=t=out:st={max(total - mix.music_fade_out_seconds, 0):.3f}:d={mix.music_fade_out_seconds}",
        "music",
    )


def _ambience(chain: Chain, inputs: Inputs, run: Run, rows: list[Rendered], starts: Mapping[int, float],
              total: float) -> None:  # fmt: skip
    """The ambience bed, under the sections that ask for one and silent everywhere else."""
    mix = inputs.document.mix
    flagged = [row for row in rows if isinstance(row.section, PageSection) and row.section.ambience]
    if not mix.ambience or not flagged:
        return
    path = inputs.path(mix.ambience)
    if not path.exists():
        _missing_sound(inputs, run, mix.ambience, "ambience", Artifact.SOUNDSCAPE.next_step)
        return
    ramps = inputs.settings.mix
    pad = ramps.ambience_pad_seconds
    spans = [(starts[row.number] - pad, starts[row.number] + row.seconds + pad) for row in flagged]
    chain.layer(
        chain.add(LOOP, str(path)),
        f",atrim=duration={total:.3f},asetpts=PTS-STARTPTS,"
        f"volume='{gain(mix.ambience_db):.5f}*{ramps_expr(spans, ramps.ambience_ramp_seconds)}':eval=frame",
        "amb",
    )


def effect_second(effect: MixEffect, cue_times: CueTimes | None, starts: Mapping[int, float]) -> float | None:
    """The film second one effect plays at, or None while its cue or its section has no place in the film."""
    at = None if cue_times is None else cue_times.at(effect.section, effect.cue)
    return None if effect.section not in starts or at is None else starts[effect.section] + at + effect.offset


def _effects(chain: Chain, inputs: Inputs, run: Run, starts: Mapping[int, float]) -> None:
    """Each sound effect, at the second its own cue resolved to."""
    cue_times = inputs.cue_times()
    for number, effect in enumerate(inputs.document.mix.effects):
        path = inputs.path(effect.file)
        if not path.exists():
            _missing_sound(inputs, run, effect.file, f"the effect cued at {effect.cue}", "", section=effect.section)
            continue
        where = effect_second(effect, cue_times, starts)
        if where is None:
            run.note(
                f"The cue {effect.cue!r} in section {effect.section} is unresolved, so {effect.file} does not play.",
                level=Level.WARNING,
            )
            continue
        chain.layer(chain.add(ONCE, str(path)), f",volume={gain(effect.db):.5f},{delay(where)}", f"effect{number}")


def _missing_sound(inputs: Inputs, run: Run, named: str, what: str, hint: str, *, section: int | None = None) -> None:
    """One judgement for a sound file the project names and has not got, which plays as silence."""
    run.found(
        judge(
            Code.FILE_MISSING,
            f"the project names {named} as {what} and it is not on disk, so that layer plays as "
            f"silence. {hint}".strip(),
            Location(where=named, file=inputs.relative(inputs.path(named)), section=section),
            stage=Stage.ASSEMBLE,
        )
    )


def plan_mix(inputs: Inputs, run: Run, rows: list[Rendered], takes: Takes, *, soundscape: bool) -> MixPlan:
    """The whole soundtrack as one graph, laid layer by layer under a picture of a fixed length."""
    chain = Chain(sample_rate=inputs.settings.audio.sample_rate)
    starts = rendered_starts(rows)
    total = round(sum(row.seconds for row in rows), SECOND_DIGITS)
    _anchor(chain)
    _narration(chain, inputs, rows, takes, starts)
    _clip_audio(chain, rows, starts)
    if soundscape:
        speech = speech_spans(rows, takes, starts)
        _music(chain, inputs, run, takes, starts, speech, total)
        _ambience(chain, inputs, run, rows, starts, total)
        _effects(chain, inputs, run, starts)
    return chain.mixed(total)


def mix_input_args(plan: MixPlan) -> list[str]:
    """The ffmpeg input arguments for one plan, in the order its filter graph indexes them."""
    return [arg for layer in plan.inputs for arg in layer.args(plan.total)]


def mix_soundtrack(inputs: Inputs, run: Run, rows: list[Rendered], takes: Takes, work: Path, *, soundscape: bool
                   ) -> MixPlan:  # fmt: skip
    """Join the section cuts and lay the whole soundtrack under them, into one work file.

    The soundtrack is written as floating-point samples, so a sum of layers louder than 0 dBFS is
    carried rather than clipped, and the delivery encoder runs once, downstream of the limiter. The
    graph reaches ffmpeg as a file read with `-/filter_complex`, because a long film's graph runs
    past the 32,767 characters a Windows command line holds.
    """
    picture = inputs.workspace.final_dir / ".picture.mp4"
    graph = work.with_suffix(".graph")
    concat_files = [row.path for row in rows]
    if not concat_files:
        raise InputError(
            "decktalk.toml declares no section, so there is no film to assemble.",
            hint="Add a [[section]] that names a page or a clip.",
        )
    concat(concat_files, picture)
    plan = plan_mix(inputs, run, rows, takes, soundscape=soundscape)
    enc = Encoder(inputs.settings.video, inputs.settings.audio)
    try:
        graph.write_text(plan.filter, encoding="utf-8")
        ffmpeg.run(
            "-i", str(picture), *mix_input_args(plan),
            "-/filter_complex", str(graph),
            "-map", "0:v", "-map", "[a]", "-c:v", "copy", *enc.amix, str(work),
        )  # fmt: skip
    finally:
        picture.unlink(missing_ok=True)
        graph.unlink(missing_ok=True)
    return plan


__all__ = [
    "CLIP_FADE_SECONDS",
    "LAVFI",
    "LOOP",
    "ONCE",
    "Chain",
    "MixInput",
    "MixPlan",
    "Span",
    "delay",
    "encode_soundtrack",
    "max_expr",
    "mix_input_args",
    "mix_soundtrack",
    "plan_mix",
    "ramp_expr",
    "ramp_groups",
    "ramps_expr",
    "resolve_marker_time",
    "second",
    "speech_spans",
]
