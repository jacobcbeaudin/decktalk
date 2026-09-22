"""The whole soundtrack as one ffmpeg filter graph, one `MixInput` per layer.

A silent anchor of the picture's length fixes the duration, the narration plays under the page
sections, each clip's own audio lands at its section start, the music is ducked under speech and
shaped by `markers.json`, an ambience bed sits under the sections that ask for one, and each sound
effect lands on its resolved cue. A clip between two page sections, or a hold, pauses the
narration, so the track plays in the runs `model.timeline` splits it into, each from where its first
section starts.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from ...artifacts import CueTimes, Takes, Word, read_words
from ...media import ffmpeg
from ...media.encode import Encoder
from ...model import PageSection, Project
from ...model.cues import find_phrase
from ...model.markers import Marker
from ...model.timeline import narration_offsets, narration_runs
from ...settings import AudioConfig
from .cut import RenderedSection, rendered_starts

log = logging.getLogger(__name__)

CLIP_FADE_SECONDS = 0.02  # Every clip's audio fades in and out over this long, so a cut never clicks.


ONCE = "once"  # An input read from its start, once.
LOOP = "loop"  # An input repeated until the picture ends, such as a music or ambience bed.
LAVFI = "lavfi"  # A generated input, such as the silent anchor that fixes the mix duration.


@dataclass(frozen=True)
class MixInput:
    """One input of the soundtrack: how ffmpeg reads it, and what it reads."""

    mode: str  # ONCE, LOOP or LAVFI
    path: str

    def args(self, total: float) -> list[str]:
        """The ffmpeg input arguments for this layer, in the order the filter graph indexes them."""
        if self.mode == LOOP:
            return ["-stream_loop", "-1", "-i", self.path]
        if self.mode == LAVFI:
            return ["-f", "lavfi", "-t", f"{total:.3f}", "-i", self.path]
        return ["-i", self.path]


@dataclass
class MixPlan:
    """The whole soundtrack: its inputs, the filter graph over them, and what could not be honoured."""

    inputs: list[MixInput] = field(default_factory=list)
    filter: str = ""
    total: float = 0.0
    warnings: list[str] = field(default_factory=list)


def encode_soundtrack(project: Project, src: Path, dst: Path) -> None:
    """Copy the picture and encode the mixed soundtrack to the delivery codec, once.

    This is the path a build takes when the loudness pass is skipped, so the soundtrack still meets
    the delivery encoder exactly once rather than never or twice.
    """
    enc = Encoder(project.settings.video)
    ffmpeg.run(
        "-i", str(src), "-map", "0:v", "-map", "0:a", "-c:v", "copy",
        *enc.aenc, "-movflags", "+faststart", str(dst),
    )  # fmt: skip


def db(x: float) -> float:
    return 10 ** (x / 20)


def ramp_expr(a: float, b: float, r: float) -> str:
    """0 outside [a,b], 1 inside, linear ramps of r seconds at both edges."""
    return f"min(1,max(0,(t-{a:.3f})/{r}))*min(1,max(0,({b:.3f}-t)/{r}))"


def max_expr(terms: list[str]) -> str:
    if not terms:
        return "0"
    expr = terms[0]
    for t in terms[1:]:
        expr = f"max({expr},{t})"
    return expr


def resolve_marker_time(
    marker: Marker,
    starts: dict[str, float],
    takes: Takes,
    takes_dir: Path,
    leads: Mapping[str, float] | None = None,
) -> float | None:
    """Where a marker falls in the final file, or None when its phrase is not in the narration.

    `starts` gives each section's start in the final file and `leads` each section's lead_seconds,
    which the marker's words already sit after.
    """
    if marker.key not in starts:
        return None
    if marker.on == "$start":
        return starts[marker.key] + marker.offset
    entry = takes.sections.get(marker.key)
    if entry is None:
        return None
    lead = (leads or {}).get(marker.key, 0.0)
    words = [Word(w.word, w.start + lead, w.end + lead) for w in read_words(takes_dir / entry.words_file)]
    if marker.on == "$end":
        return starts[marker.key] + words[-1].end + marker.offset if words else None
    idx = find_phrase(words, marker.on, marker.occurrence, marker.case_sensitive)
    return None if idx is None else starts[marker.key] + words[idx].start + marker.offset


def plan_mix(project: Project, rows: list[RenderedSection], takes: Takes, *, soundscape: bool) -> MixPlan:
    mix = project.mix
    audio: AudioConfig = project.settings.audio
    sr = project.settings.video.sample_rate
    cue_times: CueTimes = project.cue_times()
    plan = MixPlan()
    starts = rendered_starts(rows)
    plan.total = sum(r.duration for r in rows)
    chain: list[str] = []
    labels: list[str] = []

    def add_input(mode: str, path: str) -> int:
        plan.inputs.append(MixInput(mode=mode, path=path))
        return len(plan.inputs)

    fmt = f"aresample={sr},aformat=channel_layouts=stereo"

    # A silent anchor of the picture's length fixes the mix duration, since the picture
    # itself carries no audio.
    idx = add_input(LAVFI, f"anullsrc=r={sr}:cl=stereo")
    chain.append(f"[{idx}:a]{fmt}[anchor]")
    labels.append("[anchor]")

    # narration under the picture from the first page section
    narration = project.narration_path
    played = [r.section for r in rows]
    runs = narration_runs(played, takes, starts)
    if len(runs) <= 1:
        t0 = runs[0].at if runs else 0.0
        idx = add_input(ONCE, str(narration))
        chain.append(f"[{idx}:a]{fmt},adelay={int(round(t0 * 1000))}:all=1[narr]")
        labels.append("[narr]")
    else:
        # A clip between page sections pauses the narration. Each run of page sections plays
        # its own stretch of the track, starting where its first section starts.
        for n, run in enumerate(runs):
            idx = add_input(ONCE, str(narration))
            trim = f"atrim=start={run.start:.3f}" + ("" if run.end is None else f":end={run.end:.3f}")
            chain.append(
                f"[{idx}:a]{fmt},{trim},asetpts=PTS-STARTPTS,adelay={int(round(run.at * 1000))}:all=1[narr{n}]"
            )
            labels.append(f"[narr{n}]")
    offsets = narration_offsets(played, takes, starts)
    # A section is speaking from its start until its last word, or until it ends when it says nothing.
    speech: list[tuple[float, float]] = []
    for key in takes.keys:
        said = takes.speech_end_seconds(key)
        until = said if said is not None else takes.end(key)
        speech.append((offsets[key] + (takes.start(key) or 0.0), offsets[key] + (until or 0.0)))
    speech += [(starts[r.section.key], starts[r.section.key] + r.duration) for r in rows if r.section.is_clip]

    # each clip's own audio, at its section start, trimmed to its picture and faded at both ends
    for row in rows:
        if row.audio is None:
            continue
        idx = add_input(ONCE, str(row.audio))
        key = row.section.key
        fade_out_at = max(row.duration - CLIP_FADE_SECONDS, 0)
        chain.append(
            f"[{idx}:a]{fmt},atrim=duration={row.duration:.3f},asetpts=PTS-STARTPTS,"
            f"afade=t=in:d={CLIP_FADE_SECONDS},afade=t=out:st={fade_out_at:.3f}:d={CLIP_FADE_SECONDS},"
            f"adelay={int(round(starts[key] * 1000))}:all=1[clip{key}]"
        )
        labels.append(f"[clip{key}]")

    # music
    music = project.path(mix.music) if mix.music and soundscape else None
    if music is not None and music.exists():
        idx = add_input(LOOP, str(music))
        base = db(mix.music_db)
        duck = db(mix.music_duck_db)
        factors = [f"(1-{1 - duck:.5f}*{max_expr([ramp_expr(a, b, audio.duck_ramp_seconds) for a, b in speech])})"]
        if mix.music_markers:
            mpath = project.path(mix.music_markers)
            spec = project.markers()
            if spec is None:
                plan.warnings.append(f"markers file missing ({mpath}), so the music has no structure")
            else:
                boost = db(spec.boost_db) - 1
                boosts: list[str] = []
                mutes: list[str] = []
                leads = {r.section.key: project.lead_seconds(r.section.key) for r in rows}
                for marker in spec.markers:
                    mt = resolve_marker_time(marker, starts, takes, project.takes_dir, leads)
                    if mt is None:
                        plan.warnings.append(f"marker {marker.name!r} is unresolved, so it is skipped")
                        continue
                    if marker.mute_seconds > 0:
                        mutes.append(ramp_expr(mt, mt + marker.mute_seconds, audio.marker_mute_ramp_seconds))
                    swell_at = mt + marker.mute_seconds
                    boosts.append(ramp_expr(swell_at, swell_at + spec.boost_seconds, audio.marker_boost_ramp_seconds))
                if boosts:
                    factors.append(f"(1+{boost:.5f}*{max_expr(boosts)})")
                if mutes:
                    factors.append(f"(1-{max_expr(mutes)})")
        vol = f"{base:.5f}*" + "*".join(factors)
        chain.append(
            f"[{idx}:a]{fmt},atrim=duration={plan.total:.3f},asetpts=PTS-STARTPTS,volume='{vol}':eval=frame,"
            f"afade=t=in:d={mix.music_fade_in_seconds},"
            f"afade=t=out:st={max(plan.total - mix.music_fade_out_seconds, 0):.3f}"
            f":d={mix.music_fade_out_seconds}[music]"
        )
        labels.append("[music]")
    elif mix.music and soundscape:
        plan.warnings.append(
            f"music missing ({project.path(mix.music)}), so there is no music. Run `decktalk soundscape` to make it"
        )

    # ambience under flagged sections
    amb = project.path(mix.ambience) if mix.ambience and soundscape else None
    flagged = [r for r in rows if isinstance(r.section, PageSection) and r.section.ambience]
    if amb is not None and amb.exists() and flagged:
        idx = add_input(LOOP, str(amb))
        pad = audio.ambience_pad_seconds
        spans = [
            ramp_expr(
                starts[r.section.key] - pad, starts[r.section.key] + r.duration + pad, audio.ambience_ramp_seconds
            )
            for r in flagged
        ]
        chain.append(
            f"[{idx}:a]{fmt},atrim=duration={plan.total:.3f},asetpts=PTS-STARTPTS,volume='{db(mix.ambience_db):.5f}*{max_expr(spans)}':eval=frame[amb]"
        )
        labels.append("[amb]")
    elif mix.ambience and soundscape and flagged:
        plan.warnings.append(f"ambience missing ({amb}), so there is no ambience")

    # one-shot sfx on resolved cues
    for n, sfx in enumerate(mix.sfx if soundscape else ()):
        path = project.path(sfx.file)
        key = f"{sfx.section:02d}"
        cue_t = cue_times.get(key, sfx.cue)
        if not path.exists():
            plan.warnings.append(f"sfx {sfx.file} is missing, so it is skipped")
            continue
        if key not in starts or cue_t is None:
            plan.warnings.append(
                f"sfx {sfx.file}: cue {sfx.cue!r} in section {sfx.section} is unresolved, so it is skipped"
            )
            continue
        idx = add_input(ONCE, str(path))
        at_ms = int(round((starts[key] + cue_t + sfx.offset) * 1000))
        chain.append(f"[{idx}:a]{fmt},volume={db(sfx.db):.5f},adelay={at_ms}:all=1[sfx{n}]")
        labels.append(f"[sfx{n}]")

    chain.append("".join(labels) + f"amix=inputs={len(labels)}:duration=first:normalize=0[a]")
    plan.filter = ";".join(chain)
    return plan


def mix_input_args(plan: MixPlan) -> list[str]:
    """ffmpeg input arguments for the plan, in the order its filter graph indexes them."""
    return [arg for layer in plan.inputs for arg in layer.args(plan.total)]
