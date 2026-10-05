"""Every path under `build/`, named once.

    build/narrate/      the take index, the joined narration and every placeholder take,
                        while every take a voice spoke sits in the takes directory
    build/cue-times.json  every cue resolved against those words
    build/recordings/   one webm and one recording log per page section
    build/score/        the music joined from the parts the score bought,
                        while every bought sound and its ledger sit in the score directory
    build/sections/     one mp4 per section, cut to its span, and the key it was cut from
    build/frames/       the frozen slides `check` compares
    build/stills/       every frozen frame kept by what drew it, which check, storyboard
                        and the poster all read before they draw one
    build/storyboard/   one still per panel, under the page that lays them out
    build/final/        the deliverables: the film, its captions, chapters, placements,
                        transcript page and poster
    build/events/       one JSON lines file per run, which is what a run says it is doing
    build/kept.json     what the last build's assemble and verify read, wrote and found
    build/.lock         the lock a writing run holds, and `.lock.owner` beside it naming who

A new artifact gets a property here and nowhere else, so a reader who wants to know what a build
leaves behind opens one module and no stage ever spells a build path by hand. The five paths the
pipeline declares are read off `Artifact` itself, moved under whichever build directory the project
names, so the two cannot drift.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from decktalk.artifacts import take_file
from decktalk.inputs.paths import confined
from decktalk.pipeline import Artifact

SECTION_CUT = re.compile(r"(\d+)\.(?:mp4|json)")
"""What a section's cut and the key beside it are called, which is how a pair a renumbering left is spotted."""

EVENTS_SUFFIX = ".jsonl"
"""What a run's event file is called after its run id, which is one JSON object per line."""

KEPT_FILE = "kept.json"
"""What the record of the last assemble and verify is called, under the project's build directory."""

LOCK_FILE = ".lock"
"""What the file a writer holds is called, under the build directory it is writing into."""

OWNER_FILE = ".lock.owner"
"""What the note that names the writer holding the lock is called, beside the lock itself."""

LEDGER_FILE = "ledger.json"
"""What the record of bought audio is called, in the score directory beside the audio it records."""

WORK_MARK = "."
"""What a work file's name opens with, so nothing a viewer can open is written until the film is whole."""


@dataclass(frozen=True)
class Workspace:
    """Where one project's build output lives, and what each file in it is called."""

    root: Path
    build: Path
    name: str
    suffix: str
    """What a voiced take is written under, which the voice's adapter declares for the format it asks for."""
    takes: Path
    """The project's takes directory, which `[narration] takes_dir` names inside the project."""
    score_dir: Path
    """The project's score directory, which `[score] dir` names inside the project and holds what the score bought."""
    store: Path | None = None
    """The machine's take store, a second copy of every voiced take this machine makes, which many projects read."""

    def confine(self) -> None:
        """Refuse this build directory when anything in it leads outside it, before a run writes there.

        Every path below is a plain join onto `build`, and a stage, ffmpeg and Chromium each write
        and delete through whatever those joins find on disk. So the tree is resolved again at the
        start of every run, which is the one moment before any of them touch it, rather than at each
        of the places that write. DeckTalk itself never makes a link under the build directory, so a
        tree that passes here stays inside the project for the length of the run.
        """
        confined(self.root, self.build)
        confined(self.root, self.takes, named="the takes directory")
        confined(self.root, self.score_dir, named="the score directory")

    def of(self, artifact: Artifact) -> Path:
        """Where this project keeps one artifact the pipeline declares, under its own build directory."""
        return self.build / artifact.value

    @property
    def narrate_dir(self) -> Path:
        """The joined narration and every placeholder take, which a build makes again for nothing."""
        return self.build / "narrate"

    def take_file(self, digest: str) -> str:
        """The name a take of this digest is written under, which is the suffix of the voice's own format."""
        return take_file(digest, self.suffix)

    @property
    def takes_path(self) -> Path:
        """The take index, which is a cache over the takes and so always sits under the build directory.

        A run that plays committed takes rewrites it, so keeping it beside them would change a tracked
        file on every checkout that builds, which is what a job with no key must never do.
        """
        return self.of(Artifact.TAKES)

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
    def joined_dir(self) -> Path:
        """The music joined from its bought parts, which is a cache the score makes again for nothing."""
        return self.of(Artifact.SCORE)

    @property
    def joined_music(self) -> Path:
        """Where the score joins the music from its parts, when the project names no file of its own."""
        return self.joined_dir / "music.mp3"

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
        """The storyboard, which a person reads before anything is bought."""
        return self.build / "storyboard.html"

    @property
    def kept_path(self) -> Path:
        return self.build / KEPT_FILE

    @property
    def lock_path(self) -> Path:
        """The lock a run that writes into the build directory holds for its length."""
        return self.build / LOCK_FILE

    @property
    def owner_path(self) -> Path:
        """The note beside the lock that names the process holding it."""
        return self.build / OWNER_FILE

    @property
    def ledger_path(self) -> Path:
        return self.score_dir / LEDGER_FILE

    @property
    def events_dir(self) -> Path:
        return self.build / "events"

    @property
    def final_dir(self) -> Path:
        return self.of(Artifact.FINAL)

    @property
    def film(self) -> Path:
        return self.final_dir / f"{self.name}.mp4"

    def stamped_film(self, stamp: str) -> Path:
        """A copy of the film named with when it was made, which `[output] timestamped_copy` asks for."""
        return self.final_dir / f"{self.name}-{stamp}.mp4"

    def work_file(self, step: str) -> Path:
        """A file assemble writes on its way to the film, such as `picture.mp4`, which the film's step names.

        It sits beside the film so the last step is a rename, and its name opens with a dot, so nothing
        a viewer can open is written until the film is whole.
        """
        return self.final_dir / f"{WORK_MARK}{self.name}.{step}"

    @property
    def placements_path(self) -> Path:
        return self.final_dir / "placements.json"

    def deliverables(self) -> dict[str, Path]:
        """Every file `assemble` writes into the final directory, keyed by what it is."""
        return {
            "film": self.film,
            "srt": self.final_dir / f"{self.name}.srt",
            "vtt": self.final_dir / f"{self.name}.vtt",
            "chapters": self.final_dir / f"{self.name}.chapters.txt",
            "placements": self.placements_path,
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

    def stray_videos(self, keys: tuple[str, ...]) -> tuple[Path, ...]:
        """Section videos and their keys in the sections directory whose section is no longer in `decktalk.toml`.

        A build made before the sections were renumbered or one was removed leaves such files
        behind, and nothing reads them again.
        """
        if not self.sections_dir.is_dir():
            return ()
        found = ((f, SECTION_CUT.fullmatch(f.name)) for f in self.sections_dir.iterdir())
        return tuple(sorted(f for f, cut in found if cut and cut[1] not in keys))


__all__ = ["EVENTS_SUFFIX", "Workspace"]
