"""The KaTeX release inside the wheel: complete, pinned, copied into every new project, and never a CDN tag."""

from __future__ import annotations

import re
import shutil

from decktalk import scaffold
from decktalk.scaffold import init
from decktalk.toolchain import assets
from decktalk.toolchain.assets import (
    KATEX_FILES,
    KATEX_VERSION,
    katex_dir,
    katex_fonts,
    katex_missing,
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


def test_init_copies_the_packaged_copy_and_the_page_loads_it_locally(tmp_path):
    root = init(tmp_path / "proj", name="proj").root
    deck = root / "deck"
    assert katex_missing(deck / "katex") == []
    packaged = sorted(p.relative_to(katex_dir()) for p in katex_dir().rglob("*") if p.is_file())
    copied = sorted(p.relative_to(deck / "katex") for p in (deck / "katex").rglob("*") if p.is_file())
    assert copied == packaged
    for rel in packaged:
        assert (deck / "katex" / rel).read_bytes() == (katex_dir() / rel).read_bytes(), rel
    html = (deck / "index.html").read_text(encoding="utf-8")
    assert '<link rel="stylesheet" href="./katex/katex.min.css">' in html
    assert '<script src="./katex/katex.min.js"></script>' in html
    assert html.index("katex.min.js") < html.index("decktalk-runtime.js")  # KaTeX loads before the runtime
    for page in deck.glob("*.html"):
        text = page.read_text(encoding="utf-8")
        assert "cdnjs" not in text and "https://" not in text.split("<style>")[0], page.name


def test_katex_missing_names_each_absent_file(tmp_path, monkeypatch):
    copy = tmp_path / "katex"
    shutil.copytree(katex_dir(), copy)
    (copy / "fonts" / "KaTeX_Main-Regular.woff2").unlink()
    (copy / "LICENSE").unlink()
    assert katex_missing(copy) == ["LICENSE", "fonts/KaTeX_Main-Regular.woff2"]
    monkeypatch.setattr(assets, "katex_dir", lambda: copy)
    row = {r.name: r for r in scaffold.doctor()}["katex"]
    assert row.ok is False
    assert row.detail.endswith("lacks LICENSE, fonts/KaTeX_Main-Regular.woff2  -> reinstall decktalk")
    assert str(copy) in row.detail


def test_doctor_reports_the_packaged_version_when_it_is_complete():
    row = {r.name: r for r in scaffold.doctor()}["katex"]
    assert row.ok is True and row.detail == f"{KATEX_VERSION} in the wheel ({katex_dir()})"
