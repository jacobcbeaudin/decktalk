"""`decktalk init DIR` scaffolds a project; `decktalk setup` fetches Chromium and ffmpeg; `decktalk doctor` reports."""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
import sys
from importlib import resources
from pathlib import Path

from .errors import ConfigError, ToolError

log = logging.getLogger(__name__)

TEMPLATE_FILES = [
    ("decktalk.toml", "decktalk.toml"),
    ("script.md", "script.md"),
    ("cues.json", "cues.json"),
    ("deck/index.html", "deck/index.html"),
    ("media/markers.json", "media/markers.json"),
    ("gitignore", ".gitignore"),
    ("env.example", ".env.example"),
]
RUNTIME_FILE = "decktalk-runtime.js"


def package_file(rel: str) -> Path:
    return Path(str(resources.files("decktalk").joinpath(rel)))


def runtime_path() -> Path:
    return package_file(f"runtime/{RUNTIME_FILE}")


def title_from(name: str) -> str:
    words = re.split(r"[-_\s]+", name.strip())
    return " ".join(w[:1].upper() + w[1:] for w in words if w) or "Untitled"


def init(target: Path, *, name: str | None = None, force: bool = False) -> Path:
    """Write the template project into `target`. Returns the project directory."""
    target = target.resolve()
    name = name or target.name
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", name):
        raise ConfigError(f"project name {name!r} must be letters, digits, dots, dashes or underscores")
    if target.exists() and any(target.iterdir()) and not force:
        raise ConfigError(f"{target} is not empty (pass force to write into it anyway)")
    for src_rel, dst_rel in TEMPLATE_FILES:
        src = package_file(f"template/{src_rel}")
        dst = target / dst_rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(src.read_text().replace("__NAME__", name).replace("__TITLE__", title_from(name)))
    shutil.copyfile(runtime_path(), target / "deck" / RUNTIME_FILE)
    return target


def update_runtime(project_root: Path) -> list[Path]:
    """Copy the packaged runtime over every decktalk-runtime.js in the project."""
    found = list(project_root.rglob(RUNTIME_FILE)) or [project_root / "deck" / RUNTIME_FILE]
    for dst in found:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(runtime_path(), dst)
    return found


def setup() -> None:
    """Fetch the headless Chromium and the ffmpeg binaries."""
    log.info("== Chromium (Playwright)")
    cmd = [sys.executable, "-m", "playwright", "install", "chromium"]
    if sys.platform.startswith("linux"):
        cmd.append("--with-deps")
    if subprocess.call(cmd) != 0:
        raise ToolError("playwright install failed; see the output above")
    log.info("== ffmpeg / ffprobe")
    from .media.ffmpeg import ffmpeg_paths

    ff, fp = ffmpeg_paths()
    log.info("   ffmpeg  %s", ff)
    log.info("   ffprobe %s", fp)


def doctor() -> list[tuple[str, bool, str]]:
    """(component, ok, detail) for python, chromium, ffmpeg and ffprobe. Changes nothing."""
    rows: list[tuple[str, bool, str]] = [("python", True, f"{sys.version.split()[0]} ({sys.executable})")]
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as pw:
            try:
                b = pw.chromium.launch()
                rows.append(("chromium", True, b.version))
                b.close()
            except Exception as exc:
                rows.append(("chromium", False, f"{str(exc).splitlines()[0]}  -> run `decktalk setup`"))
    except ImportError:
        rows.append(("chromium", False, "playwright package missing"))
    try:
        from .media.ffmpeg import ffmpeg_paths

        ff, fp = ffmpeg_paths()
        rows.append(("ffmpeg", True, ff))
        rows.append(("ffprobe", True, fp))
    except ToolError as exc:
        rows.append(("ffmpeg", False, str(exc)))
    return rows
