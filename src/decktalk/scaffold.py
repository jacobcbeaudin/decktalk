"""The project scaffold and the machine report.

`decktalk init DIR` writes the template project, with the packaged runtime and KaTeX beside its
pages. `decktalk install` fetches Chromium and ffmpeg into the per-user cache. `decktalk doctor`
reports on all of them. What ships in the wheel and where a fetched tool lives is `toolchain/`.
"""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
import sys
from collections.abc import Iterator
from dataclasses import asdict, dataclass
from pathlib import Path

from .errors import ConfigError, ToolError
from .media.ffmpeg import ffmpeg_paths, installed_paths
from .settings import user_config_path
from .toolchain import assets
from .toolchain.ffmpeg_fetch import FFMPEG_VERSION, installed_pinned

log = logging.getLogger(__name__)


def title_from(name: str) -> str:
    """The project name as a title, for the pages the starter writes."""
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

    def fill(text: str) -> str:
        return text.replace("__NAME__", name).replace("__TITLE__", title_from(name))

    for src_rel, dst_rel in assets.TEMPLATE_FILES:
        dst = target / dst_rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(fill(assets.package_file(f"template/{src_rel}").read_text(encoding="utf-8")), encoding="utf-8")
    deck_src = assets.package_file(f"template/{assets.TEMPLATE_DECK}")
    for src in sorted(deck_src.rglob("*")):
        if not src.is_file() or src.name.startswith(".") or "__pycache__" in src.parts:
            continue
        dst = target / assets.TEMPLATE_DECK / src.relative_to(deck_src)
        dst.parent.mkdir(parents=True, exist_ok=True)
        if src.suffix == ".html":
            dst.write_text(fill(src.read_text(encoding="utf-8")), encoding="utf-8")
        else:
            shutil.copyfile(src, dst)
    shutil.copyfile(assets.runtime_path(), target / assets.TEMPLATE_DECK / assets.RUNTIME_FILE)
    assets.vendor_katex(target / assets.TEMPLATE_DECK)
    return target


def install() -> None:
    """Fetch the headless Chromium and the pinned ffmpeg build."""
    log.info("== Chromium (Playwright)")
    cmd = [sys.executable, "-m", "playwright", "install", "chromium"]
    if sys.platform.startswith("linux"):
        cmd.append("--with-deps")
    if subprocess.call(cmd) != 0:
        raise ToolError("playwright install failed. See the output above.")
    log.info("== ffmpeg %s", FFMPEG_VERSION)
    found = installed_pinned()
    if found:
        log.info("   cached  %s", found[0])
    ff, fp = ffmpeg_paths()
    log.info("   ffmpeg  %s", ff)
    log.info("   ffprobe %s", fp)


@dataclass(frozen=True)
class DoctorRow:
    """One component that `decktalk doctor` reports. Every component it reports is needed to build."""

    name: str
    ok: bool
    detail: str

    def __iter__(self) -> Iterator[str | bool]:
        return iter((self.name, self.ok, self.detail))

    def to_dict(self) -> dict[str, str | bool]:
        return asdict(self)


def doctor() -> list[DoctorRow]:
    """One DoctorRow for each of python, chromium, ffmpeg, ffprobe, config and katex.

    Nothing is fetched or written. In particular the ffmpeg row looks for executables that are
    already on disk, because asking for them would download the pinned build.
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
                rows.append(DoctorRow("chromium", False, f"{str(exc).splitlines()[0]}  -> run `decktalk install`"))
    except ImportError:
        rows.append(DoctorRow("chromium", False, "playwright package missing"))
    found = installed_paths()
    if found:
        rows.append(DoctorRow("ffmpeg", True, found[0]))
        rows.append(DoctorRow("ffprobe", True, found[1]))
    else:
        rows.append(DoctorRow("ffmpeg", False, "not fetched yet and none on PATH  -> run `decktalk install`"))
    cfg_path = user_config_path()
    rows.append(DoctorRow("config", True, str(cfg_path) if cfg_path.exists() else f"none (optional, at {cfg_path})"))
    missing = assets.katex_missing()
    if missing:
        lacks = f"{assets.katex_dir()} lacks {', '.join(missing)}  -> reinstall decktalk"
        rows.append(DoctorRow("katex", False, lacks))
    else:
        rows.append(DoctorRow("katex", True, f"{assets.KATEX_VERSION} in the wheel ({assets.katex_dir()})"))
    return rows
