"""The stability page names what DeckTalk promises a caller, held to what the code does.

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
from decktalk.artifacts.takes import Takes
from decktalk.artifacts.words import WORDS_SUFFIX, ProviderWords
from decktalk.cli.session import FOUND_SOMETHING
from decktalk.errors import ErrorCode
from decktalk.inputs.workspace import EVENTS_SUFFIX
from decktalk.results import SCHEMA
from decktalk.settings import BY_ID
from decktalk.stages.score.ledger import LEDGER_FILE, Ledger
from support.paths import REPO
from support.projects import load_project

PAGE = REPO / "docs" / "reference" / "stability.mdx"
NAV = REPO / "docs" / "docs.json"


def page() -> str:
    return PAGE.read_text(encoding="utf-8")


def test_the_stability_page_is_in_the_navigation() -> None:
    pages = re.findall(r'"(reference/[^"]+)"', NAV.read_text(encoding="utf-8"))
    assert "reference/stability" in pages
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


def paid_records() -> str:
    """The page's section that lists the paid records, up to the next heading."""
    return page().split("## The paid records", 1)[1].split("\n## ", 1)[0]


def paid_rows() -> dict[str, tuple[str, str]]:
    """The table of paid records, as each kind against the file it is and the folder it lives in."""
    rows = re.findall(r"^\| ([^|]+?) \| ([^|]+?) \| ([^|]+?) \|$", paid_records(), re.MULTILINE)
    return {kind: (file, folder) for kind, file, folder in rows if kind not in {"Kind", "---"}}


def test_the_page_names_the_events_file_and_every_paid_record() -> None:
    assert f"build/events/<run>{EVENTS_SUFFIX}" in page()
    paid = paid_records()
    assert "never deleted by DeckTalk" in paid, "the take audio is not a stored model, so the page says how it is kept"
    assert "`takes.json`" in paid and "cache" in paid, "the page says the take index is rebuilt rather than refused"
    assert not Takes.paid, "the take index is a cache, not a paid record"
    assert not Stored.paid, "a cache is the default, and a paid record says so"


def test_the_paid_records_are_listed_by_kind_and_by_the_folder_the_code_keeps_them_in(tmp_path: Path) -> None:
    workspace = load_project(tmp_path).workspace
    rows = paid_rows()
    assert set(rows) == {"Take audio", "Provider words", "Score audio", "Ledger"}
    takes = f"`{workspace.takes.relative_to(workspace.root).as_posix()}/`"
    store = f"`[{BY_ID['narration.store_dir'].table}] {BY_ID['narration.store_dir'].name}`"
    score = f"`{workspace.score_dir.relative_to(workspace.root).as_posix()}/`"
    for kind in ("Take audio", "Provider words"):
        assert takes in rows[kind][1] and store in rows[kind][1], kind
    kept = f"`[{BY_ID['score.dir'].table}] {BY_ID['score.dir'].name}`"
    for kind in ("Score audio", "Ledger"):
        assert score in rows[kind][1] and "build/" not in " ".join(rows[kind]), kind
    assert kept in rows["Score audio"][1]
    assert f"`<digest>{WORDS_SUFFIX}`" in rows["Provider words"][0] and ProviderWords.paid
    assert f"`{LEDGER_FILE}`" in rows["Ledger"][0] and Ledger.paid
    assert not [kind for kind, row in rows.items() if "takes.json" in " ".join(row)], "the take index is a cache"
