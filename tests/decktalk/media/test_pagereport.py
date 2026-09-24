"""Reading what a page said about itself, which is the boundary where a page stops being trusted."""

from __future__ import annotations

from decktalk.findings import Code
from decktalk.media import pagereport
from decktalk.page import REPORT, PageWarning, RaisedBy

REPORTED = {
    "version": "0.5.0",
    "mode": "cue",
    "scene": "intro",
    "slide": "1.1",
    "warnings": [
        {"code": "PAGE_UNKNOWN_ATTR", "message": "data-lift is not an attribute.", "slide": "1.1", "attr": "data-lift"}
    ],
    "catalog": [
        {
            "scene": "intro",
            "slides": [{"id": "1.1", "cues": ["1.1:expand"]}],
            "elements": {
                "1.1": [
                    {
                        "attrs": {"data-in": "expand"},
                        "moments": {"data-in": "1.1:expand"},
                        "text": "Halfway there",
                        "box": {"x": 10, "y": 20, "w": 300, "h": 40},
                    }
                ]
            },
        }
    ],
    "cues": [{"id": "1.1:expand", "due": 1.5, "ran": 1.52, "frame": 1.5, "describe": None, "next": 1.56, "after": 1.6}],
    "words": [{"text": "Halfway there", "cueAt": 1.5, "runAt": 1.6, "count": 2, "firstOn": 1.62}],
    "frameGaps": [{"at": None, "ms": 180}, {"at": 2.0, "ms": 140}],
    "longFrames": [{"start": 1.4, "ms": 62, "render": 1.44, "presented": None}],
}
"""One page's answer, in the shape and the spelling `window.__dtprobe.report()` uses."""


def test_the_report_reads_into_models_with_the_page_names_on_the_left():
    report = pagereport.read(REPORTED)
    assert report.version == "0.5.0" and report.mode == "cue" and report.slide == "1.1"
    assert report.unreadable == ()
    assert report.words[0].cue_at == 1.5 and report.words[0].first_shown == 1.62
    assert report.frame_gaps[0].at is None and report.frame_gaps[1].ms == 140
    assert report.long_frames[0].presented is None
    assert report.cues[0].id == "1.1:expand" and report.cues[0].after == 1.6


def test_every_warning_the_page_reports_carries_its_code():
    """`record` dispatches on the code, which is what replaced a sentence classified by substring."""
    row = pagereport.read(REPORTED).warnings[0]
    assert row.code is Code.PAGE_UNKNOWN_ATTR
    assert row.attr == "data-lift" and row.slide == "1.1" and row.cue is None


def test_a_row_this_contract_cannot_read_is_named_rather_than_carried():
    """A page is written by an author, so one row it got wrong never decides what the rest of it said."""
    said = {
        **REPORTED,
        "warnings": [{"code": "PAGE_NOT_A_CODE", "message": "who knows"}, REPORTED["warnings"][0]],
        "cues": [{"id": "1.1:expand"}],
    }
    report = pagereport.read(said)
    assert [row.code for row in report.warnings] == [Code.PAGE_UNKNOWN_ATTR]
    assert report.cues == ()
    assert len(report.unreadable) == 2
    assert any("warnings[0]" in line for line in report.unreadable), report.unreadable


def test_a_page_with_no_probe_in_it_reports_nothing_and_says_so():
    report = pagereport.read(None)
    assert report.warnings == () and report.catalog == ()
    assert report.unreadable and "rather than a report" in report.unreadable[0]


def test_a_field_reported_as_something_other_than_a_list_is_one_sentence():
    report = pagereport.read({**REPORTED, "cues": {"1.1:expand": 1.5}})
    assert report.cues == ()
    assert any(line.startswith("the page reported cues as dict") for line in report.unreadable), report.unreadable


def test_the_catalog_keeps_what_the_recorder_does_not_read():
    """`pagescan.py` is the catalog's reader, so the slides and the cues survive this boundary."""
    scene = pagereport.read(REPORTED).catalog[0]
    assert scene.scene == "intro"
    assert scene.elements["1.1"][0].moments == {"data-in": "1.1:expand"}
    assert scene.model_dump()["slides"] == [{"id": "1.1", "cues": ["1.1:expand"]}]


def test_the_worst_stall_counts_only_what_a_viewer_can_see():
    """A gap under the cover is trimmed out of the cut, so only the part after t=0 is a stall."""
    report = pagereport.read({**REPORTED, "frameGaps": [{"at": None, "ms": 900}, {"at": 0.05, "ms": 200}]})
    assert report.worst_gap_ms == 50


def test_the_report_names_every_field_the_contract_names():
    """The page and the reader share one field list, so a field added to one is missing from the other."""
    named = {field.alias or name for name, field in pagereport.PageReport.model_fields.items()}
    assert set(REPORT) <= named, set(REPORT) - named


def test_the_contract_and_the_finding_vocabulary_hold_one_list_of_page_codes():
    """Two enums name these codes, so a real warning would be dropped the day they disagree."""
    for warning in PageWarning:
        code = Code[warning.name]
        assert code.raised_by.value == warning.raised_by.value, code


def test_a_code_decktalk_measures_itself_is_refused_when_a_page_reports_it():
    """Half the page codes are measured from the frames, and a page reporting one decides its own verdict."""
    measured = next(w for w in PageWarning if w.raised_by is not RaisedBy.RUNTIME)
    report = pagereport.read({**REPORTED, "warnings": [{"code": measured.name, "message": "not mine to say"}]})
    assert report.warnings == ()
    assert report.unreadable and "warnings[0]" in report.unreadable[0]
