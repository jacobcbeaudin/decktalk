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

from decktalk.artifacts import (
    ClipWords,
    CueTimes,
    EstimatedWords,
    Placements,
    ProviderWords,
    RecordingLog,
    Takes,
    Unreadable,
    Words,
    content_digest,
    file_digest,
    is_placeholder,
)
from decktalk.artifacts.stills import Stills, still_key
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
    Score,
    Section,
    SoundSpec,
    Transition,
)
from decktalk.inputs.env import Env
from decktalk.inputs.markers import Markers, load_markers
from decktalk.inputs.paths import at, contained, relative
from decktalk.inputs.script import Segment, read_script
from decktalk.inputs.workspace import Workspace
from decktalk.page import PREVIEW_CUE_TIMES
from decktalk.results import Word
from decktalk.settings import BY_ID, PROJECT_FILE, Layers, Loaded, Settings
from decktalk.settings.layers import key_warnings, load, machine_folder, read_project_toml, value_of
from decktalk.speech import output_of

log = logging.getLogger(__name__)

ENV_FILE = ".env"
"""What a project calls the file its speech credential lives in, which is never committed."""

PAID_FOLDERS: Mapping[str, str] = {
    "narration.takes_dir": "the takes",
    "score.dir": "the bought sounds",
}
"""Every setting that names a project folder of paid records, with what the folder holds.

Each one is read through `Inputs._paid_folder`, so a folder added here is held to the project and
kept out of the build directory without a check of its own.
"""


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
        store: Path | None = None,
    ) -> Inputs:
        """The project in `root`, with every layer above the defaults already applied.

        Nothing here reads the environment or the working directory. The caller passes the
        environment it wants read, the machine's own tuning tables and any override for this run,
        which is what lets one process hold two projects without either leaking into the other.
        `store` is the take store the machine keeps when `[narration] store_dir` names none, or None
        for a machine that keeps none.
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
        build = contained(root, document.build)
        paid = {key: cls._paid_folder(root, loaded.settings, key, build, document.build) for key in PAID_FOLDERS}
        store = cls._take_store(root, loaded, environ, store)
        notes = document.notes + tuple(key_warnings(toml, PROJECT_FILE))
        cls._refuse_served_build(root, build, document)
        return cls(
            root=root,
            document=document,
            workspace=Workspace(
                root=root,
                build=build,
                name=document.name,
                suffix=output_of(loaded.settings, loaded.settings.voice.provider).suffix,
                takes=paid["narration.takes_dir"],
                score_dir=paid["score.dir"],
                store=store,
            ),
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
    def _paid_folder(root: Path, settings: Settings, key: str, build: Path, build_named: str) -> Path:
        """The project directory one paid-folder key names, refused unless the project keeps it.

        Every key in `PAID_FOLDERS` goes through here. The folder is committed and travels with the
        project, so it is held to the project the way every file the project names is. An absolute
        path is refused by its spelling even when it happens to point inside, because the same file on
        another clone would point somewhere else, and the project directory itself is refused because
        the paid records would then sit among the files an author writes. A folder inside the build
        directory, which `[project] build` names, is refused too, and so is a build directory inside the
        folder, because the build is a cache that is deleted and a paid record deleted with it is bought
        again. The key's own range has already refused an empty value.
        """
        spec = BY_ID[key]
        named = str(value_of(settings, key))
        what = PAID_FOLDERS[key]
        located = at(root / PROJECT_FILE, root)
        suggestion = f'Name a directory inside the project, such as {spec.name} = "{spec.default}", and commit it.'
        refusal = InputError(
            f"[{spec.table}] {spec.name} is {named}, which is not a directory inside the project, "
            f"so {what} would not travel with it.",
            hint=suggestion,
            location=located,
        )
        if Path(named).is_absolute():
            raise refusal
        try:
            kept = contained(root, named)
        except InputError as outside:
            raise refusal from outside
        if kept.resolve() == root.resolve():
            raise refusal
        held, cache = kept.resolve(), build.resolve()
        if held.is_relative_to(cache) or cache.is_relative_to(held):
            raise InputError(
                f"[{spec.table}] {spec.name} is {named} and [project] build is {build_named}, so one is inside "
                f"the other. The build directory is a cache that is deleted, and {what} are paid records that "
                "must never be deleted with it or bought again.",
                hint=suggestion,
                location=located,
            )
        return kept

    @staticmethod
    def _take_store(root: Path, loaded: Loaded, environ: Mapping[str, str], standard: Path | None) -> Path | None:
        """The machine's take store, which `[narration] store_dir` names and is read after the project's.

        When the key names none it is the machine's own standard folder, or none for a machine that
        keeps none. It is refused inside this project, where it would be a second takes directory with
        none of its rules. The machine refuses one inside its tool cache, where it knows that folder.
        """
        store = machine_folder(loaded, "narration.store_dir", environ) or standard
        if store is None:
            return None
        if store.resolve().is_relative_to(root.resolve()):
            raise InputError(
                f"[narration] store_dir is {store}, which is inside this project, so the take store would be a "
                "second takes directory that a commit could carry.",
                hint="Name a folder outside every project, such as ~/decktalk-takes, or leave the key unset.",
                location=at(root / PROJECT_FILE, root),
            )
        return store

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

    def clip_words(self, section: ClipSection) -> ClipWords | None:
        """The words file a clip section's `words` key names, or None when it names none or it is not there.

        The author wrote that file, or kept the one `decktalk clip` wrote, so DeckTalk cannot build it
        again. One that does not read is refused as `INPUT`, naming the key that named it, and nothing
        here deletes it.
        """
        if not section.words:
            return None
        path = self.path(section.words)
        try:
            return ClipWords.parse(path)
        except Unreadable as unread:
            raise InputError(
                f"{PROJECT_FILE}: [[section]] number={section.number} words names {section.words}, and {unread}",
                hint=f"Fix {section.words}, write it again with decktalk clip, or take the words key off "
                f"section {section.number} in {PROJECT_FILE}.",
                location=at(path, self.root, section=section.number),
            ) from unread

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

        It is the section's own `tail_seconds`, or `[narration] tail_seconds` when the section
        sets none, and like the lead it is placement rather than take content.
        """
        found = self.document.section(section)
        if not isinstance(found, PageSection):
            return 0.0
        own = found.tail_seconds
        return round(self.settings.narration.tail_seconds if own is None else own, 3)

    def words(self, section: int, digest: str) -> tuple[Word, ...]:
        """One take's words in seconds after its section starts, which is after that section's lead."""
        found = self.take_words(digest)
        if found is None:
            return ()
        lead = self.lead_seconds(section)
        return found.shifted(lead) if lead else found.words

    # ---- the artifacts under build/ ---------------------------------------------------------

    def take_words(self, digest: str) -> Words | None:
        """One take's words on the take's own clock, or None when it has none yet.

        Every reader of a take's words comes here, because the digest says who timed them: the speech
        provider sent back the words of a voiced take, which are a paid record refused rather than built
        again when they do not read, and DeckTalk estimated a placeholder's, which are a cache.
        """
        model = EstimatedWords if is_placeholder(digest) else ProviderWords
        return model.read(self.workspace.words_path(digest))

    def takes(self) -> Takes | None:
        """The take index as narrate last wrote it, or None when there is none that reads.

        The index is a cache over the takes on disk: every row is read again off the script, the
        settings and the take and words files its digest names, so narrate builds an index that does
        not read again for nothing and never buys a take for it. A stage that cannot run without the
        index asks `Takes.require`, which refuses one that does not read as never built.
        """
        return Takes.previous(self.workspace.takes_path)

    def cue_times(self) -> CueTimes | None:
        return CueTimes.read(self.workspace.cue_times_path)

    def placements(self) -> Placements | None:
        return Placements.read(self.workspace.placements_path)

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
        score generates, and nothing else, so a recorded page and a preview an author leaves
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
        score = document.score
        generated = (score.ambience, score.music, *score.effects.values())
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
    "Score",
    "Transition",
    "Workspace",
]
