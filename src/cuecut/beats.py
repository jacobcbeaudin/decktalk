"""Resolve cue phrases to narration timestamps: cues.json + words.json -> beats.json.

cues.json shape
    {"sections": {"3": {"min_seconds": 25,
                        "cues": [{"step": "3.2", "on": "On a typical"},
                                 {"step": "3.x", "on": "Zero", "occurrence": 2, "case_sensitive": true},
                                 {"step": "15.2", "on": "$end", "offset": 0.3}]}}}

    step    a cue id the page understands (see cuecut-runtime.js)
    on      a word or short phrase from that section's narration: first occurrence,
            case-insensitive, punctuation ignored. "$start" = 0, "$end" = end of speech.
    occurrence / case_sensitive / offset (seconds) refine the match.

Output build/audio/beats.json: {"03": "3.2@11.42,3.x@9.30", ...}, times relative to the
section's start; the recorder passes it to the page as ?beats=. Unresolved cues are
reported and left out; the recording still runs.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .project import Project

Word = dict[str, Any]


def norm(token: str, case_sensitive: bool = False) -> str:
    token = re.sub(r"[^0-9A-Za-z']", "", token)
    return token if case_sensitive else token.lower()


def find_phrase(words: list[Word], phrase: str, occurrence: int = 1, case_sensitive: bool = False) -> int | None:
    """Index of the first word of the n-th occurrence of phrase, or None."""
    target = [t for t in (norm(t, case_sensitive) for t in phrase.split()) if t]
    if not target:
        return None
    normalized = [norm(w["word"], case_sensitive) for w in words]
    seen = 0
    for i in range(len(normalized) - len(target) + 1):
        if normalized[i : i + len(target)] == target:
            seen += 1
            if seen == occurrence:
                return i
    return None


def resolve_cue(cue: dict[str, Any], words: list[Word]) -> float | None:
    on = str(cue["on"])
    offset = float(cue.get("offset", 0))
    if on == "$start":
        return round(max(0.0, offset), 2)
    if on == "$end":
        return round(float(words[-1]["end"]) + offset, 2) if words else None
    idx = find_phrase(words, on, int(cue.get("occurrence", 1)), bool(cue.get("case_sensitive", False)))
    if idx is None:
        return None
    return round(float(words[idx]["start"]) + offset, 2)


def load_words(audio_dir: Path, entry: dict[str, Any]) -> list[Word]:
    words_file = entry.get("words_file")
    if not words_file:
        return []
    path = audio_dir / words_file
    return json.loads(path.read_text()) if path.exists() else []


def parse_beats_string(value: str) -> dict[str, float]:
    """'a@1.5,b@2' -> {'a': 1.5, 'b': 2.0}."""
    out: dict[str, float] = {}
    for item in value.split(","):
        item = item.strip()
        if not item or "@" not in item:
            continue
        cue_id, _, t = item.rpartition("@")
        try:
            out[cue_id] = float(t)
        except ValueError:
            continue
    return out


def beats(project: Project) -> int:
    if not project.cues.exists():
        print(f"note: no cues file at {project.cues}; pages will run their built-in timing")
        project.beats.write_text("{}\n")
        return 0
    manifest = project.manifest_data()
    if not manifest:
        raise SystemExit(f"error: manifest not found: {project.manifest} (run `cuecut narrate` first)")
    cues = json.loads(project.cues.read_text()).get("sections", {})
    segments = manifest.get("segments", {})
    estimated = bool(manifest.get("estimated"))

    out: dict[str, str] = {}
    problems = 0
    print(f"{'sec':>3}  {'speech':>6}  {'need':>5}  cues")
    for num in sorted(cues, key=int):
        key = f"{int(num):02d}"
        spec = cues[num]
        entry = segments.get(key)
        if not entry:
            print(
                f"{key:>3}  {'--':>6}  {spec.get('min_seconds', '-'):>5}  (no narration; video section or not rendered)"
            )
            continue
        words = load_words(project.audio_dir, entry)
        duration = float(entry.get("duration_seconds", 0))
        speech_end = float(words[-1]["end"]) if words else duration
        resolved: list[str] = []
        notes: list[str] = []
        for cue in spec.get("cues", []):
            if not words and cue["on"] != "$start":
                notes.append(f"{cue['step']}: no words.json ({'estimated manifest' if estimated else 'missing'})")
                problems += 1
                continue
            t = resolve_cue(cue, words)
            if t is None:
                notes.append(f"{cue['step']}: phrase not found: {cue['on']!r}")
                problems += 1
                continue
            if t > duration:
                notes.append(f"{cue['step']}: {t}s is past the end of the audio ({duration}s)")
            resolved.append(f"{cue['step']}@{t}")
        need = spec.get("min_seconds")
        if need is not None and speech_end < float(need):
            notes.append(
                f"speech {speech_end:.1f}s is {float(need) - speech_end:.1f}s shorter than the visuals need ({need}s)"
            )
        if resolved:
            out[key] = ",".join(resolved)
        print(f"{key:>3}  {speech_end:>6.1f}  {str(need or '-'):>5}  {out.get(key, '-')}")
        for note in notes:
            print(f"{'':>3}  {'':>6}  {'':>5}  ! {note}")

    project.beats.write_text(json.dumps(out, indent=2) + "\n")
    est = "  (estimated words: times are placeholders)" if estimated else ""
    print(f"\nwrote {project.beats}  ({len(out)} sections with cues; {problems} unresolved){est}")
    return 0
