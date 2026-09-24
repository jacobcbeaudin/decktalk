"""The KaTeX release inside the wheel: complete, pinned, copied into every new project, and never a CDN tag."""

from __future__ import annotations

import re
import shutil

from decktalk.toolchain.assets import (
    KATEX_FILES,
    KATEX_VERSION,
    katex_dir,
    katex_fonts,
    katex_missing,
    probe_path,
    runtime_path,
    vendor_katex,
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


def test_vendoring_copies_the_packaged_release_beside_a_deck_byte_for_byte(tmp_path):
    """A project renders equations with no network and no CDN tag, which is what the copy is for."""
    copied_to = vendor_katex(tmp_path / "deck")
    assert katex_missing(copied_to) == []
    packaged = sorted(p.relative_to(katex_dir()) for p in katex_dir().rglob("*") if p.is_file())
    copied = sorted(p.relative_to(copied_to) for p in copied_to.rglob("*") if p.is_file())
    assert copied == packaged
    for rel in packaged:
        assert (copied_to / rel).read_bytes() == (katex_dir() / rel).read_bytes(), rel


def test_vendoring_again_replaces_what_was_there_rather_than_adding_to_it(tmp_path):
    copied_to = vendor_katex(tmp_path / "deck")
    (copied_to / "fonts" / "left-behind.woff2").write_bytes(b"")
    assert vendor_katex(tmp_path / "deck") == copied_to
    assert not (copied_to / "fonts" / "left-behind.woff2").exists()


def test_the_runtime_and_the_probe_ship_in_the_wheel_and_only_one_of_them_is_copied():
    """A deck loads the runtime and never the probe, because a command injects the probe into the page."""
    assert runtime_path().is_file() and probe_path().is_file()
    assert runtime_path().parent == probe_path().parent


def test_katex_missing_names_each_absent_file(tmp_path):
    copy = tmp_path / "katex"
    shutil.copytree(katex_dir(), copy)
    (copy / "fonts" / "KaTeX_Main-Regular.woff2").unlink()
    (copy / "LICENSE").unlink()
    assert katex_missing(copy) == ["LICENSE", "fonts/KaTeX_Main-Regular.woff2"]
