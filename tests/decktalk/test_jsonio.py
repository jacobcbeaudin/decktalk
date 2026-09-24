"""The one JSON reader, the one atomic writer, and the two walks between a dataclass and JSON."""

from __future__ import annotations

import json
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path, PurePosixPath

import pytest

from decktalk.jsonio import ShapeError, as_json, dumps, read_as, read_json, relative, write_json
from decktalk.verdicts import Finding, Verdict


class Colour(Enum):
    SLATE = "0x0e1116"


@dataclass(frozen=True)
class Leaf:
    word: str
    start: float


@dataclass(frozen=True)
class Branch:
    leaves: tuple[Leaf, ...]
    file: Path
    colour: Colour
    note: str | None = None
    counts: dict[str, int] = field(default_factory=dict)


def test_a_write_makes_the_file_whole_or_not_at_all(tmp_path):
    """A reader never opens a half-written file, which is why every artifact goes through here."""
    out = tmp_path / "build" / "narration" / "takes.json"
    write_json(out, {"estimated": False})
    assert json.loads(out.read_text(encoding="utf-8")) == {"estimated": False}
    assert out.read_text(encoding="utf-8").endswith("\n")
    assert [p.name for p in out.parent.iterdir()] == ["takes.json"]

    # The name the reader watches only ever holds a whole file, because the text is written under
    # another name and moved onto the target by one rename, which the filesystem makes atomic.
    renames: list[tuple[str, str]] = []
    real_replace = Path.replace

    def record(self, target):
        renames.append((self.name, Path(target).name))
        return real_replace(self, target)

    with monkey(Path, "replace", record):
        write_json(out, {"estimated": True})
    assert renames == [(".takes.json.tmp", "takes.json")], renames
    assert json.loads(out.read_text(encoding="utf-8")) == {"estimated": True}

    # A writer that failed part way leaves the file the reader watches exactly as it was.
    written: list[str] = []

    def fail(self, data, encoding=None):
        written.append(self.name)
        raise OSError("the disk filled up")

    with monkey(Path, "write_text", fail), pytest.raises(OSError, match="disk filled"):
        write_json(out, {"estimated": False})
    assert written and not written[0].endswith("takes.json"), written
    assert json.loads(out.read_text(encoding="utf-8")) == {"estimated": True}


@contextmanager
def monkey(cls, name, replacement):
    """Swap one method for the body of a `with` block, so a write can be made to fail."""
    original = getattr(cls, name)
    setattr(cls, name, replacement)
    try:
        yield
    finally:
        setattr(cls, name, original)


def test_a_write_replaces_an_existing_file(tmp_path):
    """The take index is rewritten on every run, so the replacement is the ordinary case."""
    out = tmp_path / "takes.json"
    write_json(out, {"version": 1})
    write_json(out, {"version": 2})
    assert read_json(out) == {"version": 2}


def test_a_value_that_is_not_finite_is_refused_rather_than_written(tmp_path):
    """`NaN` and `Infinity` are not JSON, and no strict reader accepts what Python would emit."""
    out = tmp_path / "times.json"
    for bad in (float("nan"), float("inf"), float("-inf")):
        with pytest.raises(ValueError):
            write_json(out, {"at": bad})
        with pytest.raises(ValueError):
            dumps({"at": bad})
    assert not out.exists()
    assert list(out.parent.iterdir()) == []


def test_the_walker_turns_a_whole_tree_into_json_ready_data():
    """A nested dataclass becomes an object, a path a forward-slashed string, an enum its value."""
    branch = Branch(
        leaves=(Leaf("hello", 0.5), Leaf("world", 1.25)),
        file=Path("build") / "out" / "lesson.mp4",
        colour=Colour.SLATE,
        counts={"sections": 3},
    )
    assert as_json(branch) == {
        "leaves": [{"word": "hello", "start": 0.5}, {"word": "world", "start": 1.25}],
        "file": "build/out/lesson.mp4",
        "colour": "0x0e1116",
        "note": None,
        "counts": {"sections": 3},
    }
    assert json.loads(json.dumps(as_json(branch)))["colour"] == "0x0e1116"


def test_the_envelope_and_the_files_are_printed_by_the_same_rule():
    """The JSON an agent parses obeys what the writer obeys, so neither can carry what the other refuses."""
    assert dumps({"ok": True}) == json.dumps({"ok": True}, indent=2)


def test_the_walker_leaves_a_plain_value_alone():
    """A string, a number, a boolean and None are already JSON, so the walker does not touch them."""
    assert as_json(["a", 1, 1.5, True, None]) == ["a", 1, 1.5, True, None]
    assert as_json(PurePosixPath("deck/index.html")) == "deck/index.html"
    assert as_json({1: "one"}) == {"1": "one"}


def test_the_walker_does_not_turn_a_class_into_an_object():
    """`is_dataclass` is true of the class as well as the instance, which would be a silent mistake."""
    assert as_json(Leaf) is Leaf


def test_a_path_inside_the_root_is_relative_and_one_outside_is_whole(tmp_path):
    """Every path a result reports is relative to the project root, so an agent can act on it."""
    root = tmp_path / "my-lesson"
    assert relative(root / "build" / "out" / "lesson.mp4", root) == "build/out/lesson.mp4"
    assert relative(root, root) == "."
    outside = tmp_path / "elsewhere" / "music.mp3"
    assert relative(outside, root) == outside.as_posix()


def test_a_file_written_here_is_read_back_by_the_reader(tmp_path):
    """The reader and the writer are one pair, and the caller turns the data into a dataclass at once."""
    out = tmp_path / "cue-times.json"
    write_json(out, as_json({"sections": {"03": [Leaf("bowl", 2.0)]}}))
    assert read_json(out) == {"sections": {"03": [{"word": "bowl", "start": 2.0}]}}


@dataclass(frozen=True)
class Judged:
    """A row with a verdict, an enum and an optional, which are the three things the reader opens."""

    leaf: Leaf
    verdict: Verdict
    colour: Colour
    rows: list[Finding]
    counts: dict[str, int]
    note: str | None
    either: Leaf | Colour


def test_a_verdict_is_written_as_its_object_and_read_back_as_its_member():
    judged = Judged(
        Leaf("a", 1.0), Verdict.OFF_CUE, Colour.SLATE, [Finding("d", Verdict.BLACK)], {"n": 1}, None, Colour.SLATE
    )
    data = json.loads(json.dumps(as_json(judged)))
    assert data["verdict"] == Verdict.OFF_CUE.to_dict() and data["colour"] == Colour.SLATE.value
    assert read_as(Judged, data) == judged


@pytest.mark.parametrize(
    ("change", "refusal"),
    [
        ({"note": None, "leaf": {"word": "a"}}, r"\$\.leaf has no start"),
        ({"leaf": {"word": "a", "start": 1.0, "end": 2.0}}, r"\$\.leaf carries end, which Leaf does not declare"),
        ({"leaf": {"word": "a", "start": True}}, r"\$\.leaf\.start is bool True, not float"),
        ({"verdict": {"code": "NOPE", "label": "x", "certain": True}}, r"\$\.verdict: 'NOPE' is not a verdict code"),
        ({"colour": "0xffffff"}, r"\$\.colour is '0xffffff', which is not a Colour"),
        ({"rows": [{"detail": "d"}]}, r"\$\.rows\[0\]: a finding row is an object of exactly"),
        ({"counts": {"n": "1"}}, r"\$\.counts\.n is str '1', not int"),
        ({"note": 3}, r"\$\.note is int 3, not str"),
        ({"either": 3}, r"\$\.either fits none of its shapes"),
        ({"rows": {}}, r"\$\.rows is dict, not an array"),
    ],
)
def test_json_that_is_not_the_shape_its_type_declares_is_refused_where_it_goes_wrong(change, refusal):
    """A missing key, an unknown key, a wrong kind and an unknown code each name their place."""
    good = as_json(Judged(Leaf("a", 1.0), Verdict.OK, Colour.SLATE, [], {}, None, Leaf("b", 2.0)))
    with pytest.raises(ShapeError, match=refusal):
        read_as(Judged, {**good, **change})


def test_a_number_written_as_a_whole_number_is_still_a_float_and_null_is_only_an_optional():
    assert read_as(Leaf, {"word": "a", "start": 1}) == Leaf("a", 1.0)
    with pytest.raises(ShapeError, match=r"\$\.word is null"):
        read_as(Leaf, {"word": None, "start": 1})


def test_a_class_with_a_lenient_loader_of_its_own_is_still_read_strictly():
    """Only a verdict and a finding row read themselves, so a coercing `from_dict` elsewhere is never used here."""
    from decktalk.artifacts import Word

    assert read_as(Word, {"word": "a", "start": 1, "end": 2}) == Word("a", 1.0, 2.0)
    with pytest.raises(ShapeError, match=r"\$ carries extra, which Word does not declare"):
        read_as(Word, {"word": "a", "start": 1, "end": 2, "extra": 1})
    with pytest.raises(ShapeError, match=r"\$\.word is int 5, not str"):
        read_as(Word, {"word": 5, "start": 1, "end": 2})
