"""The stage commands: what each result becomes in the table, the envelope and the exit code."""

from __future__ import annotations

import json
import logging
from types import SimpleNamespace

import pytest

from decktalk.artifacts import CueTimes
from decktalk.cli import main, video
from decktalk.stages.align import AlignResult, SectionCueTimes
from decktalk.verdicts import Findings, Verdict


def test_record_exits_1_on_truncated_without_strict(fake_project, recording_row, record_result, monkeypatch, capsys):
    monkeypatch.setattr(video, "capture", lambda project, **kw: record_result(recording_row(Verdict.TRUNCATED)))
    assert main(["record"]) == 1
    assert "TRUNCATED" in capsys.readouterr().out
    assert main(["record", "--exit-zero"]) == 0
    capsys.readouterr()
    assert main(["record", "--json"]) == 1
    doc = json.loads(capsys.readouterr().out)
    assert doc["ok"] is False and doc["findings"]["certain"] == 1
    assert doc["record"]["recordings"][0]["verdicts"][0]["code"] == "TRUNCATED"
    assert doc["findings"]["items"] == [
        {
            "code": "TRUNCATED",
            "label": "TRUNCATED",
            "certain": True,
            "section": 1,
            "cue": None,
            "where": None,
            "detail": None,
        }
    ]


def test_record_exits_0_on_uncertain_unless_strict(fake_project, recording_row, record_result, monkeypatch, capsys):
    monkeypatch.setattr(
        video, "capture", lambda project, **kw: record_result(recording_row(Verdict.BLACK_UNSURE, Verdict.THIN_CHANGE))
    )
    assert main(["record"]) == 0
    assert main(["record", "--strict"]) == 1
    capsys.readouterr()
    assert main(["record", "--json"]) == 0
    doc = json.loads(capsys.readouterr().out)
    assert doc["ok"] is True and (doc["findings"]["certain"], doc["findings"]["uncertain"]) == (0, 2)
    assert main(["record", "--json", "--strict"]) == 1
    assert json.loads(capsys.readouterr().out)["ok"] is False


def test_verify_exit_zero_returns_0_on_off_cue(fake_project, cue_row, verify_result, monkeypatch, capsys):
    result = verify_result(
        cue_row("1:1.1a", Verdict.OFF_CUE, offset_ms=200, note="first change +200 ms from the cue, limit 67 ms"),
        cue_row("1:1.1b", Verdict.SKIPPED, changed_percent=None, final_seconds=None, reason="OPTED_OUT"),
    )
    monkeypatch.setattr(video, "read_final", lambda project, checks=None, only=None: result)
    assert main(["verify"]) == 1
    out = capsys.readouterr().out
    assert "OFF CUE" in out and "skipped OPTED_OUT" in out
    assert main(["verify", "--exit-zero"]) == 0
    capsys.readouterr()
    assert main(["verify", "--json", "--exit-zero"]) == 0
    doc = json.loads(capsys.readouterr().out)
    assert doc["ok"] is False and doc["exit_code"] == 0 and doc["findings"]["certain"] == 1
    assert doc["verify"]["cues"][0]["verdict"]["code"] == "OFF_CUE"
    assert [r["code"] for r in doc["findings"]["items"]] == ["OFF_CUE"]
    assert doc["summary"] == {"sections": 1, "cues": 2, "seconds": 10.0}


def test_verify_takes_its_cues_as_positionals_and_its_sections_as_ranges(
    fake_project, cue_row, verify_result, monkeypatch
):
    calls = []

    def fake_verify(project, checks=None, only=None):
        calls.append((checks, only))
        return verify_result(cue_row("1:1.1a", Verdict.CHANGED))

    monkeypatch.setattr(video, "read_final", fake_verify)
    assert main(["verify", "1:1.1a", "2:2.1b", "3:3.1c", "--only", "2-3"]) == 0
    assert main(["verify"]) == 0
    assert calls == [(["1:1.1a", "2:2.1b", "3:3.1c"], [2, 3]), (None, None)]


def test_align_counts_unknown_ids_unless_allowed(fake_project, monkeypatch, capsys):
    seen = []

    def fake_align(project, *, allow_unknown_cues=False):
        # The real result's own arithmetic, so this test proves the CLI and not a number typed here.
        seen.append(allow_unknown_cues)
        section = SectionCueTimes(key="04", speech_end=20.0, min_seconds=25.0, resolved=[])
        section.note("4.1a", Verdict.UNKNOWN_CUE, "no element carries this id")
        return AlignResult(
            cue_times=CueTimes(),
            sections=[section],
            unresolved=0,
            estimated=False,
            unknown=1,
            allow_unknown_cues=allow_unknown_cues,
        )

    monkeypatch.setattr(video, "resolve", fake_align)
    assert main(["align", "--json"]) == 1
    doc = json.loads(capsys.readouterr().out)
    assert (doc["findings"]["certain"], doc["findings"]["uncertain"]) == (1, 1)
    assert doc["summary"] == {"sections": 1, "unresolved": 0, "unknown": 1}
    row = doc["findings"]["items"][0]
    assert (row["code"], row["section"], row["cue"]) == ("UNKNOWN_CUE", 4, "4.1a")
    assert main(["align", "--allow-unknown-cues"]) == 0
    assert main(["align", "--allow-unknown-cues", "--strict"]) == 1  # the min_seconds shortfall is uncertain
    assert seen == [False, True, True]


def _narrate_result(seconds: float = 12.5) -> SimpleNamespace:
    """One narrate run with one take, which is enough to read its summary and its written list."""
    take = SimpleNamespace(file="a1b2c3d4.mp3", words_file="a1b2c3d4.words.json")
    return SimpleNamespace(
        segments=[],
        synthesized=["01"],
        cached=[],
        plans=[],
        rows=[],
        note=None,
        rate=0.0,
        narration=SimpleNamespace(words_per_minute=150),
        takes=SimpleNamespace(sections={"01": take}, total_seconds=seconds),
        timeline=SimpleNamespace(sections={}, total_seconds=seconds, estimated=True),
        findings=Findings(),
        to_dict=lambda root: {"sections": [{"key": "01"}]},
    )


def test_narrate_lists_every_take_it_wrote_and_not_the_index_alone(fake_project, monkeypatch, capsys):
    """`written` is what a caller opens next, and a take is the thing narrate makes."""
    fake_project.narration_dir.mkdir(parents=True)
    for name in ("a1b2c3d4.mp3", "a1b2c3d4.words.json", "takes.json", "timeline.json"):
        (fake_project.narration_dir / name).write_bytes(b"x")
    monkeypatch.setattr(video, "voice", lambda project, **kw: _narrate_result())
    assert main(["narrate", "--no-voice", "--json"]) == 0
    doc = json.loads(capsys.readouterr().out)
    assert doc["written"] == [
        "build/narration/a1b2c3d4.mp3",
        "build/narration/a1b2c3d4.words.json",
        "build/narration/takes.json",
        "build/narration/timeline.json",
    ]
    assert doc["summary"] == {"sections": 0, "synthesized": 1, "cached": 0, "total_seconds": 12.5}


def test_record_reports_each_webm_it_wrote_with_its_section(
    fake_project, recording_row, record_result, monkeypatch, capsys
):
    path = fake_project.root / "build" / "recordings" / "01.webm"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"webm")
    (path.parent / "01.json").write_text("{}", encoding="utf-8")
    row = recording_row()
    row.path = path
    monkeypatch.setattr(video, "capture", lambda project, **kw: record_result(row))
    assert main(["record", "--json"]) == 0
    doc = json.loads(capsys.readouterr().out)
    # The recording and the log beside it are one artifact in two files, so both are listed.
    assert doc["written"] == ["build/recordings/01.webm", "build/recordings/01.json"]
    assert doc["summary"] == {"sections": 1, "recorded": 1, "kept": 0}
    assert doc["record"]["recordings"] == [{"key": "01", "verdicts": []}]


def test_assemble_lists_the_final_file_and_every_file_beside_it(fake_project, monkeypatch, capsys):
    out = fake_project.root / "build" / "out"
    cut = fake_project.root / "build" / "sections"
    out.mkdir(parents=True)
    cut.mkdir(parents=True)
    for name in ("deck.mp4", "deck.srt", "deck.vtt"):
        (out / name).write_bytes(b"x")
    for name in ("01.mp4", "02.mp4"):
        (cut / name).write_bytes(b"x")
    result = SimpleNamespace(
        final=out / "deck.mp4",
        stamped=None,
        captions_srt=out / "deck.srt",
        captions_vtt=out / "deck.vtt",
        chapters=out / "deck.chapters.txt",
        sections=[SimpleNamespace(path=cut / "01.mp4"), SimpleNamespace(path=cut / "02.mp4")],
        duration=41.238,
        findings=Findings(),
        to_dict=lambda root: {"sections": ["01", "02"]},
    )
    monkeypatch.setattr(video, "cut_and_mix", lambda project, **kw: result)
    assert main(["assemble", "--json"]) == 0
    doc = json.loads(capsys.readouterr().out)
    # Each section as it was cut, then the final video and everything written beside it.
    assert doc["written"] == [
        "build/sections/01.mp4",
        "build/sections/02.mp4",
        "build/out/deck.mp4",
        "build/out/deck.srt",
        "build/out/deck.vtt",
    ]
    assert doc["summary"] == {"sections": 2, "seconds": 41.24}


def test_build_hands_the_run_the_log_to_write_and_lists_what_each_stage_wrote(fake_project, monkeypatch, capsys):
    """The log is the run's own, so the handler passes the path and gathers the files stage by stage."""

    cue_times = fake_project.build / "cue-times.json"
    seen: dict[str, object] = {}

    def pipeline(project, *, report=None, progress_path=None, **kw):
        seen["progress_path"] = progress_path
        seen["plan"] = (kw["from_stage"], kw["to_stage"])
        report("narrate", None)  # the stage is starting
        report("narrate", _narrate_result())  # the stage is done, and this is what it produced
        report("align", None)
        report("align", SimpleNamespace(cue_times_file=cue_times))
        return SimpleNamespace(assembly=None, findings=Findings(), to_dict=lambda root: {})

    fake_project.narration_dir.mkdir(parents=True)
    for name in ("a1b2c3d4.mp3", "a1b2c3d4.words.json", "takes.json", "timeline.json"):
        (fake_project.narration_dir / name).write_bytes(b"x")
    cue_times.write_bytes(b"{}")
    (fake_project.root / "run.jsonl").write_bytes(b"")
    monkeypatch.setattr(video, "run_pipeline", pipeline)
    assert main(["build", "--no-voice", "--progress", "run.jsonl", "--json"]) == 0
    assert seen["progress_path"] == fake_project.root / "run.jsonl" and seen["plan"] == (None, None)
    doc = json.loads(capsys.readouterr().out)
    assert doc["summary"]["stages"] == 2
    # Every file the run wrote, stage by stage, and the log a caller polled while it ran.
    assert doc["written"] == [
        "run.jsonl",
        "build/narration/a1b2c3d4.mp3",
        "build/narration/a1b2c3d4.words.json",
        "build/narration/takes.json",
        "build/narration/timeline.json",
        "build/cue-times.json",
    ]


def test_build_from_and_to_name_the_stages_the_run_executes(fake_project, monkeypatch, capsys):
    """The two stage flags are the CLI's over the run's own plan, and both ends are inclusive."""
    seen: dict[str, object] = {}

    def pipeline(project, *, report=None, **kw):
        seen.update(kw)
        return SimpleNamespace(assembly=None, findings=Findings(), to_dict=lambda root: {})

    monkeypatch.setattr(video, "run_pipeline", pipeline)
    assert main(["build", "--no-voice", "--from", "record", "--to", "assemble", "--json"]) == 0
    assert (seen["from_stage"], seen["to_stage"]) == ("record", "assemble")
    capsys.readouterr()
    # A stage name the parser does not know is a usage error, before anything is loaded.
    assert main(["build", "--from", "measure"]) == 2


def test_build_dry_run_names_the_plan_and_every_artifact_it_would_need(fake_project, monkeypatch, capsys):
    """A plan that starts past a stage reads the artifacts that stage writes, and says which are absent."""
    monkeypatch.setattr(video, "required_inputs", lambda project, plan: [fake_project.takes_path])
    monkeypatch.setattr(video, "run_pipeline", lambda *a, **kw: pytest.fail("a dry run ran the pipeline"))
    assert main(["build", "--from", "record", "--dry-run", "--json"]) == 1
    doc = json.loads(capsys.readouterr().out)
    assert doc["build"] == {"stages": ["record", "assemble", "verify"], "missing": ["build/narration/takes.json"]}
    assert doc["findings"]["certain"] == 1 and doc["written"] == []


def test_the_stage_prefix_leaves_with_the_run(fake_project, monkeypatch, capsys):
    """A person watching stderr reads where the run is without opening the progress log."""
    from decktalk.cli import configure_logging

    configure_logging(verbose=False, quiet=False)

    def pipeline(project, *, report=None, **kw):
        report("record", None)
        logging.getLogger("decktalk.stages.record").info("section 4 of 9")
        report("record", None if False else _record_result())
        return SimpleNamespace(assembly=None, findings=Findings(), to_dict=lambda root: {})

    monkeypatch.setattr(video, "run_pipeline", pipeline)
    assert main(["build", "--no-voice", "--json"]) == 0
    err = capsys.readouterr().err
    assert "[3/5 record] section 4 of 9" in err
    assert "[3/5 record] done in " in err
    # The filter leaves with the run, so the next command's lines carry no stage at all.
    logging.getLogger("decktalk.stages.record").info("after the run")
    assert "[3/5 record] after the run" not in capsys.readouterr().err


def _record_result() -> SimpleNamespace:
    """A record result with no section in it, which is enough for the handler to close the stage."""
    return SimpleNamespace(sections=[], kept_sections=[], findings=Findings(), to_dict=lambda root: {})
