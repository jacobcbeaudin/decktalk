"""One row per component a build needs, and whether it is there.

`decktalk doctor` reports and fetches nothing. In particular the ffmpeg row looks for executables
that are already on disk, because asking for them would download the pinned build.
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from dataclasses import asdict, dataclass

from ..media.ffmpeg import installed_paths
from ..settings import user_config_path
from ..toolchain import assets


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
