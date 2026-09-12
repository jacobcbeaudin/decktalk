"""`cuecut init DIR` scaffolds a project; `cuecut setup` fetches Chromium and ffmpeg."""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from importlib import resources
from pathlib import Path

TEMPLATE_FILES = [
    ("script.md", "script.md"),
    ("scenes.json", "scenes.json"),
    ("cues.json", "cues.json"),
    ("deck/index.html", "deck/index.html"),
    ("media/markers.json", "media/markers.json"),
    ("gitignore", ".gitignore"),
]


def package_file(rel: str) -> Path:
    return Path(str(resources.files("cuecut").joinpath(rel)))


def runtime_path() -> Path:
    return package_file("runtime/cuecut-runtime.js")


def title_from(name: str) -> str:
    words = re.split(r"[-_\s]+", name.strip())
    return " ".join(w[:1].upper() + w[1:] for w in words if w) or "Untitled"


def init(target: Path, *, name: str | None = None, force: bool = False) -> int:
    target = target.resolve()
    name = name or target.name
    title = title_from(name)
    if target.exists() and any(target.iterdir()) and not force:
        sys.exit(f"error: {target} is not empty (pass --force to write into it anyway)")
    for src_rel, dst_rel in TEMPLATE_FILES:
        src = package_file(f"template/{src_rel}")
        dst = target / dst_rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        text = src.read_text().replace("__NAME__", name).replace("__TITLE__", title)
        dst.write_text(text)
    shutil.copyfile(runtime_path(), target / "deck" / "cuecut-runtime.js")
    shutil.copyfile(package_file("template/env.example"), target / ".env.example")
    print(f"created {target}")
    print("  script.md      the narration (## N. sections)")
    print("  scenes.json    the plan: sections -> pages or clips, mix, soundscape")
    print("  cues.json      which spoken phrase each visual lands on")
    print("  deck/          index.html + cuecut-runtime.js (open index.html for the scene index)")
    print("  media/         your clips, b-roll, markers.json")
    print("next: cp .env.example .env  (ELEVENLABS_API_KEY, ELEVENLABS_VOICE_ID), then `cuecut build`")
    print("      or `cuecut build --silent` to render with placeholder narration and no API key")
    return 0


def update_runtime(project_root: Path) -> int:
    """Copy the packaged runtime over every deck/cuecut-runtime.js in the project."""
    found = list(project_root.rglob("cuecut-runtime.js"))
    if not found:
        found = [project_root / "deck" / "cuecut-runtime.js"]
    for dst in found:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(runtime_path(), dst)
        print(f"updated {dst}")
    return 0


def setup(*, with_deps: bool = True) -> int:
    """Fetch the headless Chromium and the ffmpeg binaries."""
    print("== Chromium (Playwright)")
    cmd = [sys.executable, "-m", "playwright", "install", "chromium"]
    if with_deps and sys.platform.startswith("linux"):
        cmd.append("--with-deps")
    rc = subprocess.call(cmd)
    if rc != 0:
        print("playwright install failed; see the output above", file=sys.stderr)
        return rc
    print("== ffmpeg / ffprobe")
    from .tools import ffmpeg_paths

    ff, fp = ffmpeg_paths()
    print(f"   ffmpeg  {ff}")
    print(f"   ffprobe {fp}")
    print("setup complete")
    return 0


def doctor() -> int:
    """Report what is available without changing anything."""
    ok = True
    print(f"python   {sys.version.split()[0]}  ({sys.executable})")
    try:
        import playwright  # noqa: F401
        from playwright.sync_api import sync_playwright

        with sync_playwright() as pw:
            try:
                b = pw.chromium.launch()
                print(f"chromium ok ({b.version})")
                b.close()
            except Exception as exc:
                ok = False
                print(f"chromium MISSING: {str(exc).splitlines()[0]}  -> run `cuecut setup`")
    except ImportError:
        ok = False
        print("playwright MISSING (pip package)")
    try:
        from .tools import ffmpeg_paths

        ff, fp = ffmpeg_paths()
        print(f"ffmpeg   {ff}")
        print(f"ffprobe  {fp}")
    except SystemExit as exc:
        ok = False
        print(str(exc))
    return 0 if ok else 1
