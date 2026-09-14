"""The project scaffold and the tool cache.

`decktalk init DIR` writes the template project. `decktalk setup` fetches Chromium, ffmpeg
and KaTeX into the per-user cache. `decktalk doctor` reports on all of them.
"""

from __future__ import annotations

import io
import logging
import os
import re
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from collections.abc import Iterator
from dataclasses import asdict, dataclass
from importlib import resources
from pathlib import Path

from .errors import ConfigError, ToolError

log = logging.getLogger(__name__)

TEMPLATE_FILES = [
    ("decktalk.toml", "decktalk.toml"),
    ("script.md", "script.md"),
    ("cues.json", "cues.json"),
    ("media/markers.json", "media/markers.json"),
    ("gitignore", ".gitignore"),
    ("env.example", ".env.example"),
]
# The deck directory is copied whole, so every page and every asset beside it arrives. Each HTML
# page gets the project name and the KaTeX tags filled in, and every other file, such as the
# bundled fonts, is copied byte for byte.
TEMPLATE_DECK = "deck"
RUNTIME_FILE = "decktalk-runtime.js"

# KaTeX typesets the [data-tex] elements. `decktalk setup` caches one release and `decktalk init`
# copies it into deck/katex/, so a project renders equations with no network at all.
KATEX_VERSION = "0.18.7"
KATEX_ZIP_URL = f"https://github.com/KaTeX/KaTeX/releases/download/v{KATEX_VERSION}/katex.zip"
KATEX_FILES = ("katex.min.js", "katex.min.css")
KATEX_FONT_DIR = "fonts"
KATEX_LOCAL_TAGS = '<link rel="stylesheet" href="./katex/katex.min.css">\n<script src="./katex/katex.min.js"></script>'
KATEX_CDN_TAGS = (
    f'<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/KaTeX/{KATEX_VERSION}/katex.min.css">\n'
    f'<script src="https://cdnjs.cloudflare.com/ajax/libs/KaTeX/{KATEX_VERSION}/katex.min.js"></script>'
)


def package_file(rel: str) -> Path:
    return Path(str(resources.files("decktalk").joinpath(rel)))


def runtime_path() -> Path:
    return package_file(f"runtime/{RUNTIME_FILE}")


def cache_dir() -> Path:
    """The per-user cache directory, next to Playwright's ms-playwright folder. DECKTALK_CACHE_DIR overrides it."""
    override = os.environ.get("DECKTALK_CACHE_DIR")
    if override:
        return Path(override)
    if sys.platform == "darwin":
        root = Path.home() / "Library" / "Caches"
    elif sys.platform == "win32":
        root = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    else:
        root = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
    return root / "decktalk"


def katex_cache_dir() -> Path:
    return cache_dir() / "katex" / KATEX_VERSION


def katex_cached() -> Path | None:
    """The cached KaTeX directory when it is complete, else None."""
    d = katex_cache_dir()
    if all((d / f).is_file() for f in KATEX_FILES) and any((d / KATEX_FONT_DIR).glob("*.woff2")):
        return d
    return None


def fetch_katex(dest: Path | None = None) -> Path:
    """Download the KaTeX release zip and unpack katex.min.js, katex.min.css and fonts/ into the cache."""
    dest = dest or katex_cache_dir()
    log.info("   fetching %s", KATEX_ZIP_URL)
    with urllib.request.urlopen(KATEX_ZIP_URL, timeout=60) as resp:
        data = resp.read()
    tmp = dest.with_name(f".{dest.name}.tmp")
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        for member in zf.namelist():
            rel = member.removeprefix("katex/")
            wanted = rel in KATEX_FILES or (rel.startswith(f"{KATEX_FONT_DIR}/") and not member.endswith("/"))
            if member.startswith("katex/") and wanted:
                out = tmp / rel
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_bytes(zf.read(member))
    shutil.rmtree(dest, ignore_errors=True)
    tmp.replace(dest)
    return dest


def vendor_katex(deck_dir: Path) -> bool:
    """Copy the cached KaTeX into deck/katex/. Returns False when the cache does not have it."""
    src = katex_cached()
    if src is None:
        return False
    dst = deck_dir / "katex"
    shutil.rmtree(dst, ignore_errors=True)
    dst.mkdir(parents=True)
    for f in KATEX_FILES:
        shutil.copyfile(src / f, dst / f)
    shutil.copytree(src / KATEX_FONT_DIR, dst / KATEX_FONT_DIR)
    return True


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
    vendored = vendor_katex(target / "deck")
    if not vendored:
        log.warning(
            "KaTeX is not cached, so the pages in deck/ load it from a CDN. Run `decktalk setup` once, then "
            "`decktalk init` again, to render equations offline."
        )
    katex_tags = KATEX_LOCAL_TAGS if vendored else KATEX_CDN_TAGS

    def fill(text: str) -> str:
        text = text.replace("__NAME__", name).replace("__TITLE__", title_from(name))
        return text.replace("__KATEX__", katex_tags)

    for src_rel, dst_rel in TEMPLATE_FILES:
        dst = target / dst_rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(fill(package_file(f"template/{src_rel}").read_text(encoding="utf-8")), encoding="utf-8")
    deck_src = package_file(f"template/{TEMPLATE_DECK}")
    for src in sorted(deck_src.rglob("*")):
        if not src.is_file() or src.name.startswith(".") or "__pycache__" in src.parts:
            continue
        dst = target / TEMPLATE_DECK / src.relative_to(deck_src)
        dst.parent.mkdir(parents=True, exist_ok=True)
        if src.suffix == ".html":
            dst.write_text(fill(src.read_text(encoding="utf-8")), encoding="utf-8")
        else:
            shutil.copyfile(src, dst)
    shutil.copyfile(runtime_path(), target / "deck" / RUNTIME_FILE)
    return target


def update_runtime(project_root: Path) -> list[tuple[Path, bool]]:
    """Copy the packaged runtime over every decktalk-runtime.js in the project.

    Returns (path, existed) per file written, so the caller can say whether each one was
    created or replaced. A project with no copy at all gets one at deck/decktalk-runtime.js.
    """
    found = list(project_root.rglob(RUNTIME_FILE)) or [project_root / "deck" / RUNTIME_FILE]
    out: list[tuple[Path, bool]] = []
    for dst in found:
        existed = dst.exists()
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(runtime_path(), dst)
        out.append((dst, existed))
    return out


def setup() -> None:
    """Fetch the headless Chromium, the ffmpeg binaries, and the KaTeX release."""
    log.info("== Chromium (Playwright)")
    cmd = [sys.executable, "-m", "playwright", "install", "chromium"]
    if sys.platform.startswith("linux"):
        cmd.append("--with-deps")
    if subprocess.call(cmd) != 0:
        raise ToolError("playwright install failed. See the output above.")
    log.info("== ffmpeg / ffprobe")
    from .media.ffmpeg import ffmpeg_paths

    ff, fp = ffmpeg_paths()
    log.info("   ffmpeg  %s", ff)
    log.info("   ffprobe %s", fp)
    log.info("== KaTeX %s", KATEX_VERSION)
    if katex_cached():
        log.info("   cached  %s", katex_cache_dir())
        return
    try:
        log.info("   katex   %s", fetch_katex())
    except Exception as exc:
        log.warning("   KaTeX download failed (%s). Projects will load it from a CDN instead.", exc)


@dataclass(frozen=True)
class DoctorRow:
    """One component that `decktalk doctor` reports. It unpacks like the (name, ok, detail) tuple it replaced.

    A missing component that is not `required`, which is KaTeX because pages fall back to a
    CDN, is a warning: an uncertain finding, so `doctor` exits 1 on it only with --strict.
    """

    name: str
    ok: bool
    detail: str
    required: bool = True

    def __iter__(self) -> Iterator[str | bool]:
        return iter((self.name, self.ok, self.detail))

    def to_dict(self) -> dict[str, str | bool]:
        return asdict(self)


def doctor() -> list[DoctorRow]:
    """One DoctorRow for each of python, chromium, ffmpeg, ffprobe, config and katex.

    Nothing is fetched or written. In particular the ffmpeg row looks for binaries that are
    already on disk, because asking static-ffmpeg for them would download them.
    """
    rows = [DoctorRow("python", True, f"{sys.version.split()[0]} ({sys.executable})")]
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as pw:
            try:
                b = pw.chromium.launch()
                rows.append(DoctorRow("chromium", True, b.version))
                b.close()
            except Exception as exc:
                rows.append(DoctorRow("chromium", False, f"{str(exc).splitlines()[0]}  -> run `decktalk setup`"))
    except ImportError:
        rows.append(DoctorRow("chromium", False, "playwright package missing"))
    from .media.ffmpeg import installed_paths

    found = installed_paths()
    if found:
        rows.append(DoctorRow("ffmpeg", True, found[0]))
        rows.append(DoctorRow("ffprobe", True, found[1]))
    else:
        rows.append(DoctorRow("ffmpeg", False, "not fetched yet and none on PATH  -> run `decktalk setup`"))
    from .config import user_config_path

    cfg_path = user_config_path()
    rows.append(DoctorRow("config", True, str(cfg_path) if cfg_path.exists() else f"none (optional, at {cfg_path})"))
    cached = katex_cached()
    if cached:
        rows.append(DoctorRow("katex", True, str(cached)))
    else:
        detail = f"not cached at {katex_cache_dir()}  -> run `decktalk setup` (pages load KaTeX from a CDN until then)"
        rows.append(DoctorRow("katex", False, detail, required=False))
    return rows
