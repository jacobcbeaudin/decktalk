"""A DeckTalk project, which is a directory holding decktalk.toml, a script, cues, pages and media.

    my-lesson/
      decktalk.toml      the document (document.py) plus optional tuning tables (settings.py)
      script.md          narration in "## N. Title" sections, with [bracketed directions] unspoken
      cues.json          which spoken phrase each visual lands on
      deck/index.html    HTML scenes, which decktalk-runtime.js gives the ?cues= contract
      media/             your clips, b-roll, slate, markers.json
      .env               ELEVENLABS_API_KEY, ELEVENLABS_VOICE_ID (never committed)
      build/             everything generated (git-ignored)

`Project` is the thin composer of those four, which are the parsed document, the workspace under
`build/`, the secrets in `.env`, and the tuning. It resolves relative paths against the project
root, answers the questions that need more than one of the four, and reads the artifacts the
stages share. Everything it knows lives in one of the four, and each property below forwards to
the one that owns it, so a reader who wants the rule rather than the answer opens that module.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..artifacts import CueTimes, Takes, Timeline, Word, read_words
from ..errors import ConfigError, DeckTalkError
from ..jsonio import relative
from ..secret import Secret
from ..settings import PROJECT_FILE, Settings, load_settings, read_project_toml, settings_key_warnings
from .cues import SectionCues, load_cues
from .document import ClipSection, Document, Mix, PageSection, Section, Soundscape, Transition, Voice
from .env import Env
from .markers import Markers, load_markers
from .script import Segment, read_script
from .workspace import Workspace

log = logging.getLogger(__name__)


@dataclass
class Project:
    """A loaded, validated project. Build with Project.load(directory)."""

    root: Path
    document: Document
    workspace: Workspace
    env: Env
    settings: Settings
    # script.md parsed once, because the chapters, the leads and every take row ask for it.
    _script_sections: tuple[list[Segment], list[Segment]] | None = field(default=None, repr=False, compare=False)

    @classmethod
    def load(cls, where: Path | str | None = None, *, environ: dict[str, str] | None = None) -> Project:
        """The project in `where`, or in DECKTALK_PROJECT, or in the current directory."""
        root = Path(where or os.environ.get("DECKTALK_PROJECT") or ".").resolve()
        if root.is_file() and root.name == PROJECT_FILE:
            root = root.parent
        if not (root / PROJECT_FILE).exists():
            raise ConfigError(
                f"{root / PROJECT_FILE} not found. Run from a project directory, pass --project DIR, "
                "or create one with `decktalk init DIR`."
            )
        return cls.from_toml(root, read_project_toml(root), environ=environ)

    @classmethod
    def from_toml(cls, root: Path, doc: dict[str, Any], *, environ: dict[str, str] | None = None) -> Project:
        """The project a parsed decktalk.toml describes, rooted at `root`."""
        root = root.resolve()
        document = Document.from_toml(doc, default_name=root.name)
        for message in settings_key_warnings(doc, PROJECT_FILE):
            log.warning(message)
        settings = load_settings(root, toml=doc, environ=environ)
        cache = settings.narration.cache_dir
        takes = (root / cache).resolve() if cache else None
        if takes is not None and not takes.is_relative_to(root):
            # A project file somebody else wrote should not send this machine's takes somewhere
            # surprising without saying so, and the path is the user's own to allow or change.
            variable = "DECKTALK_NARRATION_CACHE_DIR"
            chose = (os.environ if environ is None else environ).get(variable)
            named_by = variable if chose else "[narration] cache_dir"
            log.warning(
                "%s puts the takes at %s, which is outside this project. Takes are named by content "
                "hash, so several projects may share one such directory.",
                named_by,
                takes,
            )
        return cls(
            root=root,
            document=document,
            workspace=Workspace(
                build=root / document.build,
                name=document.name,
                takes=takes,
            ),
            env=Env(file=root / ".env", environ=os.environ if environ is None else environ),
            settings=settings,
        )

    def path(self, rel: str | Path) -> Path:
        """A path from the document, resolved against the project root."""
        p = Path(rel)
        return p if p.is_absolute() else self.root / p

    # ---- the document ----------------------------------------------------------------
    @property
    def name(self) -> str:
        return self.document.name

    @property
    def script(self) -> Path:
        return self.path(self.document.script)

    @property
    def cues(self) -> Path:
        return self.path(self.document.cues)

    @property
    def sections(self) -> list[Section]:
        return self.document.sections

    @property
    def voice(self) -> Voice:
        return self.document.voice

    @property
    def transition(self) -> Transition:
        return self.document.transition

    @property
    def mix(self) -> Mix:
        return self.document.mix

    @property
    def soundscape(self) -> Soundscape:
        return self.document.soundscape

    def section(self, number: int) -> Section | None:
        return self.document.section(number)

    @property
    def page_sections(self) -> list[PageSection]:
        return self.document.page_sections

    @property
    def clip_sections(self) -> list[ClipSection]:
        return self.document.clip_sections

    @property
    def clip_numbers(self) -> set[int]:
        return self.document.clip_numbers

    @property
    def page_files(self) -> list[str]:
        return self.document.page_files

    # ---- the workspace ---------------------------------------------------------------
    @property
    def build(self) -> Path:
        return self.workspace.build

    @property
    def narration_dir(self) -> Path:
        return self.workspace.narration_dir

    @property
    def takes_dir(self) -> Path:
        return self.workspace.takes_dir

    @property
    def recordings_dir(self) -> Path:
        return self.workspace.recordings_dir

    @property
    def out_dir(self) -> Path:
        return self.workspace.out_dir

    @property
    def sections_dir(self) -> Path:
        return self.workspace.sections_dir

    @property
    def screenshots_dir(self) -> Path:
        return self.workspace.screenshots_dir

    @property
    def takes_path(self) -> Path:
        return self.workspace.takes_path

    @property
    def timeline_path(self) -> Path:
        return self.workspace.timeline_path

    @property
    def cue_times_path(self) -> Path:
        return self.workspace.cue_times_path

    @property
    def final(self) -> Path:
        return self.workspace.final

    def recording(self, section: Section) -> Path:
        return self.workspace.recording(section.key)

    def recording_log(self, section: Section) -> Path:
        return self.workspace.recording_log(section.key)

    def section_video(self, section: Section) -> Path:
        return self.workspace.section_video(section.key)

    def stray_section_videos(self) -> list[Path]:
        """Section videos in build/sections whose section is no longer in decktalk.toml."""
        return self.workspace.stray_section_videos([s.key for s in self.sections])

    def stray_section_warnings(self, command: str) -> list[str]:
        """One sentence per leftover section video, naming the command that ignores it."""
        return [
            f"{relative(f, self.root)} is not a section in decktalk.toml, so {command} ignores it. "
            "Delete the file if an earlier build left it."
            for f in self.stray_section_videos()
        ]

    # ---- secrets ---------------------------------------------------------------------
    def require_env(self, *keys: str) -> list[Secret]:
        """The values of these variables, or a ConfigError naming every one that is not set."""
        return self.env.require(*keys)

    # ---- the input files ---------------------------------------------------------------
    def script_sections(self) -> tuple[list[Segment], list[Segment]]:
        """(every section in script.md, the spoken ones in order), checked against decktalk.toml.

        The file is read once per project, as every other file this model owns is, because the
        chapters, the leads and each take row ask for it once per section.
        """
        if self._script_sections is None:
            self._script_sections = read_script(
                self.script, declared={s.number for s in self.sections}, clips=self.clip_numbers
            )
        return self._script_sections

    def chapters(self) -> dict[int, str]:
        """One chapter title per section, which the mp4's chapter markers and a slate carry.

        A section names its own title with `chapter`. When it names none, the script's own
        "## N. Title" heading is the title, because that is the heading the author already wrote,
        and a section with neither is named by its number.
        """
        try:
            headings = {s.index: s.title for s in self.script_sections()[0]}
        except DeckTalkError:
            headings = {}
        return {s.number: s.chapter or headings.get(s.number) or f"Section {s.number}" for s in self.sections}

    def cue_specs(self) -> list[SectionCues]:
        """Every section's cues from cues.json, checked against decktalk.toml."""
        return load_cues(self.cues, {s.number for s in self.sections})

    def markers(self) -> Markers | None:
        """The parsed `[mix] music_markers` file, or None when the project names none."""
        if not self.mix.music_markers:
            return None
        path = self.path(self.mix.music_markers)
        return load_markers(path) if path.exists() else None

    # ---- narration times -------------------------------------------------------------
    @property
    def first_spoken_key(self) -> str | None:
        """The two-digit key of the section the voice reads first, or None when the script cannot be read."""
        try:
            spoken = self.script_sections()[1]
        except DeckTalkError:
            return None
        return spoken[0].key if spoken else None

    def lead_seconds(self, key: str) -> float:
        """Silence before the first word of the section with this two-digit key, in whole milliseconds.

        It is the section's own `lead_seconds` plus, for the section the voice reads first,
        `[narration] opening_silence_seconds`. Both are silence rather than speech, so they are
        joined in when the takes are joined and are never part of a take or of its content hash,
        which is what lets a take serve whatever section number it ends up under.
        """
        sec = self.section(int(key))
        lead = sec.lead_seconds if isinstance(sec, PageSection) else 0.0
        if key == self.first_spoken_key:
            lead += self.settings.narration.opening_silence_seconds
        return round(lead, 3)

    def section_words(self, key: str, words_file: str) -> list[Word]:
        """A take's words in seconds after its section starts, which is after the section's lead_seconds."""
        lead = self.lead_seconds(key)
        words = read_words(self.takes_dir / words_file)
        if not lead:
            return words
        return [Word(w.word, round(w.start + lead, 3), round(w.end + lead, 3)) for w in words]

    # ---- artifacts -------------------------------------------------------------------
    def takes(self) -> Takes | None:
        return Takes.load(self.takes_path)

    def timeline(self) -> Timeline | None:
        return Timeline.load(self.timeline_path)

    def cue_times(self) -> CueTimes:
        return CueTimes.load(self.cue_times_path)
