"""Tables the CLI prints from stage results. Internal."""

from __future__ import annotations

from .artifacts import Timeline
from .stages.beats import BeatsResult
from .stages.measure import LeadMeasurement, RecordingCheck
from .stages.narrate import NarrateResult, Segment
from .stages.soundscape import SoundscapeItem
from .stages.verify import VerifyResult
from .status import StatusReport, relpath
from .verdicts import BLACK, OK, QUIET, SKIPPED, SPEECH_AT_CUT


def mmss(seconds: float | None) -> str:
    if seconds is None:
        return "  --  "
    whole = int(round(seconds))  # round first, so 179.6 s reads 3:00, not 2:60
    return f"{whole // 60}:{whole % 60:02d}"


def segments_table(segments: list[Segment], wpm: int, result: NarrateResult | None = None) -> str:
    lines = [f"{'#':>2}  {'section':<22} {'words':>5}  {'est':>5}  {'target':>6}  {'actual':>6}  placeholders"]
    lines.append("-" * len(lines[0]))
    total_words = total_est = total_actual = 0.0
    for seg in segments:
        est = seg.word_count / wpm * 60
        total_words += seg.word_count
        total_est += est
        actual = None
        if result is not None:
            entry = result.manifest.segments.get(seg.key)
            if entry:
                actual = entry.duration_seconds
                total_actual += actual
        ph = ",".join(seg.placeholders) if seg.placeholders else "-"
        lines.append(
            f"{seg.index:>2}  {seg.slug[:22]:<22} {seg.word_count:>5}  {mmss(est):>5}  "
            f"{mmss(seg.target_seconds):>6}  {mmss(actual):>6}  {ph}"
        )
    lines.append("-" * len(lines[0]))
    actual_total = mmss(total_actual) if total_actual else "  --  "
    lines.append(f"{'':>2}  {'total':<22} {int(total_words):>5}  {mmss(total_est):>5}  {'':>6}  {actual_total:>6}")
    return "\n".join(lines)


def timeline_table(timeline: Timeline) -> str:
    lines = [f"{'#':>3}  {'section':<22} {'start':>7} {'end':>7} {'length':>7}"]
    for key, sec in timeline.sections.items():
        lines.append(
            f"{int(key):>3}  {sec.title[:22]:<22} {mmss(sec.start):>7} {mmss(sec.end):>7} {sec.duration:>7.1f}"
        )
    est = "  (estimated: silent placeholders)" if timeline.estimated else ""
    lines.append(f"     narration total {mmss(timeline.total_seconds)}{est}")
    return "\n".join(lines)


def beats_table(result: BeatsResult) -> str:
    lines = [f"{'sec':>3}  {'speech':>6}  {'need':>5}  cues"]
    for s in result.sections:
        if s.skipped:
            lines.append(f"{s.key:>3}  {'--':>6}  {s.min_seconds or '-':>5}  ({s.skipped})")
            continue
        cues = ",".join(f"{k}@{v}" for k, v in s.resolved.items()) or "-"
        lines.append(f"{s.key:>3}  {s.speech_end:>6.1f}  {str(s.min_seconds or '-'):>5}  {cues}")
        for note in s.notes:
            lines.append(f"{'':>3}  {'':>6}  {'':>5}  ! {note}")
    tail = f"{len(result.beats.sections)} sections with cues; {result.unresolved} unresolved"
    if result.estimated:
        tail += "  (estimated words: times are placeholders)"
    lines.append(tail)
    return "\n".join(lines)


def leads_table(rows: list[LeadMeasurement]) -> str:
    lines = [f"{'sec':>3} {'trim':>7} {'wall':>7}  method"]
    lines += [f"{r.key:>3} {r.lead_in_seconds:>7.3f} {r.wallclock_seconds:>7.3f}  {r.method}" for r in rows]
    return "\n".join(lines)


def checks_table(rows: list[RecordingCheck]) -> str:
    lines = [f"{'sec':<4} {'webm_s':<8} {'want_s':<8} {'Y10':<6} {'Y50':<6} {'Y90':<6} {'MAX50':<6}  verdict"]
    for r in rows:
        lines.append(
            f"{r.key:<4} {r.duration:<8.1f} {r.wanted:<8.1f} {r.y10:<6.0f} {r.y50:<6.0f} {r.y90:<6.0f} "
            f"{r.max50:<6.0f}  {r.verdict}"
        )
    return "\n".join(lines)


def verify_table(result: VerifyResult) -> str:
    lines = [f"{'sec':>3} {'start':>8} {'probe':>8} {'YAVG':>6} {'YMAX':>6}  result"]
    for s in result.starts:
        lines.append(
            f"{s.key:>3} {s.start:>8.2f} {s.probe_at:>8.2f} {s.yavg:>6.0f} {s.ymax:>6.0f}  {OK if s.ok else BLACK}"
        )
    lines.append(f"total {result.total_seconds:.2f}s; {result.black_starts} black section start(s)")
    if result.cuts:
        lines.append("")
        lines.append(f"{'sec':>3} {'cut at':>8} {'before cut':>11}  result")
        for c in result.cuts:
            lines.append(f"{c.key:>3} {c.cut_at:>8.2f} {c.rms_db:>8.1f} dB  {QUIET if c.ok else SPEECH_AT_CUT}")
    if result.carries:
        lines.append("")
        lines.append(f"{'sec':>3} {'cut at':>8} {'chg %':>7}  result")
        for k in result.carries:
            lines.append(f"{k.key:>3} {k.cut_at:>8.2f} {k.changed_percent:>7.2f}  {k.verdict}")
    if result.cues:
        lines.append("")
        av = any(c.av_ms is not None for c in result.cues)
        head = f"{'check':<18} {'cue':>6} {'at':>8} {'chg %':>7} {'ctl %':>7} {'offset':>8}"
        lines.append(head + (f" {'a/v':>7}" if av else "") + "  result")
        for c in result.cues:
            if c.changed_percent is None or c.verdict == SKIPPED:
                # A row that was never measured shows its verdict, its reason code, and its note.
                cue = f"{c.cue_seconds:>6.2f}" if c.cue_seconds is not None else f"{'-':>6}"
                # A skipped row's note already starts with its verdict and reason, so print it alone.
                if c.note and c.note.startswith(c.verdict):
                    label = c.note
                else:
                    label = " ".join(part for part in (c.verdict, c.reason, c.note) if part)
                lines.append(
                    f"{c.check:<18} {cue} {'-':>8} {'-':>7} {'-':>7} {'-':>8}"
                    + (f" {'-':>7}" if av else "")
                    + f"  {label}"
                )
                continue
            offset = f"{c.offset_ms:+d}ms" if c.offset_ms is not None else "-"
            lines.append(
                f"{c.check:<18} {c.cue_seconds:>6.2f} {c.final_seconds or 0:>8.2f} {c.changed_percent or 0:>7.2f} "
                f"{c.control_percent or 0:>7.2f} {offset:>8}"
                + (f" {(f'{c.av_ms:+d}ms' if c.av_ms is not None else '-'):>7}" if av else "")
                + f"  {c.verdict}"
            )
    return "\n".join(lines)


def status_table(report: StatusReport) -> str:
    """The text of `decktalk status`, read from the same report its --json output prints."""
    root = report.root
    lines = [
        f"project  {root}  (name: {report.name})",
        f"script   {relpath(report.script, root)}  {'ok' if report.script_exists else 'MISSING'}",
        f"cues     {relpath(report.cues, root)}  {'ok' if report.cues_exists else 'none'}",
    ]
    for sec in report.sections:
        what = f"clip {sec.source}" if sec.kind == "clip" else sec.source
        lines.append(f"  {sec.key}  {what:<40} {'rec ' if sec.recorded else '    '}{'cut' if sec.cut else ''}")
    lines.append(timeline_table(report.timeline) if report.timeline else "timeline none (run `decktalk narrate`)")
    lines.append(
        f"beats    {len(report.beats_sections)} section(s) with resolved cues"
        if report.beats_sections
        else "beats    none (run `decktalk beats`)"
    )
    if report.final_exists:
        lines.append(f"final    {relpath(report.final, root)}  {mmss(report.final_duration)}")
    else:
        lines.append("final    not built")
    for out in report.outputs:
        lines.append(f"{out.label:<8} {relpath(out.path, root)}  {'ok' if out.exists else 'not built'}")
    return "\n".join(lines)


def soundscape_table(items: list[SoundscapeItem]) -> str:
    lines = []
    for it in items:
        dur = f" ({it.duration_seconds}s)" if it.duration_seconds else ""
        lines.append(f"== {it.name} -> {it.out}  [{it.status}{dur}]")
        lines.append(f"   POST {it.endpoint}")
        for r in it.requests:
            lines.append(f"   {r}")
    return "\n".join(lines) or "nothing to generate"
