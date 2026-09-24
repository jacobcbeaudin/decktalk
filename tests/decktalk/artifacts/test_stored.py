"""Every artifact reads itself, writes itself atomically, and says when it was never built."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import Field

from decktalk.artifacts.stored import Stored
from decktalk.errors import ErrorCode, NotBuiltError
from decktalk.pipeline import Artifact


class Tiny(Stored):
    """The smallest artifact there could be, which is what these tests judge the base on."""

    count: int = Field(description="A number, so the file has something in it to read back.")


def test_reading_a_file_nothing_has_written_is_none(tmp_path: Path) -> None:
    assert Tiny.read(tmp_path / "tiny.json") is None


def test_a_written_artifact_reads_back_as_itself(tmp_path: Path) -> None:
    path = Tiny(count=3).write(tmp_path / "a" / "tiny.json")
    assert Tiny.read(path) == Tiny(count=3)


def test_a_write_leaves_no_temporary_file_behind(tmp_path: Path) -> None:
    Tiny(count=1).write(tmp_path / "tiny.json")
    assert [p.name for p in sorted(tmp_path.iterdir())] == ["tiny.json"]


def test_a_write_replaces_the_file_whole(tmp_path: Path) -> None:
    path = tmp_path / "tiny.json"
    Tiny(count=1).write(path)
    Tiny(count=2).write(path)
    assert json.loads(path.read_text(encoding="utf-8")) == {"count": 2}


def test_a_missing_artifact_names_the_stage_that_writes_it(tmp_path: Path) -> None:
    with pytest.raises(NotBuiltError) as refused:
        Tiny.require(tmp_path / "takes.json", Artifact.TAKES)
    assert refused.value.code is ErrorCode.NOT_BUILT
    assert "decktalk narrate" in (refused.value.hint or "")


def test_an_artifact_that_will_not_parse_is_reported_as_one_that_was_never_built(tmp_path: Path) -> None:
    """The recovery is the same either way, so the two do not need two codes between them."""
    path = tmp_path / "takes.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(NotBuiltError) as refused:
        Tiny.require(path, Artifact.TAKES)
    assert "takes.json" in (refused.value.hint or "")


def test_an_artifact_is_frozen() -> None:
    tiny = Tiny(count=1)
    with pytest.raises(ValueError, match="frozen"):
        tiny.count = 2
