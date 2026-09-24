"""Every result a command returns serialises each row it judges, so a reader can dispatch on it.

`findings.items[]` has one shape, and the CLI lifts those rows out of the result's own payload
rather than being handed them. So a row a result holds and does not write under a key of its own is
a finding no reader sees, and a row with an empty `where` or `detail` is one no reader can act on.
Both are invisible at the result that makes them and obvious here, which is why the rule is a test
over every result rather than a rule a reader applies at one site at a time.

Each result the package returns is driven through its real stage, arranged to judge something, and
read back four ways: every judged row the result holds reaches the payload, each names the file it
is about, each carries its sentence, and the payload is exactly the shape `cli/schema.py` declares
for its command, read back into enum members by the same reader a caller uses. The toolchain is
replaced where a stage would shell out, because what is under test is the wiring from a judgement to
a payload and not the pixels. A result class added later fails `test_every_stage_result_is_driven_here`
until it is driven here as well.
"""

from __future__ import annotations

import importlib
import inspect
import json
import pkgutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

import decktalk
from decktalk.artifacts import Luma, RecordingChecks, RecordingLog, Take, Takes, Word, write_words
from decktalk.cli.envelope import finding_rows
from decktalk.cli.schema import PAYLOADS
from decktalk.jsonio import dumps, read_as
from decktalk.model import Project
from decktalk.pipeline import Stage, Substitute
from decktalk.verdicts import Finding, Findings, StageResult, Verdict

# ---- every result the package returns ---------------------------------------------------


def _modules() -> list[Any]:
    """Every module of the package, imported, so a result class cannot hide in one nothing imports."""
    found = [decktalk]
    for info in pkgutil.walk_packages(decktalk.__path__, f"{decktalk.__name__}."):
        # `__main__` runs the CLI when it is imported, which is the whole of what it is for.
        if not info.name.endswith(".__main__"):
            found.append(importlib.import_module(info.name))
    return found


def _is_stage_result(obj: Any, module_name: str) -> bool:
    """Whether a class is one of the results a command returns, by the protocol and not by its name.

    `StageResult` is a tally and JSON-ready data keyed on the project root, which is what separates a
    result from the row shapes inside it: a row serialises itself against the film or against nothing.
    """
    if not inspect.isclass(obj) or obj.__module__ != module_name or obj is StageResult:
        return False
    if not (hasattr(obj, "findings") and callable(getattr(obj, "to_dict", None))):
        return False
    return "root" in inspect.signature(obj.to_dict).parameters


def stage_results() -> dict[str, type]:
    """Every `StageResult` class in the package, by name."""
    found: dict[str, type] = {}
    for module in _modules():
        for name, obj in vars(module).items():
            if _is_stage_result(obj, module.__name__):
                found[name] = obj
    return found


# ---- the projects each stage is driven on -----------------------------------------------

PAGE_TOML = """
[project]
name = "t"

[[section]]
number = 1
page = "deck/index.html"

[[section]]
number = 2
page = "deck/index.html"
scene = "2"
"""


def _page_project(
    tmp_path: Path, html: str = "<!doctype html>", toml: str = PAGE_TOML, voiced: bool = False
) -> Project:
    """Two page sections on one page, with a take and a words file for each, voiced or not."""
    (tmp_path / "decktalk.toml").write_text(toml, encoding="utf-8")
    (tmp_path / "script.md").write_text("## 1. A\n\nHello there.\n\n## 2. B\n\nBye now.\n", encoding="utf-8")
    (tmp_path / "deck").mkdir(exist_ok=True)
    (tmp_path / "deck" / "index.html").write_text(html, encoding="utf-8")
    project = Project.load(tmp_path, environ={})
    project.takes_dir.mkdir(parents=True, exist_ok=True)
    index = Takes(script="script.md", model="m", output_format="mp3")
    for key, words in (("01", [Word("Hello", 0.5, 0.9), Word("there", 1.0, 1.4)]), ("02", [Word("Bye", 0.4, 0.8)])):
        write_words(project.takes_dir / f"{key}.words.json", words)
        (project.takes_dir / f"{key}.mp3").write_bytes(b"a take")
        index.sections[key] = Take(
            index=int(key), chapter=key, file=f"{key}.mp3", words_file=f"{key}.words.json", hash=f"h{key}",
            word_count=len(words), estimated_seconds=2.0, duration_seconds=3.0, speech_end_seconds=2.5,
            voiced=voiced,
        )  # fmt: skip
    index.total_seconds = 6.0
    index.save(project.takes_path)
    return project


def _cues(project: Project, sections: dict[str, Any]) -> None:
    (project.root / "cues.json").write_text(json.dumps({"sections": sections}), encoding="utf-8")


def _assembled(project: Project) -> None:
    """The files `verify` measures: a section video each, and the final film."""
    project.out_dir.mkdir(parents=True, exist_ok=True)
    project.sections_dir.mkdir(parents=True, exist_ok=True)
    for key in ("01", "02"):
        (project.sections_dir / f"{key}.mp4").write_bytes(b"x")
    project.final.write_bytes(b"x")


# ---- one driver per result --------------------------------------------------------------


def drive_align(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> StageResult:
    """A phrase that is not in the narration, an element no cue fires, and a section too short."""
    from decktalk.stages.align import align

    html = '<b data-cue="1.1a"></b><b data-cue="1.1b"></b><i data-cue="1.2forgotten"></i>'
    project = _page_project(tmp_path, html)
    _cues(
        project,
        {
            "1": {
                "min_seconds": 9,
                "cues": [{"cue": "1.1a", "on": "hello"}, {"cue": "1.1b", "on": "a phrase nobody says"}],
            }
        },
    )
    return align(project)


def drive_narrate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> StageResult:
    """A number written in digits, which is the wording finding a silent run still makes."""
    from decktalk.media import audio, ffmpeg
    from decktalk.stages.narrate import narrate

    project = _page_project(tmp_path)
    (project.root / "script.md").write_text("## 1. A\n\n41 bowls.\n\n## 2. B\n\nBye now.\n", encoding="utf-8")
    monkeypatch.setattr(audio, "write_clicks", lambda path, *a, **kw: path.write_bytes(b"x"))
    monkeypatch.setattr(audio, "concat_audio", lambda parts, out, *a, **kw: out.write_bytes(b"x"))
    monkeypatch.setattr(ffmpeg, "probe_duration", lambda path: 3.0)
    monkeypatch.setattr(ffmpeg, "decoded_duration", lambda path: 3.0)
    return narrate(project, silent=True)


def drive_preflight(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> StageResult:
    """The rehearsal with no frames: a cue phrase nobody says, and a placeholder nobody filled."""
    from decktalk.stages.preflight import preflight

    project = _page_project(tmp_path, '<b data-cue="1.1a"></b>')
    _cues(project, {"1": {"cues": [{"cue": "1.1a", "on": "a phrase nobody says"}]}})
    (project.root / "script.md").write_text(
        "## 1. A\n\nHello there.\n\n## 2. B\n\nBye now, [CLIENT_NAME].\n", encoding="utf-8"
    )
    return preflight(project, frames=False)


def drive_record(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> StageResult:
    """One section whose recording was judged, and one `--only` passed over that no longer stands."""
    from decktalk.stages.record import start as start_module

    record_stage = importlib.import_module("decktalk.stages.record")
    project = _page_project(tmp_path)

    def a_log() -> RecordingLog:
        return RecordingLog(
            url="u", requested_seconds=3.5, settle_seconds=0.6, load_seconds=0.1, clock_start_seconds=1.5
        )

    def capture_section(p: Project, browser: Any, job: Any) -> RecordingLog:
        log = a_log()
        log.assets = [job.section.page]
        log.input_hash = job.input_hash
        job.out.parent.mkdir(parents=True, exist_ok=True)
        job.out.write_bytes(b"a recording")
        return log

    class _NoBrowser:
        def __enter__(self) -> object:
            return object()

        def __exit__(self, *exc: object) -> bool:
            return False

    monkeypatch.setattr(record_stage, "chromium", lambda path: _NoBrowser())
    monkeypatch.setattr(record_stage, "capture_section", capture_section)
    monkeypatch.setattr(record_stage, "find_start", lambda webm, settle, cfg: start_module.Start(0.44, "cover"))
    monkeypatch.setattr(
        record_stage,
        "check_recording",
        lambda webm, log, cfg: RecordingChecks(3.5, 3.5, Luma(90.0, 90.0, 90.0, 200.0), (Verdict.KATEX_NOT_LOADED,)),
    )
    record_stage.record(project)
    # The page moves under section 1, and only section 2 is recorded again, so section 1 is left
    # behind out of date: a certain finding whose row the run reports rather than the author noticing.
    (project.root / "deck" / "index.html").write_text("<!doctype html><p>new</p>", encoding="utf-8")
    return record_stage.record(project, only=[2])


def drive_assemble(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> StageResult:
    """A cued sound with no caption, a section playing black for its missing recording, and a loud film."""
    from decktalk.media.audio import Loudness
    from decktalk.stages.assemble.cut import RenderedSection

    asm = importlib.import_module("decktalk.stages.assemble")
    cut_module = importlib.import_module("decktalk.stages.assemble.cut")
    publish_module = importlib.import_module("decktalk.stages.assemble.publish")
    loudness_module = importlib.import_module("decktalk.stages.assemble.loudness")

    sfx = "\n[[mix.sfx]]\nfile = 'media/hum.mp3'\nsection = 1\ncue = '1.1a'\n"
    # Voiced takes, because a build without voice skips the loudness pass and would miss nothing.
    project = _page_project(tmp_path, toml=PAGE_TOML + sfx, voiced=True)
    _assembled(project)
    rows = [
        RenderedSection(project.sections[0], project.sections_dir / "01.mp4", 3.0, "01.webm"),
        RenderedSection(
            project.sections[1],
            project.sections_dir / "02.mp4",
            3.0,
            Substitute.BLACK.value,
            "build/recordings/02.webm",
            Substitute.BLACK,
        ),
    ]
    # The film is measured louder than the ceiling allows, so the pass reports a miss it cannot fix.
    measured = Loudness(i=-11.0, tp=0.5, lra=5, thresh=-27, offset=0)
    monkeypatch.setattr(asm, "render_sections", lambda project, takes, strict: rows)
    monkeypatch.setattr(asm, "render_poster", lambda project, out: None)
    monkeypatch.setattr(publish_module, "render_poster", lambda project, out: None)
    monkeypatch.setattr(asm, "concat", lambda files, out: out.write_bytes(b"x"))
    monkeypatch.setattr(publish_module, "mux_chapters", lambda src, chapters, dst, language: dst.write_bytes(b"x"))
    monkeypatch.setattr(loudness_module.audio, "measure_loudness", lambda path, **kw: measured)
    for module in (asm, cut_module, publish_module, loudness_module):
        monkeypatch.setattr(module.ffmpeg, "run", lambda *args: Path(args[-1]).write_bytes(b"x"))
        monkeypatch.setattr(module.ffmpeg, "probe_duration", lambda path: 3.0)
    return asm.assemble(project, soundscape=False)


def drive_verify(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> StageResult:
    """A cue whose picture never changed, a dark section start, and a recording log's own verdict."""
    from decktalk.media import ffmpeg
    from decktalk.media import frames as frames_module
    from decktalk.stages.verify import verify

    project = _page_project(tmp_path)
    _assembled(project)
    rows = [{"cue": "1.1a", "on": "hello", "at": 1.0, "word_at": 1.0}]
    project.cue_times_path.write_text(json.dumps({"sections": {"01": rows}}), encoding="utf-8")
    checks = RecordingChecks(3.0, 3.0, Luma(90.0, 90.0, 90.0, 200.0), (Verdict.KATEX_NOT_LOADED,))
    RecordingLog(
        url="u", requested_seconds=3.0, settle_seconds=0.5, load_seconds=0.1, clock_start_seconds=1.5,
        t0_seconds=1.44, t0_method="cover", checks=checks,
    ).save(project.recording_log(project.page_sections[0]))  # fmt: skip
    monkeypatch.setattr(ffmpeg, "probe_duration", lambda path: 3.0)
    # A dark first frame is BLACK, and a cue whose picture does not move is NO CHANGE.
    monkeypatch.setattr(frames_module, "luma_at", lambda path, t, crop=None: (1.0, 2.0))
    monkeypatch.setattr(frames_module, "changed_pixels_percent", lambda path, t1, t2, **kw: 0.0)
    monkeypatch.setattr(frames_module, "changed_series", lambda *a, **kw: [])
    return verify(project)


def drive_status(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> StageResult:
    """A project whose page is not on disk, which is the input check `status` alone makes."""
    from decktalk.stages.status import status

    project = _page_project(tmp_path)
    (project.root / "deck" / "index.html").unlink()
    return status(project)


def drive_clip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> StageResult:
    """A span that ends in the middle of a word, which the words file leaves out."""
    clip_module = importlib.import_module("decktalk.stages.clip")

    project = _page_project(tmp_path)
    _assembled(project)
    monkeypatch.setattr(clip_module.ffmpeg, "probe_duration", lambda path: 3.0)
    monkeypatch.setattr(clip_module.ffmpeg, "run", lambda *args: Path(args[-1]).write_bytes(b"x"))
    # Section 01's 0.5 s lead puts "Hello" at 1.0 to 1.4 s, so a span ending at 1.2 s cuts it in two.
    return clip_module.clip(project, 1, start=0.0, end=1.2, out="media/cut.mp4")


def drive_words(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> StageResult:
    """`words` reads the narration clock and judges nothing, so its tally and its rows are both empty."""
    from decktalk.stages.clip import words

    return words(_page_project(tmp_path))


def drive_screenshots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> StageResult:
    """`screenshots` writes pictures for a person to look at and judges nothing."""
    screenshots_module = importlib.import_module("decktalk.stages.screenshots")

    project = _page_project(tmp_path)
    written = project.screenshots_dir / "01.png"

    def taken(*args: Any, **kwargs: Any) -> list[Any]:
        written.parent.mkdir(parents=True, exist_ok=True)
        written.write_bytes(b"png")
        return [screenshots_module.Screenshot(path=written, page="deck/index.html", section=1, at=0.5)]

    monkeypatch.setattr(screenshots_module, "screenshot_slides", taken)
    monkeypatch.setattr(screenshots_module, "screenshot_frames", taken)
    return screenshots_module.screenshots(project, section=1)


def drive_soundscape(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> StageResult:
    """A request that fails raises, so a run that returns has nothing to judge."""
    from decktalk.stages.soundscape import soundscape

    toml = PAGE_TOML + "\n[soundscape.music]\nprompt = 'a hum'\nseconds = 4\nout = 'media/music.mp3'\n"
    return soundscape(_page_project(tmp_path, toml=toml), dry_run=True)


def drive_build(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> StageResult:
    """One stage of a run, so the whole-run result carries the stage's rows and its tally."""
    from decktalk.stages.build import build

    project = _page_project(tmp_path, '<b data-cue="1.1a"></b><i data-cue="1.2forgotten"></i>')
    _cues(project, {"1": {"cues": [{"cue": "1.1a", "on": "hello"}]}})
    return build(project, from_stage=Stage.ALIGN, to_stage=Stage.ALIGN)


DRIVERS: dict[str, Callable[[Path, pytest.MonkeyPatch], StageResult]] = {
    "AlignResult": drive_align,
    "AssembleResult": drive_assemble,
    "BuildResult": drive_build,
    "ClipResult": drive_clip,
    "NarrateResult": drive_narrate,
    "PreflightResult": drive_preflight,
    "RecordResult": drive_record,
    "ScreenshotsResult": drive_screenshots,
    "SoundscapeResult": drive_soundscape,
    "StatusResult": drive_status,
    "VerifyResult": drive_verify,
    "WordsResult": drive_words,
}

JUDGES_NOTHING = {"ScreenshotsResult", "SoundscapeResult", "WordsResult"}
"""The three results whose `findings` is empty by construction, which this test holds them to."""


# ---- what a result holds, however deep in its own rows ----------------------------------


def _fields_and_properties(obj: Any) -> list[Any]:
    """Every value a result or a row of one exposes, its stored fields and its derived ones alike.

    A derived collection counts, because `PreflightResult.error_rows` and `ClipResult.rows` are
    properties and are exactly the kind of row list that is wired to the payload by hand and forgotten.
    """
    values = list(vars(obj).values())
    for name, _prop in inspect.getmembers(type(obj), lambda a: isinstance(a, property)):
        if not name.startswith("_"):
            values.append(getattr(obj, name))
    return values


def _own(value: Any) -> bool:
    return type(value).__module__.startswith(f"{decktalk.__name__}.")


def judged(obj: Any, seen: set[int] | None = None) -> list[Verdict]:
    """Every non-passing verdict this result holds, wherever in its own rows it sits.

    A row is anything of the package's own that carries a `verdict` or a `verdicts`, so the walk
    finds the rows a result keeps in a list, in a property, and inside another result it holds.
    """
    seen = set() if seen is None else seen
    # A verdict is a singleton of its enum, so it is read every time it is met and never memoised:
    # two rows that judge the same way are two findings.
    if isinstance(obj, Verdict):
        return [obj] if not obj.passing else []
    if id(obj) in seen:
        return []
    seen.add(id(obj))
    if isinstance(obj, (str, bytes, Path)) or not _own(obj) and not isinstance(obj, (list, tuple, dict)):
        return []
    if isinstance(obj, dict):
        return [verdict for value in obj.values() for verdict in judged(value, seen)]
    if isinstance(obj, (list, tuple)):
        return [verdict for item in obj for verdict in judged(item, seen)]
    return [verdict for value in _fields_and_properties(obj) for verdict in judged(value, seen)]


# ---- the mechanism ----------------------------------------------------------------------


def test_every_stage_result_is_driven_here():
    """A result class the package grows is driven through its stage here, or this fails.

    This is the half of the mechanism a reader cannot supply: without it the rules below would hold
    over whichever results somebody remembered to list.
    """
    assert sorted(stage_results()) == sorted(DRIVERS)


@pytest.mark.parametrize("name", sorted(DRIVERS))
def test_a_result_serialises_every_row_it_judges(name, tmp_path, monkeypatch):
    """Every judged row a result holds reaches its payload, and every one is filled in.

    `finding_rows` is the CLI's own lift, so this reads the payload exactly as `findings.items[]`
    does. A row list wired to the payload by hand and forgotten, a row with no file and a row with
    no sentence each fail here, at the result that makes them, rather than at whichever one of a
    dozen reading sites somebody happens to look at.
    """
    result = DRIVERS[name](tmp_path, monkeypatch)
    assert isinstance(result, stage_results()[name])
    held = sorted(judged(result), key=lambda verdict: verdict.name)
    rows = [Finding.from_dict(row) for row in finding_rows(result.to_dict(tmp_path))]
    lifted = sorted((row.verdict for row in rows), key=lambda verdict: verdict.name)

    if name in JUDGES_NOTHING:
        assert result.findings == Findings() and held == [] and rows == [], f"{name} judges nothing and reports nothing"
        return

    assert held, f"{name} is driven here without judging anything, so this test proves nothing"
    assert result.findings != Findings(), f"{name} holds judged rows its tally does not count"
    assert lifted == held, (
        f"{name} holds the judged rows {held} and its payload serialises {lifted}. A row a result "
        f"tallies but does not write under a key of its own is a finding no reader of findings.items[] "
        f"can dispatch on."
    )
    # The tally is the rows, so `findings.certain` and `findings.uncertain` count what `items` lists.
    assert Findings.of(lifted) == result.findings, f"{name} tallies {result.findings} over rows {lifted}"
    for row in rows:
        assert row.where, f"{name} row {row.verdict!r} names no file, page or artifact: {row}"
        assert row.detail, f"{name} row {row.verdict!r} carries no sentence: {row}"


@pytest.mark.parametrize("name", sorted(DRIVERS))
def test_a_result_writes_exactly_the_payload_its_command_declares(name, tmp_path, monkeypatch):
    """The payload is JSON as it is, and it reads back as its command's type with every enum a member.

    `json.dumps` refuses an enum member or a path, so a result that left one in its data fails here
    rather than as an internal error on a caller's machine. The strict reader refuses a key the type
    does not declare and a declared key the result forgot, so the schema a caller reads by and the
    data a result writes cannot drift apart, and a verdict that is not its code's own object fails.
    """
    result = DRIVERS[name](tmp_path, monkeypatch)
    command = name.removesuffix("Result").lower()
    payload = read_as(PAYLOADS[command], json.loads(dumps(result.to_dict(tmp_path))), f"$.{command}")
    assert type(payload).__name__.startswith(command.title()), f"{name} read back as {type(payload).__name__}"
