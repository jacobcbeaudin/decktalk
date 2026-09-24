"""The stage commands: what each result becomes in the table, the envelope and the exit code."""

from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest

from decktalk.artifacts import CueTimes, Take, Takes
from decktalk.cli import main, video
from decktalk.cli.schema import BuildPayload, BuildPlanPayload, NarratePayload, RecordPayload, read_envelope
from decktalk.model import PageSection
from decktalk.model.script import Segment
from decktalk.pipeline import Stage, TakeStatus
from decktalk.settings import NarrationConfig
from decktalk.stages.align import AlignResult, SectionCueTimes
from decktalk.stages.assemble import AssembleResult
from decktalk.stages.assemble.cut import RenderedSection
from decktalk.stages.build import BuildResult
from decktalk.stages.narrate import NarrateResult, TakePlan
from decktalk.verdicts import Finding, SkipReason, Verdict


def test_record_exits_1_on_truncated_without_strict(fake_project, recording_row, record_result, monkeypatch, capsys):
    monkeypatch.setattr(video, "capture", lambda project, **kw: record_result(recording_row(Verdict.TRUNCATED)))
    assert main(["record"]) == 1
    assert Verdict.TRUNCATED.label in capsys.readouterr().out
    assert main(["record", "--exit-zero"]) == 0
    capsys.readouterr()
    assert main(["record", "--json"]) == 1
    doc = read_envelope(capsys.readouterr().out)
    assert doc.ok is False and doc.findings.certain == 1
    assert isinstance(doc.payload, RecordPayload) and doc.payload.recordings[0].verdicts == [Verdict.TRUNCATED]
    [row] = doc.findings.items
    assert (row.verdict, row.section, row.cue) == (Verdict.TRUNCATED, 1, None)
    assert row.where == "build/recordings/01.webm" and row.detail.startswith("Section 01 recorded TRUNCATED.")


def test_record_exits_0_on_uncertain_unless_strict(fake_project, recording_row, record_result, monkeypatch, capsys):
    monkeypatch.setattr(
        video, "capture", lambda project, **kw: record_result(recording_row(Verdict.BLACK_UNSURE, Verdict.THIN_CHANGE))
    )
    assert main(["record"]) == 0
    assert main(["record", "--strict"]) == 1
    capsys.readouterr()
    assert main(["record", "--json"]) == 0
    doc = read_envelope(capsys.readouterr().out)
    # `ok` says what was found and the exit code says what that costs, so an uncertain finding is
    # never reported as a clean run whatever `--strict` was passed.
    assert doc.ok is False and (doc.findings.certain, doc.findings.uncertain) == (0, 2)
    assert doc.exit_code == 0
    assert main(["record", "--json", "--strict"]) == 1
    strict = read_envelope(capsys.readouterr().out)
    assert strict.ok is False and strict.exit_code == 1


def test_verify_exit_zero_returns_0_on_off_cue(fake_project, cue_row, verify_result, monkeypatch, capsys):
    result = verify_result(
        cue_row("1:1.1a", Verdict.OFF_CUE, offset_ms=200, note="first change +200 ms from the cue, limit 67 ms"),
        cue_row("1:1.1b", Verdict.SKIPPED, SkipReason.OPTED_OUT, changed_percent=None, final_seconds=None),
    )
    monkeypatch.setattr(video, "read_final", lambda project, checks=None, only=None: result)
    assert main(["verify"]) == 1
    out = capsys.readouterr().out
    assert Verdict.OFF_CUE.label in out and f"{Verdict.SKIPPED.label} {SkipReason.OPTED_OUT.value}" in out
    assert main(["verify", "--exit-zero"]) == 0
    capsys.readouterr()
    assert main(["verify", "--json", "--exit-zero"]) == 0
    doc = read_envelope(capsys.readouterr().out)
    assert doc.ok is False and doc.exit_code == 0 and doc.findings.certain == 1
    assert [cue.verdict for cue in doc.payload.cues] == [Verdict.OFF_CUE, Verdict.SKIPPED]
    assert doc.payload.cues[1].reason is SkipReason.OPTED_OUT
    assert [row.verdict for row in doc.findings.items] == [Verdict.OFF_CUE]
    assert doc.summary == {"sections": 1, "cues": 2, "seconds": 10.0}


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
        section = SectionCueTimes(
            key="04", speech_end_seconds=20.0, min_seconds=25.0, resolved=[], cues_file="cues.json"
        )
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
    doc = read_envelope(capsys.readouterr().out)
    assert (doc.findings.certain, doc.findings.uncertain) == (1, 1)
    assert doc.summary == {"sections": 1, "unresolved": 0, "unknown": 1}
    row = doc.findings.items[0]
    assert (row.verdict, row.section, row.cue) == (Verdict.UNKNOWN_CUE, 4, "4.1a")
    assert main(["align", "--allow-unknown-cues"]) == 0
    assert main(["align", "--allow-unknown-cues", "--strict"]) == 1  # the min_seconds shortfall is uncertain
    assert seen == [False, True, True]


def _narrate_result(seconds: float = 12.5) -> NarrateResult:
    """One narrate run with one take, which is enough to read its summary and its written list."""
    take = Take(
        index=1, chapter="Open", file="a1b2c3d4.mp3", words_file="a1b2c3d4.words.json", hash="a1b2c3d4",
        word_count=2, estimated_seconds=seconds, duration_seconds=seconds, voiced=False,
    )  # fmt: skip
    takes = Takes(script="script.md", model="m", output_format="mp3", estimated=True, sections={"01": take})
    takes.total_seconds = seconds
    return NarrateResult(
        plans=[],
        voice={"provider": "elevenlabs", "model": "m", "settings": {}},
        narration=NarrationConfig(),
        rate=0.0,
        takes=takes,
        synthesized=["01"],
    )


def test_narrate_lists_every_take_it_wrote_and_not_the_index_alone(fake_project, monkeypatch, capsys):
    """`written` is what a caller opens next, and a take is the thing narrate makes."""
    fake_project.narration_dir.mkdir(parents=True)
    for name in ("a1b2c3d4.mp3", "a1b2c3d4.words.json", "takes.json", "narration.mp3"):
        (fake_project.narration_dir / name).write_bytes(b"x")
    monkeypatch.setattr(video, "voice", lambda project, **kw: _narrate_result())
    assert main(["narrate", "--no-voice", "--json"]) == 0
    doc = read_envelope(capsys.readouterr().out)
    assert doc.written == [
        "build/narration/a1b2c3d4.mp3",
        "build/narration/a1b2c3d4.words.json",
        "build/narration/takes.json",
        "build/narration/narration.mp3",
    ]
    assert doc.summary == {"sections": 0, "synthesized": 1, "cached": 0, "total_seconds": 12.5}
    assert isinstance(doc.payload, NarratePayload) and doc.payload.takes is not None
    assert [take.hash for take in doc.payload.takes.sections] == ["a1b2c3d4"]


def test_a_dry_run_reports_what_it_would_voice_and_never_what_it_voiced(fake_project, monkeypatch, capsys):
    """A rehearsal voices nothing, so a summary of `synthesized 0` would read as nothing to do."""
    result = _narrate_result()
    result.takes, result.synthesized, result.cached = None, [], []
    result.plans = [
        TakePlan(
            segment=Segment(index=1, title="Open", slug="open", text="Hello there."), status=TakeStatus.SYNTHESIZE
        ),
        TakePlan(segment=Segment(index=2, title="Close", slug="close", text="Bye now."), status=TakeStatus.CACHED),
    ]
    monkeypatch.setattr(video, "voice", lambda project, **kw: result)
    assert main(["narrate", "--dry-run", "--json"]) == 0
    doc = read_envelope(capsys.readouterr().out)
    assert doc.summary == {"sections": 2, "would_synthesize": 1, "cached": 1, "total_seconds": None}
    assert doc.written == [] and doc.payload.takes is None
    assert [plan.status for plan in doc.payload.sections] == [TakeStatus.SYNTHESIZE, TakeStatus.CACHED]


def test_record_leaves_a_section_it_kept_out_of_what_it_wrote(
    fake_project, recording_row, record_result, monkeypatch, capsys
):
    """A kept recording is on disk from an earlier run, so a caller re-reads nothing spare."""
    fresh, reused = recording_row(number=1), recording_row(number=2, kept=True)
    fresh.path.parent.mkdir(parents=True)
    for row in (fresh, reused):
        row.path.write_bytes(b"webm")
        row.path.with_suffix(".json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(video, "capture", lambda project, **kw: record_result(fresh, reused))
    assert main(["record", "--json"]) == 0
    doc = read_envelope(capsys.readouterr().out)
    assert doc.written == ["build/recordings/01.webm", "build/recordings/01.json"]
    assert [(row.key, row.kept) for row in doc.payload.recordings] == [("01", False), ("02", True)]


def test_narrate_leaves_a_take_it_reused_out_of_what_it_wrote(fake_project, monkeypatch, capsys):
    """A cached take is on disk already, so a caller that re-reads `written` re-reads nothing spare."""
    fake_project.narration_dir.mkdir(parents=True)
    for name in ("a1b2c3d4.mp3", "a1b2c3d4.words.json", "takes.json", "narration.mp3"):
        (fake_project.narration_dir / name).write_bytes(b"x")
    result = _narrate_result()
    result.synthesized, result.cached = [], ["01"]
    monkeypatch.setattr(video, "voice", lambda project, **kw: result)
    assert main(["narrate", "--no-voice", "--json"]) == 0
    doc = read_envelope(capsys.readouterr().out)
    assert doc.written == ["build/narration/takes.json", "build/narration/narration.mp3"]


def test_record_reports_each_webm_it_wrote_with_its_section(
    fake_project, recording_row, record_result, monkeypatch, capsys
):
    row = recording_row()
    row.path.parent.mkdir(parents=True)
    row.path.write_bytes(b"webm")
    (row.path.parent / "01.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(video, "capture", lambda project, **kw: record_result(row))
    assert main(["record", "--json"]) == 0
    doc = read_envelope(capsys.readouterr().out)
    # The recording and the log beside it are one artifact in two files, so both are listed.
    assert doc.written == ["build/recordings/01.webm", "build/recordings/01.json"]
    assert doc.summary == {"sections": 1, "recorded": 1, "kept": 0}
    [recording] = doc.payload.recordings
    assert (recording.key, recording.file, recording.verdicts) == ("01", "build/recordings/01.webm", [])


def _assemble_result(fake_project, sections: tuple[str, ...] = ("01",)) -> AssembleResult:
    """One assembled film with every file beside it on disk, for a run that gathers what it wrote."""
    out, cut = fake_project.root / "build" / "out", fake_project.root / "build" / "sections"
    out.mkdir(parents=True, exist_ok=True)
    cut.mkdir(parents=True, exist_ok=True)
    names = (
        "deck.mp4", "deck.srt", "deck.vtt", "deck.chapters.txt", "cuts.json",
        "deck-transcript.html", "deck-poster.png",
    )  # fmt: skip
    for name in names:
        (out / name).write_bytes(b"x")
    rows = []
    for key in sections:
        (cut / f"{key}.mp4").write_bytes(b"x")
        page = PageSection(number=int(key), page="deck/index.html", scene=key)
        rows.append(RenderedSection(section=page, path=cut / f"{key}.mp4", duration=20.619, note=f"{key}.webm"))
    return AssembleResult(
        final=out / "deck.mp4",
        stamped=None,
        duration=41.238,
        sections=rows,
        warnings=[],
        loudness=None,
        captions_srt=out / "deck.srt",
        captions_vtt=out / "deck.vtt",
        chapters=out / "deck.chapters.txt",
        cuts_file=out / "cuts.json",
        transcript=out / "deck-transcript.html",
        poster=out / "deck-poster.png",
    )


def test_assemble_lists_the_final_file_and_every_file_beside_it(fake_project, monkeypatch, capsys):
    result = _assemble_result(fake_project, ("01", "02"))
    monkeypatch.setattr(video, "cut_and_mix", lambda project, **kw: result)
    assert main(["assemble", "--json"]) == 0
    doc = read_envelope(capsys.readouterr().out)
    # Each section as it was cut, then every file the result says it wrote, the cut list, the
    # transcript and the poster among them, because a caller reads `written` to know what to open.
    assert doc.written == [
        "build/sections/01.mp4",
        "build/sections/02.mp4",
        "build/out/deck.srt",
        "build/out/deck.vtt",
        "build/out/deck.chapters.txt",
        "build/out/cuts.json",
        "build/out/deck-transcript.html",
        "build/out/deck-poster.png",
        "build/out/deck.mp4",
    ]
    assert doc.summary == {"sections": 2, "seconds": 41.24}
    assert doc.payload.captions.transcript == "build/out/deck-transcript.html"


def test_build_hands_the_run_the_log_to_write_and_lists_what_each_stage_wrote(fake_project, monkeypatch, capsys):
    """The log is the run's own, so the handler passes the path and gathers the files stage by stage."""

    cue_times = fake_project.build / "cue-times.json"
    seen: dict[str, object] = {}

    def pipeline(project, *, report=None, progress_path=None, **kw):
        seen["progress_path"] = progress_path
        seen["plan"] = (kw["from_stage"], kw["to_stage"])
        report(Stage.NARRATE, None)  # the stage is starting
        report(Stage.NARRATE, _narrate_result())  # the stage is done, and this is what it produced
        report(Stage.ALIGN, None)
        report(Stage.ALIGN, SimpleNamespace(cue_times_file=cue_times))
        # A caller runs `build`, not `assemble` alone, so the files a viewer receives are gathered
        # through this path and are asserted here rather than only on the standalone command.
        report(Stage.ASSEMBLE, None)
        report(Stage.ASSEMBLE, _assemble_result(fake_project))
        return BuildResult()

    fake_project.narration_dir.mkdir(parents=True)
    for name in ("a1b2c3d4.mp3", "a1b2c3d4.words.json", "takes.json", "narration.mp3"):
        (fake_project.narration_dir / name).write_bytes(b"x")
    cue_times.write_bytes(b"{}")
    (fake_project.root / "run.jsonl").write_bytes(b"")
    monkeypatch.setattr(video, "run_pipeline", pipeline)
    assert main(["build", "--no-voice", "--progress", "run.jsonl", "--json"]) == 0
    assert seen["progress_path"] == fake_project.root / "run.jsonl" and seen["plan"] == (None, None)
    doc = read_envelope(capsys.readouterr().out)
    assert doc.summary["stages"] == 3
    # Every file the run wrote, stage by stage, and the log a caller polled while it ran.
    assert doc.written == [
        "run.jsonl",
        "build/narration/a1b2c3d4.mp3",
        "build/narration/a1b2c3d4.words.json",
        "build/narration/takes.json",
        "build/narration/narration.mp3",
        "build/cue-times.json",
        "build/sections/01.mp4",
        "build/out/deck.srt",
        "build/out/deck.vtt",
        "build/out/deck.chapters.txt",
        "build/out/cuts.json",
        "build/out/deck-transcript.html",
        "build/out/deck-poster.png",
        "build/out/deck.mp4",
    ]


def test_build_from_and_to_name_the_stages_the_run_executes(fake_project, monkeypatch, capsys):
    """The two stage flags are the CLI's over the run's own plan, and both ends are inclusive."""
    seen: dict[str, object] = {}

    def pipeline(project, *, report=None, **kw):
        seen.update(kw)
        return BuildResult(stages=(Stage.RECORD, Stage.ASSEMBLE))

    monkeypatch.setattr(video, "run_pipeline", pipeline)
    assert main(["build", "--no-voice", "--from", "record", "--to", "assemble", "--json"]) == 0
    assert (seen["from_stage"], seen["to_stage"]) == (Stage.RECORD, Stage.ASSEMBLE)
    doc = read_envelope(capsys.readouterr().out)
    assert isinstance(doc.payload, BuildPayload) and doc.payload.stages == [Stage.RECORD, Stage.ASSEMBLE]
    # A stage name the parser does not know is a usage error, before anything is loaded.
    assert main(["build", "--from", "measure"]) == 2


def test_build_dry_run_names_the_plan_and_every_artifact_it_would_need(fake_project, monkeypatch, capsys):
    """A plan that starts past a stage reads the artifacts that stage writes, and says which are absent."""
    monkeypatch.setattr(video, "required_inputs", lambda project, plan: [fake_project.takes_path])
    monkeypatch.setattr(video, "run_pipeline", lambda *a, **kw: pytest.fail("a dry run ran the pipeline"))
    assert main(["build", "--from", "record", "--dry-run", "--json"]) == 1
    doc = read_envelope(capsys.readouterr().out)
    assert doc.payload == BuildPlanPayload(
        stages=[Stage.RECORD, Stage.ASSEMBLE, Stage.VERIFY], missing=["build/narration/takes.json"]
    )
    missing = Finding(detail="build/narration/takes.json is not there", verdict=Verdict.MISSING)
    assert doc.findings.items == [Finding(**{**vars(missing), "where": "build/narration/takes.json"})]
    assert doc.findings.certain == 1 and doc.written == []


def test_the_stage_prefix_leaves_with_the_run(fake_project, monkeypatch, capsys):
    """A person watching stderr reads where the run is without opening the progress log."""
    from decktalk.cli import configure_logging

    configure_logging(verbose=False, quiet=False)

    def pipeline(project, *, report=None, **kw):
        # The run opens a stage with a result of None, which is the call `stages/build.py` makes and
        # `tests/decktalk/stages/test_build.py` holds it to. What is tested here is what the CLI does with it.
        report(Stage.RECORD, None)
        logging.getLogger("decktalk.stages.record").info("section 4 of 9")
        report(Stage.RECORD, _record_result())
        return BuildResult()

    monkeypatch.setattr(video, "run_pipeline", pipeline)
    assert main(["build", "--no-voice", "--json"]) == 0
    err = capsys.readouterr().err
    assert "[3/5 record] section 4 of 9" in err
    assert "[3/5 record] done in " in err
    # The filter leaves with the run, so the next command's lines carry no stage at all.
    logging.getLogger("decktalk.stages.record").info("after the run")
    assert "[3/5 record] after the run" not in capsys.readouterr().err


def _record_result():
    """A record result with no section in it, which is enough for the handler to close the stage."""
    from decktalk.stages.record import RecordResult

    return RecordResult()
