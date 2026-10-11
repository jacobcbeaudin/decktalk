"""A slide recipe pasted into the starter as its page says to passes `decktalk check`.

    uv run pytest -m e2e tests/e2e/test_recipes.py

`docs/guides/slide-recipes.mdx` promises markup the runtime accepts and a reveal `check` can see.
These tests read a recipe's markup and cue rows straight off the page, paste them into a fresh
starter the four steps "Before you start" names, speak each cue phrase in a section of its own, and
run `check` on that section with the pages opened and the frames compared. A recipe the page shows
but `check` refuses is the docs promising something the product does not do.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from decktalk.template import STARTER
from support.commands import HOSTILE_DIRECTORY, decktalk, written
from support.paths import REPO
from support.timing_policy import EVERY_PACKAGED_PROJECT_SECONDS, FIRST_FETCH_SECONDS, budget

pytestmark = [pytest.mark.e2e, pytest.mark.timeout(budget(EVERY_PACKAGED_PROJECT_SECONDS + FIRST_FETCH_SECONDS))]

RECIPES = REPO / "docs" / "guides" / "slide-recipes.mdx"
"""The page every recipe is read from, so a test of a recipe is a test of what a reader copies."""

SECTION = 4
"""The section and scene number every recipe on the page is written for."""


def recipe(heading: str) -> tuple[str, str, str]:
    """One recipe's style rules, its template and its cue rows, exactly as the page shows them under `heading`."""
    text = RECIPES.read_text(encoding="utf-8")
    body = text.split(f"\n## {heading}\n", 1)[1].split("\n## ", 1)[0]
    (html,) = re.findall(r"```html deck/index\.html\n(.*?)```", body, re.S)
    (cues,) = re.findall(r"```json cues\.json\n(.*?)```", body, re.S)
    parts = re.fullmatch(r"\s*<style>\n(.*?)</style>\s*(<template.*</template>)\s*", html, re.S)
    assert parts is not None, f"the recipe under {heading!r} is a <style> block and then a <template>"
    style, template = parts.groups()
    return style, template, cues


def pasted(root: Path, heading: str) -> list[str]:
    """Paste one recipe into the starter at `root` the way "Before you start" says, and answer its cue ids."""
    style, template, cues = recipe(heading)
    page = root / "deck" / "index.html"
    html = page.read_text(encoding="utf-8")
    html = html.replace("</style>", f"{style}</style>", 1)
    html = html.replace("</body>", f'<div data-scene="{SECTION}" data-name="Recipe">{template}</div>\n</body>', 1)
    page.write_text(html, encoding="utf-8")

    rows = json.loads("{" + cues + "}")[str(SECTION)]["cues"]
    cue_file = root / "cues.json"
    document = json.loads(cue_file.read_text(encoding="utf-8"))
    document["sections"][str(SECTION)] = {"cues": rows}
    cue_file.write_text(json.dumps(document, indent=2), encoding="utf-8")

    toml = root / "decktalk.toml"
    section = f'\n[[section]]\nnumber = {SECTION}\nchapter = "Recipe"\npage = "deck/index.html"\nscene = "{SECTION}"\n'
    text = toml.read_text(encoding="utf-8")
    at = text.index("\n[narration]")
    toml.write_text(text[:at] + section + text[at:], encoding="utf-8")

    spoken = " [beat] ".join(f"Here is {row['phrase']}." for row in rows)
    with (root / "script.md").open("a", encoding="utf-8") as script:
        script.write(f"\n## {SECTION}. Recipe\n\n{spoken}\n")
    return [row["id"] for row in rows]


def test_an_element_that_steps_back_and_returns_on_a_cue_passes_check_as_written(tmp_path):
    """Recipe 4.4's return to the front is a cue of its own, and a still after it shows the panel at full strength."""
    home = tmp_path / HOSTILE_DIRECTORY
    home.mkdir()
    root = written(home, STARTER)
    ids = pasted(root, "An element that steps back while a second comes to the front")
    checked = decktalk("--project", str(root), "check", "--section", str(SECTION), "--fail-on", "never", cwd=home)
    assert checked["frames_compared"] is True, checked
    named = [row for row in checked["findings"] if row.get("location", {}).get("cue") in ids]
    assert named == [], named
