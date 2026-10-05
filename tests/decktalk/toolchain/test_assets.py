"""The runtime and the KaTeX release inside the wheel: complete, pinned, served by the origin, and never a CDN tag."""

from __future__ import annotations

import re
import shutil

from decktalk.toolchain.assets import (
    KATEX_FILES,
    KATEX_VERSION,
    RUNTIME_FILE,
    engine_files,
    katex_dir,
    katex_fonts,
    katex_missing,
    probe_path,
    runtime_path,
)


def test_the_packaged_copy_is_complete():
    """Both files, the licence, and every woff2 the stylesheet loads are in the package."""
    root = katex_dir()
    assert katex_missing() == []
    css = (root / "katex.min.css").read_text(encoding="utf-8")
    fonts = katex_fonts(css)
    assert len(fonts) == 20 and all(f.startswith("fonts/KaTeX_") for f in fonts)
    shipped = sorted(p.name for p in (root / "fonts").iterdir())
    assert shipped == sorted(f.removeprefix("fonts/") for f in fonts)  # nothing missing, nothing extra
    assert all(p.suffix == ".woff2" for p in (root / "fonts").iterdir())
    assert sorted(p.name for p in root.iterdir() if p.is_file()) == sorted(KATEX_FILES)


def test_the_packaged_copy_is_the_pinned_version():
    js = (katex_dir() / "katex.min.js").read_text(encoding="utf-8")
    assert re.search(rf'version:"{re.escape(KATEX_VERSION)}"', js)
    licence = (katex_dir() / "LICENSE").read_text(encoding="utf-8")
    assert "MIT License" in licence and "Khan Academy" in licence


def test_the_engine_serves_the_runtime_and_every_file_of_the_release_and_nothing_else():
    """The origin answers exactly these names, so the list holds every packaged KaTeX file and no probe."""
    served = engine_files()
    packaged = sorted(f"katex/{p.relative_to(katex_dir()).as_posix()}" for p in katex_dir().rglob("*") if p.is_file())
    assert sorted(served) == sorted([RUNTIME_FILE, *packaged])
    assert served[RUNTIME_FILE] == runtime_path() and probe_path() not in served.values()
    assert all(path.is_file() for path in served.values())


def test_the_runtime_and_the_probe_ship_in_the_wheel_beside_each_other():
    """A deck loads the runtime and never the probe, because a command injects the probe into the page."""
    assert runtime_path().is_file() and probe_path().is_file()
    assert runtime_path().parent == probe_path().parent


def test_katex_missing_names_each_absent_file(tmp_path):
    copy = tmp_path / "katex"
    shutil.copytree(katex_dir(), copy)
    (copy / "fonts" / "KaTeX_Main-Regular.woff2").unlink()
    (copy / "LICENSE").unlink()
    assert katex_missing(copy) == ["LICENSE", "fonts/KaTeX_Main-Regular.woff2"]
