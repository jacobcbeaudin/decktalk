# /// script
# requires-python = ">=3.12"
# ///
"""Generate the homepage's data from the Halfway build, so the page cannot drift from the film.

    uv run scripts/build_homepage_data.py --write
    uv run scripts/build_homepage_data.py --check
    uv run scripts/build_homepage_data.py --write --media   # also re-cut the clips the page streams

The film is `--project`, and the hero cut is the project inside it at `hero/`: sections 1 to 3 word
for word the film's, section 4 trimmed. Everything is read through the SDK, so the page's numbers are
the artifacts' own numbers rather than a second reading of the same files.

- site/data.js   the words with their times, the cues with their times and what each one shows, the
                 band lines, the hero cut's own verify offsets, the rebuild's stage lines with the
                 cost of the take the edit bought, and the full film's verify offsets, in film seconds
- site/stage.js  the deck's scenes and styles, scoped, so the hero replays the production slides as
                 HTML and SVG under the real take rather than as a captured video

With `--media` it also writes the files the page streams, which are the four takes laid out as the
film lays them out, the section 3 take before and after the edit, the film's poster and the film's
captions. The mp3s are not in git. The page streams them from media.decktalk.ai under names that
carry the first twelve hex digits of their SHA-256, like the films, and `data.js` carries those names
next to the clips' loudness envelopes, so a clip and its name change together. Without `--media` the
committed names and envelopes are carried over and the run first proves the film still lays its
sections out where those clips were cut, because a new name is a file nobody has uploaded yet.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import math
import re
import shutil
import struct
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from itertools import combinations
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter

import decktalk
from decktalk import Certainty, CueTimes, Cut, Cuts, Project, Take, Takes, Words
from decktalk.artifacts.words import words_file
from decktalk.cli.output import Report
from decktalk.events import Event, Line, SectionDone, StageDone
from decktalk.inputs import Inputs
from decktalk.inputs.cues import find_phrase
from decktalk.inputs.script import Segment
from decktalk.media import audio, ffmpeg
from decktalk.page import ATTRS, ENTRANCES, Attr, wire_id
from decktalk.results import VerifyResult, Word
from decktalk.stages.assemble.loudness import (
    LIMITER_ATTACK_MS,
    LIMITER_HEADROOM_DB,
    LIMITER_OVERSAMPLE_RATE,
    LIMITER_RELEASE_MS,
)
from decktalk.stages.assemble.mix import gain

ROOT = Path(__file__).resolve().parent.parent
SITE = ROOT / "site"
MEDIA = SITE / "media"
DATA = SITE / "data.js"
STAGE = SITE / "stage.js"

DEFAULT_PROJECT = Path.home() / "films" / "halfway"
"""Where the film is unless `--project` names another directory, which is the founder's film shelf."""

STALE = "stale: {path}. Run `uv run scripts/{script} --write` to bring it up to date."

HERO = "hero"
"""The directory inside the film that holds the cut the homepage plays."""

BEAT = "[beat]"
"""How the page writes a pause, which is the direction the author writes in `script.md`."""

BEAT_MARK = "\N{EM DASH}"
"""How `Segment.text` carries a pause, which is one token between two sentences."""

ENVELOPE_STEP = 0.02
"""Seconds per envelope sample, which is fine enough that a waveform glyph moves with the voice."""

ENVELOPE_FLOOR = -60
"""The level in dB a sample is floored at, which is silence."""

ENVELOPE_RATE = 8000
"""The rate the envelope is measured at, which resolves every syllable and decodes in a moment."""

TAKE_TAIL = 0.5
"""Seconds of silence after the last sound of each take on the page, so the pair breathes alike."""

CONTEXT_CHARS = 46
"""How much of a changed line the edit strip shows either side of the change."""

MAX_LINE_CHARS = 44
"""A band line fits the phone band at 17 px, so the dimmed previous line never needs an ellipsis."""

DANGLING = {"a", "an", "the", "and", "or", "but", "of", "to", "for", "in", "on", "at", "with", "by"}
"""Words a line may not end on, because the eye reaches for the noun that is on the next line."""

POSTER_CUE = "1.1:forty"
"""The cue whose picture the film's card shows, which is the route with its forty minute label."""

POSTER_AFTER_SECONDS = 0.6
"""How long after that cue the poster frame is taken, which is after the reveal has finished."""

POSTER_WIDTH = 960
"""How wide the card's poster is written, which is twice the widest the card is ever drawn."""

POSTER_QUALITY = 82
"""The WebP quality of the poster, which is where the gradient stops banding."""

MACHINE_PATH = re.compile(r"(/Users/|/home/|/root/|[A-Za-z]:\\Users\\|/private/tmp/|/tmp/)")
"""What a path on somebody's machine opens with, none of which the page may ever show."""

CLIPS = {"hero": "hero", "before": "take-before", "after": "take-after"}
"""Each clip the page streams, against the stem of the file it is cut into."""


# ---- the deck ---------------------------------------------------------------------------------


def scoped_css(css: str) -> str:
    """The deck's stylesheet scoped under .stage-deck, without the page-level rules."""
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    rules = []
    for rule in re.finditer(r"([^{}]+)\{([^{}]*)\}", css):
        selectors, body = rule.group(1).strip(), " ".join(rule.group(2).split())
        if selectors == ":root":
            rules.append(f".stage-deck {{ {body} }}")
            continue
        if selectors.startswith(("html", "body", "#dt-stage")):
            continue
        scoped = ", ".join(f".stage-deck {one.strip()}" for one in selectors.split(","))
        rules.append(f"{scoped} {{ {body} }}")
    return "\n".join(rules)


SCENE_RE = re.compile(
    rf'<div {Attr.SCENE.value}="(\d+)"[^>]*>\s*<template[^>]*{Attr.SLIDE.value}="([^"]+)"[^>]*>(.*?)</template>',
    re.S,
)
ARRIVAL_RE = re.compile(rf'<[^>]+{Attr.IN.value}="([^"]+)"[^>]*>')
DESCRIBE_RE = re.compile(rf'{Attr.DESCRIBE.value}="([^"]*)"')


@dataclass(frozen=True)
class Deck:
    """One deck page as the hero replays it, read once through the contract's own attribute table."""

    css: str
    """The deck's stylesheet, scoped under the page's stage."""

    scenes: dict[str, str]
    """The markup of each scene's slide, by scene."""

    slides: dict[str, str]
    """The slide each scene declares, by scene, which is what composes a moment's wire id."""

    describes: dict[str, list[str]]
    """What each moment's elements describe themselves as, by wire id, in document order."""


def entrances() -> dict[str, float]:
    """How long each entrance plays, which is the contract's own table and never a second one.

    The page draws every reveal itself, so it needs the lengths an element that writes none is drawn
    at. They are published here rather than written into the page's script, where they would be a
    second contract that nothing holds to this one.
    """
    return {style: effect.seconds for style, effect in ENTRANCES.items()}


def unwritten() -> dict[str, str]:
    """What the contract draws an element with when the element names none of it."""
    return {attr.value: str(ATTRS[attr].default) for attr in (Attr.IN_STYLE, Attr.WORDS)}


def read_deck(path: Path) -> Deck:
    """The deck the hero replays: its scoped styles, each scene's slide and markup, and every moment.

    A slide names each moment by the local word the element carries, so the id the rest of the page
    keys on is composed here the one way the contract composes it, and never spelled by hand.
    """
    source = path.read_text(encoding="utf-8")
    styles = re.search(r"<style>(.*?)</style>", source, re.S)
    if styles is None:
        sys.exit(f"{path.name} has no stylesheet, and the hero replays the deck's own styles")
    deck = Deck(scoped_css(styles.group(1)), {}, {}, {})
    for scene in SCENE_RE.finditer(source):
        markup = re.sub(r"<!--.*?-->", "", scene.group(3), flags=re.S)
        deck.scenes[scene.group(1)] = " ".join(markup.split())
        deck.slides[scene.group(1)] = scene.group(2)
        for tag in ARRIVAL_RE.finditer(markup):
            cue = wire_id(scene.group(2), tag.group(1))
            said = DESCRIBE_RE.search(tag.group(0))
            if said:
                deck.describes.setdefault(cue, []).append(html.unescape(said.group(1)))
    return deck


def scene_lines(path: Path, scene: str) -> list[str]:
    """The deck's own lines inside one scene wrapper, comments and blank lines dropped."""
    source = path.read_text(encoding="utf-8")
    block = re.search(rf'<div {Attr.SCENE.value}="{scene}"[^>]*>.*?</template>', source, re.S)
    if not block:
        return []
    body = re.sub(r"<!--.*?-->", "", block.group(0), flags=re.S)
    return [line.strip() for line in body.splitlines() if line.strip()]


# ---- the band ---------------------------------------------------------------------------------


def spoken_words(segment: Segment) -> list[str]:
    """The words of one section, which is what the take says and what the band lights."""
    return segment.spoken.split()


def written(segment: Segment) -> str:
    """One section as the page prints it, which is the spoken words with the author's own pauses."""
    return " ".join(BEAT if token == BEAT_MARK else token for token in segment.text.split())


def band_lines(md: str, first: int) -> list[list[int]]:
    """Word index ranges for the band: one per sentence, and a sentence longer than MAX_LINE_CHARS broken
    into the fewest lines that each fit, as balanced as they can be, at a beat or after a comma when one
    lies there, so the line under the frame is always whole and the callback word is never cut."""
    lines: list[list[int]] = []
    sentence: list[tuple[int, str, bool]] = []  # (word index, word, a beat before it)
    index, beat = first, False

    def flush() -> None:
        nonlocal sentence
        if sentence:
            lines.extend(break_sentence(sentence))
        sentence = []

    for unit in [part for part in re.split(re.escape(BEAT), md) if part.strip()]:
        for word in unit.split():
            sentence.append((index, word, beat))
            beat = False
            index += 1
            if re.search(r"[.!?]$", word):
                flush()
        beat = True
    flush()
    return lines


def break_sentence(sentence: list[tuple[int, str, bool]]) -> list[list[int]]:
    """One sentence as the fewest band lines that each fit, broken where a reader would breathe."""
    words = [word for _index, word, _beat in sentence]
    count = len(words)

    def length(start: int, end: int) -> int:
        return len(" ".join(words[start:end]))

    if length(0, count) <= MAX_LINE_CHARS:
        return [[sentence[0][0], sentence[-1][0]]]
    for lines in range(2, count + 1):
        best: tuple[tuple[int, int, int], list[tuple[int, int]]] | None = None
        for cuts in combinations(range(1, count), lines - 1):
            bounds = [0, *cuts, count]
            runs = [(bounds[at], bounds[at + 1]) for at in range(lines)]
            if any(length(start, end) > MAX_LINE_CHARS for start, end in runs):
                continue
            longest = max(length(start, end) for start, end in runs)
            soft = sum(1 for cut in cuts if not (sentence[cut][2] or words[cut - 1].endswith(",")))
            dangling = sum(1 for cut in cuts if words[cut - 1].strip(".,;:!?").lower() in DANGLING)
            key = (longest, dangling, soft)
            if best is None or key < best[0]:
                best = (key, runs)
        if best:
            return [[sentence[start][0], sentence[end - 1][0]] for start, end in best[1]]
    return [[sentence[0][0], sentence[-1][0]]]


# ---- the edit ---------------------------------------------------------------------------------


def one_change(before: str, after: str) -> dict[str, str] | None:
    """The single run of words that differs between two lines, with the words either side of it.

    The page shows the edit as `prefix before -> after suffix`, so it can never claim a smaller edit
    than the one the two projects record. Returns None when the lines are the same.
    """
    old, new = before.split(), after.split()
    if old == new:
        return None
    start = next((i for i, (a, b) in enumerate(zip(old, new, strict=False)) if a != b), min(len(old), len(new)))
    end_old, end_new = len(old), len(new)
    while end_old > start and end_new > start and old[end_old - 1] == new[end_new - 1]:
        end_old, end_new = end_old - 1, end_new - 1

    def clip(words: list[str], keep_last: bool) -> str:
        """Enough of the line to place the change, from the end nearest it, and an ellipsis for the rest."""
        text = " ".join(words)
        if len(text) <= CONTEXT_CHARS:
            return text
        return (
            "\N{HORIZONTAL ELLIPSIS}" + text[-CONTEXT_CHARS:]
            if keep_last
            else text[:CONTEXT_CHARS] + "\N{HORIZONTAL ELLIPSIS}"
        )

    return {
        "prefix": clip(old[:start], keep_last=True),
        "before": " ".join(old[start:end_old]),
        "after": " ".join(new[start:end_new]),
        "suffix": clip(old[end_old:], keep_last=False),
    }


def edit_diff(before: Inputs, after: Inputs, section: int) -> list[dict]:
    """Every place the edit changed, file by file, read from the two projects rather than written by hand.

    A number a film speaks and shows lives in three files: the sentence in `script.md`, the cue phrase
    in `cues.json` that binds a picture to those words, and the slide in the deck that draws it. A page
    that showed only the first would read as though a script edit were the whole edit.
    """
    files: list[dict] = []
    said = tuple(one_section(inputs, section).spoken for inputs in (before, after))
    if change := one_change(*said):
        files.append({"file": "script.md", "changes": [{"where": f"section {section}", **change}]})

    def phrases(inputs: Inputs) -> dict[str, str]:
        block = next(block for block in inputs.cues() if block.number == section)
        return {cue.cue: cue.on for cue in block.cues}

    was, now = phrases(before), phrases(after)
    cue_changes = [
        {"where": cue, "label": "on", **change}
        for cue, on in was.items()
        if cue in now and (change := one_change(on, now[cue]))
    ]
    if cue_changes:
        files.append({"file": "cues.json", "changes": cue_changes})

    deck = Path(before.document.page_files[0])
    slides = read_deck(before.path(deck)).slides
    deck_changes = []
    old_lines = scene_lines(before.path(deck), str(section))
    new_lines = scene_lines(after.path(deck), str(section))
    for old, new in zip(old_lines, new_lines, strict=False):
        if old == new:
            continue
        arrival = ARRIVAL_RE.search(old)
        where = wire_id(slides[str(section)], arrival.group(1)) if arrival else f"scene {section}"
        # A slide line carries the number in two places that are not the same change: the text a
        # viewer reads, and the description, which is what a screen reader hears. Comparing the
        # fields rather than the markup keeps each one to the words that changed.
        for label, pattern in (("describe", DESCRIBE_RE), (None, re.compile(r">([^<>]+)<"))):
            first, second = pattern.search(old), pattern.search(new)
            if first and second and (change := one_change(first.group(1), second.group(1))):
                deck_changes.append({"where": where, "label": label, **change})
    if deck_changes:
        files.append({"file": deck.as_posix(), "changes": deck_changes})
    return files


def one_section(inputs: Inputs, section: int) -> Segment:
    """One section of a project's script, by its number."""
    return next(segment for segment in inputs.spoken() if segment.index == section)


def edited_section(film: Inputs, hero: Inputs) -> int:
    """The one section whose words differ between the film and the cut, which is what the page edits."""
    cut = {segment.index: segment.spoken for segment in hero.spoken()}
    differ = [
        segment.index
        for segment in film.spoken()
        if segment.index in cut and cut[segment.index] != segment.spoken and segment.index in _shared(film, hero)
    ]
    if len(differ) != 1:
        sys.exit(f"the film and its hero cut differ in {len(differ)} spoken section(s), and the page shows one edit")
    return differ[0]


def _shared(film: Inputs, hero: Inputs) -> set[int]:
    """Every section both projects declare with the same cue list length, which is a section the cut kept."""
    counts = {block.number: len(block.cues) for block in hero.cues()}
    return {block.number for block in film.cues() if counts.get(block.number) == len(block.cues)}


# ---- the run ----------------------------------------------------------------------------------


EVENT: TypeAdapter[Event] = TypeAdapter(Line)


def read_events(path: Path) -> list[Event]:
    """One run's lines, parsed into the events the library minted."""
    return [EVENT.validate_json(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def build_runs(project: Project) -> list[tuple[Path, list[Event]]]:
    """Every run under `build/events/` this version can read, newest first, whichever wrote them."""
    runs = []
    for path in sorted(project.inputs.workspace.events_dir.glob("*.jsonl"), key=lambda one: one.stat().st_mtime):
        try:
            runs.append((path, read_events(path)))
        except ValueError:
            continue
    return list(reversed(runs))


def the_rebuild(project: Project, wanted: str | None) -> tuple[str, list[Event]]:
    """The run whose stage lines the page prints, which is a build that ran every stage.

    The run the page was written from is named in `data.js`, and that one wins while its file is
    still there, so a later run of this script prints the same lines and `--check` compares a page
    against its own run rather than against whatever was built last.
    """
    runs = build_runs(project)
    asked = wanted or carried()["edit"].get("run")
    for path, events in runs:
        if {event.stage for event in events if isinstance(event, StageDone)} != set(decktalk.Stage):
            continue
        if path.stem == asked:
            return path.stem, events
        if wanted is None:
            return path.stem, events
    sys.exit(f"no whole build is recorded under {project.inputs.relative(project.inputs.workspace.events_dir)}")


def stage_lines(events: Sequence[Event]) -> list[str]:
    """The lines a pipe saw that run print, rendered by the command line's own stage row."""
    return [
        Report(stage=event.stage, seconds=event.seconds, outcome=event.outcome).line().plain
        for event in events
        if isinstance(event, StageDone)
    ]


def lanes(events: Sequence[Event]) -> list[dict]:
    """What each stage did with each section, which is the run's own account of what it redid."""
    rows: dict[str, list[dict]] = {}
    for event in events:
        if isinstance(event, SectionDone):
            rows.setdefault(event.stage.value, []).append({"n": event.section, "outcome": event.outcome.value})
    return [{"stage": stage, "sections": sections} for stage, sections in rows.items()]


# ---- the audio --------------------------------------------------------------------------------


def normalize(inputs: Inputs, src: Path, dst: Path) -> None:
    """One clip at the film's own loudness: a plain gain to the target, then the true-peak limiter.

    It is the film's pass over an audio file rather than over a film, so the numbers are `[mix.loudness]`
    and the limiter is the assemble stage's own, and a clip on the page sits where the film sits.
    """
    loudness = inputs.settings.mix.loudness
    measured = audio.measure_loudness(
        src, i=loudness.target_lufs, tp=loudness.true_peak_max_dbtp, lra=loudness.range_max_lu
    )
    lift = loudness.target_lufs - measured.i
    ceiling = gain(loudness.true_peak_max_dbtp - LIMITER_HEADROOM_DB)
    rate = inputs.settings.video.sample_rate
    ffmpeg.run(
        "-i", str(src),
        "-af",
        f"volume={lift:.2f}dB,aresample={LIMITER_OVERSAMPLE_RATE},"
        f"alimiter=limit={ceiling:.4f}:attack={LIMITER_ATTACK_MS}:release={LIMITER_RELEASE_MS}:level=false,"
        f"aresample={rate}",
        "-ac", "1", "-b:a", inputs.settings.narration.mp3_bitrate, str(dst),
    )  # fmt: skip


def envelope(mp3: Path) -> list[int]:
    """The clip's loudness, RMS in dB every ENVELOPE_STEP seconds, from its own samples, floored at silence."""
    pcm = ffmpeg.raw("-i", str(mp3), "-f", "f32le", "-ac", "1", "-ar", str(ENVELOPE_RATE), "-")
    samples = struct.unpack(f"<{len(pcm) // 4}f", pcm)
    window = int(ENVELOPE_RATE * ENVELOPE_STEP)
    out = []
    for at in range(0, len(samples), window):
        chunk = samples[at : at + window]
        rms = math.sqrt(sum(one * one for one in chunk) / len(chunk))
        out.append(max(ENVELOPE_FLOOR, round(20 * math.log10(rms))) if rms > 0 else ENVELOPE_FLOOR)
    return out


def write_clips(hero: Project, film: Project, section: int) -> None:
    """The three clips the page plays, cut and levelled the way the film cuts and levels them."""
    inputs, takes = hero.inputs, _takes(hero)
    MEDIA.mkdir(parents=True, exist_ok=True)
    placements = [
        audio.Placement(
            inputs.workspace.takes_dir / take.file,
            lead=take.lead_seconds,
            play=take.sound_seconds,
            tail=round(_cut(hero, take.section).seconds - take.lead_seconds - take.sound_seconds, 3),
        )
        for take in takes.sections
    ]
    joined = MEDIA / "halfway-hero-joined.mp3"
    audio.concat_audio(
        placements,
        joined,
        bitrate=inputs.settings.narration.mp3_bitrate,
        sample_rate=inputs.settings.video.sample_rate,
    )
    normalize(inputs, joined, MEDIA / "halfway-hero.mp3")
    joined.unlink()
    for name, project in (("before", hero), ("after", film)):
        take = _take(project, section)
        source = project.inputs.workspace.takes_dir / take.file
        # Each take ends TAKE_TAIL seconds after its last sound, so the pair a visitor compares
        # breathes alike, and each keeps its section's own lead so it opens the way the film opens.
        one = MEDIA / f"halfway-take-{name}-cut.mp3"
        audio.concat_audio(
            [audio.Placement(source, lead=take.lead_seconds, play=take.sound_seconds, tail=TAKE_TAIL)],
            one,
            bitrate=project.inputs.settings.narration.mp3_bitrate,
            sample_rate=project.inputs.settings.video.sample_rate,
        )
        normalize(project.inputs, one, MEDIA / f"halfway-take-{name}.mp3")
        one.unlink()


def write_poster_and_captions(film: Project, at: float) -> None:
    """The film's card picture and its captions, copied here so the text track is same-origin."""
    deliverables = film.inputs.workspace.deliverables()
    shutil.copy(deliverables["vtt"], MEDIA / "halfway.vtt")
    ffmpeg.run(
        "-ss", f"{at}", "-i", str(deliverables["film"]), "-frames:v", "1",
        "-vf", f"scale={POSTER_WIDTH}:-1", "-quality", str(POSTER_QUALITY), str(MEDIA / "halfway-poster.webp"),
    )  # fmt: skip


def clip_names() -> dict[str, str]:
    """The name each clip is uploaded under, from its own bytes, so the page never plays another build's."""
    return {
        name: f"decktalk-halfway-{stem}-{_digest(MEDIA / f'halfway-{stem}.mp3')}.mp3" for name, stem in CLIPS.items()
    }


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:12]


# ---- the page's own document -------------------------------------------------------------------


def _takes(project: Project) -> Takes:
    index = project.inputs.takes()
    if index is None:
        sys.exit(f"{project.root} has no take index, so nothing says what it says. Build it first.")
    return index


def _cuts(project: Project) -> Cuts:
    cuts = project.inputs.cuts()
    if cuts is None:
        sys.exit(f"{project.root} has no cut list, so nothing says how long it runs. Build it first.")
    return cuts


def _take(project: Project, section: int) -> Take:
    """One section's row of the take index, which is what it says and what saying it cost."""
    take = _takes(project).of(section)
    if take is None:
        sys.exit(f"{project.root} has no take for section {section}, so nothing says what it says.")
    return take


def _cut(project: Project, section: int) -> Cut:
    """One section's row of the cut list, which is where it plays in the film."""
    cut = _cuts(project).of(section)
    if cut is None:
        sys.exit(f"{project.root} does not play section {section}, so the page has no place for it.")
    return cut


def sections_of(hero: Project) -> list[dict]:
    """One row per section of the cut: its words with their times, its band lines and its cues."""
    inputs, times = hero.inputs, hero.inputs.cue_times()
    if times is None:
        sys.exit(f"{hero.root} has no resolved cues, so the page has no second for any of them.")
    describes = read_deck(_deck(inputs)).describes
    chapters = inputs.chapters()
    rows, first = [], 0
    for segment in inputs.spoken():
        number = segment.index
        take, cut = _take(hero, number), _cut(hero, number)
        words = inputs.words(number, take.hash)
        spoken = spoken_words(segment)
        if len(words) != len(spoken):
            sys.exit(f"section {number} says {len(spoken)} words and its take timed {len(words)}")
        md = written(segment)
        rows.append(
            {
                "n": number,
                "chapter": chapters[number],
                "md": md,
                "hash": take.hash,
                "start": round(cut.start, 2),
                "end": round(cut.end, 2),
                "first": first,
                "words": [
                    [word, round(cut.start + timed.start, 3), round(cut.start + timed.end, 3)]
                    for word, timed in zip(spoken, words, strict=True)
                ],
                "lines": band_lines(md, first),
                "cues": cues_of(inputs, times, describes, number, words, first, cut.start),
            }
        )
        first += len(spoken)
    return rows


def cues_of(
    inputs: Inputs,
    times: CueTimes,
    describes: dict[str, list[str]],
    section: int,
    words: Sequence[Word],
    first: int,
    start: float,
) -> list[dict]:
    """One section's cues: where each lands in the film, which words it is written against, and what it shows."""
    block = next((block for block in inputs.cues() if block.number == section), None)
    rows = []
    for cue in block.cues if block else ():
        at = times.at(section, cue.cue)
        index = find_phrase(words, cue.on, cue.occurrence, cue.case_sensitive)
        if at is None or index is None:
            sys.exit(f"{cue.cue} is not resolved against its words, so the page has no second for it")
        rows.append(
            {
                "cue": cue.cue,
                "on": cue.on,
                "at": round(start + at, 2),
                "first": first + index,
                "n": len(cue.on.split()),
                "describe": ". ".join(describes.get(cue.cue, [])),
            }
        )
    return rows


def _deck(inputs: Inputs) -> Path:
    """The one page every section of this project plays, which is the deck the hero replays."""
    return inputs.path(inputs.document.page_files[0])


def offsets(result: VerifyResult) -> dict[str, int]:
    """Every cue's landing in milliseconds, positive when the picture came after its word.

    The page prints these as a measurement of a film that landed, so a film this stage is certain
    about stops the run instead. An uncertain judgement is a reading for the author to weigh and
    never a claim the page makes.
    """
    certain = [finding.code.value for finding in result.findings if finding.certainty is Certainty.CERTAIN]
    if certain:
        sys.exit(f"{result.film.as_posix()} is judged {', '.join(certain)}, and the page shows a clean measurement")
    return {row.cue: round(row.offset * 1000) for row in result.cues if row.offset is not None}


def beat_of(rows: Sequence[dict]) -> float:
    """How long one word of this film lasts on average, which is what the page paces its glyphs at."""
    starts = [word[1] for row in rows for word in row["words"]]
    gaps = [later - earlier for earlier, later in zip(starts, starts[1:], strict=False)]
    return round(sum(gaps) / len(gaps), 3)


def take_words(project: Project, section: int) -> list[list[str | float]]:
    """One take's own words, in take seconds, which is the clock the two clips of the edit play on."""
    take = _take(project, section)
    found = Words.read(project.inputs.workspace.takes_dir / words_file(take.hash))
    if found is None:
        sys.exit(f"the take of section {section} has no words file, so nothing says when it says them.")
    return [[word.word, word.start, word.end] for word in found.words]


def carried() -> dict[str, Any]:
    """The committed page data, which a run that did not re-cut the clips carries parts of over."""
    text = DATA.read_text(encoding="utf-8")
    return json.loads(text[text.index("{") : text.rindex(";")])


def hold_the_layout(rows: Sequence[dict], total: float) -> None:
    """Refuse to carry the committed clips over a film whose sections have moved.

    The clips are named by their own bytes and are not in git, so a name this run did not change is a
    promise that the file on the CDN is still this film's. That promise is only true while every
    section still starts and ends where it did when the clip was cut.
    """
    was = carried()["sections"]
    now = [{"n": row["n"], "start": row["start"], "end": row["end"]} for row in rows]
    same = [{"n": row["n"], "start": row["start"], "end": row["end"]} for row in was]
    if now != same or round(total, 2) != carried()["total"]:
        sys.exit(
            "the film lays its sections out differently from the clips the page streams, so the clips "
            "no longer fit it. Run this again with --media and upload the three names it prints."
        )


def document(film: Project, hero: Project, run: str | None, *, media: bool) -> dict:
    """Everything the page reads, which is this film measured rather than anything written by hand."""
    rows = sections_of(hero)
    total = _cuts(hero).total_seconds
    section = edited_section(film.inputs, hero.inputs)
    take = _take(film, section)
    price = film.inputs.settings.voice.price_per_1000_characters
    run_id, events = the_rebuild(film, run)
    measured = film.verify()
    if media:
        write_clips(hero, film, section)
        write_poster_and_captions(film, _poster_at(rows))
        clips = clip_names()
        envelopes = {name: envelope(MEDIA / f"halfway-{stem}.mp3") for name, stem in CLIPS.items()}
    else:
        hold_the_layout(rows, total)
        before = carried()
        clips = before["media"]
        envelopes = {name: before["envelope"][name] for name in CLIPS}
    return {
        "fps": _cuts(hero).fps,
        "lead": hero.inputs.lead_seconds(rows[0]["n"]),
        "tail": hero.inputs.tail_seconds(rows[0]["n"]),
        "beat": beat_of(rows),
        "total": round(total, 2),
        "sections": rows,
        "edit": {
            "section": section,
            "before": {
                "hash": _take(hero, section).hash,
                "md": written(one_section(hero.inputs, section)),
                "words": take_words(hero, section),
            },
            "after": {
                "hash": take.hash,
                "md": written(one_section(film.inputs, section)),
                "words": take_words(film, section),
            },
            "diff": edit_diff(hero.inputs, film.inputs, section),
            "kept": len(_takes(film).sections) - 1,
            "cost": {
                "characters": take.characters,
                "cached": len(_takes(film).sections) - 1,
                "usd": round(take.characters / 1000 * price, 2),
                "rate": price,
            },
            "run": run_id,
            "log": stage_lines(events),
            "lanes": lanes(events),
            "film_seconds": round(measured.film_seconds, 2),
            "verify": offsets(measured),
        },
        "verify": offsets(hero.verify()),
        "media": clips,
        "envelope": {"step": ENVELOPE_STEP, "floor": ENVELOPE_FLOOR, **envelopes},
    }


def _poster_at(rows: Sequence[dict]) -> float:
    """The second of the film the card's picture is taken at, which is just after its cue has landed."""
    return next(cue["at"] for row in rows for cue in row["cues"] if cue["cue"] == POSTER_CUE) + POSTER_AFTER_SECONDS


def stage_document(hero: Project) -> dict:
    """The deck as the hero replays it, which is enough of the page for the stage to draw itself."""
    deck = read_deck(_deck(hero.inputs))
    return {
        "css": deck.css,
        "scenes": deck.scenes,
        "slides": deck.slides,
        "entrances": entrances(),
        "unwritten": unwritten(),
    }


# ---- the two files ------------------------------------------------------------------------------


def render(name: str, what: str, payload: dict) -> str:
    """One generated module: one line of provenance and one global."""
    body = json.dumps(payload, separators=(",", ":"))
    return (
        f"/* Generated by scripts/{Path(__file__).name} from the Halfway build. {what} Do not edit. */\n"
        f"window.{name} = {body};\n"
    )


def refuse_machine_paths(files: Iterable[tuple[Path, str]]) -> None:
    """Nothing the page shows may name a machine path, which `tests/contract/test_site.py` also holds."""
    for path, text in files:
        found = MACHINE_PATH.search(text)
        if found:
            sys.exit(
                f"{path.relative_to(ROOT)} would name a machine path ({found.group(0)}), which the page never shows"
            )


def committed(path: Path, name: str, what: str) -> str:
    """One committed file re-rendered from its own content, which is the shape this script writes.

    A machine without the film cannot compare the page against the film, and a check that always
    fails for want of a file nobody has is a check somebody deletes. This one is still real: it holds
    both files to the shape and the header the generator writes, which is what a hand edit breaks.
    """
    text = path.read_text(encoding="utf-8")
    return render(name, what, json.loads(text[text.index("{") : text.rindex(";")]))


def check_the_shape() -> int:
    """Hold the two committed files to the shape this script writes, having nothing to measure them against."""
    stale = [path for path, name, what in FILES if path.read_text(encoding="utf-8") != committed(path, name, what)]
    for path in stale:
        print(STALE.format(path=path.relative_to(ROOT), script=Path(__file__).name))
    return 1 if stale else 0


def write_the_shape(project: Path) -> int:
    """Rewrite the two committed files in the shape this script writes, having no film to read.

    `uv run scripts/check.py --group generated --write` runs every generator on a machine that has
    no film, which is every machine but the founder's. Refusing there failed the release pull request
    for a page the version bump never touches, so the write keeps the committed data and restores
    the shape and the header, exactly the part `--check` can judge without the film.
    """
    for path, name, what in FILES:
        path.write_text(committed(path, name, what), encoding="utf-8")
    print(f"the film is not at {project}, so both files kept their committed data and were rewritten in shape.")
    return 0


FILES = (
    (DATA, "HALFWAY", "Times are film seconds."),
    (STAGE, "HALFWAY_STAGE", "The hero replays these scenes."),
)
"""Each generated file, against the global it declares and the sentence its header carries."""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--write", action="store_true", help="write site/data.js and site/stage.js")
    action.add_argument("--check", action="store_true", help="exit 1 if either committed file would change")
    parser.add_argument("--project", type=Path, default=DEFAULT_PROJECT, help=f"the film (default {DEFAULT_PROJECT})")
    parser.add_argument("--run", help="the build whose stage lines the page prints (default the run data.js names)")
    parser.add_argument("--media", action="store_true", help="also re-cut the clips, the poster and the captions")
    args = parser.parse_args()

    if not (args.project / "decktalk.toml").exists():
        if args.media or args.run:
            sys.exit(f"there is no film at {args.project} to cut or read a run from. Name it with --project DIR.")
        if args.write:
            return write_the_shape(args.project)
        print(f"the film is not at {args.project}, so both files were held to their own shape alone.")
        return check_the_shape()

    film = decktalk.open(args.project)
    hero = decktalk.open(args.project / HERO)
    with ffmpeg.using_tools(film.inputs.settings.tools):
        payload = document(film, hero, args.run, media=args.media)
    written_now = {DATA: payload, STAGE: stage_document(hero)}
    files = [(path, render(name, what, written_now[path])) for path, name, what in FILES]
    refuse_machine_paths(files)
    if args.check:
        stale = [path for path, text in files if not path.exists() or path.read_text(encoding="utf-8") != text]
        for path in stale:
            print(STALE.format(path=path.relative_to(ROOT), script=Path(__file__).name))
        return 1 if stale else 0
    for path, text in files:
        path.write_text(text, encoding="utf-8")
    words = sum(len(row["words"]) for row in payload["sections"])
    cues = sum(len(row["cues"]) for row in payload["sections"])
    print(f"wrote site/data.js ({words} words, {cues} cues, {payload['total']:.2f} s, beat {payload['beat']} s)")
    print("wrote site/stage.js")
    if args.media:
        print("upload to media.decktalk.ai, each under the name the page now asks for:")
        for name, stem in CLIPS.items():
            print(f"  site/media/halfway-{stem}.mp3  ->  {payload['media'][name]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
