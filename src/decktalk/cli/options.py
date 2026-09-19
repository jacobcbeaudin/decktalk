"""One typed options dataclass per command, read from the parsed arguments by field name.

A handler never touches an `argparse.Namespace`. The parser fills a namespace, `Options.of` copies
the fields its class declares, and the handler reads a frozen object whose fields are typed and
whose defaults say what the command does when a flag is absent. A flag the parser does not offer a
command is simply a field that command's class does not declare, so a handler can never read one.

Every class inherits the flags every command shares, which is why `--json`, `-v` and `-q` need no
row of their own anywhere below.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, fields, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, Self

from ..model import Project
from ..model.document import PROJECT_FILE
from ..pipeline import Stage

if TYPE_CHECKING:  # pragma: no cover - the parser is the only caller, and it imports this module
    import argparse


@dataclass(frozen=True)
class Options:
    """The flags every command takes: the machine output, the exit policy and the log level."""

    json: bool = False
    strict: bool = False
    exit_zero: bool = False
    verbose: bool = False
    quiet: bool = False

    @classmethod
    def of(cls, args: argparse.Namespace) -> Self:
        """This command's options, taking each declared field from the namespace by its own name."""
        given: dict[str, Any] = {f.name: getattr(args, f.name) for f in fields(cls) if hasattr(args, f.name)}
        return cls(**given)


@dataclass(frozen=True)
class ProjectOptions(Options):
    """A command that works on a project directory."""

    project: str | None = None


@dataclass(frozen=True)
class EncodeOptions(ProjectOptions):
    """A command that encodes video, and may override the encoder for one run."""

    preset: str | None = None
    crf: int | None = None


@dataclass(frozen=True)
class SectionOptions(ProjectOptions):
    """A command that works per section."""

    only: list[int] | None = None


# ---- one class per command -------------------------------------------------------------


@dataclass(frozen=True)
class InitOptions(Options):
    """`init`: the directory to create, and what to name the project inside it."""

    dir: str = "."
    name: str | None = None
    force: bool = False
    example: str | None = None
    no_skills: bool = False


@dataclass(frozen=True)
class InstallOptions(Options):
    """`install`: it takes the shared flags alone."""


@dataclass(frozen=True)
class DoctorOptions(Options):
    """`doctor`: `--report` prints the pasteable block instead of the table."""

    report: bool = False


@dataclass(frozen=True)
class NarrateOptions(SectionOptions):
    """`narrate`: what to voice, what to leave cached, and what a run would cost."""

    force: bool = False
    allow_placeholders: bool = False
    dry_run: bool = False
    no_voice: bool = False
    model: str | None = None


@dataclass(frozen=True)
class AlignOptions(ProjectOptions):
    """`align`: whether a cue id its page never mentions stops the run."""

    allow_unknown_cues: bool = False


@dataclass(frozen=True)
class PreflightOptions(SectionOptions):
    """`preflight`: the rehearsal, with or without the frozen frames."""

    no_frames: bool = False
    model: str | None = None
    allow_unknown_cues: bool = False


@dataclass(frozen=True)
class SoundscapeOptions(ProjectOptions):
    """`soundscape`: which items to generate, and whether to send anything at all."""

    names: list[str] | None = None
    force: bool = False
    dry_run: bool = False


@dataclass(frozen=True)
class RecordOptions(SectionOptions):
    """`record`: how long to record, how long to settle, and whether to play the resolved cues."""

    seconds: float | None = None
    settle: float | None = None
    no_cues: bool = False


@dataclass(frozen=True)
class AssembleOptions(EncodeOptions):
    """`assemble`: which layers of the soundtrack to lay down, and how to finish them."""

    no_soundscape: bool = False
    no_loudness: bool = False


@dataclass(frozen=True)
class VerifyOptions(SectionOptions):
    """`verify`: the cues to check, written `SECTION:CUE`, or every cue when none is named."""

    checks: list[str] | None = None


@dataclass(frozen=True)
class ScreenshotsOptions(ProjectOptions):
    """`screenshots`: one PNG per slide, per cue of one slide, or per second of a playing section."""

    page: list[str] | None = None
    slide: list[str] | None = None
    after: list[str] | None = None
    before: list[str] | None = None
    section: int | None = None
    at: list[float] | None = None


@dataclass(frozen=True)
class WordsOptions(SectionOptions):
    """`words`: which sections to print."""


@dataclass(frozen=True)
class ClipOptions(EncodeOptions):
    """`clip`: the span of one built section, and where to write it."""

    section: int = 1
    start: float = 0.0
    end: float = 0.0
    out: str = ""
    words: str | None = None
    gain: float = 0.0
    hold: float = 0.0


@dataclass(frozen=True)
class StatusOptions(ProjectOptions):
    """`status`: it takes the shared flags alone."""


@dataclass(frozen=True)
class ServeOptions(ProjectOptions):
    """`serve`: where to bind the local origin, and whether to open a browser on it."""

    host: str = "127.0.0.1"
    port: int = 0
    open: bool = False


@dataclass(frozen=True)
class BuildOptions(EncodeOptions):
    """`build`: the whole pipeline, with the flags of the stages it runs."""

    only: list[int] | None = None
    no_voice: bool = False
    force: bool = False
    no_soundscape: bool = False
    no_loudness: bool = False
    allow_unresolved_cues: bool = False
    allow_unknown_cues: bool = False
    progress: str | None = None
    from_stage: Stage | None = None
    to_stage: Stage | None = None
    dry_run: bool = False


def project_root(opts: Options) -> Path | None:
    """The directory a command works in, resolved as `Project.load` resolves it, or None.

    An error names the file it is about, and the envelope prints that file relative to this
    directory, so the two have to agree on where the project is before anything is loaded. That
    includes naming the project file itself, which stands for the directory holding it.
    """
    if not isinstance(opts, ProjectOptions):
        return None
    root = Path(opts.project or os.environ.get("DECKTALK_PROJECT") or ".").resolve()
    return root.parent if root.is_file() and root.name == PROJECT_FILE else root


def load_project(opts: ProjectOptions) -> Project:
    """The project a command works on, with the flags that override a tuning key applied.

    The settings are frozen, so a flag rebuilds the table it belongs to rather than assigning into
    one that another caller may already hold. Each flag is read from the class that declares it, so
    a field that is renamed is a type error rather than an override that silently stops happening.
    """
    project = Project.load(opts.project)
    video, record = project.settings.video, project.settings.record
    if isinstance(opts, EncodeOptions):
        if opts.preset:
            video = replace(video, preset=opts.preset)
        if opts.crf is not None:
            video = replace(video, crf=opts.crf)
    if isinstance(opts, RecordOptions) and opts.settle is not None:
        record = replace(record, settle_seconds=opts.settle)
    return replace(project, settings=replace(project.settings, video=video, record=record))
