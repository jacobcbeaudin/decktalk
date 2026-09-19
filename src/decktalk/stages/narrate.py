"""Stage 1: the script becomes one mp3 per section with word timestamps, plus the continuous track.

The script is markdown with "## N. Title" sections. Bracketed directions such as
[Deck. The curve draws.] or [beat] are not spoken and become a short pause, and a
[pause N] direction becomes a pause of N seconds instead. Markdown formatting is
stripped, and ALL-CAPS placeholders like [NUMBER] refuse a real run. Sections that the
project maps to a clip are skipped.

Each section is synthesized with word timestamps, cached by a hash of model, voice,
settings and text, padded so speech ends at least min_tail_seconds before the file
ends, then every section is concatenated with no gaps into build/narration/narration.mp3.
A renumbered section keeps its take: when its key misses, an entry of the previous
take index with the same hash lends its files, copied to the new name.
A page section's lead_seconds joins that much silence in before its take when the takes are
concatenated, and its tail_seconds replaces min_tail_seconds for it. Neither is part of the
hash, so neither voices a take again.
build/narration/timeline.json records each section's absolute start and end and every
word at absolute time; the recorder and the assembler cut the visuals to it.

silent=True needs no API key: silent placeholders sized at silent_words_per_minute
plus the declared pauses, with evenly spaced estimated words, so the whole pipeline
runs offline. A silent run refuses a take index that holds voiced takes unless force is
set, because it would write click tracks over them.
"""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from ..artifacts import Take, Takes, Timeline, TimelineSection, Word, write_words
from ..errors import ConfigError
from ..media import audio, ffmpeg
from ..model import PageSection, Project
from ..model.script import PUNCT, Segment
from ..providers import elevenlabs as _elevenlabs  # noqa: F401  (registers the default provider)
from ..providers.speech import SpeechProvider, SpeechRequest, get_provider
from ..settings import NarrationConfig

log = logging.getLogger(__name__)

# ---- audio ---------------------------------------------------------------------------


def section_config(project: Project, seg: Segment) -> NarrationConfig:
    """The narration settings for one section: its own `tail_seconds`, when it sets one, replaces min_tail_seconds."""
    cfg = project.settings.narration
    sec = project.section(seg.index)
    tail = sec.tail_seconds if isinstance(sec, PageSection) else None
    return cfg if tail is None else replace(cfg, min_tail_seconds=tail)


def ensure_tail(path: Path, cfg: NarrationConfig, *, tolerance: float = 0.0) -> float:
    """Pad with silence so speech ends at least min_tail_seconds before the file ends. Returns seconds added.

    A tail within `tolerance` of min_tail_seconds counts as long enough.
    """
    tail = audio.trailing_silence(path)
    if tail >= cfg.min_tail_seconds - tolerance:
        return 0.0
    add = round(cfg.min_tail_seconds - tail + cfg.tail_slack_seconds, 3)
    audio.pad_tail(path, add, bitrate=cfg.mp3_bitrate)
    return add


def estimated_words(segment: Segment, duration: float, cfg: NarrationConfig) -> list[Word]:
    """Evenly spaced words for silent runs, so cues resolve to plausible times."""
    tokens = segment.spoken.split()
    if not tokens:
        return []
    lead = cfg.opening_silence_seconds if segment.first_spoken else 0.0
    span = max(0.1, duration - lead - cfg.min_tail_seconds)
    per = span / len(tokens)
    return [
        Word(word=t.strip(PUNCT), start=round(lead + i * per, 3), end=round(lead + (i + 1) * per - 0.02, 3))
        for i, t in enumerate(tokens)
    ]


def text_hash(segment: Segment, cfg: NarrationConfig, provider_key: str, settings: dict[str, Any]) -> str:
    """Cache key for one section: provider identity, voice settings and the exact text sent."""
    payload = f"{provider_key}\n{json.dumps(settings, sort_keys=True)}\n{segment.tts_text(cfg)}"
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def is_cached(entry: Take | None, seg: Segment, digest: str, narration_dir: Path) -> bool:
    """True when the entry holds this section's take under its current file names."""
    return (
        entry is not None
        and entry.hash == digest
        and entry.file == seg.filename
        and (narration_dir / seg.filename).exists()
        and (narration_dir / seg.words_filename).exists()
    )


def reusable_entry(previous: Takes | None, seg: Segment, digest: str, narration_dir: Path) -> tuple[str, Take] | None:
    """(key, entry) of a previous take of the same text under another section number, or None.

    A section that was renumbered keeps its hash, because the hash has no section number in
    it. Only the first spoken section carries the opening silence, so a take never moves into
    or out of that place.
    """
    if previous is None or seg.first_spoken or not previous.sections:
        return None
    first = min(previous.sections)
    for key, entry in previous.sections.items():
        if (
            key != first
            and key != seg.key
            and entry.hash == digest
            and (narration_dir / entry.file).exists()
            and (narration_dir / entry.words_file).exists()
        ):
            return key, entry
    return None


def reuse_takes(moves: list[tuple[Take, Segment]], narration_dir: Path) -> None:
    """Copy each take to its new file names. Every source is copied aside first, so a move never
    overwrites a file that another move still reads."""
    staged: list[tuple[Path, Path]] = []
    for i, (entry, seg) in enumerate(moves):
        for j, (src, dst) in enumerate(((entry.file, seg.filename), (entry.words_file, seg.words_filename))):
            tmp = narration_dir / f".reuse-{i}-{j}.tmp"
            shutil.copyfile(narration_dir / src, tmp)
            staged.append((tmp, narration_dir / dst))
    for tmp, dst in staged:
        tmp.replace(dst)


def refuse_silent_over_voiced(project: Project, previous: Takes | None) -> None:
    """Raise when a build without voice would replace voiced takes, which only a paid voiced run can bring back."""
    voiced = sorted(k for k, entry in (previous.sections.items() if previous else ()) if entry.hash != "silent")
    if not voiced:
        return
    takes = project.takes_path
    where = takes.relative_to(project.root).as_posix() if takes.is_relative_to(project.root) else takes
    raise ConfigError(
        f"{where} holds voiced takes for sections {', '.join(voiced)}. A build without voice writes click tracks over "
        "those mp3 files and replaces the take index, so the next voiced build voices every section again and "
        "spends credits on all of them. Rehearse the build without voice in a copy of the project, or pass --force "
        "to replace the voiced takes."
    )


# ---- the take plan ----------------------------------------------------------------------

SYNTHESIZE = "synthesize"  # The section is sent to the voice, which spends credits.
CACHED = "cached"  # The take under the section's own file names is used as it is.
MOVED = "moved"  # The take of the same text under another section number is copied to this section.
UNKNOWN = "unknown"  # The provider could not be set up, so the cache key cannot be computed.


@dataclass
class TakePlan:
    """What a voiced narrate would do with one section, and why."""

    segment: Segment
    status: str
    reason: str = ""
    digest: str | None = None
    source_key: str | None = None  # The section key whose take a move copies.
    source: Take | None = None

    def to_dict(self, cfg: NarrationConfig) -> dict[str, Any]:
        seg = self.segment
        return {
            "key": seg.key,
            "chapter": seg.title,
            "file": seg.filename,
            "status": self.status,
            "reason": self.reason or None,
            "moved_from": self.source_key,
            "characters_sent": len(seg.tts_text(cfg)),
            "characters_spoken": len(seg.spoken),
            "word_count": seg.word_count,
            "estimated_seconds": seg.estimated_seconds(cfg),
            "placeholders": seg.placeholders,
        }


def plan_totals(plans: list[TakePlan], cfg: NarrationConfig) -> dict[str, int]:
    """How many sections have each status, and the characters of the sections that would be voiced."""
    totals = {status: sum(p.status == status for p in plans) for status in (SYNTHESIZE, CACHED, MOVED, UNKNOWN)}
    voiced = [p.segment for p in plans if p.status == SYNTHESIZE]
    totals["characters_sent"] = sum(len(s.tts_text(cfg)) for s in voiced)
    totals["characters_spoken"] = sum(len(s.spoken) for s in voiced)
    return totals


def _miss_reason(entry: Take | None, seg: Segment, digest: str, previous: Takes | None) -> str:
    if entry is None:
        if previous is not None and previous.estimated and seg.key in previous.sections:
            return "only a silent take exists"
        return "no take yet"
    if entry.hash != digest:
        return "the text, voice, model, or voice settings changed"
    if entry.file != seg.filename:
        return f"the file name changed from {entry.file}"
    return "the mp3 or the words file is missing"


def plan_takes(
    project: Project,
    targets: list[Segment],
    previous: Takes | None,
    *,
    provider: SpeechProvider | None,
    model: str,
    force: bool = False,
) -> list[TakePlan]:
    """What a voiced run would do with each target section. It sends nothing and writes nothing.

    The checks are narrate's own, in the same order: force, the cache under the section's file
    names, then a take of the same text under another section number. With no provider, a section
    that has no voiced take still needs one, and every other section is unknown.
    """
    cfg = project.settings.narration
    voice_settings = project.voice.api_settings()
    voiced = previous if previous is not None and not previous.estimated else None
    plans: list[TakePlan] = []
    for seg in targets:
        entry = voiced.sections.get(seg.key) if voiced else None
        if provider is None:
            if entry is None:
                plans.append(TakePlan(seg, SYNTHESIZE, _miss_reason(None, seg, "", previous)))
            else:
                plans.append(TakePlan(seg, UNKNOWN, "the voice is not set up, so the cache cannot be checked"))
            continue
        request = SpeechRequest(
            text=seg.tts_text(cfg), model=model, voice_settings=voice_settings, output_format=cfg.output_format
        )
        digest = text_hash(seg, cfg, provider.cache_key(request), voice_settings)
        if force:
            plans.append(TakePlan(seg, SYNTHESIZE, "forced", digest))
        elif is_cached(entry, seg, digest, project.narration_dir):
            plans.append(TakePlan(seg, CACHED, "", digest))
        elif (found := reusable_entry(previous, seg, digest, project.narration_dir)) is not None:
            key, source = found
            plans.append(TakePlan(seg, MOVED, f"the same text as section {int(key)}", digest, key, source))
        else:
            plans.append(TakePlan(seg, SYNTHESIZE, _miss_reason(entry, seg, digest, previous), digest))
    return plans


def narration_plan(
    project: Project, targets: list[Segment], *, model: str, force: bool = False
) -> tuple[list[TakePlan], str | None]:
    """(the take plan, why the provider could not be set up or None), for a dry run that needs no key."""
    try:
        provider: SpeechProvider | None = get_provider(project)
    except ConfigError as exc:
        provider, note = None, str(exc)
    else:
        note = None
    return plan_takes(project, targets, project.takes(), provider=provider, model=model, force=force), note


def build_timeline(project: Project, takes: Takes, order: list[Segment]) -> Timeline:
    cfg = project.settings.narration
    keys = [s.key for s in order if s.key in takes.sections]
    files = [project.narration_dir / takes.sections[k].file for k in keys]
    # A section's lead_seconds is silence joined in before its take, so the take and its cache stay as they are.
    leads = [project.lead_seconds(k) for k in keys]
    narration = project.narration_dir / "narration.mp3"
    audio.concat_audio(
        files, narration, bitrate=cfg.mp3_bitrate, sample_rate=project.settings.video.sample_rate, leads=leads
    )
    t = 0.0
    sections: dict[str, TimelineSection] = {}
    for k, f, lead in zip(keys, files, leads, strict=True):
        dur = lead + ffmpeg.decoded_duration(f, sample_rate=project.settings.video.sample_rate)
        words = project.section_words(k, takes.sections[k].words_file)
        sections[k] = TimelineSection(
            title=takes.sections[k].chapter,
            start=round(t, 3),
            end=round(t + dur, 3),
            duration=round(dur, 3),
            speech_end=round(t + words[-1].end, 3) if words else None,
            words=[Word(w.word, round(t + w.start, 3), round(t + w.end, 3)) for w in words],
            lead_seconds=lead,
        )
        t += dur
    timeline = Timeline(
        narration=narration.name,
        estimated=takes.estimated,
        total_seconds=ffmpeg.decoded_duration(narration, sample_rate=project.settings.video.sample_rate),
        sections=sections,
    )
    timeline.save(project.timeline_path)
    return timeline


# ---- entry -----------------------------------------------------------------------------


@dataclass
class NarrateResult:
    takes: Takes
    timeline: Timeline
    segments: list[Segment]  # the sections this run considered
    synthesized: list[str]  # keys that hit the API (or were regenerated silently)
    cached: list[str]


def narrate(
    project: Project,
    *,
    only: list[int] | None = None,
    force: bool = False,
    allow_placeholders: bool = False,
    silent: bool = False,
    model: str | None = None,
) -> NarrateResult:
    cfg = project.settings.narration
    all_segments, spoken = project.script_sections()
    model = model or project.voice.model or cfg.model
    voice_settings = project.voice.api_settings()
    targets = [s for s in spoken if not only or s.index in set(only)]
    if not targets:
        raise ConfigError(f"no spoken sections match {only}; spoken sections are {[s.index for s in spoken]}")
    previous = project.takes()
    if silent and not force:
        refuse_silent_over_voiced(project, previous)
    if project.clip_numbers:
        log.info("skipping clip sections (no narration): %s", sorted(project.clip_numbers))

    project.narration_dir.mkdir(parents=True, exist_ok=True)
    takes = Takes(
        script=str(project.script.relative_to(project.root))
        if project.script.is_relative_to(project.root)
        else str(project.script),
        model="silent-placeholder" if silent else model,
        output_format=cfg.output_format,
        estimated=silent,
        estimate_basis=f"{cfg.silent_words_per_minute} wpm + declared pauses" if silent else "",
    )
    if previous is not None and previous.estimated == silent:
        takes.sections = dict(previous.sections)

    provider: SpeechProvider | None = None
    if not silent:
        unfilled = sorted({p for s in targets for p in s.placeholders})
        if unfilled and not allow_placeholders:
            raise ConfigError(f"unfilled placeholders {unfilled} in the script; fill them or pass allow_placeholders")
        provider = get_provider(project)
        # A renumbered section keeps its take. Its files are copied to the new names before any
        # section is voiced, so a new take never replaces a file that a move still needs.
        moves: list[tuple[Take, Segment]] = []
        for plan in plan_takes(project, targets, previous, provider=provider, model=model, force=force):
            if plan.status == MOVED and plan.source is not None and plan.source_key is not None:
                moves.append((plan.source, plan.segment))
                log.info(
                    "[move] %s  from %s, the same text under section %s",
                    plan.segment.filename,
                    plan.source.file,
                    int(plan.source_key),
                )
        reuse_takes(moves, project.narration_dir)
        for entry, seg in moves:
            takes.sections[seg.key] = replace(
                entry, index=seg.index, chapter=seg.title, file=seg.filename, words_file=seg.words_filename
            )

    by_index = {s.index: s for s in all_segments}
    order = [s.index for s in all_segments]
    synthesized: list[str] = []
    cached: list[str] = []
    for seg in targets:
        out_path = project.narration_dir / seg.filename
        words_path = project.narration_dir / seg.words_filename
        scfg = section_config(project, seg)
        if silent:
            duration = seg.silent_seconds(scfg)
            words = estimated_words(seg, duration, scfg)
            audio.write_clicks(
                out_path,
                duration,
                [w.start for w in words],
                sample_rate=project.settings.video.sample_rate,
                bitrate=cfg.mp3_bitrate,
            )
            duration = ffmpeg.probe_duration(out_path)
            write_words(words_path, words)
            log.info("[sil ] %s  %d words -> %.2fs (estimated words)", seg.filename, seg.word_count, duration)
            takes.sections[seg.key] = Take(
                index=seg.index,
                chapter=seg.title,
                file=seg.filename,
                words_file=seg.words_filename,
                hash="silent",
                word_count=seg.word_count,
                estimated_seconds=seg.estimated_seconds(cfg),
                duration_seconds=duration,
                target_seconds=seg.target_seconds,
                speech_end_seconds=words[-1].end if words else None,
                spoken=seg.spoken,
            )
            synthesized.append(seg.key)
            continue
        assert provider is not None
        request = SpeechRequest(
            text=seg.tts_text(cfg),
            model=model,
            voice_settings=voice_settings,
            output_format=cfg.output_format,
        )
        digest = text_hash(seg, cfg, provider.cache_key(request), voice_settings)
        entry = takes.sections.get(seg.key)
        if not force and entry is not None and is_cached(entry, seg, digest, project.narration_dir):
            # min_tail_seconds is not part of the hash, so a cached take made under a shorter
            # tail is padded here. ensure_tail measures the silence first, so a take that
            # already has enough is left untouched. A take that narrate already padded keeps
            # its tail when the measurement lands within a frame of min_tail_seconds, so a
            # rounding difference never pads it again on every run.
            tolerance = audio.SILENCE_END_TOLERANCE_SECONDS if entry.tail_padded_seconds else 0.0
            added = ensure_tail(out_path, scfg, tolerance=tolerance)
            if added:
                entry.duration_seconds = ffmpeg.probe_duration(out_path)
                entry.tail_padded_seconds = round((entry.tail_padded_seconds or 0.0) + added, 3)
                log.info("[skip] %s  unchanged, tail +%ss (%.2fs)", seg.filename, added, entry.duration_seconds)
            else:
                log.info("[skip] %s  unchanged (%.2fs)", seg.filename, entry.duration_seconds)
            # A cached entry carries its spoken text forward, since the text is part of the hash
            # and so cannot have changed.
            entry.spoken = seg.spoken
            cached.append(seg.key)
            continue
        pos = order.index(seg.index)
        prev_seg = by_index[order[pos - 1]] if pos > 0 else None
        next_seg = by_index[order[pos + 1]] if pos + 1 < len(order) else None
        log.info("[tts ] %s  %d words, est %.1fs ...", seg.filename, seg.word_count, seg.estimated_seconds(cfg))
        request = SpeechRequest(
            text=request.text,
            model=model,
            voice_settings=voice_settings,
            output_format=cfg.output_format,
            previous_text=prev_seg.spoken if prev_seg else None,
            next_text=next_seg.spoken if next_seg else None,
        )
        mp3, words = provider.speak(request)
        out_path.write_bytes(mp3)
        if seg.first_spoken and cfg.opening_silence_seconds > 0:
            audio.pad_head(out_path, cfg.opening_silence_seconds, bitrate=cfg.mp3_bitrate)
            words = [
                Word(
                    w.word,
                    round(w.start + cfg.opening_silence_seconds, 3),
                    round(w.end + cfg.opening_silence_seconds, 3),
                )
                for w in words
            ]
        write_words(words_path, words)
        added = ensure_tail(out_path, scfg)
        duration = ffmpeg.probe_duration(out_path)
        speech_end = words[-1].end if words else None
        log.info(
            "       %.2fs (%d words, speech ends %s%s)",
            duration,
            len(words),
            speech_end,
            f", tail +{added}s" if added else "",
        )
        takes.sections[seg.key] = Take(
            index=seg.index,
            chapter=seg.title,
            file=seg.filename,
            words_file=seg.words_filename,
            hash=digest,
            word_count=seg.word_count,
            estimated_seconds=seg.estimated_seconds(cfg),
            duration_seconds=duration,
            target_seconds=seg.target_seconds,
            speech_end_seconds=speech_end,
            tail_padded_seconds=added,
            spoken=seg.spoken,
        )
        takes.save(project.takes_path)  # checkpoint after every paid call
        synthesized.append(seg.key)

    valid = {s.key for s in spoken}
    takes.sections = {k: v for k, v in takes.sections.items() if k in valid}
    takes.save(project.takes_path)
    missing = [s.key for s in spoken if s.key not in takes.sections]
    if missing:
        log.warning("sections %s have no narration yet; the timeline covers the rest", missing)
    timeline = build_timeline(project, takes, spoken)
    return NarrateResult(takes=takes, timeline=timeline, segments=targets, synthesized=synthesized, cached=cached)
