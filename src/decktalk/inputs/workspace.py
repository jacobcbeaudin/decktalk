"""Every path under `build/`, named once.

    build/narrate/      the takes, their words files and the take index
    build/cue-times.json  every cue resolved against those words
    build/recordings/   one webm and one recording log per page section
    build/soundscape/   the music, the ambience bed and the effects
    build/sections/     one mp4 per section, cut to its span
    build/frames/       the frozen slides `check` compares
    build/storyboard/   one still per panel, under the page that lays them out
    build/final/        the deliverables: the film, its captions, chapters, cut list,
                        transcript page and poster
    build/events/       one JSON lines file per run, which is what a run says it is doing

A new artifact gets a property here and nowhere else, so a reader who wants to know what a build
leaves behind opens one module and no stage ever spells a build path by hand. The five paths the
pipeline declares are held against `Artifact` by this module's own test, so the two cannot drift.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

SECTION_VIDEO = re.compile(r"\d+\.mp4")
"""What a section's cut is called, which is how a cut left over from a renumbering is spotted."""

EVENTS_SUFFIX = ".jsonl"
"""What a run's event file is called after its run id, which is one JSON object per line."""


@dataclass(frozen=True)
class Workspace:
    """Where one project's build output lives, and what each file in it is called."""

    root: Path
    build: Path
    name: str
    takes: Path | None = None

    @property
    def narrate_dir(self) -> Path:
        """The take index and the joined narration, which belong to this project alone."""
        return self.build / "narrate"

    @property
    def takes_dir(self) -> Path:
        """Where the take files live, which many projects may share because a digest names each one."""
        return self.takes or self.narrate_dir

    @property
    def takes_path(self) -> Path:
        return self.narrate_dir / "takes.json"

    @property
    def narration_path(self) -> Path:
        """The takes joined into one track, which `assemble` mixes under the picture."""
        return self.narrate_dir / "narration.mp3"

    @property
    def cue_times_path(self) -> Path:
        return self.build / "cue-times.json"

    @property
    def recordings_dir(self) -> Path:
        return self.build / "recordings"

    @property
    def soundscape_dir(self) -> Path:
        return self.build / "soundscape"

    @property
    def sections_dir(self) -> Path:
        return self.build / "sections"

    @property
    def frames_dir(self) -> Path:
        """The frozen slides `check` compares, which are pictures rather than a deliverable."""
        return self.build / "frames"

    @property
    def storyboard_dir(self) -> Path:
        return self.build / "storyboard"

    @property
    def storyboard_path(self) -> Path:
        """The contact sheet a person reads before any credit is spent."""
        return self.build / "storyboard.html"

    @property
    def events_dir(self) -> Path:
        return self.build / "events"

    @property
    def final_dir(self) -> Path:
        return self.build / "final"

    @property
    def film(self) -> Path:
        return self.final_dir / f"{self.name}.mp4"

    @property
    def cuts_path(self) -> Path:
        return self.final_dir / "cuts.json"

    def deliverables(self) -> dict[str, Path]:
        """Every file `assemble` writes into the final directory, keyed by what it is."""
        return {
            "film": self.film,
            "srt": self.final_dir / f"{self.name}.srt",
            "vtt": self.final_dir / f"{self.name}.vtt",
            "chapters": self.final_dir / f"{self.name}.chapters.txt",
            "cuts": self.cuts_path,
            "transcript": self.final_dir / f"{self.name}-transcript.html",
            "poster": self.final_dir / f"{self.name}-poster.png",
        }

    def events_path(self, run: str) -> Path:
        """Where one run appends its lines, which is one file per run so two runs never collide."""
        return self.events_dir / f"{run}{EVENTS_SUFFIX}"

    def recording(self, key: str) -> Path:
        return self.recordings_dir / f"{key}.webm"

    def recording_log(self, key: str) -> Path:
        return self.recordings_dir / f"{key}.json"

    def section_video(self, key: str) -> Path:
        return self.sections_dir / f"{key}.mp4"

    def stray_section_videos(self, keys: tuple[str, ...]) -> tuple[Path, ...]:
        """Cuts in the sections directory whose section is no longer in `decktalk.toml`.

        A build made before the sections were renumbered or one was removed leaves such files
        behind, and a later run would otherwise cut them into a film nobody asked for.
        """
        if not self.sections_dir.is_dir():
            return ()
        listed = {self.section_video(key).name for key in keys}
        found = self.sections_dir.iterdir()
        return tuple(sorted(f for f in found if SECTION_VIDEO.fullmatch(f.name) and f.name not in listed))


__all__ = ["EVENTS_SUFFIX", "Workspace"]
