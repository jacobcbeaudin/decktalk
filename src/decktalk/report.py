"""The tables the CLI prints from stage results.

Every table reads one result object, so the text output and the `--json` payload can never
disagree. Internal: nothing here is in `decktalk.__all__`.
"""

from __future__ import annotations

from .artifacts import Timeline
from .jsonio import relative
from .model.script import Segment
from .settings import NarrationConfig
from .stages.align import AlignResult
from .stages.clip import SectionWords
from .stages.narrate import NarrateResult, TakePlan, plan_totals
from .stages.preflight import PreflightResult
from .stages.record import RecordResult
from .stages.soundscape import SoundscapeItem
from .stages.verify import VerifyResult
from .status import StatusResult
from .verdicts import Verdict


def mmss(seconds: float | None) -> str:
    if seconds is None:
        return "  --  "
    whole = int(round(seconds))  # round first, so 179.6 s reads 3:00, not 2:60
    return f"{whole // 60}:{whole % 60:02d}"


def narrate_table(result: NarrateResult) -> str:
    """One narrate run: the sections, the plan with its price, and the narration clock it wrote."""
    wpm = result.narration.words_per_minute
    parts = [
        segments_table(result.segments, wpm, result),
        "",
        plan_table(result.plans, result.narration, result.rate, result.note),
    ]
    for row in result.rows:
        parts.append(f"! {row.verdict.value} section {row.section}: {row.detail}")
    if result.timeline is not None:
        parts += ["", timeline_table(result.timeline)]
    return "\n".join(parts)


def segments_table(segments: list[Segment], wpm: int, result: NarrateResult | None = None) -> str:
    lines = [f"{'#':>2}  {'section':<22} {'words':>5}  {'est':>5}  {'target':>6}  {'actual':>6}  placeholders"]
    lines.append("-" * len(lines[0]))
    total_words = total_est = total_actual = 0.0
    for seg in segments:
        est = seg.word_count / wpm * 60
        total_words += seg.word_count
        total_est += est
        actual = None
        if result is not None and result.takes is not None:
            entry = result.takes.sections.get(seg.key)
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


def plan_table(plans: list[TakePlan], cfg: NarrationConfig, rate: float = 0.0, note: str | None = None) -> str:
    """What a run would do with each section: voice it, or play the take of that text it already holds."""
    lines = [f"{'#':>2}  {'section':<22} {'take':<10} {'sent':>6} {'spoken':>6}  reason"]
    lines.append("-" * len(lines[0]))
    for p in plans:
        seg = p.segment
        lines.append(
            f"{seg.index:>2}  {seg.slug[:22]:<22} {p.status:<10} {p.characters_sent:>6} "
            f"{len(seg.spoken):>6}  {p.reason or '-'}"
        )
    t = plan_totals(plans, cfg, rate)
    lines.append("-" * len(lines[0]))
    unknown = f", {t['unknown']} unknown" if t["unknown"] else ""
    cost = ""
    if rate:
        cost = f" About ${t['estimated_cost']:.2f} at ${rate:.2f} per 1,000."
        if t["most_it_can_cost"] != t["estimated_cost"]:
            cost += f" Up to ${t['most_it_can_cost']:.2f} if the {t['unknown']} unknown section(s) are voiced too."
    lines.append(
        f"voice {t['synthesize']} section(s): {t['characters_sent']} characters sent, "
        f"{t['characters_spoken']} spoken, {t['characters_with_context']} with context. "
        f"{t['cached']} cached{unknown}.{cost}"
    )
    if note:
        lines.append(f"note: {note}")
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


def align_table(result: AlignResult) -> str:
    lines = [f"{'sec':>3}  {'speech':>6}  {'need':>5}  cues"]
    for s in result.sections:
        if s.skipped:
            lines.append(f"{s.key:>3}  {'--':>6}  {s.min_seconds or '-':>5}  ({s.skipped})")
            continue
        cues = ",".join(f"{r.cue}@{r.at}" for r in s.resolved) or "-"
        lines.append(f"{s.key:>3}  {s.speech_end:>6.1f}  {str(s.min_seconds or '-'):>5}  {cues}")
        for note in s.notes:
            lines.append(f"{'':>3}  {'':>6}  {'':>5}  ! {note}")
    tail = f"{len(result.cue_times.sections)} sections with cues; {result.unresolved} unresolved"
    if result.estimated:
        tail += "  (estimated words: times are placeholders)"
    lines.append(tail)
    return "\n".join(lines)


def preflight_table(result: PreflightResult) -> str:
    """The take plan, the cues resolved on the words each section will have, and the frozen-frame estimates."""
    root = result.root
    voice = result.voice
    lines = [f"voice: provider={voice['provider']} model={voice['model']}"]
    lines.append(plan_table(result.takes, result.narration, result.rate, result.note))
    if result.placeholders:
        lines.append(f"a voiced run refuses the unfilled placeholders {result.placeholders}")
    lines += ["", align_table(result.align)]
    if result.estimated:
        lines.append(f"estimated words in sections {', '.join(result.estimated)}, which a voiced run will voice")
    if result.frames is None:
        lines += ["", "frames skipped (--no-frames)"]
        return "\n".join(lines)
    lines.append("")
    lines.append(f"{'check':<18} {'slide':<8} {'cue':>6} {'chg %':>7}  result")
    for c in result.cues:
        chg = f"{c.changed_percent:>7.2f}" if c.changed_percent is not None else f"{'-':>7}"
        label = " ".join(part for part in (c.verdict, c.reason) if part)
        label += f": {c.detail}" if c.detail else ""
        label += f" ({c.note})" if c.note else ""
        lines.append(f"{c.check:<18} {c.slide or '-':<8} {c.cue_seconds:>6.2f} {chg}  {label}")
    judged = (Verdict.CHANGED, Verdict.THIN_CHANGE, Verdict.NO_CHANGE, Verdict.SKIPPED)
    tally = {v: sum(c.verdict == v for c in result.cues) for v in judged}
    counts = ", ".join(f"{n} {v}" for v, n in tally.items() if n) or "none"
    where = relative(result.frames, root) if root is not None else result.frames
    lines.append(f"{len(result.cues)} cue(s): {counts}. Frozen frames in {where}")
    if result.seams:
        lines.append("")
        lines.append(f"{'sec':>3} {'chg %':>7}  result")
        for k in result.seams:
            chg = f"{k.changed_percent:>7.2f}" if k.changed_percent is not None else f"{'-':>7}"
            label = " ".join(part for part in (k.verdict, k.reason) if part) + (f": {k.detail}" if k.detail else "")
            lines.append(f"{k.key:>3} {chg}  {label}")
    return "\n".join(lines)


def record_table(result: RecordResult) -> str:
    """One row per section: how long it ran, where narration t=0 landed, how bright it is, and its verdicts."""
    head = f"{'sec':<4} {'webm_s':<8} {'want_s':<8} {'t0_s':<7} {'Y10':<6} {'Y50':<6} {'Y90':<6} {'MAX50':<6}  result"
    lines = [head]
    kept = [row.key for row in result.kept_sections]
    for row in result.sections:
        checks = row.log.checks
        luma = checks.luma if checks else None
        lines.append(
            f"{row.key:<4} {checks.duration_seconds if checks else 0:<8.1f} "
            f"{checks.wanted_seconds if checks else 0:<8.1f} {row.log.trim_seconds:<7.3f} "
            f"{luma.y10 if luma else 0:<6.0f} {luma.y50 if luma else 0:<6.0f} {luma.y90 if luma else 0:<6.0f} "
            f"{luma.max50 if luma else 0:<6.0f}  {row.label}"
        )
    if kept:
        lines.append(f"kept {len(kept)} unchanged section(s): {', '.join(kept)}")
    for row in result.sections:
        for message in row.log.page_errors:
            lines.append(f"{row.key:<4} page error: {message}")
    guessed = [row.key for row in result.sections if row.log.t0_guessed]
    if guessed:
        lines.append(f"narration t=0 is a guess in section(s) {', '.join(guessed)}")
    return "\n".join(lines)


def verify_table(result: VerifyResult) -> str:
    lines: list[str] = []
    if result.recordings:
        lines.append(f"{'sec':>3}  recording")
        for r in result.recordings:
            label = " ".join(v.value for v in r.verdicts) or str(Verdict.OK)
            lines.append(f"{r.key:>3}  {label}")
        for r in result.recordings:
            for message in r.page_errors:
                lines.append(f"{r.key:>3}  page error: {message}")
        lines.append("")
    lines.append(f"{'sec':>3} {'start':>8} {'probe':>8} {'YAVG':>6} {'YMAX':>6}  result")
    for s in result.starts:
        lines.append(f"{s.key:>3} {s.start:>8.2f} {s.probe_at:>8.2f} {s.yavg:>6.0f} {s.ymax:>6.0f}  {s.verdict}")
    lines.append(f"total {result.total_seconds:.2f}s; {result.black_starts} black section start(s)")
    if result.cuts:
        lines.append("")
        lines.append(f"{'sec':>3} {'cut at':>8} {'before cut':>11}  result")
        for c in result.cuts:
            lines.append(f"{c.key:>3} {c.cut_at:>8.2f} {c.rms_db:>8.1f} dB  {c.verdict}")
    if result.seams:
        lines.append("")
        lines.append(f"{'sec':>3} {'cut at':>8} {'chg %':>7}  result")
        for k in result.seams:
            lines.append(f"{k.key:>3} {k.cut_at:>8.2f} {k.changed_percent:>7.2f}  {k.verdict}")
    if result.cues:
        lines.append("")
        av = any(c.av_ms is not None for c in result.cues)
        head = f"{'check':<18} {'cue':>6} {'at':>8} {'chg %':>7} {'ctl %':>7} {'offset':>8}"
        lines.append(head + (f" {'a/v':>7}" if av else "") + "  result")
        for c in result.cues:
            if c.changed_percent is None or c.verdict == Verdict.SKIPPED:
                # A row that was never measured shows its verdict, its reason code, and its note.
                cue = f"{c.cue_seconds:>6.2f}" if c.cue_seconds is not None else f"{'-':>6}"
                # A skipped row's note already starts with its verdict and reason, so print it alone.
                if c.note and c.verdict is not None and c.note.startswith(c.verdict):
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


def status_table(report: StatusResult) -> str:
    """The text of `decktalk status`, read from the same report its --json output prints."""
    root = report.root
    lines = [
        f"project   {root}  (name: {report.name})",
        f"script    {relative(report.script, root)}  {'ok' if report.script_exists else 'MISSING'}",
        f"cues      {relative(report.cues, root)}  {'ok' if report.cues_exists else 'none'}",
    ]
    for sec in report.sections:
        what = f"clip {sec.source}" if sec.kind == "clip" else sec.source
        lines.append(f"  {sec.key}  {what:<40} {'rec ' if sec.recorded else '    '}{'cut' if sec.cut else ''}")
    lines.append(timeline_table(report.timeline) if report.timeline else "timeline  none (run `decktalk narrate`)")
    lines.append(
        f"cue times {len(report.cue_times_sections)} section(s) resolved"
        if report.cue_times_sections
        else "cue times none (run `decktalk align`)"
    )
    if report.final_exists:
        lines.append(f"final     {relative(report.final, root)}  {mmss(report.final_duration)}")
    else:
        lines.append("final     not built")
    for out in report.outputs:
        lines.append(f"{out.label:<9} {relative(out.path, root)}  {'ok' if out.exists else 'not built'}")
    return "\n".join(lines)


def words_table(sections: list[SectionWords]) -> str:
    """The text of `decktalk words`: each section's words, in seconds after the section starts."""
    lines: list[str] = []
    for sec in sections:
        notes = [f"{sec.duration:.2f}s"]
        if sec.lead_seconds:
            notes.append(f"lead {sec.lead_seconds:g}s")
        if sec.estimated:
            notes.append("estimated")
        if lines:
            lines.append("")
        lines.append(f"== {sec.key} {sec.title}  ({', '.join(notes)})")
        lines.append("  start     end  word")
        lines += [f"{w.start:7.3f} {w.end:7.3f}  {text}" for w, text in zip(sec.words, sec.texts, strict=True)]
    return "\n".join(lines) or "no spoken sections"


def soundscape_table(items: list[SoundscapeItem]) -> str:
    lines = []
    for it in items:
        dur = f" ({it.duration_seconds}s)" if it.duration_seconds else ""
        lines.append(f"== {it.name} -> {it.out}  [{it.status}{dur}]")
        lines.append(f"   POST {it.endpoint}")
        for r in it.requests:
            lines.append(f"   {r}")
    return "\n".join(lines) or "nothing to generate"
