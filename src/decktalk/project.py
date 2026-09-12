"""A decktalk project: a directory with scenes.json, a script, cues, HTML pages and media.

Layout (all paths in scenes.json are relative to the project directory):

    my-lesson/
      scenes.json        the plan: name, script, sections, transitions, mix, soundscape
      script.md          narration; "## N. Title" sections, [bracketed directions] unspoken
      cues.json          which spoken phrase each visual lands on
      deck/index.html    HTML scenes (decktalk-runtime.js gives them the ?beats= contract)
      media/             your clips, b-roll, slate, markers.json
      .env               ELEVENLABS_API_KEY, ELEVENLABS_VOICE_ID (never committed)
      build/             everything generated (git-ignored)
        audio/           NN-slug.mp3 + .words.json, manifest.json, narration.mp3, timeline.json, beats.json
        rec/             NN-scene.webm + NN-scene.json (recorder sidecars)
        sfx/ music/      soundscape outputs
        shots/           per-step screenshots
        out/             NN-section.mp4 and the final <name>.mp4
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SCENES_FILE = "scenes.json"


def load_dotenv(path: Path) -> dict[str, str]:
    """Tiny .env reader: KEY=value, optional quotes, # comments, export prefix."""
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export ") :]
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        else:
            value = value.split(" #", 1)[0].strip()
        values[key.strip()] = value
    return values


def section_key(num: int | str) -> str:
    return f"{int(num):02d}"


@dataclass
class Section:
    key: str  # "03"
    index: int
    data: dict[str, Any]

    @property
    def is_video(self) -> bool:
        return bool(self.data.get("video"))

    @property
    def file(self) -> str | None:
        return self.data.get("file")

    @property
    def scene(self) -> Any:
        return self.data.get("scene", self.index)

    @property
    def params(self) -> dict[str, Any]:
        return dict(self.data.get("params") or {})

    @property
    def extra_seconds(self) -> float:
        return float(self.data.get("extra_seconds", 0.3))

    @property
    def hold_seconds(self) -> float:
        return float(self.data.get("hold_seconds", 0))

    @property
    def slate_seconds(self) -> float:
        return float(self.data.get("slate_seconds", 5))

    @property
    def ambience(self) -> bool:
        return bool(self.data.get("ambience") or self.data.get("crowd"))

    @property
    def title(self) -> str:
        return str(self.data.get("title", ""))


@dataclass
class Project:
    root: Path
    data: dict[str, Any] = field(default_factory=dict)

    # ---- loading -------------------------------------------------------------
    @classmethod
    def load(cls, where: Path | str | None = None) -> Project:
        root = Path(where or os.environ.get("DECKTALK_PROJECT") or ".").resolve()
        if root.is_file() and root.name == SCENES_FILE:
            root = root.parent
        scenes = root / SCENES_FILE
        if not scenes.exists():
            sys.exit(
                f"error: {scenes} not found. Run from a project directory, pass --project DIR, "
                "or create one with `decktalk init DIR`."
            )
        try:
            data = json.loads(scenes.read_text())
        except json.JSONDecodeError as exc:
            sys.exit(f"error: {scenes}: {exc}")
        return cls(root=root, data=data)

    # ---- paths ---------------------------------------------------------------
    def path(self, rel: str | Path) -> Path:
        p = Path(rel)
        return p if p.is_absolute() else (self.root / p)

    @property
    def name(self) -> str:
        return str(self.data.get("name") or self.root.name)

    @property
    def script(self) -> Path:
        return self.path(self.data.get("script", "script.md"))

    @property
    def cues(self) -> Path:
        return self.path(self.data.get("cues", "cues.json"))

    @property
    def build(self) -> Path:
        return self.path(self.data.get("build", "build"))

    @property
    def audio_dir(self) -> Path:
        return self.build / "audio"

    @property
    def rec_dir(self) -> Path:
        return self.build / "rec"

    @property
    def out_dir(self) -> Path:
        return self.build / "out"

    @property
    def shots_dir(self) -> Path:
        return self.build / "shots"

    @property
    def manifest(self) -> Path:
        return self.audio_dir / "manifest.json"

    @property
    def timeline(self) -> Path:
        return self.audio_dir / "timeline.json"

    @property
    def beats(self) -> Path:
        return self.audio_dir / "beats.json"

    @property
    def final(self) -> Path:
        return self.out_dir / f"{self.name}.mp4"

    @property
    def env_file(self) -> Path:
        return self.root / ".env"

    def env(self, key: str) -> str:
        value = os.environ.get(key) or load_dotenv(self.env_file).get(key, "")
        return "" if value.startswith("<") else value

    def require_env(self, *keys: str) -> list[str]:
        values = [self.env(k) for k in keys]
        missing = [k for k, v in zip(keys, values, strict=True) if not v]
        if missing:
            sys.exit(
                f"error: {', '.join(missing)} not set. Put them in {self.env_file} "
                "(see .env.example) or export them. Keys are never printed."
            )
        return values

    # ---- plan ----------------------------------------------------------------
    @property
    def sections(self) -> list[Section]:
        raw = self.data.get("sections", {})
        items = [
            Section(key=section_key(k), index=int(k), data=v)
            for k, v in raw.items()
            if str(k).lstrip("-").isdigit() and isinstance(v, dict)
        ]
        return sorted(items, key=lambda s: s.index)

    def section(self, num: int | str) -> Section | None:
        key = section_key(num)
        return next((s for s in self.sections if s.key == key), None)

    @property
    def video_section_indexes(self) -> set[int]:
        return {s.index for s in self.sections if s.is_video}

    @property
    def mix(self) -> dict[str, Any]:
        return dict(self.data.get("mix") or {})

    @property
    def transition(self) -> dict[str, Any]:
        return dict(self.data.get("transition") or {})

    @property
    def dips(self) -> list[list[int]] | None:
        return self.data.get("dips")

    @property
    def soundscape(self) -> dict[str, Any]:
        return dict(self.data.get("soundscape") or {})

    @property
    def page_files(self) -> list[str]:
        seen: list[str] = []
        for s in self.sections:
            if s.file and s.file not in seen:
                seen.append(s.file)
        return seen

    # ---- generated state -----------------------------------------------------
    def read_json(self, path: Path, default: Any = None) -> Any:
        if not path.exists():
            return default
        try:
            return json.loads(path.read_text())
        except json.JSONDecodeError:
            return default

    def manifest_data(self) -> dict[str, Any]:
        return self.read_json(self.manifest, {}) or {}

    def timeline_data(self) -> dict[str, Any] | None:
        return self.read_json(self.timeline, None)

    def beats_data(self) -> dict[str, str]:
        return self.read_json(self.beats, {}) or {}
