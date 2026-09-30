"""One take row, one take index and the plans of a run, built one way for every test that needs them."""

from __future__ import annotations

from decktalk.artifacts import Take, Takes
from decktalk.inputs import Inputs, Segment
from decktalk.results import TakeStatus
from decktalk.stages.narrate.plan import TakePlan


def a_take(section: int, *, seconds: float = 1.0, spoken: str = "x", **fields: object) -> Take:
    """One voiced take that runs `seconds` long, with its speech and its sound ending where it ends.

    The count of words and characters is read off `spoken`, so a row stays consistent with its own
    text. A call site names every other field its assertions read, and those replace the defaults.
    """
    row: dict[str, object] = {
        "section": section,
        "key": f"{section:02d}",
        "chapter": f"Section {section}",
        "hash": f"{section:016x}",
        "voiced": True,
        "word_count": len(spoken.split()),
        "characters": len(spoken),
        "estimated_seconds": seconds,
        "duration_seconds": seconds,
        "speech_end_seconds": seconds,
        "sound_end_seconds": seconds,
        "spoken": spoken,
    }
    return Take.model_validate({**row, **fields})


def write_takes(inputs: Inputs, *takes: Take) -> Takes:
    """The take index of a project, written where the stages read it, with no stage reading its model."""
    index = Takes(script="script.md", model="m", output_format="mp3_44100_128", sections=takes)
    index.write(inputs.workspace.takes_path)
    return index


def planned(*sections: int) -> list[TakePlan]:
    """One plan to voice each numbered section, as the narrate stage hands its pool of workers them."""
    return [
        TakePlan(
            segment=Segment(index=n, title=f"Section {n}", slug=f"section-{n}", text="x"), status=TakeStatus.VOICED
        )
        for n in sections
    ]
