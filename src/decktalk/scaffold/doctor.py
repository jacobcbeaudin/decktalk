"""One row per component a build needs, and whether it is there, and the same report as a block.

`decktalk doctor` reports and fetches nothing. In particular the ffmpeg row looks for executables
that are already on disk, because asking for them would download the pinned build.

Nothing here prints the value of an environment variable, because a variable may hold a key. A
component's detail is a version or a path DeckTalk resolved itself.
"""

from __future__ import annotations

import platform
import sys
from collections.abc import Iterator
from dataclasses import asdict, dataclass

from ..media.ffmpeg import env_missing, installed_paths, unnamed_paths
from ..settings import user_config_path
from ..toolchain import assets
from ..toolchain.cache import cache_dir


@dataclass(frozen=True)
class DoctorRow:
    """One component that `decktalk doctor` reports. Every component it reports is needed to build."""

    name: str
    ok: bool
    detail: str
    optional: bool = False  # A component a build runs without, so its absence is no finding.

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
    found, named_but_absent = installed_paths(), env_missing()
    if named_but_absent:
        # One row per component, so a broken variable names its own tool and the other still
        # reports the path it resolved. A reader pasting this block debugs the right thing.
        absent = {"DECKTALK_FFMPEG": "ffmpeg", "DECKTALK_FFPROBE": "ffprobe"}
        broken = {absent[name]: name for name in named_but_absent}
        on_disk = unnamed_paths()
        for index, tool in enumerate(("ffmpeg", "ffprobe")):
            if tool in broken:
                rows.append(DoctorRow(tool, False, f"{broken[tool]} names a file that is not there"))
            elif on_disk:
                rows.append(DoctorRow(tool, True, on_disk[index]))
            else:
                rows.append(DoctorRow(tool, False, "not fetched yet and none on PATH  -> run `decktalk install`"))
    elif found:
        rows.append(DoctorRow("ffmpeg", True, found[0]))
        rows.append(DoctorRow("ffprobe", True, found[1]))
    else:
        rows.append(DoctorRow("ffmpeg", False, "not fetched yet and none on PATH  -> run `decktalk install`"))
    cfg_path = user_config_path()
    found_cfg = cfg_path.exists()
    rows.append(DoctorRow("config", True, str(cfg_path) if found_cfg else f"none (at {cfg_path})", optional=True))
    missing = assets.katex_missing()
    if missing:
        lacks = f"{assets.katex_dir()} lacks {', '.join(missing)}  -> reinstall decktalk"
        rows.append(DoctorRow("katex", False, lacks))
    else:
        rows.append(DoctorRow("katex", True, f"{assets.KATEX_VERSION} in the wheel ({assets.katex_dir()})"))
    return rows


def report_block(rows: list[DoctorRow], version: str) -> str:
    """The machine report as a block to paste into a bug report or a Discussions post.

    It carries the versions and the paths a run would use and nothing else. No environment
    variable's value is in it, because a variable may hold a key, and `doctor` never reads one.
    """
    lines = [
        "```text",
        f"{'decktalk':<9} {version}",
        f"{'platform':<9} {platform.platform()} ({sys.platform})",
        *(f"{row.name:<9} {row.detail if row.ok else f'MISSING  {row.detail}'}" for row in rows),
        f"{'cache':<9} {cache_dir()}",
        "```",
    ]
    return "\n".join(lines)
