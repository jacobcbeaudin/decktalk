"""The status report: what is on disk, what has gone stale, which runs are open, and what to do next."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from decktalk.artifacts import Take, file_digest, take_file, words_file
from decktalk.events import Level, RunLog
from decktalk.findings import Code
from decktalk.inputs import Inputs
from decktalk.page import Q
from decktalk.pipeline import Stage
from decktalk.results import SectionKind, StatusResult, TakeState
from decktalk.speech import DECLARED, FREE
from decktalk.stages import kept, voice_model
from decktalk.stages import status as stage
from decktalk.stages.narrate.plan import placeholder_inputs, take_inputs
from decktalk.stages.narrate.state import CHANGED, UNINDEXED, WITHOUT_A_VOICE, take_states
from decktalk.stages.status import next_command, source_of, status
from support.fakes import FakeFfmpeg
from support.pages import SCENE_ONE
from support.projects import load_project
from support.runs import a_run, notes
from support.takes import TAKE_SUFFIX, a_take, damage_take, write_takes

TOML = """
[project]
name = "demo"

[[section]]
number = 1
page = "deck/index.html"
scene = "1"

[[section]]
number = 2
clip = "media/b-roll.mp4"
"""
"""One page section and one clip section, which is the smallest project with both kinds in it."""

SCRIPT = "# Demo\n\n## 1. One\n\nHello there again.\n"


pytestmark = pytest.mark.usefixtures("fake_ffmpeg")
"""Every case here drives a stage that reaches for ffmpeg, so the encoder is faked at its own seam."""


def a_project(tmp_path: Path, *, script: str | None = SCRIPT, toml: str = TOML) -> Inputs:
    """A project whose page and clip are both on disk, so nothing is missing until a case removes it."""
    return load_project(tmp_path, toml, page=SCENE_ONE, script=script, media=("media/b-roll.mp4",))


def take_on_disk(inputs: Inputs, *, spoken: str = "Hello there again.", voiced: bool = True) -> Take:
    """One take for section one, written to the index with its audio and its words file beside it, so it is held."""
    take = a_take(1, seconds=2.0, voiced=voiced, spoken=spoken)
    write_takes(inputs, take)
    inputs.workspace.takes.mkdir(parents=True, exist_ok=True)
    inputs.workspace.take_path(take.digest).write_bytes(b"audio")
    inputs.workspace.words_path(take.digest).write_text("{}", encoding="utf-8")
    return take


# ---- what to do next ---------------------------------------------------------------------------


def built_up_to(inputs: Inputs, steps: int) -> None:
    """The first `steps` artifacts on disk in the order a build makes them, with the take speaking the script."""
    made = [
        lambda: take_on_disk(inputs),
        lambda: inputs.workspace.cue_times_path.write_text('{"sections": []}', encoding="utf-8"),
        lambda: inputs.workspace.recording("01").write_bytes(b""),
        lambda: inputs.workspace.film.write_bytes(b"film"),
    ]
    inputs.workspace.recording("01").parent.mkdir(parents=True, exist_ok=True)
    inputs.workspace.film.parent.mkdir(parents=True, exist_ok=True)
    for make in made[:steps]:
        make()


@pytest.mark.parametrize(
    ("steps", "command"),
    [
        # The first move a project is told to make never costs money.
        pytest.param(0, "decktalk narrate --no-spend", id="nothing built rehearses the voice"),
        pytest.param(1, f"decktalk {Stage.CUE.value}", id="takes resolve their cues"),
        pytest.param(2, f"decktalk {Stage.RECORD.value}", id="cue times record"),
        # A project that describes no score is never told to generate one: a stage with nothing
        # to do is not the next thing to do.
        pytest.param(3, f"decktalk {Stage.ASSEMBLE.value}", id="recordings assemble, past the score"),
        pytest.param(4, f"decktalk {Stage.VERIFY.value}", id="a film is measured"),
    ],
)
def test_a_project_is_told_the_next_stage_its_artifacts_leave(tmp_path: Path, steps: int, command: str) -> None:
    inputs = a_project(tmp_path)
    built_up_to(inputs, steps)
    assert next_command(inputs, take_states(inputs)) == command


def measured(inputs: Inputs) -> None:
    """The record a build leaves after it assembled this film and verified it."""
    options = {"only": None}
    made = kept.assemble_digest(inputs, options)
    film = inputs.relative(inputs.workspace.film).as_posix()
    kept.Kept(
        assemble=kept.KeptStage(digest=made, options=options, outputs={film: file_digest(inputs.workspace.film)}),
        verify=kept.KeptStage(digest=kept.verify_digest(inputs, made, options), options=options),
    ).write(inputs.workspace.kept_path)


def test_a_film_the_last_build_measured_leaves_nothing_next(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """After a build that verified, there is nothing to do, and saying verify again would be false."""
    # The recording here has no log beside it, so the recorder's own rule is told it still stands.
    monkeypatch.setattr(stage, "stale_recording", lambda _inputs, _section: None)
    inputs = a_project(tmp_path)
    built_up_to(inputs, 4)
    measured(inputs)
    assert next_command(inputs, take_states(inputs)) is None
    assert status(inputs, a_run(tmp_path)).next is None


def test_a_script_edit_after_the_build_names_build(tmp_path: Path) -> None:
    """A take of older words is stale, and verify would measure a film its inputs no longer describe."""
    inputs = a_project(tmp_path)
    built_up_to(inputs, 4)
    measured(inputs)
    (tmp_path / "script.md").write_text(SCRIPT.replace("again", "once more"), encoding="utf-8")
    assert next_command(inputs, take_states(inputs)) == "decktalk build"


def test_a_stale_recording_names_build(tmp_path: Path) -> None:
    inputs = a_project(tmp_path)
    built_up_to(inputs, 4)
    assert next_command(inputs, take_states(inputs), stale=True) == "decktalk build"


def test_a_film_changed_since_the_build_names_build(tmp_path: Path) -> None:
    """A film rewritten outside the build is not the one the record vouches for."""
    inputs = a_project(tmp_path)
    built_up_to(inputs, 4)
    measured(inputs)
    inputs.workspace.film.write_bytes(b"another film")
    assert next_command(inputs, take_states(inputs)) == "decktalk build"


# ---- the sections ------------------------------------------------------------------------------


def test_a_page_section_names_the_scene_it_plays_with_the_runtime_s_own_word(tmp_path: Path) -> None:
    """The query vocabulary has one home, so the report reads the key rather than spelling it."""
    inputs = a_project(tmp_path)
    page, clip = inputs.document.sections
    assert source_of(page) == f"deck/index.html?{Q.SCENE.value}=1"
    assert source_of(clip) == "media/b-roll.mp4"


def test_a_section_is_voiced_when_a_take_of_its_current_inputs_is_held(tmp_path: Path, fake_ffmpeg: FakeFfmpeg) -> None:
    inputs = a_project(tmp_path, toml=VOICED)
    digest = played_digest(inputs)
    write_takes(inputs, a_take(1, digest=digest, spoken="Hello there again."))
    a_take_pair(inputs, digest)
    result = status(inputs, a_run(tmp_path))
    assert [row.take_state for row in result.sections] == [TakeState.VOICED, None]
    assert [row.kind for row in result.sections] == [SectionKind.PAGE, SectionKind.CLIP]
    assert fake_ffmpeg.calls == []


def test_a_held_placeholder_reads_placeholder(tmp_path: Path) -> None:
    """A placeholder is not a take a voice spoke, so the state that says what was paid for never counts it."""
    inputs = a_project(tmp_path)
    [section] = inputs.spoken()
    stand_in = placeholder_inputs(inputs, section).digest
    write_takes(inputs, a_take(1, digest=stand_in, voiced=False, spoken="Hello there again."))
    inputs.workspace.narrate_dir.mkdir(parents=True, exist_ok=True)
    (inputs.workspace.narrate_dir / take_file(stand_in, TAKE_SUFFIX)).write_bytes(b"click")
    (inputs.workspace.narrate_dir / words_file(stand_in)).write_text("{}", encoding="utf-8")
    result = status(inputs, a_run(tmp_path))
    assert result.sections[0].take_state is TakeState.PLACEHOLDER


def test_a_take_of_older_words_is_stale(tmp_path: Path) -> None:
    """A take the author has since rewritten is not a take of what this section says now."""
    inputs = a_project(tmp_path)
    take_on_disk(inputs, spoken="Something else entirely.")
    result = status(inputs, a_run(tmp_path))
    assert result.sections[0].take_state is TakeState.STALE
    assert result.sections[0].take_reason == CHANGED


@pytest.mark.parametrize(("spoken", "bought"), [("Hello there again.", True), ("Something else.", False)])
def test_a_current_take_or_an_old_placeholder_is_not_stale(tmp_path: Path, spoken: str, bought: bool) -> None:
    """A placeholder of older words was never bought, so there is nothing to voice again."""
    inputs = a_project(tmp_path)
    take_on_disk(inputs, spoken=spoken, voiced=bought)
    assert status(inputs, a_run(tmp_path)).sections[0].take_state is not TakeState.STALE


def test_a_recorded_section_says_so_and_a_cut_one_says_so(tmp_path: Path) -> None:
    inputs = a_project(tmp_path)
    inputs.workspace.recording("01").parent.mkdir(parents=True, exist_ok=True)
    inputs.workspace.recording("01").write_bytes(b"")
    inputs.workspace.section_video("02").parent.mkdir(parents=True, exist_ok=True)
    inputs.workspace.section_video("02").write_bytes(b"")
    result = status(inputs, a_run(tmp_path))
    assert [row.recorded for row in result.sections] == [True, False]
    assert [row.assembled for row in result.sections] == [False, True]


def test_whether_a_recording_still_stands_is_asked_of_record(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """One rule decides what a run skips and what this report calls stale, and `record` owns it."""
    inputs = a_project(tmp_path)
    inputs.workspace.recording("01").parent.mkdir(parents=True, exist_ok=True)
    inputs.workspace.recording("01").write_bytes(b"")
    monkeypatch.setattr(stage, "stale_recording", lambda _inputs, _section: "section 1: its page changed")
    run = a_run(tmp_path)
    said: list[RunLog] = []
    run.machine.events.subscribe(lambda event: said.append(event) if isinstance(event, RunLog) else None)
    result = status(inputs, run)
    assert result.sections[0].recording_stale is True
    # A reading a reader may act on is a warning, because it is not what the author asked for.
    assert any("its page changed" in line.message and line.level is Level.WARNING for line in said)


def test_a_section_with_no_recording_is_never_stale(tmp_path: Path) -> None:
    """Nothing on disk cannot have stopped matching the project, so the row says what it has."""
    result = status(a_project(tmp_path), a_run(tmp_path))
    assert [row.recording_stale for row in result.sections] == [False, False]


# ---- what the project has not got ---------------------------------------------------------------


def test_a_missing_script_is_a_finding(tmp_path: Path) -> None:
    result = status(a_project(tmp_path, script=None), a_run(tmp_path))
    assert [found.code for found in result.findings] == [Code.FILE_MISSING]
    assert result.ok is False


def test_a_missing_page_is_a_finding_that_names_its_section(tmp_path: Path) -> None:
    inputs = a_project(tmp_path)
    (tmp_path / "deck" / "index.html").unlink()
    result = status(inputs, a_run(tmp_path))
    found = next(row for row in result.findings if row.code is Code.FILE_MISSING)
    assert found.location.section == 1
    assert "deck/index.html" in found.message


def test_an_optional_clip_the_project_has_not_got_is_not_a_finding(tmp_path: Path) -> None:
    """A slate stands in for an optional clip, so its absence is the project working as written."""
    toml = TOML.replace('clip = "media/b-roll.mp4"', 'clip = "media/b-roll.mp4"\noptional = true')
    inputs = a_project(tmp_path, toml=toml)
    (tmp_path / "media" / "b-roll.mp4").unlink()
    assert status(inputs, a_run(tmp_path)).findings == ()


def test_a_file_that_will_not_parse_is_a_line_and_not_a_refusal(tmp_path: Path) -> None:
    """Reading the inputs against each other is this command's work rather than its obstacle."""
    inputs = a_project(tmp_path)
    (tmp_path / "cues.json").write_text("{not json", encoding="utf-8")
    run = a_run(tmp_path)
    said = notes(run)
    result = status(inputs, run)
    assert isinstance(result, StatusResult)
    assert any("cues.json will not parse" in line for line in said)


# ---- the runs that are still open ----------------------------------------------------------------


def an_events_file(inputs: Inputs, name: str, lines: list[dict]) -> Path:
    path = inputs.workspace.events_path(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(line) for line in lines) + "\n", encoding="utf-8")
    return path


def opened(run: str, seq: int = 0) -> dict:
    return {"event": "run.start", "time": "2026-09-24T01:00:00Z", "seq": seq, "run": run, "events_file": None}


def test_a_run_that_has_not_closed_is_listed_with_the_stage_it_opened(tmp_path: Path) -> None:
    inputs = a_project(tmp_path)
    an_events_file(
        inputs,
        "other",
        [
            opened("other"),
            {"event": "stage.start", "time": "2026-09-24T01:00:01Z", "seq": 1, "run": "other",
             "stage": "record", "index": 1, "count": 2},
        ],
    )  # fmt: skip
    result = status(inputs, a_run(tmp_path))
    assert [row.run for row in result.runs] == ["other"]
    assert result.runs[0].stage is Stage.RECORD
    assert result.runs[0].events_file == Path("build/events/other.jsonl")


def test_a_run_that_has_closed_is_not_listed(tmp_path: Path) -> None:
    inputs = a_project(tmp_path)
    an_events_file(
        inputs,
        "done",
        [
            opened("done"),
            {"event": "run.done", "time": "2026-09-24T01:00:02Z", "seq": 1, "run": "done",
             "outcome": "ran", "elapsed_seconds": 1.0},
        ],
    )  # fmt: skip
    assert status(inputs, a_run(tmp_path)).runs == ()


def test_the_run_making_the_report_is_not_one_of_the_runs_it_reports(tmp_path: Path) -> None:
    """A caller asking what is running wants the runs it is not making."""
    inputs = a_project(tmp_path)
    an_events_file(inputs, "r1", [opened("r1")])
    assert status(inputs, a_run(tmp_path)).runs == ()


def test_an_events_file_this_version_cannot_read_is_skipped_with_a_line(tmp_path: Path) -> None:
    inputs = a_project(tmp_path)
    an_events_file(inputs, "odd", [{"event": "nothing-like-this"}])
    run = a_run(tmp_path)
    said = notes(run)
    result = status(inputs, run)
    assert result.runs == ()
    assert any("odd.jsonl" in line for line in said)


# ---- the film ------------------------------------------------------------------------------------


def test_the_built_film_is_measured_and_an_unbuilt_one_is_null(tmp_path: Path, fake_ffmpeg: FakeFfmpeg) -> None:
    inputs = a_project(tmp_path)
    assert status(inputs, a_run(tmp_path)).film is None
    inputs.workspace.film.parent.mkdir(parents=True, exist_ok=True)
    inputs.workspace.film.write_bytes(b"")
    fake_ffmpeg.duration_seconds = 12.5
    result = status(inputs, a_run(tmp_path))
    assert result.film == Path("build/final/demo.mp4")
    assert result.film_seconds == 12.5


def test_the_report_writes_nothing(tmp_path: Path) -> None:
    """`status` reads what is on disk, so no result of it may claim to have written anything."""
    assert "written" not in StatusResult.model_fields
    run = a_run(tmp_path)
    status(a_project(tmp_path), run)
    assert run.written == []


def test_the_report_names_the_two_files_the_author_writes(tmp_path: Path) -> None:
    result = status(a_project(tmp_path), a_run(tmp_path))
    assert result.script == Path("script.md")
    assert result.cues_file == Path("cues.json")
    assert result.name == "demo"


# ---- takes no section plays --------------------------------------------------------------------

VOICED = TOML.replace("[[section]]", '[voice]\nid = "voice-under-test"\n\n[[section]]', 1)
"""The demo project with its voice named, which is what lets the report name the take each section plays."""


def played_digest(inputs: Inputs) -> str:
    """The digest of the take section one plays, taken the one way narrate takes it."""
    return played_digest_of(inputs, 1)


def played_digest_of(inputs: Inputs, number: int) -> str:
    """The digest of the take this section plays, taken the one way narrate takes it."""
    section = next(section for section in inputs.spoken() if section.number == number)
    voice = inputs.settings.voice
    return take_inputs(inputs, section, provider=voice.provider, voice_id=voice.id, model=voice_model(inputs)).digest


def a_take_pair(inputs: Inputs, digest: str, *, audio: bytes = b"audio", words: bytes = b"{}") -> list[Path]:
    """One take and its words file in the takes directory, which is all the listing reads of them."""
    inputs.workspace.takes.mkdir(parents=True, exist_ok=True)
    pair = [inputs.workspace.takes / take_file(digest, TAKE_SUFFIX), inputs.workspace.takes / words_file(digest)]
    pair[0].write_bytes(audio)
    pair[1].write_bytes(words)
    return pair


def test_takes_no_section_plays_are_counted_with_their_size_and_none_is_deleted(tmp_path: Path) -> None:
    inputs = a_project(tmp_path, toml=VOICED)
    a_take_pair(inputs, played_digest(inputs))
    old = a_take_pair(inputs, "00000000000000ab", audio=b"x" * 1_500_000, words=b"y" * 2_000)
    aligned = inputs.workspace.takes / "aligned" / "k1.words.json"
    aligned.parent.mkdir()
    aligned.write_bytes(b"z" * 500)
    (inputs.workspace.takes / "README.md").write_text("not a take", encoding="utf-8")
    (inputs.workspace.takes / "00000000000000cd.words.json.unreadable").write_bytes(b"set aside")
    before = sorted(path for path in inputs.workspace.takes.rglob("*"))
    run = a_run(tmp_path)
    unplayed = status(inputs, run).unplayed
    assert unplayed is not None
    assert unplayed.directory == Path("takes")
    assert (unplayed.takes, unplayed.aligned) == (1, 1)
    assert unplayed.bytes == 1_500_000 + 2_000 + 500
    assert unplayed.files == tuple(sorted(inputs.relative(path) for path in [*old, aligned]))
    sentence = unplayed.sentence or ""
    assert sentence.startswith("1 take and 1 aligned words file in takes/ that no section plays (1.5 MB). ")
    assert "git rm" in sentence
    assert sorted(path for path in inputs.workspace.takes.rglob("*")) == before, "the report deletes nothing"
    assert run.written == []


def test_a_take_the_index_still_plays_is_not_listed_after_the_script_moves_on(tmp_path: Path) -> None:
    """The index names what the film plays until narrate runs again, so its takes are never offered for removal."""
    inputs = a_project(tmp_path, toml=VOICED, script=SCRIPT.replace("Hello there again.", "Hello once more."))
    played = a_take(1, digest="00000000000000ef", spoken="Hello there again.")
    write_takes(inputs, played)
    a_take_pair(inputs, played.digest)
    unplayed = status(inputs, a_run(tmp_path)).unplayed
    assert unplayed is not None
    assert (unplayed.takes, unplayed.files) == (0, ())


def test_a_project_with_every_take_played_says_nothing_of_them(tmp_path: Path) -> None:
    inputs = a_project(tmp_path, toml=VOICED)
    a_take_pair(inputs, played_digest(inputs))
    unplayed = status(inputs, a_run(tmp_path)).unplayed
    assert unplayed is not None
    assert (unplayed.takes, unplayed.aligned, unplayed.bytes) == (0, 0, 0)
    assert unplayed.sentence is None


def test_with_no_voice_named_no_take_is_called_unplayed(tmp_path: Path) -> None:
    """Without the voice the digest the script plays cannot be taken, so the report claims nothing about any take."""
    inputs = a_project(tmp_path)
    old = a_take_pair(inputs, "00000000000000ab")
    assert status(inputs, a_run(tmp_path)).unplayed is None
    assert all(path.is_file() for path in old)


# ---- each section's take state -------------------------------------------------------------------

TWO = VOICED + '\n[[section]]\nnumber = 3\npage = "deck/index.html"\nscene = "3"\n'
"""The voiced demo project with a second page section, so one section's refusal leaves another to read."""

TWO_SCRIPT = SCRIPT + "\n## 3. Three\n\nThe third one closes.\n"


def finished(inputs: Inputs, *rows: Take) -> None:
    """Every artifact a build writes on disk and measured, with these rows as the take index."""
    write_takes(inputs, *rows)
    inputs.workspace.cue_times_path.write_text('{"sections": []}', encoding="utf-8")
    inputs.workspace.recording("01").parent.mkdir(parents=True, exist_ok=True)
    inputs.workspace.recording("01").write_bytes(b"")
    inputs.workspace.film.parent.mkdir(parents=True, exist_ok=True)
    inputs.workspace.film.write_bytes(b"film")
    measured(inputs)


@pytest.fixture
def recordings_stand(monkeypatch: pytest.MonkeyPatch) -> None:
    """The recording here has no log beside it, so the recorder's own rule is told it still stands."""
    monkeypatch.setattr(stage, "stale_recording", lambda _inputs, _section: None)


@pytest.mark.usefixtures("recordings_stand")
def test_a_voice_change_makes_the_take_stale_and_names_build(tmp_path: Path) -> None:
    before = a_project(tmp_path, toml=VOICED)
    digest = played_digest(before)
    a_take_pair(before, digest)
    after = a_project(tmp_path, toml=VOICED.replace("voice-under-test", "another-voice"))
    finished(after, a_take(1, digest=digest, spoken="Hello there again."))
    result = status(after, a_run(tmp_path))
    assert result.sections[0].take_state is TakeState.STALE
    assert result.sections[0].take_reason == CHANGED
    assert result.next == "decktalk build"


@pytest.mark.usefixtures("recordings_stand")
def test_a_held_take_the_index_does_not_play_names_build(tmp_path: Path) -> None:
    """The take is voiced, and a run that buys nothing would still rewrite the index to play it."""
    inputs = a_project(tmp_path, toml=VOICED)
    a_take_pair(inputs, played_digest(inputs))
    [section] = inputs.spoken()
    stand_in = placeholder_inputs(inputs, section).digest
    finished(inputs, a_take(1, digest=stand_in, voiced=False, spoken="Hello there again."))
    result = status(inputs, a_run(tmp_path))
    assert result.sections[0].take_state is TakeState.VOICED
    assert result.sections[0].take_reason == UNINDEXED
    assert result.next == "decktalk build"


@pytest.mark.usefixtures("recordings_stand")
def test_with_no_voice_named_a_held_take_is_unchecked_and_names_no_build(tmp_path: Path) -> None:
    inputs = a_project(tmp_path)
    played = a_take(1, digest="00000000000000ef", spoken="Hello there again.")
    a_take_pair(inputs, played.digest)
    finished(inputs, played)
    result = status(inputs, a_run(tmp_path))
    assert result.sections[0].take_state is TakeState.UNCHECKED
    assert result.sections[0].take_reason == WITHOUT_A_VOICE
    assert result.next is None


@pytest.mark.usefixtures("recordings_stand")
def test_a_renamed_takes_directory_is_one_warning_and_names_nothing_next(tmp_path: Path) -> None:
    """A folder that moved is not a script edit, so the report names the setting rather than a build that buys."""
    inputs = a_project(tmp_path, toml=VOICED)
    digest = played_digest(inputs)
    a_take_pair(inputs, digest)
    finished(inputs, a_take(1, digest=digest, spoken="Hello there again."))
    inputs.workspace.takes.rename(tmp_path / "voice")
    run = a_run(tmp_path)
    said: list[RunLog] = []
    run.machine.events.subscribe(lambda event: said.append(event) if isinstance(event, RunLog) else None)
    result = status(inputs, run)
    assert result.sections[0].take_state is TakeState.MISSING
    assert result.next is None
    warned = [line.message for line in said if line.level is Level.WARNING and "takes_dir" in line.message]
    assert warned == [
        "The takes directory takes holds none of the 1 take this project played before, which is what a renamed "
        "or missing folder looks like, so no build is named next. Point [narration] takes_dir at the folder that "
        "holds them."
    ]


def test_a_take_damaged_everywhere_is_an_error_line_and_only_its_row_has_no_take_state(tmp_path: Path) -> None:
    inputs = a_project(tmp_path, toml=TWO, script=TWO_SCRIPT)
    damaged = played_digest_of(inputs, 1)
    a_take_pair(inputs, damaged)
    damage_take(inputs, damaged)
    write_takes(inputs, a_take(1, digest=damaged, spoken="Hello there again."))
    run = a_run(tmp_path)
    said: list[RunLog] = []
    run.machine.events.subscribe(lambda event: said.append(event) if isinstance(event, RunLog) else None)
    result = status(inputs, run)
    assert [row.take_state for row in result.sections] == [None, None, TakeState.MISSING]
    assert [line for line in said if line.level is Level.ERROR and "section 1 plays" in line.message]


def test_a_script_that_will_not_parse_leaves_every_take_state_null(tmp_path: Path) -> None:
    inputs = a_project(tmp_path, script="# Demo\n\nNo section heading anywhere.\n")
    result = status(inputs, a_run(tmp_path))
    assert [row.take_state for row in result.sections] == [None, None]
    assert [row.take_reason for row in result.sections] == [None, None]


@pytest.mark.usefixtures("recordings_stand")
def test_a_free_voice_s_placeholders_name_build(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A named free voice makes its takes for nothing, so a project playing placeholders is not finished."""
    monkeypatch.setitem(DECLARED, "elevenlabs", replace(DECLARED["elevenlabs"], billing=FREE))
    inputs = a_project(tmp_path, toml=VOICED)
    [section] = inputs.spoken()
    stand_in = placeholder_inputs(inputs, section).digest
    inputs.workspace.narrate_dir.mkdir(parents=True, exist_ok=True)
    (inputs.workspace.narrate_dir / take_file(stand_in, TAKE_SUFFIX)).write_bytes(b"click")
    (inputs.workspace.narrate_dir / words_file(stand_in)).write_text("{}", encoding="utf-8")
    finished(inputs, a_take(1, digest=stand_in, voiced=False, spoken="Hello there again."))
    result = status(inputs, a_run(tmp_path))
    assert result.sections[0].take_state is TakeState.PLACEHOLDER
    assert result.next == "decktalk build"


def test_a_clip_row_has_no_take_state(tmp_path: Path) -> None:
    result = status(a_project(tmp_path), a_run(tmp_path))
    assert result.sections[1].kind is SectionKind.CLIP
    assert (result.sections[1].take_state, result.sections[1].take_reason) == (None, None)
