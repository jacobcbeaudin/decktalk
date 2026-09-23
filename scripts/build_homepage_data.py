# /// script
# requires-python = ">=3.12"
# dependencies = ["pillow>=10"]
# ///
"""Generate the homepage's data from the Halfway build, so the page cannot drift from the film.

    uv run scripts/build_homepage_data.py --project ~/films/halfway/hero --hero-log ~/films/halfway/hero/build-fixed.log \
        --takes ~/films/halfway/build/narration --film ~/films/halfway --rebuild-log ~/films/halfway/build-edit.log

The hero cut is its own DeckTalk project (sections 1 to 3 shared with the full film, section 4
trimmed). From it and the full film's voiced takes this writes:

- site/data.js        the words with their times, the cues with their times and what each one shows,
                      the band lines, the hero cut's own verify offsets, the rebuild log with its cost and
                      the full film's verify offsets, in film seconds
- site/stage.js       the deck's scenes and styles, scoped, so the hero replays the production slides
                      as HTML and SVG under the real take rather than as a captured video
- site/media/halfway-hero.mp3         the four takes laid out as the film lays them out, at -16 LUFS
- site/media/halfway-take-before.mp3  the section 3 take before the edit, at -16 LUFS
- site/media/halfway-take-after.mp3   the section 3 take after the edit, at -16 LUFS
- site/media/halfway-poster.webp      the `1.1forty` frame of the film, for the card
- site/media/halfway.vtt              the film's captions, copied so the text track is same-origin

data.js also carries each clip's loudness envelope, RMS in dB every 20 ms measured from the clip's own
bytes, so the page's waveform glyphs move with the real sound at any playhead position.

The mp3s are not in git. The page streams them from media.decktalk.ai under names that carry the first
twelve hex digits of their SHA-256, like the films, and data.js carries those names next to the clips'
envelopes, so a clip and its name change together. The script prints the upload list at the end.

Every take is matched to its section by its words, so the script decides which take plays. The hero
cut's last section, "Halfway. Meet in the middle.", is the tail of the full film's section 4 take,
clipped at the word "Halfway", because sections that share their words share their takes.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import math
import os
import re
import shutil
import struct
import subprocess
import sys
import tomllib
from itertools import combinations
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SITE = ROOT / "site"
FFMPEG = os.environ.get("DECKTALK_FFMPEG") or next(
    (str(p) for p in sorted(Path.home().glob("Library/Caches/decktalk/ffmpeg/*/ffmpeg"))), "ffmpeg"
)
ENVELOPE_STEP = 0.02  # seconds per envelope sample
ENVELOPE_FLOOR = -60  # dB, silence
MEDIA = SITE / "media"
FPS = 25
TAKE_TAIL = 0.5  # seconds of silence after the last sound of each take on the page
CONTEXT_CHARS = 46  # of a changed line, the words either side of the change that the edit strip shows
MAX_LINE_CHARS = 44  # a band line fits the phone band at 17 px, so the dimmed previous line never needs an ellipsis
DANGLING = {"a", "an", "the", "and", "or", "but", "of", "to", "for", "in", "on", "at", "with", "by"}


def norm(word: str) -> str:
    return re.sub(r"[^a-z0-9]", "", word.lower())


def read_script(path: Path) -> list[dict]:
    """The spoken sections: number, chapter, and the text with [beat] kept and other directions dropped."""
    sections = []
    for m in re.finditer(
        r"^## (\d+)\.\s*(.+?)\s*$\n(.*?)(?=^## \d+\.|\Z)", path.read_text(encoding="utf-8"), re.M | re.S
    ):
        body = re.sub(r"\[(?!beat\])[^\]]*\]", "", m.group(3))  # drop [Scene …] and other notes, keep [beat]
        text = " ".join(body.split())
        sections.append({"n": int(m.group(1)), "chapter": m.group(2), "md": text})
    return sections


def script_words(md: str) -> list[str]:
    return [w for w in md.split() if w != "[beat]"]


def find_take(takes: Path, words: list[str]) -> tuple[Path, list[dict], int]:
    """The voiced take whose words end with these words: (mp3, its word list, index of the first word)."""
    keys = [norm(w) for w in words]
    best = None
    for wf in sorted(takes.glob("*.words.json")):
        if wf.name.startswith("silent-"):
            continue
        data = json.loads(wf.read_text(encoding="utf-8"))
        have = [norm(w["word"]) for w in data]
        if have == keys:
            return wf.with_suffix("").with_suffix(".mp3"), data, 0
        if len(have) > len(keys) and have[-len(keys) :] == keys and best is None:
            best = (wf.with_suffix("").with_suffix(".mp3"), data, len(have) - len(keys))
    if best is None:
        sys.exit(f"no voiced take in {takes} says: {' '.join(words)}")
    return best


def sound_end(mp3: Path, start: float) -> float:
    """Where the take's sound ends, in take seconds after `start`: the start of the silence that runs to
    its end, measured with the threshold, run and end tolerance decktalk.media.audio.sound_end uses, so
    the page's sections are as long as the film's."""
    out = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-nostats",
            "-i",
            str(mp3),
            "-af",
            "silencedetect=noise=-35dB:d=0.05",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stderr
    duration = float(
        subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(mp3)],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    )
    starts = re.findall(r"silence_start: ([0-9.]+)", out)
    ends = re.findall(r"silence_end: ([0-9.]+)", out)
    rate = re.search(r"Audio: [^\n]*?(\d+) Hz", out)
    tolerance = max(0.06, 1152 / int(rate.group(1)) if rate else 0.0)
    if starts and (len(ends) < len(starts) or float(ends[-1]) >= duration - tolerance):
        return round(float(starts[-1]) - start, 3)
    return round(duration - start, 3)


def frames(seconds: float) -> float:
    return round(seconds * FPS) / FPS


def band_lines(md: str, first: int) -> list[list[int]]:
    """Word index ranges for the band: one per sentence, and a sentence longer than MAX_LINE_CHARS broken
    into the fewest lines that each fit, as balanced as they can be, at a beat or after a comma when one
    lies there, so the line under the frame is always whole and the callback word is never cut."""
    lines: list[list[int]] = []
    sentence: list[tuple[int, str, bool]] = []  # (word index, word, a beat before it)
    i, beat = first, False

    def flush() -> None:
        nonlocal sentence
        if sentence:
            lines.extend(break_sentence(sentence))
        sentence = []

    for unit in [u for u in re.split(r"(\[beat\])", md) if u.strip()]:
        if unit == "[beat]":
            beat = True
            continue
        for w in unit.split():
            sentence.append((i, w, beat))
            beat = False
            i += 1
            if re.search(r"[.!?]$", w):
                flush()
    flush()
    return lines


def break_sentence(sentence: list[tuple[int, str, bool]]) -> list[list[int]]:
    words = [w for _, w, _ in sentence]
    n = len(words)

    def length(a: int, b: int) -> int:
        return len(" ".join(words[a:b]))

    if length(0, n) <= MAX_LINE_CHARS:
        return [[sentence[0][0], sentence[-1][0]]]
    for k in range(2, n + 1):
        best: tuple[tuple[int, int, int], list[tuple[int, int]]] | None = None
        for cuts in combinations(range(1, n), k - 1):
            bounds = [0, *cuts, n]
            runs = [(bounds[j], bounds[j + 1]) for j in range(k)]
            if any(length(a, b) > MAX_LINE_CHARS for a, b in runs):
                continue
            longest = max(length(a, b) for a, b in runs)
            soft = sum(1 for c in cuts if not (sentence[c][2] or words[c - 1].endswith(",")))
            dangling = sum(1 for c in cuts if norm(words[c - 1]) in DANGLING)
            key = (longest, dangling, soft)
            if best is None or key < best[0]:
                best = (key, runs)
        if best:
            return [[sentence[a][0], sentence[b - 1][0]] for a, b in best[1]]
    return [[sentence[0][0], sentence[-1][0]]]


def scoped_css(css: str) -> str:
    """The deck's stylesheet scoped under .stage-deck, without the page-level rules."""
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    rules = []
    for m in re.finditer(r"([^{}]+)\{([^{}]*)\}", css):
        selectors, body = m.group(1).strip(), " ".join(m.group(2).split())
        if selectors in (":root",):
            rules.append(f".stage-deck {{ {body} }}")
            continue
        if selectors.startswith(("html", "body", "#dt-stage")):
            continue
        scoped = ", ".join(f".stage-deck {s.strip()}" for s in selectors.split(","))
        rules.append(f"{scoped} {{ {body} }}")
    return "\n".join(rules)


def read_deck(path: Path) -> tuple[str, dict[str, str], dict[str, list[str]]]:
    """The deck's scoped css, one markup string per scene, and every cue's data-describe texts in document order."""
    src = path.read_text(encoding="utf-8")
    css = scoped_css(re.search(r"<style>(.*?)</style>", src, re.S).group(1))
    scenes: dict[str, str] = {}
    describes: dict[str, list[str]] = {}
    for m in re.finditer(r'<div data-scene="(\d+)"[^>]*>\s*<template[^>]*>(.*?)</template>', src, re.S):
        markup = re.sub(r"<!--.*?-->", "", m.group(2), flags=re.S)
        scenes[m.group(1)] = " ".join(markup.split())
        for tag in re.finditer(r"<[^>]+data-cue=\"([^\"]+)\"[^>]*>", markup):
            d = re.search(r'data-describe="([^"]*)"', tag.group(0))
            if d:
                describes.setdefault(tag.group(1), []).append(html.unescape(d.group(1)))
    return css, scenes, describes


def one_change(before: str, after: str) -> dict[str, str] | None:
    """The single run of words that differs between two lines, with the words either side of it.

    The page shows the edit as `prefix before -> after suffix`, so it can never claim a smaller edit
    than the one the two projects record. Returns None when the lines are the same.
    """
    a, b = before.split(), after.split()
    if a == b:
        return None
    lo = next((i for i, (x, y) in enumerate(zip(a, b, strict=False)) if x != y), min(len(a), len(b)))
    hi_a, hi_b = len(a), len(b)
    while hi_a > lo and hi_b > lo and a[hi_a - 1] == b[hi_b - 1]:
        hi_a, hi_b = hi_a - 1, hi_b - 1

    def clip(words: list[str], keep_last: bool) -> str:
        """Enough of the line to place the change, from the end nearest it, and an ellipsis for the rest."""
        text = " ".join(words)
        if len(text) <= CONTEXT_CHARS:
            return text
        return "…" + text[-CONTEXT_CHARS:] if keep_last else text[:CONTEXT_CHARS] + "…"

    return {
        "prefix": clip(a[:lo], keep_last=True),
        "before": " ".join(a[lo:hi_a]),
        "after": " ".join(b[lo:hi_b]),
        "suffix": clip(a[hi_a:], keep_last=False),
    }


def scene_lines(path: Path, scene: str) -> list[str]:
    """The deck's own lines inside one data-scene wrapper, comments and blank lines dropped."""
    src = path.read_text(encoding="utf-8")
    m = re.search(rf'<div data-scene="{scene}"[^>]*>.*?</template>', src, re.S)
    if not m:
        return []
    body = re.sub(r"<!--.*?-->", "", m.group(0), flags=re.S)
    return [line.strip() for line in body.splitlines() if line.strip()]


def edit_diff(before: Path, after: Path, section: int, spoken: tuple[str, str]) -> list[dict]:
    """Every place the edit changed, file by file, read from the two projects rather than written by hand.

    A number a film speaks and shows lives in three files: the sentence in script.md, the cue phrase in
    cues.json that binds a picture to those words, and the slide in deck/index.html that draws it. The
    page showed only the first, which read as though a script edit were the whole edit.
    """
    files: list[dict] = []

    # The sentence as the page prints it elsewhere: the words, without the bracketed directions.
    said = tuple(" ".join(re.sub(r"\[[^\]]*\]", " ", text).split()) for text in spoken)
    if change := one_change(*said):
        files.append({"file": "script.md", "changes": [{"where": f"section {section}", **change}]})

    def cues_of(project: Path) -> dict[str, str]:
        doc = json.loads((project / "cues.json").read_text(encoding="utf-8"))
        return {c["cue"]: c["on"] for c in doc["sections"][str(section)]["cues"]}

    cues_before, cues_after = cues_of(before), cues_of(after)
    cue_changes = [
        {"where": cue, "label": "on", **change}
        for cue, on in cues_before.items()
        if cue in cues_after and (change := one_change(on, cues_after[cue]))
    ]
    if cue_changes:
        files.append({"file": "cues.json", "changes": cue_changes})

    deck = ("deck", "index.html")
    deck_changes = []
    old_lines = scene_lines(before.joinpath(*deck), str(section))
    new_lines = scene_lines(after.joinpath(*deck), str(section))
    for old, new in zip(old_lines, new_lines, strict=False):
        if old == new:
            continue
        cue = re.search(r'data-cue="([^"]+)"', old)
        where = cue.group(1) if cue else f"scene {section}"
        # A slide line carries the number in two places that are not the same change: the text a
        # viewer reads, and data-describe, which is what a screen reader hears. Comparing the
        # fields rather than the markup keeps each one to the words that changed.
        for label, pattern in (("describe", r'data-describe="([^"]*)"'), (None, r">([^<>]+)<")):
            a, b = re.search(pattern, old), re.search(pattern, new)
            if a and b and (change := one_change(a.group(1), b.group(1))):
                deck_changes.append({"where": where, "label": label, **change})
    if deck_changes:
        files.append({"file": "deck/index.html", "changes": deck_changes})

    return files


def ffmpeg(*args: str) -> str:
    return subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-y", *args], capture_output=True, text=True, check=True
    ).stderr


def measure(inputs: list[str], filter_graph: str) -> float:
    err = ffmpeg(
        *inputs, "-filter_complex", f"{filter_graph}loudnorm=I=-16:TP=-1.5:LRA=11:print_format=json", "-f", "null", "-"
    )
    return float(re.search(r'"input_i"\s*:\s*"([-0-9.]+)"', err).group(1))


def section_gain(mp3: Path, clip: float, end: float) -> float:
    """The gain, in dB, that brings this take's spoken span to -16 LUFS, so no level jump is audible at a cut."""
    return -16.0 - measure(["-i", str(mp3)], f"[0]atrim=start={clip}:end={clip + end},asetpts=PTS-STARTPTS,")


def loudnorm(filter_graph: str, inputs: list[str], target: Path) -> None:
    """The film's own loudness pass: a plain gain to -16 LUFS, then a true-peak limiter at -1.5 dBTP,
    oversampled, as decktalk.stages.assemble.loudness does it. The gain is corrected once for what
    the limiter took, so the takes land within a fraction of a LU of the film. Then a mono mp3."""
    target_lufs, true_peak_db, headroom_db = -16.0, -1.5, 0.3
    ceiling = 10 ** ((true_peak_db - headroom_db) / 20)
    gain = target_lufs - measure(inputs, filter_graph)
    for _ in range(3):
        chain = f"{filter_graph}volume={gain:.2f}dB,aresample=192000,alimiter=limit={ceiling:.4f}:attack=5:release=50:level=false,aresample=44100,"
        got = measure(inputs, chain)
        if abs(got - target_lufs) < 0.25:
            break
        gain += target_lufs - got
    ffmpeg(*inputs, "-filter_complex", chain.rstrip(","), "-ac", "1", "-b:a", "96k", str(target))


def envelope(mp3: Path, rate: int = 8000) -> list[int]:
    """The clip's loudness, RMS in dB every ENVELOPE_STEP seconds, from its decoded samples, floored at silence."""
    pcm = subprocess.run(
        [FFMPEG, "-hide_banner", "-nostats", "-i", str(mp3), "-f", "f32le", "-ac", "1", "-ar", str(rate), "-"],
        capture_output=True,
        check=True,
    ).stdout
    samples = struct.unpack(f"<{len(pcm) // 4}f", pcm)
    window = int(rate * ENVELOPE_STEP)
    out = []
    for i in range(0, len(samples), window):
        chunk = samples[i : i + window]
        rms = math.sqrt(sum(x * x for x in chunk) / len(chunk))
        out.append(max(ENVELOPE_FLOOR, round(20 * math.log10(rms))) if rms > 0 else ENVELOPE_FLOOR)
    return out


HOME_PATH = re.compile(r"(/Users/|/home/|/root/|[A-Za-z]:\\Users\\|/private/tmp/|/tmp/)")


def relative_paths(line: str, project: Path) -> str:
    """The line with every path under the project written relative to it, as a reader's own build would print it."""
    return line.replace(str(project) + "/", "")


def parse_rebuild_log(path: Path, project: Path) -> dict:
    """What the page prints from the captured build: the lines that show one section voiced, the cost, and the verify rows."""
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    keep = [
        relative_paths(ln, project)
        for ln in lines
        if re.match(
            r"\[1/5 narrate\] \[(skip|tts )\]|\[1/5 narrate\]        03|\[2/5 align\] Wrote|\[3/5 record\] \[rec \]|\[3/5 record\]        build|\[4/5 assemble\] \[(cat |loud)\]|\[4/5 assemble\] done:|\[5/5 verify\] done",
            ln,
        )
    ]
    leaked = [ln for ln in keep if HOME_PATH.search(ln)]
    if leaked:
        sys.exit(
            "the build log still names a machine path, and the page shows only project-relative paths:\n  "
            + "\n  ".join(leaked)
        )
    # The cost line's numbers. The page writes its own sentence from them, with the unit the CLI's line lacked.
    m = re.search(
        r"^voice 1 section(?:\(s\))?: (\d+) characters sent, (\d+) spoken.*?(\d+) cached\. About \$([\d.]+) at \$([\d.]+) per 1,000",
        text,
        re.M,
    )
    if not m:
        sys.exit("the build log has no cost line for one voiced section")
    cost = {
        "sent": int(m.group(1)),
        "spoken": int(m.group(2)),
        "cached": int(m.group(3)),
        "usd": float(m.group(4)),
        "rate": float(m.group(5)),
    }
    findings = re.search(r"^stages 5, seconds [\d.]+ \| (\d+) certain", text, re.M)
    certain = int(findings.group(1)) if findings else 0
    if certain:
        print(
            f"warning: this build's verify stage reported {certain} certain finding(s). Recapture the log from a clean build before the page ships.",
            file=sys.stderr,
        )
    kept = re.search(r"^kept (\d+) unchanged section\(s\)", text, re.M).group(1)
    return {"lines": keep, "cost": cost, "verify": verify_rows(text), "kept": int(kept), "findings": certain}


def verify_rows(text: str) -> dict[str, int]:
    """The verify table of a build log: each cue's offset in ms, positive when the picture came after its cue."""
    return {
        m.group(1): int(m.group(2))
        for m in re.finditer(r"^\d:(\S+)\s+[\d.]+\s+[\d.]+\s+[\d.]+\s+[\d.]+\s+([+-]\d+)ms\s+(\w+)", text, re.M)
    }


def hero_verify(path: Path, cues: list[str]) -> dict[str, int]:
    """The hero cut's own measurement, from its clean build log: a row for every cue in the cut, and no finding."""
    text = path.read_text(encoding="utf-8")
    rows = verify_rows(text)
    missing = [c for c in cues if c not in rows]
    if missing:
        sys.exit(f"the hero build log has no verify row for {', '.join(missing)}")
    findings = re.search(r"^stages 5, seconds [\d.]+ \| (\d+) certain", text, re.M)
    if findings and int(findings.group(1)):
        sys.exit("the hero build log reports a certain finding, and the page shows only a clean measurement")
    return {c: rows[c] for c in cues}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project", type=Path, required=True, help="the hero cut's DeckTalk project")
    ap.add_argument("--hero-log", type=Path, required=True, help="the captured log of the hero cut's own clean build")
    ap.add_argument("--takes", type=Path, required=True, help="the narration directory with the voiced takes")
    ap.add_argument("--film", type=Path, required=True, help="the full film's project, for its captions and poster")
    ap.add_argument("--rebuild-log", type=Path, required=True, help="the captured log of the build after the edit")
    args = ap.parse_args()
    project, takes = args.project.resolve(), args.takes.resolve()
    toml = tomllib.loads((project / "decktalk.toml").read_text(encoding="utf-8"))
    lead, tail = toml["narration"]["lead_seconds"], toml["narration"]["min_tail_seconds"]
    holds = {s["number"]: s.get("hold_seconds", 0.0) for s in toml["section"]}
    price = toml["voice"]["price_per_1000_characters"]
    cues_json = json.loads((project / "cues.json").read_text(encoding="utf-8"))["sections"]
    css, scenes, describes = read_deck(project / "deck" / "index.html")

    # The build's own cut list, when the project was built with voice, so the page's timeline is the film's.
    cuts_path, cue_times_path = project / "build" / "out" / "cuts.json", project / "build" / "cue-times.json"
    cuts = (
        {c["section"]: c for c in json.loads(cuts_path.read_text(encoding="utf-8"))["sections"]}
        if cuts_path.exists()
        else {}
    )
    cue_times = (
        json.loads(cue_times_path.read_text(encoding="utf-8")) if cue_times_path.exists() else {"estimated": True}
    )
    built = (
        {} if cue_times["estimated"] else {c["cue"]: c["at"] for rows in cue_times["sections"].values() for c in rows}
    )

    sections, audio_inputs, audio_filters, t = [], [], [], 0.0
    word_index = 0
    for s in read_script(project / "script.md"):
        words = script_words(s["md"])
        mp3, take_words, first = find_take(takes, words)
        clip = take_words[first]["start"]
        end = sound_end(mp3, clip)
        if s["n"] in cuts:
            t, length = cuts[s["n"]]["start"], round(cuts[s["n"]]["end"] - cuts[s["n"]]["start"], 3)
        else:
            length = frames(lead + end + tail) + holds.get(s["n"], 0.0)
        wl = [
            [w, round(t + lead + tw["start"] - clip, 3), round(t + lead + tw["end"] - clip, 3)]
            for w, tw in zip(words, take_words[first:], strict=True)
        ]
        cues = []
        for c in cues_json[str(s["n"])]["cues"]:
            phrase = [norm(w) for w in c["on"].split()]
            keys = [norm(w) for w in words]
            k = next(i for i in range(len(keys)) if keys[i : i + len(phrase)] == phrase)
            cues.append(
                {
                    "cue": c["cue"],
                    "on": c["on"],
                    "at": round(t + built[c["cue"]], 2) if c["cue"] in built else round(wl[k][1], 2),
                    "first": word_index + k,
                    "n": len(phrase),
                    "describe": ". ".join(describes.get(c["cue"], [])),
                }
            )
        sections.append(
            {
                "n": s["n"],
                "chapter": s["chapter"],
                "md": s["md"],
                "hash": mp3.stem,
                "start": round(t, 2),
                "end": round(t + length, 2),
                "first": word_index,
                "words": wl,
                "lines": band_lines(s["md"], word_index),
                "cues": cues,
            }
        )
        i = len(audio_inputs) // 2
        audio_inputs += ["-i", str(mp3)]
        # The take is cut where its sound ends, so nothing of it bleeds into the silence before the next cut,
        # and each section is brought to the film's loudness on its own before the takes are laid end to end.
        audio_filters.append(
            f"[{i}]atrim=start={clip}:end={clip + end},asetpts=PTS-STARTPTS,volume={section_gain(mp3, clip, end):.2f}dB,"
            f"adelay={int(lead * 1000)}|{int(lead * 1000)},apad=whole_dur={length}[a{i}];"
        )
        word_index += len(words)
        t += length

    all_words = [w for s in sections for w in s["words"]]
    gaps = [b[1] - a[1] for a, b in zip(all_words, all_words[1:], strict=False)]
    beat = round(sum(gaps) / len(gaps), 3)

    # The edit: section 3 before and after, from the two takes that say it.
    edited = 3
    before_md = next(s["md"] for s in sections if s["n"] == edited)
    film_script = next(s for s in read_script(args.film / "script.md") if s["n"] == edited)
    before_mp3, before_words, _ = find_take(takes, script_words(before_md))
    after_mp3, after_words, _ = find_take(takes, script_words(film_script["md"]))
    log = parse_rebuild_log(args.rebuild_log, args.film.resolve())
    verify = hero_verify(args.hero_log, [c["cue"] for s in sections for c in s["cues"]])

    MEDIA.mkdir(parents=True, exist_ok=True)
    loudnorm(
        "".join(audio_filters)
        + "".join(f"[a{i}]" for i in range(len(sections)))
        + f"concat=n={len(sections)}:v=0:a=1,",
        audio_inputs,
        MEDIA / "halfway-hero.mp3",
    )
    for name, mp3 in (("before", before_mp3), ("after", after_mp3)):
        # Each take ends TAKE_TAIL seconds after its last sound, so the pair a visitor compares breathes alike.
        end = sound_end(mp3, 0) + TAKE_TAIL
        loudnorm(
            f"[0]atrim=end={end:.3f},apad=whole_dur={end:.3f},adelay={int(lead * 1000)}|{int(lead * 1000)},",
            ["-i", str(mp3)],
            MEDIA / f"halfway-take-{name}.mp3",
        )
    # The name each clip is uploaded under, from its own bytes, so the page never plays a clip that is not this build's.
    media = {
        name: f"decktalk-halfway-{stem}-{hashlib.sha256((MEDIA / f'halfway-{stem}.mp3').read_bytes()).hexdigest()[:12]}.mp3"
        for name, stem in (("hero", "hero"), ("before", "take-before"), ("after", "take-after"))
    }
    shutil.copy(args.film / "build" / "out" / "halfway.vtt", MEDIA / "halfway.vtt")
    poster_at = next(c["at"] for s in sections for c in s["cues"] if c["cue"] == "1.1forty") + 0.6
    from PIL import Image

    frame = MEDIA / "halfway-poster.png"
    ffmpeg(
        "-ss",
        f"{poster_at}",
        "-i",
        str(args.film / "build" / "out" / "halfway.mp4"),
        "-frames:v",
        "1",
        "-vf",
        "scale=960:-1",
        str(frame),
    )
    Image.open(frame).save(MEDIA / "halfway-poster.webp", quality=82, method=6)
    frame.unlink()

    data = {
        "fps": FPS,
        "lead": lead,
        "beat": beat,
        "total": round(t, 2),
        "sections": sections,
        "edit": {
            "section": edited,
            "before": {
                "hash": before_mp3.stem,
                "md": before_md,
                "words": [[w["word"], w["start"], w["end"]] for w in before_words],
            },
            "after": {
                "hash": after_mp3.stem,
                "md": film_script["md"],
                "words": [[w["word"], w["start"], w["end"]] for w in after_words],
            },
            "diff": edit_diff(project, args.film.resolve(), edited, (before_md, film_script["md"])),
            "price": price,
            "kept": log["kept"],
            "cost": log["cost"],
            "log": log["lines"],
            "verify": log["verify"],
        },
        "verify": verify,
        "media": media,
        "envelope": {
            "step": ENVELOPE_STEP,
            "floor": ENVELOPE_FLOOR,
            "hero": envelope(MEDIA / "halfway-hero.mp3"),
            "before": envelope(MEDIA / "halfway-take-before.mp3"),
            "after": envelope(MEDIA / "halfway-take-after.mp3"),
        },
    }
    data_js = (
        "/* Generated by scripts/build_homepage_data.py from the Halfway build. Times are film seconds. Do not edit. */\n"
        f"window.HALFWAY = {json.dumps(data, separators=(',', ':'))};\n"
    )
    stage_js = (
        "/* Generated by scripts/build_homepage_data.py from the Halfway deck. The hero replays these scenes. Do not edit. */\n"
        f"window.HALFWAY_STAGE = {json.dumps({'css': css, 'scenes': scenes}, separators=(',', ':'))};\n"
    )
    # Nothing the page shows may name a machine path. tests/test_site.py checks the committed files the same way.
    for name, text in (("data.js", data_js), ("stage.js", stage_js)):
        if m := HOME_PATH.search(text):
            sys.exit(f"site/{name} would name a machine path ({m.group(0)}...), which the page must never show")
    (SITE / "data.js").write_text(data_js, encoding="utf-8")
    (SITE / "stage.js").write_text(stage_js, encoding="utf-8")
    print(
        f"wrote site/data.js ({len(all_words)} words, {sum(len(s['cues']) for s in sections)} cues, {t:.2f} s, beat {beat} s), site/stage.js, site/media/"
    )
    print("upload to media.decktalk.ai, each under the name the page now asks for:")
    for name, stem in (("hero", "hero"), ("before", "take-before"), ("after", "take-after")):
        print(f"  site/media/halfway-{stem}.mp3  ->  {media[name]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
