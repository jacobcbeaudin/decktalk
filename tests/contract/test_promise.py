"""The contract page names what DeckTalk promises a caller, held to what the code does.

A caller builds on four things: the `--json` result of each command, the events file, `build/final/`
and the exit code. The page that promises them is checked against the result model, the workspace,
the error codes and the paid records, so a renamed deliverable or a new exit code fails here before
a reader meets a page that says otherwise.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from decktalk.artifacts.stored import Stored
from decktalk.cli.session import FOUND_SOMETHING
from decktalk.errors import ErrorCode
from decktalk.inputs.workspace import EVENTS_SUFFIX
from decktalk.results import SCHEMA
from decktalk.stages.soundscape.ledger import LEDGER_FILE, Ledger
from support.paths import REPO
from support.projects import load_project

PAGE = REPO / "docs" / "reference" / "contract.mdx"
NAV = REPO / "docs" / "docs.json"


def page() -> str:
    return PAGE.read_text(encoding="utf-8")


def test_the_contract_page_is_in_the_navigation() -> None:
    pages = re.findall(r'"(reference/[^"]+)"', NAV.read_text(encoding="utf-8"))
    assert "reference/contract" in pages
    json.loads(NAV.read_text(encoding="utf-8"))


def test_the_page_names_the_schema_every_result_carries() -> None:
    assert f"which is `{SCHEMA}`" in page()


def test_the_page_names_every_exit_code_there_is() -> None:
    exits = {0, FOUND_SOMETHING} | {code.exit_code for code in ErrorCode}
    rows = {int(found) for found in re.findall(r"^\| (\d+) \|", page(), re.MULTILINE)}
    assert rows == exits


def test_the_page_names_every_file_in_the_final_directory(tmp_path: Path) -> None:
    workspace = load_project(tmp_path).workspace
    for path in workspace.deliverables().values():
        name = re.sub(rf"^{re.escape(workspace.name)}(?=[.-])", "<name>", path.name)
        assert f"`{name}`" in page(), name


def test_the_page_names_the_events_file_and_every_paid_record() -> None:
    text = page()
    assert f"build/events/<run>{EVENTS_SUFFIX}" in text
    assert f"build/soundscape/{LEDGER_FILE}" in text and Ledger.paid
    assert "build/narrate/takes.json" in text
    assert not Stored.paid, "a cache is the default, and a paid record says so"
