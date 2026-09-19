"""Every path under `build/`, named once.

    build/narration/   the takes, their words files, the take index and the narration clock
    build/cue-times.json  every cue resolved against those words
    build/recordings/  one webm and one recording log per page section
    build/sections/    one mp4 per section, cut to its span
    build/screenshots/ the PNGs `decktalk screenshots` writes
    build/out/         the deliverables: the final mp4, its captions and its chapters

A new artifact gets a property here and nowhere else, so a reader who wants to know what a build
leaves behind opens one module, and no stage ever spells a build path by hand.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

SECTION_VIDEO_RE = re.compile(r"\d+\.mp4")


@dataclass(frozen=True)
class Workspace:
    """Where one project's build output lives."""

    build: Path
    name: str  # The project name, which the deliverables are named after.
    narration: Path | None = None  # Where the takes live, when [narration] cache_dir moves them out of build/.

    @property
    def narration_dir(self) -> Path:
        return self.narration or self.build / "narration"

    @property
    def recordings_dir(self) -> Path:
        return self.build / "recordings"

    @property
    def out_dir(self) -> Path:
        return self.build / "out"

    @property
    def sections_dir(self) -> Path:
        return self.build / "sections"

    @property
    def screenshots_dir(self) -> Path:
        return self.build / "screenshots"

    @property
    def takes_path(self) -> Path:
        return self.narration_dir / "takes.json"

    @property
    def timeline_path(self) -> Path:
        return self.narration_dir / "timeline.json"

    @property
    def cue_times_path(self) -> Path:
        return self.build / "cue-times.json"

    @property
    def final(self) -> Path:
        return self.out_dir / f"{self.name}.mp4"

    def output_paths(self) -> dict[str, Path]:
        """The files `assemble` writes next to the final mp4, keyed final, srt, vtt and chapters."""
        return {
            "final": self.final,
            "srt": self.out_dir / f"{self.name}.srt",
            "vtt": self.out_dir / f"{self.name}.vtt",
            "chapters": self.out_dir / f"{self.name}.chapters.txt",
        }

    def recording(self, key: str) -> Path:
        return self.recordings_dir / f"{key}.webm"

    def recording_log(self, key: str) -> Path:
        return self.recordings_dir / f"{key}.json"

    def section_video(self, key: str) -> Path:
        return self.sections_dir / f"{key}.mp4"

    def stray_section_videos(self, keys: list[str]) -> list[Path]:
        """Files in build/sections named like a section video whose section is not in decktalk.toml.

        A build before sections were renumbered or removed leaves such files behind.
        """
        if not self.sections_dir.is_dir():
            return []
        listed = {self.section_video(key).name for key in keys}
        return sorted(
            f for f in self.sections_dir.iterdir() if SECTION_VIDEO_RE.fullmatch(f.name) and f.name not in listed
        )
