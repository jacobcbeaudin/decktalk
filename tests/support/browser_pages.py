"""Pages written by a test, and the Chromium that opens them, shared by the two browser modules.

`tests/contract/test_runtime.py` opens these pages the way a person does, with decktalk-runtime.js and
nothing else. `tests/contract/test_probe.py` opens them the way a DeckTalk command does, with
decktalk-probe.js injected first. The pages themselves are the same either way, which is the
property the split is supposed to have.
"""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from decktalk.toolchain.assets import RUNTIME_FILE, katex_dir, runtime_path

RUNTIME = f'<script src="{runtime_path().resolve().as_uri()}"></script>'
KATEX = (
    f'<link rel="stylesheet" href="{(katex_dir() / "katex.min.css").resolve().as_uri()}">'
    f'<script src="{(katex_dir() / "katex.min.js").resolve().as_uri()}"></script>'
)


def chromium_page(instrument: Any = None) -> Iterator[Any]:
    """One Chromium page for a module, with an `errors` list of everything it threw.

    `instrument` is the hook a DeckTalk command uses to add decktalk-probe.js, and a module that
    passes none gets the page a person opens.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        pytest.skip("playwright not installed")
    with sync_playwright() as pw:
        try:
            browser = pw.chromium.launch()
        except Exception as exc:
            pytest.skip(f"Chromium unavailable: {str(exc).splitlines()[0]}")
        page = browser.new_page(viewport={"width": 1920, "height": 1080})
        if instrument is not None:
            instrument(page)
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.errors = errors  # type: ignore[attr-defined]
        yield page
        browser.close()


def write_page(tmp_path: Path, name: str, body: str, *, head: str = "") -> str:
    """A page with the runtime in its head and `body` in its body, returned as a file URL."""
    html = tmp_path / name
    html.write_text(
        f'<!doctype html><html lang="en"><head><meta charset="utf-8"><title>{name}</title>'
        f"{head}{RUNTIME}</head><body>{body}</body></html>",
        encoding="utf-8",
    )
    return html.resolve().as_uri()


def script_page(tmp_path: Path, name: str, script: str, *, head: str = "") -> str:
    """A page whose scenes come from one inline script."""
    return write_page(tmp_path, name, f"<script>{script}</script>", head=head)


def served_page(root: Path, name: str, body: str, *, head: str = "") -> str:
    """A page beside its own copy of the runtime, as the recorder opens it on the local origin.

    It returns the name the origin is asked for rather than a file URL, because a recorded page is
    served from the project directory and loads the runtime the project holds.
    """
    shutil.copyfile(runtime_path(), root / RUNTIME_FILE)
    (root / name).write_text(
        f'<!doctype html><html lang="en"><head><meta charset="utf-8"><title>{name}</title>'
        f'{head}<script src="{RUNTIME_FILE}"></script></head><body>{body}</body></html>',
        encoding="utf-8",
    )
    return name


def warnings_of(page: Any) -> list[str]:
    """The page's warnings without the note every slide a partial cue list leaves out earns."""
    return [w for w in page.evaluate("() => window.__decktalk.warnings") if "owns no cue in ?cues=" not in w]


# The three-slide scene every markup test uses: attributes only, no JavaScript anywhere.
MARKUP_SCENE = """
<div data-scene="1" data-name="Open" data-camera="push">
  <template data-slide="1.1" data-hold="6">
    <h1 class="title">A bowl</h1>
    <p data-cue="1.1ball" data-reveal="pop" data-describe="a ball rests in the bowl">A ball</p>
    <p data-cue="1.1count" data-reveal="fade" data-duration="0.4">Watch it step down</p>
  </template>
  <template data-slide="1.2" data-hold="4" data-preview="1.2sum@0.5">
    <p data-cue="1.2sum" data-tex-display data-tex="\\sum_{i=1}^{n} x_i">the sum of x i from one to n</p>
  </template>
  <template data-slide="1.3" data-hold="4" data-owns="odd-one">
    <p data-cue="odd-one">a cue id that carries no slide id</p>
  </template>
</div>
"""
