"""What a project has built so far, read from disk without running any stage.

`status(project)` collects everything `decktalk status` reports into a `StatusResult`. The
CLI's table and its `--json` output both read that one object, so the two never disagree.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .artifacts import Timeline
from .project import ClipSection, Project


def relpath(path: Path, root: Path) -> str:
    """The path relative to the project root with forward slashes, or the whole path when it lies outside."""
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


@dataclass
class SectionStatus:
    """One section: whether it is a page or a clip, where it comes from, and what exists for it."""

    key: str
    kind: str  # "page" or "clip"
    source: str  # "deck/index.html?scene=1" for a page, the clip's path for a clip
    recorded: bool
    cut: bool


@dataclass
class OutputStatus:
    """One file assemble writes beside the final mp4."""

    label: str  # "captions" or "chapters"
    key: str  # "srt", "vtt" or "chapters"
    path: Path
    exists: bool


@dataclass
class StatusResult:
    """Everything `decktalk status` prints, as data."""

    root: Path
    name: str
    script: Path
    script_exists: bool
    cues: Path
    cues_exists: bool
    sections: list[SectionStatus]
    timeline: Timeline | None
    cue_times_exists: bool
    cue_times_sections: dict[str, dict[str, float]]
    final: Path
    final_exists: bool
    final_duration: float | None
    outputs: list[OutputStatus] = field(default_factory=list)

    def to_dict(self, root: Path | None = None) -> dict[str, Any]:
        """The report as JSON-ready data, with paths relative to the project root."""
        root = root or self.root
        tl = self.timeline
        return {
            "project": {
                "root": self.root.as_posix(),
                "name": self.name,
                "script": relpath(self.script, root),
                "script_exists": self.script_exists,
                "cues": relpath(self.cues, root),
                "cues_exists": self.cues_exists,
            },
            "sections": [
                {"key": s.key, "kind": s.kind, "source": s.source, "recorded": s.recorded, "cut": s.cut}
                for s in self.sections
            ],
            "timeline": {
                "exists": tl is not None,
                "estimated": bool(tl and tl.estimated),
                "total_seconds": tl.total_seconds if tl else None,
                "sections": [
                    {"key": key, "title": sec.title, "start": sec.start, "end": sec.end, "duration": sec.duration}
                    for key, sec in (tl.sections.items() if tl else [])
                ],
            },
            "cue_times": {
                "exists": self.cue_times_exists,
                "sections": [{"key": key, "cues": dict(cues)} for key, cues in self.cue_times_sections.items()],
            },
            "final": {
                "path": relpath(self.final, root),
                "exists": self.final_exists,
                "duration": self.final_duration,
            },
            "outputs": {o.key: {"path": relpath(o.path, root), "exists": o.exists} for o in self.outputs},
        }


def status(project: Project) -> StatusResult:
    """Read what exists for the project. Nothing is written, and only the final mp4 is probed."""
    from .stages.assemble import output_paths

    sections = []
    for sec in project.sections:
        if isinstance(sec, ClipSection):
            kind, source = "clip", sec.clip
        else:
            kind, source = "page", f"{sec.page}?scene={sec.scene}"
        sections.append(
            SectionStatus(
                key=sec.key,
                kind=kind,
                source=source,
                recorded=project.recording(sec).exists(),
                cut=project.section_video(sec).exists(),
            )
        )
    duration = None
    if project.final.exists():
        from .media.ffmpeg import probe_duration

        duration = probe_duration(project.final)
    paths = output_paths(project)
    outputs = [
        OutputStatus(label, key, paths[key], paths[key].exists())
        for label, key in (("captions", "srt"), ("captions", "vtt"), ("chapters", "chapters"))
    ]
    cue_times = project.cue_times()
    return StatusResult(
        root=project.root,
        name=project.name,
        script=project.script,
        script_exists=project.script.exists(),
        cues=project.cues,
        cues_exists=project.cues.exists(),
        sections=sections,
        timeline=project.timeline(),
        cue_times_exists=project.cue_times_path.exists(),
        cue_times_sections={k: cue_times.times(k) for k in cue_times.sections if cue_times.sections[k]},
        final=project.final,
        final_exists=project.final.exists(),
        final_duration=duration,
        outputs=outputs,
    )
