"""Every path under `build/`, named once.

    build/narrate/      the takes, their words files and the take index, unless
                        `[narration] takes_dir` keeps them in the project, and the
                        joined narration and every placeholder take in every case
    build/cue-times.json  every cue resolved against those words
    build/recordings/   one webm and one recording log per page section
    build/soundscape/   the music, the ambience bed and the effects
    build/sections/     one mp4 per section, cut to its span, and the key it was cut from
    build/frames/       the frozen slides `check` compares
    build/stills/       every frozen frame kept by what drew it, which check, storyboard
                        and the poster all read before they draw one
    build/storyboard/   one still per panel, under the page that lays them out
    build/final/        the deliverables: the film, its captions, chapters, cut list,
                        transcript page and poster
    build/events/       one JSON lines file per run, which is what a run says it is doing

A new artifact gets a property here and nowhere else, so a reader who wants to know what a build
leaves behind opens one module and no stage ever spells a build path by hand. The five paths the
pipeline declares are read off `Artifact` itself, moved under whichever build directory the project
names, so the two cannot drift.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from decktalk.artifacts import is_placeholder, take_file, words_file
from decktalk.inputs.paths import confined
from decktalk.pipeline import Artifact

SECTION_CUT = re.compile(r"(\d+)\.(?:mp4|json)")
"""What a section's cut and the key beside it are called, which is how a pair a renumbering left is spotted."""

EVENTS_SUFFIX = ".jsonl"
"""What a run's event file is called after its run id, which is one JSON object per line."""


@dataclass(frozen=True)
class Workspace:
    """Where one project's build output lives, and what each file in it is called."""

    root: Path
    build: Path
    name: str
    takes: Path | None = None
    """The project's own take directory, which `[narration] takes_dir` names inside the project."""
    shared: Path | None = None
    """The machine's take store, which `[narration] cache_dir` names and many projects may read."""

    def confine(self) -> None:
        """Refuse this build directory when anything in it leads outside it, before a run writes there.

        Every path below is a plain join onto `build`, and a stage, ffmpeg and Chromium each write
        and delete through whatever those joins find on disk. So the tree is resolved again at the
        start of every run, which is the one moment before any of them touch it, rather than at each
        of the places that write. DeckTalk itself never makes a link under the build directory, so a
        tree that passes here stays inside the project for the length of the run.
        """
        confined(self.root, self.build)
        if self.takes is not None:
            confined(self.root, self.takes, named="the take directory")

    def of(self, artifact: Artifact) -> Path:
        """Where this project keeps one artifact the pipeline declares, under its own build directory."""
        return self.build.joinpath(*PurePosixPath(artifact.value).parts[1:])

    @property
    def narrate_dir(self) -> Path:
        """The joined narration and every placeholder take, which a build makes again for nothing."""
        return self.build / "narrate"

    @property
    def takes_dir(self) -> Path:
        """Where a bought take is written: the project's take directory, else the machine's store, else the build."""
        return self.takes or self.shared or self.narrate_dir

    @property
    def take_places(self) -> tuple[Path, ...]:
        """Every directory a take is looked for in, first to last: the project's, the machine's, the build's."""
        found = (self.takes, self.shared, self.narrate_dir)
        return tuple(dict.fromkeys(place for place in found if place is not None))

    def holding(self, digest: str) -> Path | None:
        """The first place that holds both the take of this digest and its words file, or None."""
        places = (self.narrate_dir,) if is_placeholder(digest) else self.take_places
        both = (take_file(digest), words_file(digest))
        return next((place for place in places if all((place / name).exists() for name in both)), None)

    def home_of(self, digest: str) -> Path:
        """Where the take of this digest is written, which is the build for a placeholder nobody paid for."""
        return self.narrate_dir if is_placeholder(digest) else self.takes_dir

    def take_path(self, digest: str) -> Path:
        """The audio file of this take where it is found, or where it would be written when it is nowhere."""
        return (self.holding(digest) or self.home_of(digest)) / take_file(digest)

    def words_path(self, digest: str) -> Path:
        """The words file of this take where it is found, or where it would be written when it is nowhere."""
        return (self.holding(digest) or self.home_of(digest)) / words_file(digest)

    @property
    def takes_path(self) -> Path:
        """The take index, which sits beside the project's own takes and is never in the machine's store."""
        index = self.of(Artifact.TAKES)
        return index if self.takes is None else self.takes / index.name

    @property
    def narration_path(self) -> Path:
        """The takes joined into one track, which `assemble` mixes under the picture."""
        return self.narrate_dir / "narration.mp3"

    @property
    def cue_times_path(self) -> Path:
        return self.of(Artifact.CUE_TIMES)

    @property
    def recordings_dir(self) -> Path:
        return self.of(Artifact.RECORDINGS)

    @property
    def soundscape_dir(self) -> Path:
        return self.of(Artifact.SOUNDSCAPE)

    @property
    def sections_dir(self) -> Path:
        return self.build / "sections"

    @property
    def frames_dir(self) -> Path:
        """The frozen slides `check` compares, which are pictures rather than a deliverable."""
        return self.build / "frames"

    @property
    def stills_dir(self) -> Path:
        """Every frozen frame kept by what drew it, so one state of a page is drawn once."""
        return self.build / "stills"

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
        return self.of(Artifact.FINAL)

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

    def stray_cuts(self, keys: tuple[str, ...]) -> tuple[Path, ...]:
        """Cuts and cut keys in the sections directory whose section is no longer in `decktalk.toml`.

        A build made before the sections were renumbered or one was removed leaves such files
        behind, and nothing reads them again.
        """
        if not self.sections_dir.is_dir():
            return ()
        found = ((f, SECTION_CUT.fullmatch(f.name)) for f in self.sections_dir.iterdir())
        return tuple(sorted(f for f, cut in found if cut and cut[1] not in keys))


__all__ = ["EVENTS_SUFFIX", "Workspace"]
