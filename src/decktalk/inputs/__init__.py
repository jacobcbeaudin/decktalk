"""Everything DeckTalk knows about one project before a stage runs.

    document.py    the frozen decktalk.toml tables and the two kinds of section
    workspace.py   every path under build/, named once
    env.py         the project's .env, with every value handed back as a Secret
    script.py      script.md parsed into the sections the voice reads
    cues.py        cues.json parsed, and phrase matching over a take's words
    markers.py     media/markers.json parsed into typed Marker rows
    timeline.py    where the joined narration plays in the finished film
    paths.py       how a path and a place are published, which is project-relative

`Inputs` is the thin composer of the parsed document, the workspace, the project's secrets and the
tuning in force. It resolves relative paths against the project root, answers the questions that
need more than one of the four, and reads the artifacts under `build/`. Every rule lives in one of
the modules above, so a reader who wants the rule rather than the answer opens that module.

This layer sits below the stages, which is why a stage is handed one of these and never a `Project`.
It knows nothing about a run, a machine, an event or a result. Each loader takes a path rather than
a project, so a stage can parse one file without loading a whole project.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path, PurePosixPath
from typing import Any

from decktalk.artifacts import CueTimes, Cuts, RecordingLog, Takes, Words, content_digest, file_digest
from decktalk.artifacts.stills import Stills, still_key
from decktalk.artifacts.words import words_file
from decktalk.errors import InputError
from decktalk.files import current_text
from decktalk.inputs.cues import CuedSection, load_cues
from decktalk.inputs.document import (
    ClipSection,
    Document,
    Mix,
    MixEffect,
    MusicSpec,
    PageSection,
    Section,
    Soundscape,
    SoundSpec,
    Transition,
    Voice,
)
from decktalk.inputs.env import Env
from decktalk.inputs.markers import Markers, load_markers
from decktalk.inputs.paths import at, contained, relative
from decktalk.inputs.script import Segment, read_script
from decktalk.inputs.workspace import Workspace
from decktalk.page import PREVIEW_CUE_TIMES
from decktalk.results import Word
from decktalk.settings import PROJECT_FILE, Layers, Settings, key_warnings, load, read_project_toml

log = logging.getLogger(__name__)

ENV_FILE = ".env"
"""What a project calls the file its speech credential lives in, which is never committed."""


@dataclass(frozen=True)
class Inputs:
    """One project as it is written on disk: its document, its paths, its secrets and its tuning."""

    root: Path
    document: Document
    workspace: Workspace
    env: Env
    settings: Settings
    layers: Layers
    notes: tuple[str, ...] = ()
    """Every sentence the load wanted to say, which a caller reports as a line rather than printing."""

    @classmethod
    def load(
        cls,
        root: Path,
        *,
        environ: Mapping[str, str],
        machine: Mapping[str, Any] | None = None,
        overrides: tuple[str, ...] = (),
    ) -> Inputs:
        """The project in `root`, with every layer above the defaults already applied.

        Nothing here reads the environment or the working directory. The caller passes the
        environment it wants read, the machine's own tuning tables and any override for this run,
        which is what lets one process hold two projects without either leaking into the other.
        """
        root = root.resolve()
        if not (root / PROJECT_FILE).exists():
            raise InputError(
                f"{PROJECT_FILE} is not there.",
                hint="Run from a project directory, pass --project DIR, or make one with `decktalk init DIR`.",
                location=at(root / PROJECT_FILE, root),
            )
        toml = read_project_toml(root)
        document = Document.from_toml(toml, default_name=root.name)
        loaded = load(root, project=toml, machine=machine, environ=environ, overrides=overrides)
        takes_dir = cls._takes_dir(root, loaded.settings)
        notes = document.notes + tuple(key_warnings(toml, PROJECT_FILE)) + cls._takes_note(root, takes_dir)
        build = contained(root, document.build)
        cls._refuse_served_build(root, build, document)
        return cls(
            root=root,
            document=document,
            workspace=Workspace(root=root, build=build, name=document.name, takes=takes_dir),
            env=Env(file=root / ENV_FILE, environ=environ),
            settings=loaded.settings,
            layers=loaded.layers,
            notes=notes,
        )

    @staticmethod
    def _refuse_served_build(root: Path, build: Path, document: Document) -> None:
        """Refuse a build directory that shares a directory the origin serves, in either direction.

        The origin serves each page's whole directory, so a build inside one, or a page directory
        inside the build, hands the page the takes, the event lines, the recordings and the stills.
        Only a page's directory is served whole, so it is the one kind of served path that can hold
        the build, and every other served path is a single file.
        """
        held = build.resolve()
        for page in document.page_files:
            served = (root / page).parent.resolve()
            if held.is_relative_to(served) or served.is_relative_to(held):
                raise InputError(
                    f"the build directory {document.build} shares {Path(page).parent.as_posix()}, "
                    "which is served to the page, so the page could read what a build writes.",
                    hint="Keep the build directory and every page's directory apart, such as build/ and deck/.",
                    location=at(root / PROJECT_FILE, root),
                )

    @staticmethod
    def _takes_dir(root: Path, settings: Settings) -> Path | None:
        """Where the take files live when `[narration] cache_dir` moves them out of the build directory."""
        named = settings.narration.cache_dir
        return (root / named).resolve() if named else None

    @staticmethod
    def _takes_note(root: Path, takes: Path | None) -> tuple[str, ...]:
        """One sentence when the takes are kept outside the project, which the author should know about.

        A project file somebody else wrote should not send this machine's takes somewhere surprising
        without saying so, and the path is the author's own to allow or to change.
        """
        if takes is None or takes.is_relative_to(root):
            return ()
        return (
            f"[narration] cache_dir puts the takes at {takes}, which is outside this project. "
            "Takes are named by content, so several projects may share one such directory.",
        )

    # ---- paths --------------------------------------------------------------------------

    def path(self, named: str | Path) -> Path:
        """A path the document names, joined to the project root and refused when it leads outside it.

        Every file a project names is read through here, so a script, a cue file, a page, a clip or a
        sound that links out of the project is refused with `INPUT` before anything reads it.
        """
        return contained(self.root, named)

    def relative(self, path: Path) -> Path:
        """One path as every result and every finding publishes it, which is relative to the project."""
        return relative(path, self.root)

    @property
    def script_path(self) -> Path:
        return self.path(self.document.script)

    @property
    def cues_path(self) -> Path:
        return self.path(self.document.cues)

    # ---- the files the author writes ------------------------------------------------------

    def script(self) -> tuple[Segment, ...]:
        """Every section of `script.md`, checked against `decktalk.toml`, parsed once per project."""
        return self._parsed

    @cached_property
    def _parsed(self) -> tuple[Segment, ...]:
        """The script as `script` answers it, kept on this value alone so a replaced one reads it afresh."""
        declared = {section.number for section in self.document.sections}
        return tuple(read_script(self.script_path, self.root, declared=declared))

    def spoken(self) -> tuple[Segment, ...]:
        """Every section the voice reads, which is every one that does not play a clip."""
        clips = self.document.clip_numbers
        return tuple(segment for segment in self.script() if segment.index not in clips)

    def chapters(self) -> dict[int, str]:
        """One chapter title per section, which the film's chapter markers and a slate carry.

        A section names its own title with `chapter`. When it names none, the script's own heading
        is the title, because that is the heading the author already wrote, and a section with
        neither is named by its number.
        """
        try:
            headings = {segment.index: segment.title for segment in self.script()}
        except InputError as unread:
            log.debug(
                "The script did not parse, so a section with no chapter of its own is named by its number.",
                exc_info=unread,
            )
            headings = {}
        return {
            section.number: section.chapter or headings.get(section.number) or f"Section {section.number}"
            for section in self.document.sections
        }

    def cues(self) -> tuple[CuedSection, ...]:
        """Every section's cues from `cues.json`, checked against `decktalk.toml`."""
        return load_cues(self.cues_path, self.root, {section.number for section in self.document.sections})

    def cues_text(self) -> str:
        """`cues.json` as it is written, which a fix that rewrites one row is worked out on, or nothing."""
        return current_text(self.cues_path)

    def markers(self) -> Markers | None:
        """The parsed `[mix] music_markers` file, or None when the project names none."""
        if not self.document.mix.music_markers:
            return None
        path = self.path(self.document.mix.music_markers)
        return load_markers(path, self.root) if path.exists() else None

    # ---- where the narration sits ---------------------------------------------------------

    def lead_seconds(self, section: int) -> float:
        """Silence before the first word of one section, in seconds.

        It is the section's own `lead_seconds`, or `[narration] lead_seconds` when the section sets
        none, and it depends on nothing else: not on where the section sits, not on its neighbours,
        and not on whether this run voiced its take. It is silence rather than speech, so it is
        placed when the takes are joined and is never part of a take or of its digest, which is what
        lets a take serve whatever section number it ends up under. A clip has no take and no lead.
        """
        found = self.document.section(section)
        if not isinstance(found, PageSection):
            return 0.0
        own = found.lead_seconds
        return round(self.settings.narration.lead_seconds if own is None else own, 3)

    def tail_seconds(self, section: int) -> float:
        """Silence after the last sound of one section, in seconds.

        It is the section's own `tail_seconds`, or `[narration] tail_min_seconds` when the section
        sets none, and like the lead it is placement rather than take content.
        """
        found = self.document.section(section)
        if not isinstance(found, PageSection):
            return 0.0
        own = found.tail_seconds
        return round(self.settings.narration.tail_min_seconds if own is None else own, 3)

    def words(self, section: int, digest: str) -> tuple[Word, ...]:
        """One take's words in seconds after its section starts, which is after that section's lead."""
        found = Words.read(self.workspace.takes_dir / words_file(digest))
        if found is None:
            return ()
        lead = self.lead_seconds(section)
        return found.shifted(lead) if lead else found.words

    # ---- the artifacts under build/ ---------------------------------------------------------

    def takes(self) -> Takes | None:
        return Takes.read(self.workspace.takes_path)

    def cue_times(self) -> CueTimes | None:
        return CueTimes.read(self.workspace.cue_times_path)

    def cuts(self) -> Cuts | None:
        return Cuts.read(self.workspace.cuts_path)

    def recording_log(self, key: str) -> RecordingLog | None:
        """One section's recording log, or None when that section was never recorded."""
        return RecordingLog.read(self.workspace.recording_log(key))

    def preview_cues(self) -> dict[str, Any]:
        """What the origin answers the preview alias with, which is the resolved cues by scene.

        A recorded page is handed its seconds in its own URL. An author previewing the same deck has
        no such URL, so the page asks the origin for this document instead and the origin answers it
        from the artifact rather than from a file it serves.
        """
        resolved = self.cue_times()
        if resolved is None:
            return {"sections": []}
        scenes = {section.number: section.scene for section in self.document.page_sections}
        return resolved.preview(scenes)

    def served_paths(self) -> tuple[str, ...]:
        """Every project-relative path the local origin may answer for, in the order the document names them.

        The origin serves the deck directory, the files the document declares and the files the
        soundscape generates, and nothing else, so a recorded page and a preview an author leaves
        running both reach their own pictures and their own modules while the script, the cue file,
        the build directory and the credential beside them stay out of reach. A recorder and a
        preview reading two lists would be two answers to one security question.
        """
        document = self.document
        named: list[str] = [Path(page).parent.as_posix() for page in document.page_files]
        named += [section.clip for section in document.clip_sections]
        named += [section.words for section in document.clip_sections if section.words]
        mix = document.mix
        named += [name for name in (mix.music, mix.ambience, mix.slate, mix.music_markers) if name]
        named += [effect.file for effect in mix.effects]
        soundscape = document.soundscape
        generated = (soundscape.ambience, soundscape.music, *soundscape.effects.values())
        named += [item.out for item in generated if item is not None and item.out]
        # A name is folded as a path rather than stripped as text, and one that folds to the root
        # itself is dropped, because a declared root would declare the whole project.
        spelled = (PurePosixPath(name).as_posix() for name in named if name)
        return tuple(dict.fromkeys(name for name in spelled if name != "."))

    def documents(self) -> dict[str, bytes]:
        """Every path the origin answers from memory rather than from a file, as the bytes it sends.

        A recording keyed on one of these would be keyed on its own output, so the origin never
        counts them among the assets a section was recorded from.
        """
        return {PREVIEW_CUE_TIMES: json.dumps(self.preview_cues()).encode("utf-8")}

    # ---- frozen frames -------------------------------------------------------------------------

    @property
    def stills(self) -> Stills:
        """The frozen frames this project keeps, which check, storyboard and the poster share."""
        return Stills(self.workspace.stills_dir, self.root)

    def still_key(self, page: str, *identity: str, documents: Mapping[str, bytes] | None = None) -> str:
        """The name of one frozen frame of `page`, from everything that decides how it looks before it loads.

        `identity` is what the caller asks the page for, which is the URL of a frozen state or the
        section a poster stands for. The frame size, the colour scheme, the page policy, the motion and
        the page file itself decide the picture as surely as the URL does, and so does anything the
        origin answers from memory. The files the page loads once it is open are named by the manifest beside the
        frame rather than here, because nobody knows them until the page has asked.
        """
        video, record, motion = self.settings.video, self.settings.record, self.settings.motion
        served = sorted((documents or {}).items())
        return still_key(
            (
                *identity,
                f"{video.width}x{video.height}",
                record.color_scheme,
                f"policy:{record.page_policy}",
                f"motion:{motion.reduce}:{motion.scale:g}",
                f"page:{page}:{file_digest(self.path(page))}",
                *(f"served:{name}:{content_digest(body)}" for name, body in served),
            )
        )

    # ---- what a changed file touches --------------------------------------------------------

    def sections_touching(self, path: Path) -> tuple[int, ...]:
        """Every section a change to this file would change, in section order.

        A watch loop rebuilds what moved and nothing else, so this is the one reading it needs: a
        page belongs to the sections that play it, a clip to the section that plays it, and the
        script, the cue file and the project file each belong to every section at once.
        """
        wanted = self.relative(path).as_posix()
        whole = {self.relative(self.script_path).as_posix(), self.relative(self.cues_path).as_posix(), PROJECT_FILE}
        if wanted in whole:
            return tuple(section.number for section in self.document.sections)
        return tuple(
            section.number
            for section in self.document.sections
            if (section.page if isinstance(section, PageSection) else section.clip) == wanted
        )


__all__ = [
    "ClipSection",
    "Document",
    "Env",
    "Inputs",
    "Mix",
    "MixEffect",
    "MusicSpec",
    "PageSection",
    "Section",
    "Segment",
    "SoundSpec",
    "Soundscape",
    "Transition",
    "Voice",
    "Workspace",
]
