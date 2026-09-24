"""`cues.json` read against the catalog the page published, and the fixes that reconcile the two."""

from __future__ import annotations

import json
from pathlib import Path

from decktalk.findings import Applicability, Code
from decktalk.inputs.cues import Cue, CuedSection
from decktalk.inputs.document import PageSection
from decktalk.media.pagereport import SceneCatalog
from decktalk.pipeline import Stage
from decktalk.stages.cue.catalog import cue_findings, declared_cues, measured_rows, scene_cues

BOX = {"x": 0, "y": 0, "w": 10, "h": 10}
"""One element's box, which every row here shares because none of these cases measures a box."""


def entry(scene: str, moments: dict[str, list[str]], **extra: object) -> SceneCatalog:
    """One scene of a catalog, with one element per moment the slide declares."""
    elements = {
        slide: [{"attrs": {"data-in": wire.split(":", 1)[-1]}, "moments": {"data-in": wire}, "text": "", "box": BOX}
                for wire in wires]
        for slide, wires in moments.items()
    }  # fmt: skip
    return SceneCatalog.model_validate({"scene": scene, "elements": elements, **extra})


def section(number: int, *, page: str = "deck/index.html", scene: str = "1") -> PageSection:
    return PageSection(number=number, page=page, scene=scene)


def write_cues(root: Path, sections: dict[str, object]) -> Path:
    path = root / "cues.json"
    path.write_text(json.dumps({"sections": sections}, indent=2), encoding="utf-8")
    return path


def applied(path: Path, root: Path, findings: list) -> None:
    """Carry out every fix these findings offer, the way `Project.apply` carries one out."""
    for found in findings:
        for edit in getattr(found.fix, "edits", ()):
            target = root / edit.file
            lines = target.read_text(encoding="utf-8").splitlines(keepends=True) if target.exists() else []
            index = (edit.line or 1) - 1
            lines[index : index + (1 if edit.old is not None else 0)] = [edit.new + "\n"]
            target.write_text("".join(lines), encoding="utf-8")
    assert json.loads(path.read_text(encoding="utf-8")), "the fix left a file that still parses"


# ---- reading the catalog ----------------------------------------------------------------------


def test_a_scene_declares_every_moment_its_elements_carry() -> None:
    assert scene_cues(entry("1", {"1.1": ["1.1:a", "1.1:b"], "1.2": ["1.2:c"]})) == ("1.1:a", "1.1:b", "1.2:c")


def test_a_scene_also_declares_the_cues_its_own_map_names() -> None:
    """A cue a handler alone serves is in the scene's map and on no element, so both are read."""
    one = entry("1", {"1.1": ["1.1:a"]}, cues={"1.1": ["1.1:a", "1.1:handled"]})
    assert scene_cues(one) == ("1.1:a", "1.1:handled")
    flat = entry("1", {}, cues=["1.1:listed"])
    assert scene_cues(flat) == ("1.1:listed",)


def test_a_catalog_row_becomes_the_row_pagescan_judges() -> None:
    (row,) = measured_rows(entry("1", {"1.1": ["1.1:a"]}))
    assert row.cue == "1.1:a" and row.box == (0, 0, 10, 10)


def test_a_section_whose_page_published_nothing_is_left_unjudged() -> None:
    """An absent page is absent from the map, which is what tells it apart from a scene declaring none."""
    catalogs = {"deck/index.html": [entry("1", {"1.1": ["1.1:a"]})]}
    assert declared_cues(catalogs, [section(1)]) == {1: ("1.1:a",)}
    assert declared_cues({}, [section(1)]) == {}
    assert declared_cues(catalogs, [section(1, scene="9")]) == {}


# ---- the judgements ---------------------------------------------------------------------------


def test_a_moment_with_no_row_is_one_certain_finding_per_section(tmp_path: Path) -> None:
    path = write_cues(tmp_path, {"1": {"cues": [{"cue": "1.1:a", "on": "hello"}]}})
    found = cue_findings({1: ("1.1:a", "1.1:b", "1.1:c")}, _cued(1, [("1.1:a", "hello")]),
                         cues_path=path, root=tmp_path, stage=Stage.CUE)  # fmt: skip
    (judged,) = found
    assert judged.code is Code.CUE_MISSING and judged.stage is Stage.CUE
    assert "2 moment(s)" in judged.message and "1.1:b, 1.1:c" in judged.message
    assert judged.location.section == 1 and judged.location.file == Path("cues.json")


def test_the_scaffold_fix_adds_the_rows_and_leaves_the_phrase_to_the_author(tmp_path: Path) -> None:
    path = write_cues(tmp_path, {"1": {"cues": [{"cue": "1.1:a", "on": "hello"}]}})
    found = cue_findings({1: ("1.1:a", "1.1:b")}, _cued(1, [("1.1:a", "hello")]), cues_path=path, root=tmp_path)
    fix = found[0].fix
    assert fix is not None and fix.applicability is Applicability.SAFE
    applied(path, tmp_path, found)
    rows = json.loads(path.read_text(encoding="utf-8"))["sections"]["1"]["cues"]
    assert {row["cue"]: row["on"] for row in rows} == {"1.1:a": "hello", "1.1:b": ""}


def test_the_scaffold_fix_is_idempotent_because_it_only_ever_adds(tmp_path: Path) -> None:
    path = write_cues(tmp_path, {"1": {"cues": [{"cue": "1.1:a", "on": "hello"}]}})
    declared = {1: ("1.1:a", "1.1:b")}
    applied(path, tmp_path, cue_findings(declared, _cued(1, [("1.1:a", "hello")]), cues_path=path, root=tmp_path))
    again = cue_findings(declared, _cued(1, [("1.1:a", "hello"), ("1.1:b", "")]), cues_path=path, root=tmp_path)
    assert [one.code for one in again] == []


def test_an_empty_cue_array_is_rewritten_rather_than_inserted_into(tmp_path: Path) -> None:
    """An array that opens and closes on one line has no line inside it for a row to go before."""
    path = write_cues(tmp_path, {"1": {"cues": []}})
    found = cue_findings({1: ("1.1:a",)}, _cued(1, []), cues_path=path, root=tmp_path)
    applied(path, tmp_path, found)
    assert json.loads(path.read_text(encoding="utf-8"))["sections"]["1"]["cues"] == [{"cue": "1.1:a", "on": ""}]


def test_a_section_the_file_holds_no_block_for_gets_a_whole_block(tmp_path: Path) -> None:
    path = write_cues(tmp_path, {"1": {"cues": [{"cue": "1.1:a", "on": "hello"}]}})
    found = cue_findings({2: ("2.1:a",)}, _cued(1, [("1.1:a", "hello")]), cues_path=path, root=tmp_path)
    applied(path, tmp_path, found)
    sections = json.loads(path.read_text(encoding="utf-8"))["sections"]
    assert sections["2"]["cues"] == [{"cue": "2.1:a", "on": ""}]
    assert sections["1"]["cues"] == [{"cue": "1.1:a", "on": "hello"}]


def test_two_sections_scaffolded_by_one_call_do_not_land_on_top_of_each_other(tmp_path: Path) -> None:
    """Each edit is worked out against the file as the edit before it would leave it."""
    path = write_cues(tmp_path, {"1": {"cues": [{"cue": "1.1:a", "on": "hello"}]},
                                 "2": {"cues": [{"cue": "2.1:a", "on": "there"}]}})  # fmt: skip
    cued = [_one(1, [("1.1:a", "hello")]), _one(2, [("2.1:a", "there")])]
    found = cue_findings({1: ("1.1:a", "1.1:b"), 2: ("2.1:a", "2.1:b")}, cued, cues_path=path, root=tmp_path)
    assert len(found) == 2
    applied(path, tmp_path, found)
    sections = json.loads(path.read_text(encoding="utf-8"))["sections"]
    assert [row["cue"] for row in sections["1"]["cues"]] == ["1.1:b", "1.1:a"]
    assert [row["cue"] for row in sections["2"]["cues"]] == ["2.1:b", "2.1:a"]


def test_a_project_with_no_cue_file_is_one_finding_whose_fix_writes_the_whole_file(tmp_path: Path) -> None:
    path = tmp_path / "cues.json"
    found = cue_findings({1: ("1.1:a",), 2: ("2.1:b",)}, [], cues_path=path, root=tmp_path)
    (judged,) = found
    assert judged.code is Code.CUE_MISSING and "there is no cues.json" in judged.message
    assert judged.fix is not None and judged.fix.applicability is Applicability.SAFE
    applied(path, tmp_path, found)
    sections = json.loads(path.read_text(encoding="utf-8"))["sections"]
    assert sections == {"1": {"cues": [{"cue": "1.1:a", "on": ""}]}, "2": {"cues": [{"cue": "2.1:b", "on": ""}]}}


def test_a_row_no_page_declares_is_named_and_never_deleted(tmp_path: Path) -> None:
    path = write_cues(tmp_path, {"1": {"cues": [{"cue": "1.1:a", "on": "hello"}, {"cue": "9.9:x", "on": "there"}]}})
    found = cue_findings({1: ("1.1:a",)}, _cued(1, [("1.1:a", "hello"), ("9.9:x", "there")]),
                         cues_path=path, root=tmp_path)  # fmt: skip
    (judged,) = found
    assert judged.code is Code.CUE_UNKNOWN and judged.location.cue == "9.9:x"
    assert "nothing plays it" in judged.message
    assert json.loads(path.read_text(encoding="utf-8"))["sections"]["1"]["cues"][1]["cue"] == "9.9:x"


def test_allow_unknown_keeps_the_row_out_of_the_findings(tmp_path: Path) -> None:
    path = write_cues(tmp_path, {"1": {"cues": [{"cue": "9.9:x", "on": "there"}]}})
    cued = _cued(1, [("9.9:x", "there")])
    assert cue_findings({1: ("1.1:a",)}, cued, cues_path=path, root=tmp_path, allow_unknown=True)[0].code is (
        Code.CUE_MISSING
    )
    codes = {one.code for one in cue_findings({1: ("1.1:a",)}, cued, cues_path=path, root=tmp_path)}
    assert codes == {Code.CUE_MISSING, Code.CUE_UNKNOWN}


def test_one_stale_row_beside_one_new_moment_reads_as_a_rename(tmp_path: Path) -> None:
    """The phrase is what survives a rename, so the row that carries it is the row for that moment."""
    path = write_cues(tmp_path, {"1": {"cues": [{"cue": "1.1:old", "on": "hello"}]}})
    found = cue_findings({1: ("1.1:new",)}, _cued(1, [("1.1:old", "hello")]), cues_path=path, root=tmp_path)
    missing = next(one for one in found if one.code is Code.CUE_MISSING)
    unknown = next(one for one in found if one.code is Code.CUE_UNKNOWN)
    assert "looks like 1.1:new renamed" in unknown.message
    assert unknown.fix is not None and unknown.fix.applicability is Applicability.UNSAFE
    assert missing.fix is None, "the rename resolves it, so nothing adds a second row for the same moment"
    applied(path, tmp_path, [unknown])
    assert json.loads(path.read_text(encoding="utf-8"))["sections"]["1"]["cues"] == [{"cue": "1.1:new", "on": "hello"}]


def _one(number: int, rows: list[tuple[str, str]]) -> CuedSection:
    return CuedSection(number=number, cues=tuple(Cue(cue=wire, on=phrase) for wire, phrase in rows))


def _cued(number: int, rows: list[tuple[str, str]]) -> list[CuedSection]:
    return [_one(number, rows)]
