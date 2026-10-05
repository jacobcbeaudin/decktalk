"""One take row and one take index, built one way for every test that needs them."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from decktalk.artifacts import (
    AudioPrint,
    EstimatedWords,
    ProviderWords,
    Take,
    Takes,
    Words,
    is_placeholder,
    take_file,
    words_file,
)
from decktalk.artifacts.takes import PLACEHOLDER_DIGITS, PLACEHOLDER_PREFIX
from decktalk.inputs import Inputs
from decktalk.results import Word

TAKE_SUFFIX = ".mp3"
"""The suffix ElevenLabs's default format, mp3_44100_128, names a take with, which every take here is under."""


def a_take(section: int, *, seconds: float = 1.0, spoken: str = "x", **fields: object) -> Take:
    """One voiced take that runs `seconds` long, with its speech and its sound ending where it ends.

    The count of words and characters is read off `spoken`, so a row stays consistent with its own
    text. A call site names every other field its assertions read, and those replace the defaults. A row
    that says it was not voiced is named by a placeholder digest, as narrate names one.
    """
    row: dict[str, object] = {
        "section": section,
        "key": f"{section:02d}",
        "chapter": f"Section {section}",
        "digest": f"{section:016x}",
        "voiced": True,
        "word_count": len(spoken.split()),
        "characters": len(spoken),
        "estimated_seconds": seconds,
        "duration_seconds": seconds,
        "speech_end_seconds": seconds,
        "sound_end_seconds": seconds,
        "spoken": spoken,
    }
    if fields.get("voiced") is False and "digest" not in fields:
        row["digest"] = f"{PLACEHOLDER_PREFIX}{section:0{PLACEHOLDER_DIGITS}x}"
    return Take.model_validate({**row, **fields})


def write_takes(inputs: Inputs, *takes: Take) -> Takes:
    """The take index of a project, written where the stages read it, with no stage reading its model."""
    index = Takes(script="script.md", model="m", output_format="mp3_44100_128", sections=takes)
    index.write(inputs.workspace.takes_path)
    return index


def hold_take(inputs: Inputs, digest: str, *, audio: bytes = b"take", words: tuple[Word, ...] = ()) -> None:
    """Put a good pair of this take on disk: its audio and the words file that vouches for it.

    A voiced take goes to the takes directory with the words a provider would send back, and a
    placeholder goes under the build with estimated words, which is where each kind is looked for.
    """
    if is_placeholder(digest):
        place = inputs.workspace.narrate_dir
        said: Words = EstimatedWords(words=words)
    else:
        place = inputs.workspace.takes
        said = ProviderWords(words=words, audio=AudioPrint.of(audio, suffix=TAKE_SUFFIX))
    place.mkdir(parents=True, exist_ok=True)
    (place / take_file(digest, TAKE_SUFFIX)).write_bytes(audio)
    said.write(place / words_file(digest))


def narrated(inputs: Inputs, *takes: Take, words: Mapping[int, Sequence[Word]] | None = None) -> Takes:
    """A project narrated with no stage: the index of these rows, and each row's take held with its section's words.

    Each take goes where the reader looks for it and is written as the kind its digest names, so a test
    states the words a section speaks and never where they are kept or what the file is called.
    """
    said = words or {}
    for take in takes:
        hold_take(inputs, take.digest, words=tuple(said.get(take.section, ())))
    return write_takes(inputs, *takes)


def damage_take(inputs: Inputs, digest: str) -> None:
    """Have this voiced take's words vouch for other audio than the file holds, which is a damaged copy."""
    words = ProviderWords(audio=AudioPrint.of(b"another take", suffix=TAKE_SUFFIX))
    words.write(inputs.workspace.takes / words_file(digest))
