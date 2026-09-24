"""The whole pipeline on the fixture project in tests/e2e/fixture, built once and read by every test here.

    uv run pytest -m e2e

The build is unvoiced, so it needs no API key and spends nothing, and it runs with the network
blocked. The fixture is copied under tests/out/e2e, which CI uploads when a test fails. That
directory is one per machine, so a session takes a lock on it and skips rather than deleting another
session's build, and DECKTALK_E2E_OUT names another directory for a second session.

This suite is the panel's integration sample rather than a policy test. It proves no proposition on
its own and samples the joint behaviour of Chromium, ffmpeg and the filesystem on one machine, so
every assertion here is an exit code, an artifact, a JSON shape or a path, and never a pixel and
never a millisecond of wall time. Every test is one property of the finished build, so a failure
names what broke.

The command line is driven as a real subprocess of `python -m decktalk`, so nothing about the CLI's
internal module layout is assumed and nothing is faked. The commands and flags are spelled from
`~/Documents/decktalk-plan/gen5/synthesis/design.md` section 3, the final vocabulary, with the flag
families of `~/Documents/decktalk-plan/gen5/panels/cli/design.md` section 2 and the resolutions R7,
R10, R24 and R26 applied. T8 had not landed when this was written, so a failure that names a missing
command or an unknown flag is T8's spelling and not a broken property.
"""

from __future__ import annotations

import importlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Any

import pytest

from decktalk.artifacts import CueTimes, Cuts, RecordingLog, Takes, Words
from decktalk.events import Event, RunDone, RunStart, SectionDone, SectionStart, StageDone, StageStart
from decktalk.findings import Certainty
from decktalk.media import audio, ffmpeg, frames
from decktalk.pipeline import Artifact, Outcome, Stage
from decktalk.results import SectionKind, SpendState, Substitute, Voicing, Word
from decktalk.toolchain.assets import RUNTIME_FILE, katex_missing, runtime_path, vendor_katex

# An advisory lock on the output directory, where the platform has one.
fcntl = importlib.util.find_spec("fcntl") and importlib.import_module("fcntl")

BUILD_BUDGET_SECONDS = 600
"""How long one test may take, which is generous enough for a cold Chromium fetch on a slow runner.

The fixture builds in about a minute on a warm machine. The budget is a ceiling that catches a hung
page or a stalled encoder, so it is not a measurement of anything and it is deliberately loose.
"""

pytestmark = [pytest.mark.e2e, pytest.mark.timeout(BUILD_BUDGET_SECONDS)]

FIXTURE = Path(__file__).parent / "fixture"

HOSTILE_DIRECTORY = "jacob's fïlms 2"
"""The name every temporary root of this suite sits under, because a path is an input like any other.

An apostrophe and a diacritic reach every shell quote, every ffmpeg concat list and every served URL
the build writes, and the founder's own films live under a name like this one. Building anywhere
else would leave the quoting of must 1 proven by nothing that runs on every platform.
"""

OUT = Path(os.environ.get("DECKTALK_E2E_OUT") or Path(__file__).parent.parent / "out") / "e2e" / HOSTILE_DIRECTORY

FILM_NAME = "pipeline"
"""The project name in the fixture's decktalk.toml, which names every file `assemble` writes."""

SPOKEN = ("01", "02", "04")
"""The page sections of the fixture, because section 3 plays a clip and section 5 plays a slate."""

EVERY_SECTION = ("01", "02", "03", "04", "05")

CUES = (
    "1:1.1:first",
    "1:1.1:second",
    "1:1.1:third",
    "2:2.1:fourth",
    "2:2.1:fifth",
    "4:3.1:eq",
    "4:3.1:bar",
)
"""Every cue the fixture declares, as the section number, then the wire id of slide and local name."""

RESERVED_KEYS = frozenset({"schema", "ok", "findings", "error"})
"""The four keys every result carries, which is the founder's decided JSON contract."""

RETIRED_KEYS = frozenset({"command", "exit_code", "summary", "payload", "data"})
"""The envelope keys 0.4 wrapped a result in, none of which may come back under any name."""

FOUND_NOTHING, FOUND_SOMETHING = 0, 1
"""What the CLI exits when it judged nothing and when it judged something, from the CLI design."""

SLATE_SECONDS = 1.0
"""How long section 5's slate plays, which `decktalk.toml` states as `slate_seconds = 1`."""

CLIP_SECONDS = 2.5
"""How long the generated B-roll clip runs, which the ffmpeg command below asks lavfi for."""

CLIP_FPS = 30
"""The frame rate the B-roll clip is generated at, which differs from the film's on purpose."""

CLIP_TONE_HZ = 660
CLIP_TONE_FLOOR_DBFS = -30
"""How loud the clip's own tone must still be in the finished film, well over the mix's beds."""

FRAME_COMPARE_LEVEL = 40
FRAME_COMPARE_WIDTH = 480
FRAME_COMPARE_HEIGHT = 270
"""The luma step and the thumbnail size two frames are compared at, which verify's own probe uses."""

POSTER_WIDTH, POSTER_HEIGHT = 1920, 1080
"""The poster's size, which is the deck's own page size and the size every recording is made at."""

MINIMUM_CAPTION_SECONDS = 1.0
"""How long a caption is on screen at least, because a line nobody can read is not a caption."""

FILM_SECONDS_RANGE = (18.0, 26.0)
"""How long the finished fixture runs, which is a sanity range around its twenty or so seconds."""

SECOND_TOLERANCE = 0.05
"""How far two measurements of one length may differ, which is one frame and a little rounding."""


def qualified(row: dict[str, Any]) -> str:
    """One cue as a reader names it, which is its section number and then its wire id."""
    return f"{row['section']}:{row['cue']}"


def moment(line: type[Event]) -> str:
    """The word one event line carries, read from the model that declares it and never spelled here."""
    return str(line.model_fields["event"].default)


# ---- driving the command line --------------------------------------------------------------------


@dataclass
class Run:
    """One `decktalk` command that has finished, with its exit code and its two streams."""

    args: tuple[str, ...]
    code: int
    stdout: str
    stderr: str

    @property
    def json(self) -> dict[str, Any]:
        """The one flat object the command printed, checked against the reserved key contract."""
        doc = json.loads(self.stdout)
        assert isinstance(doc, dict), f"{self.args}: --json prints one object and nothing else"
        assert RESERVED_KEYS <= set(doc), f"{self.args}: missing {sorted(RESERVED_KEYS - set(doc))}"
        assert not RETIRED_KEYS & set(doc), f"{self.args}: carries the retired key {sorted(RETIRED_KEYS & set(doc))}"
        assert doc["schema"] == 2, doc["schema"]
        return doc

    @property
    def findings(self) -> list[dict[str, Any]]:
        return list(self.json["findings"])

    def certain(self) -> list[dict[str, Any]]:
        """Every finding the run is sure about, which is what decides the exit code by default."""
        return [row for row in self.findings if row["certainty"] == Certainty.CERTAIN.value]


BLOCK_THE_NETWORK = '''
"""Refuse every socket connection but loopback, and write down what was attempted.

Python imports this module at interpreter start when its directory is on PYTHONPATH, so it reaches
the `decktalk` subprocess before the CLI does. A test that blocked sockets in its own process would
prove nothing about a command an agent runs, which is the only way this build is ever driven.
"""

import os
import socket

LOOPBACK = ("127.0.0.1", "::1", "localhost")
_real = socket.socket.connect


def connect(self, address):
    host = address[0] if isinstance(address, tuple) else ""
    if not isinstance(address, tuple) or host in LOOPBACK:
        return _real(self, address)
    with open(os.environ["DECKTALK_E2E_ATTEMPTS"], "a", encoding="utf-8") as out:
        out.write(repr(address) + "\\n")
    raise OSError("the pipeline test blocks the network: " + repr(address))


socket.socket.connect = connect
'''


@dataclass
class Project:
    """A project directory the tests drive `decktalk` against, and what the network saw it do."""

    root: Path
    shim: Path
    attempts: Path
    built: Run | None = None
    verified: Run | None = None

    def cli(self, *args: str) -> Run:
        """One `decktalk` command, run as a real subprocess against this project with no network.

        The CLI is reached through `python -m decktalk` rather than through a console script, so the
        command under test is the one the wheel installs and no entry point has to be on PATH.
        """
        env = dict(os.environ)
        env["DECKTALK_E2E_ATTEMPTS"] = str(self.attempts)
        # A key or a settings file belonging to whoever runs the suite must not reach the build.
        for name in ("DECKTALK_PROJECT", "ELEVENLABS_API_KEY", "ELEVENLABS_VOICE_ID"):
            env.pop(name, None)
        env["DECKTALK_CONFIG"] = str(self.shim / "no-machine-config.toml")
        existing = env.get("PYTHONPATH")
        env["PYTHONPATH"] = os.pathsep.join([str(self.shim), *([existing] if existing else [])])
        done = subprocess.run(
            [sys.executable, "-m", "decktalk", "--project", str(self.root), *args],
            capture_output=True,
            text=True,
            check=False,
            env=env,
            cwd=self.shim,
        )
        return Run((*args,), done.returncode, done.stdout, done.stderr)

    @property
    def build_dir(self) -> Path:
        return self.root / "build"

    @property
    def final_dir(self) -> Path:
        return self.build_dir / "final"

    @property
    def film(self) -> Path:
        return self.final_dir / f"{FILM_NAME}.mp4"

    def deliverable(self, suffix: str) -> Path:
        return self.final_dir / f"{FILM_NAME}{suffix}"

    def recording_log(self, key: str) -> RecordingLog:
        log = RecordingLog.read(self.build_dir / "recordings" / f"{key}.json")
        assert log is not None, f"section {key} has no recording log"
        return log

    def network_attempts(self) -> list[str]:
        if not self.attempts.exists():
            return []
        return [line for line in self.attempts.read_text(encoding="utf-8").splitlines() if line]

    def probe(self, *args: str, path: Path | None = None) -> dict[str, Any]:
        """One ffprobe reading of the finished film, or of another file this build wrote."""
        command = [ffmpeg.ffprobe(), "-v", "error", "-of", "json", *args, str(path or self.film)]
        return json.loads(subprocess.run(command, capture_output=True, text=True, check=True).stdout)

    def spans(self) -> dict[str, tuple[float, float]]:
        """Where every section starts and ends in the finished film, read from the cut list."""
        cuts = Cuts.read(self.final_dir / "cuts.json")
        assert cuts is not None, "the build wrote no cut list"
        return {row.key: (row.start, row.end) for row in cuts.sections}


# ---- the fixture, built once -----------------------------------------------------------------------


NEEDED_TOOLS = ("chromium", "ffmpeg")
"""What a build of this fixture reaches for, which `decktalk doctor` is the one command that reports."""


def missing_tools(shim: Path) -> list[str]:
    """Every tool this machine does not hold, asked of DeckTalk through its own doctor command.

    Asking the CLI rather than importing Playwright keeps this file on the surface an author uses,
    and it means a machine with no browser skips rather than failing halfway through a recording.
    """
    done = subprocess.run(
        [sys.executable, "-m", "decktalk", "doctor", "--json"], capture_output=True, text=True, check=False, cwd=shim
    )
    doc = json.loads(done.stdout)
    held = {row["tool"]: row for row in doc["tools"]}
    return [name for name in NEEDED_TOOLS if not (held.get(name) or {}).get("path")]


def generate_media(root: Path) -> None:
    """The clip and the three beds, from ffmpeg's own sources, because no media file is tracked."""
    media = root / "media"
    ffmpeg.run(
        "-f", "lavfi", "-i", f"testsrc=s=1280x720:r={CLIP_FPS}",
        "-f", "lavfi", "-i", f"sine=f={CLIP_TONE_HZ}:r=48000",
        "-t", str(CLIP_SECONDS), "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
        str(media / "broll.mp4"),
    )  # fmt: skip
    ffmpeg.run("-f", "lavfi", "-i", "sine=f=220:r=44100", "-t", "8", "-af", "volume=0.5", str(media / "music.mp3"))
    ffmpeg.run("-f", "lavfi", "-i", "anoisesrc=c=pink:r=44100:a=0.2", "-t", "6", str(media / "ambience.mp3"))
    ffmpeg.run("-f", "lavfi", "-i", "sine=f=1000:r=44100", "-t", "0.1", str(media / "tick.mp3"))


def hold(path: Path) -> IO[str] | None:
    """The lock file held for this session, or None when another session already holds it.

    Two sessions must never share one output directory, because the build deletes it and writes it
    again. Where no advisory lock exists, one session at a time is the rule instead.
    """
    handle = path.open("w", encoding="utf-8")
    if fcntl is None:
        return handle
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return None
    return handle


@pytest.fixture(scope="session")
def built() -> Iterator[Project]:
    """Copy the fixture, add the runtime and KaTeX, generate the media, and build it with no voice."""
    assert katex_missing() == [], "the packaged KaTeX copy is incomplete"
    OUT.mkdir(parents=True, exist_ok=True)
    if absent := missing_tools(OUT):
        pytest.skip(f"{', '.join(absent)} is missing: run `decktalk install` first")
    lock = hold(OUT / "pipeline.lock")
    if lock is None:
        pytest.skip(f"another session is building under {OUT}: set DECKTALK_E2E_OUT to build elsewhere")
    root = OUT / FILM_NAME
    shim = OUT / "shim"
    shutil.rmtree(root, ignore_errors=True)
    shutil.rmtree(shim, ignore_errors=True)
    shim.mkdir(parents=True)
    (shim / "sitecustomize.py").write_text(BLOCK_THE_NETWORK, encoding="utf-8")
    shutil.copytree(FIXTURE, root)
    shutil.copyfile(runtime_path(), root / "deck" / RUNTIME_FILE)
    vendor_katex(root / "deck")
    generate_media(root)

    project = Project(root=root, shim=shim, attempts=shim / "attempts.txt")
    try:
        project.built = project.cli("build", "--no-voice", "--json")
        # One reading over the sections that carry cues, kept on disk for the CI upload on a failure.
        project.verified = project.cli(
            "verify", "--json", "--fail-on", "never", "--section", "1", "--section", "2", "--section", "4"
        )
        (root / "verify.json").write_text(project.verified.stdout, encoding="utf-8")
        yield project
    finally:
        lock.close()


# ---- the build -----------------------------------------------------------------------------------


def test_the_build_finds_nothing_with_the_network_blocked(built: Project) -> None:
    """The build's last stage is the real verify, so exit 0 means every reveal landed on its word."""
    assert built.built is not None
    assert built.network_attempts() == [], built.network_attempts()
    assert built.built.certain() == [], built.built.stderr
    assert built.built.code == FOUND_NOTHING, built.built.stderr
    assert built.film.is_file() and built.film.stat().st_size > 0


def relative(built: Project, path: Path) -> str:
    """One path as the CLI prints it, which is always relative to the project root."""
    return path.relative_to(built.root).as_posix()


def test_the_build_reports_every_stage_and_the_film_it_wrote(built: Project) -> None:
    """`build`'s own fields are the six stages, the film and what the run wrote, in one flat object."""
    assert built.built is not None
    doc = built.built.json
    assert [row["stage"] for row in doc["stages"]] == [stage.value for stage in Stage]
    assert {row["outcome"] for row in doc["stages"]} <= {outcome.value for outcome in Outcome}
    assert doc["film"] == relative(built, built.film)
    assert doc["voice"] == Voicing.PLACEHOLDER.value, "--no-voice never asks a provider for a take"
    assert doc["spend"]["dollars"] == 0, doc["spend"]
    assert relative(built, built.film) in set(doc["written"])
    assert doc["run"], "a build opens a run, so its id is on the result an agent reads"


def test_the_build_writes_one_events_file_an_agent_can_read(built: Project) -> None:
    """Every line is a whole event, the six stages each open and close, and each section is named."""
    assert built.built is not None
    run_id = built.built.json["run"]
    path = built.build_dir / "events" / f"{run_id}.jsonl"
    lines = path.read_text(encoding="utf-8").splitlines()
    rows = [json.loads(line) for line in lines]
    assert len(rows) == len(lines), "every line is one whole event"
    assert all(row["run"] == run_id for row in rows), "a run's file holds that run's lines alone"
    assert [row["seq"] for row in rows] == sorted(row["seq"] for row in rows), "seq counts up within a run"
    assert rows[0]["event"] == moment(RunStart) and rows[-1]["event"] == moment(RunDone)
    started = [row["stage"] for row in rows if row["event"] == moment(StageStart)]
    done = [row["stage"] for row in rows if row["event"] == moment(StageDone)]
    assert started == [stage.value for stage in Stage], started
    assert done == started, "a file whose last stage only started is a run that died"
    recorded = [row for row in rows if row.get("stage") == Stage.RECORD.value and row.get("section")]
    opened = {row["section"] for row in recorded if row["event"] == moment(SectionStart)}
    closed = {row["section"] for row in recorded if row["event"] == moment(SectionDone)}
    assert opened == closed == {int(key) for key in SPOKEN}
    assert all(row["time"].endswith("Z") for row in rows), "every line carries a UTC instant"


def test_the_build_writes_every_artifact_the_pipeline_declares(built: Project) -> None:
    """A path an agent finds from `Artifact` and nowhere else, so no reader spells a build path."""
    for artifact in (Artifact.TAKES, Artifact.CUE_TIMES, Artifact.RECORDINGS, Artifact.FINAL):
        assert (built.root / artifact.value).exists(), artifact.value


def test_the_missing_optional_clip_plays_its_slate(built: Project) -> None:
    """Section 5 names a clip file that is never there, so a titled slate plays for slate_seconds."""
    start, end = built.spans()["05"]
    assert end - start == pytest.approx(SLATE_SECONDS, abs=SECOND_TOLERANCE)
    cuts = Cuts.read(built.final_dir / "cuts.json")
    assert cuts is not None
    [row] = [section for section in cuts.sections if section.key == "05"]
    assert row.substitute == Substitute.SLATE


def test_every_recording_is_measured_and_checked_by_the_run_that_made_it(built: Project) -> None:
    """`record` writes one log per page section carrying its own measurement and its own findings."""
    logs = {key: built.recording_log(key) for key in SPOKEN}
    assert {key: list(log.findings) for key, log in logs.items()} == {key: [] for key in SPOKEN}
    assert all(log.checks is not None for log in logs.values())
    assert all(log.t0_seconds is not None and not log.t0_guessed for log in logs.values())
    assert all(log.url.startswith("http://") for log in logs.values())
    # Every project file the page loaded is named, which is what the next run keys its skip on.
    assert "deck/index.html" in logs["01"].assets
    assert f"deck/{RUNTIME_FILE}" in logs["01"].assets


def test_no_page_loaded_anything_from_another_origin(built: Project) -> None:
    """The film may depend on no host it does not own, which is what the local origin is for."""
    for key in SPOKEN:
        assert list(built.recording_log(key).external) == [], key


def test_a_second_record_run_keeps_every_section(built: Project) -> None:
    """Nothing the pages are recorded from has moved, so the run opens no browser and keeps each webm."""
    before = {key: (built.build_dir / "recordings" / f"{key}.webm").stat().st_mtime_ns for key in SPOKEN}
    again = built.cli("record", "--json")
    doc = again.json
    assert again.code == FOUND_NOTHING, again.stderr
    rows = {row["key"]: row for row in doc["sections"]}
    assert set(rows) == set(SPOKEN)
    assert all(row["kept"] for row in rows.values()), rows
    after = {key: (built.build_dir / "recordings" / f"{key}.webm").stat().st_mtime_ns for key in SPOKEN}
    assert after == before


# ---- what verify measured -------------------------------------------------------------------------


def test_verify_measures_every_cue_every_start_every_cut_and_the_seam(built: Project) -> None:
    """Every cue the fixture declares is measured, and nothing it measured is a certain finding."""
    assert built.verified is not None
    doc = built.verified.json
    assert [qualified(row) for row in doc["cues"]] == list(CUES)
    assert {row["section"] for row in doc["starts"]} == {1, 2, 4}
    assert {row["section"] for row in doc["cuts"]} == {1, 2, 4}
    assert [row["section"] for row in doc["seams"]] == [2], "section 2 is the one seamless section"
    assert built.verified.certain() == [], built.verified.certain()
    assert doc["film_seconds"] > FILM_SECONDS_RANGE[0]


def test_every_cue_changed_the_picture_it_was_measured_against(built: Project) -> None:
    """The guard against Chromium ceasing to present frames on a page where nothing else is moving.

    A reveal is one instant change on an otherwise motionless page, and a compositor with no other
    work can stop swapping frames until something moves, which stamps the change late and moves every
    measured cue with it. A recorder that stopped keeping frames flowing shows up here as cues that
    changed nothing at all rather than as one bad cue.
    """
    assert built.verified is not None
    rows = built.verified.json["cues"]
    assert rows, "the fixture declares cued reveals, so a run that measured none measured nothing"
    unchanged = [row for row in rows if not row["skipped"] and not (row["change_percent"] or 0) > 0]
    assert unchanged == [], unchanged


def test_every_cue_lands_inside_the_limit_the_project_publishes(built: Project) -> None:
    """The limit is read from the project's own settings, so no number here decides a verdict."""
    assert built.verified is not None
    published = built.cli("config", "get", "verify.cue_offset_max_ms", "--json")
    limit_ms = float(published.json["key"]["value"])
    late = [
        (qualified(row), row["offset"])
        for row in built.verified.json["cues"]
        if row["offset"] is not None and abs(row["offset"]) * 1000 > limit_ms
    ]
    assert late == [], late


def test_section_2_opens_on_section_1s_last_frame(built: Project) -> None:
    assert built.verified is not None
    [seam] = built.verified.json["seams"]
    assert seam["section"] == 2
    assert seam["drift"] is not None


# ---- the finished film ------------------------------------------------------------------------------


def test_streams_start_together_and_the_sections_sum_to_the_film(built: Project) -> None:
    streams = built.probe("-show_entries", "stream=codec_type,start_time")["streams"]
    starts = {row["codec_type"]: float(row["start_time"]) for row in streams if row["codec_type"] in ("video", "audio")}
    assert starts == {"video": 0.0, "audio": 0.0}
    spans = built.spans()
    assert sorted(spans) == list(EVERY_SECTION)
    presented = built.probe("-select_streams", "v", "-show_entries", "frame=pts_time")["frames"]
    frame_times = {round(float(row["pts_time"]), 3) for row in presented}
    for key, (start, _end) in spans.items():
        assert round(start, 3) in frame_times, f"section {key} starts at {start}, which is between two frames"
    total = spans["05"][1]
    assert abs(total - ffmpeg.probe_duration(built.film)) < SECOND_TOLERANCE
    assert FILM_SECONDS_RANGE[0] < total < FILM_SECONDS_RANGE[1], f"the fixture runs {total:.1f}s"


def test_the_two_blocks_sections_share_one_chapter(built: Project) -> None:
    chapters = built.probe("-show_chapters")["chapters"]
    spans = built.spans()
    assert [row["tags"]["title"] for row in chapters] == ["Blocks", "B-roll", "Equation", "Missing"]
    want = [spans["01"][0], spans["03"][0], spans["04"][0], spans["05"][0]]
    for chapter, start in zip(chapters, want, strict=True):
        assert float(chapter["start_time"]) == pytest.approx(start, abs=SECOND_TOLERANCE), chapter
    assert float(chapters[0]["end_time"]) == pytest.approx(spans["02"][1], abs=SECOND_TOLERANCE)


def test_the_cut_list_records_where_every_section_plays(built: Project) -> None:
    cuts = Cuts.read(built.final_dir / "cuts.json")
    assert cuts is not None
    spans = built.spans()
    rows = {row.key: row for row in cuts.sections}
    assert set(rows) == set(EVERY_SECTION)
    assert rows["03"].kind is SectionKind.CLIP and rows["03"].source == "media/broll.mp4"
    assert rows["01"].source == "build/recordings/01.webm"
    assert [row.key for row in cuts.sections if row.substitute is not None] == ["05"]
    assert cuts.fps > 0
    for key, (start, end) in spans.items():
        assert (rows[key].start, rows[key].end) == pytest.approx((start, end), abs=1e-3)


def test_every_deliverable_beside_the_film_is_written(built: Project) -> None:
    """One command's worth of output: the captions, the chapters, the transcript and the poster."""
    for suffix in (".srt", ".vtt", ".chapters.txt", "-transcript.html", "-poster.png"):
        assert built.deliverable(suffix).stat().st_size > 0, suffix
    assert (built.final_dir / "cuts.json").stat().st_size > 0


def test_the_transcript_page_is_the_media_alternative(built: Project) -> None:
    page = built.deliverable("-transcript.html").read_text(encoding="utf-8")
    assert page.startswith("<!doctype html>") and "<script" not in page
    for chapter in ("Blocks", "B-roll", "Equation", "Missing"):
        assert f"<h2>{chapter}</h2>" in page, chapter
    assert "A first block, a second beside it, a third below." in page


def test_the_poster_is_drawn_by_the_page_rather_than_taken_from_the_film(built: Project) -> None:
    poster = built.deliverable("-poster.png")
    assert poster.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n", "the poster is a lossless PNG"
    probed = built.probe("-show_entries", "stream=width,height,codec_name", path=poster)["streams"][0]
    assert (probed["codec_name"], probed["width"], probed["height"]) == ("png", POSTER_WIDTH, POSTER_HEIGHT)
    first = built.build_dir / "first-frame.png"
    ffmpeg.run("-ss", "0.5", "-i", str(built.film), "-frames:v", "1", "-y", str(first))
    changed = frames.changed_images_percent(
        first, poster, level=FRAME_COMPARE_LEVEL, width=FRAME_COMPARE_WIDTH, height=FRAME_COMPARE_HEIGHT
    )
    assert changed > 1.0, "the poster is drawn by the page with its reveals fired, not lifted from the mp4"


def test_the_player_is_told_what_language_the_film_speaks(built: Project) -> None:
    streams = built.probe("-show_entries", "stream=codec_type:stream_tags=language")["streams"]
    tagged = {row["codec_type"]: row.get("tags", {}).get("language") for row in streams}
    assert tagged["video"] == "eng" and tagged["audio"] == "eng"


def test_the_broll_clip_keeps_its_own_sound(built: Project) -> None:
    start, end = built.spans()["03"]
    assert end - start == pytest.approx(CLIP_SECONDS, abs=SECOND_TOLERANCE)
    assert audio.rms_db(built.film, start + 0.5, end - start - 1.0) > CLIP_TONE_FLOOR_DBFS


# ---- the captions ------------------------------------------------------------------------------------


def srt_cues(path: Path) -> list[tuple[float, float, str]]:
    """Every caption of an SRT file as its start, its end and its text, in the order it plays."""
    rows: list[tuple[float, float, str]] = []
    for block in path.read_text(encoding="utf-8").strip().split("\n\n"):
        lines = block.splitlines()
        [stamp] = [line for line in lines if " --> " in line]
        start, end = (srt_seconds(value) for value in stamp.split(" --> "))
        rows.append((start, end, "\n".join(lines[lines.index(stamp) + 1 :])))
    return rows


def srt_seconds(value: str) -> float:
    """One SRT timestamp in seconds, which is hours, minutes, seconds and a comma before the millis."""
    hms, millis = value.strip().split(",")
    hours, minutes, seconds = hms.split(":")
    return int(hours) * 3600 + int(minutes) * 60 + int(seconds) + int(millis) / 1000


def test_no_caption_plays_over_the_clip_and_the_film_has_both_caption_files(built: Project) -> None:
    spans = built.spans()
    clip_start, clip_end = spans["03"]
    captions = [(start, end) for start, end, _text in srt_cues(built.deliverable(".srt"))]
    assert captions
    over = [row for row in captions if row[0] < clip_end - 1e-3 and row[1] > clip_start + 1e-3]
    assert over == [], f"captions play over the clip at {clip_start:.2f} to {clip_end:.2f}: {over}"
    assert any(clip_end <= start < spans["04"][1] for start, _end in captions), "section 4 has no captions"
    assert built.deliverable(".vtt").read_text(encoding="utf-8").startswith("WEBVTT")


def test_the_cued_sound_reaches_the_captions(built: Project) -> None:
    """A sound a viewer is meant to notice is written down where it plays, in both caption files."""
    caption = "[a tick lands]"
    assert caption in built.deliverable(".srt").read_text(encoding="utf-8")
    assert caption in built.deliverable(".vtt").read_text(encoding="utf-8")
    start, end, text = next(row for row in srt_cues(built.deliverable(".srt")) if caption in row[2])
    first_start, first_end = built.spans()["01"]
    assert first_start <= start < first_end, "the sound caption sits in the section that cues it"
    assert text.splitlines()[-1] == caption, "the sound is a line of its own"
    assert end - start >= MINIMUM_CAPTION_SECONDS


def test_no_two_captions_are_on_screen_at_once(built: Project) -> None:
    """Two overlapping cues are drawn twice or dropped, so a sound under speech joins the speech cue."""
    spans = [(start, end) for start, end, _text in srt_cues(built.deliverable(".srt"))]
    assert spans == sorted(spans)
    assert all(end <= next_start for (_start, end), (next_start, _end) in zip(spans, spans[1:], strict=False))
    assert all(end - start >= MINIMUM_CAPTION_SECONDS - 1e-3 for start, end in spans)


# ---- the commands that read the project --------------------------------------------------------------


def test_status_reports_what_is_written_what_is_built_and_what_is_stale(built: Project) -> None:
    doc = built.cli("status", "--json").json
    assert doc["name"] == FILM_NAME
    assert doc["film"] == relative(built, built.film)
    assert doc["film_seconds"] is not None and doc["film_seconds"] > FILM_SECONDS_RANGE[0]
    rows = {row["key"]: row for row in doc["sections"]}
    assert sorted(rows) == list(EVERY_SECTION)
    assert [key for key, row in rows.items() if row["recorded"]] == list(SPOKEN)
    assert all(row["cut"] for row in rows.values())
    assert not any(row["voiced"] for row in rows.values()), "an unvoiced build owns no paid take"


def test_check_judges_the_inputs_and_prices_the_run_without_a_browser(built: Project) -> None:
    """`check --no-pages` is the door a docs job or a pre-commit hook goes through, so it fetches nothing."""
    run = built.cli("check", "--no-pages", "--json", "--fail-on", "never")
    doc = run.json
    assert doc["pages"] is False
    assert sorted(Path(path).name for path in doc["judged"]) == ["cues.json", "script.md"]
    spend = doc["spend"]
    assert spend["state"] == SpendState.ESTIMATE.value, spend
    assert spend["characters"] > 0 and spend["sections"] == len(SPOKEN), spend
    assert spend["dollars"] >= 0 and spend["price_layer"], spend


def test_words_prints_every_spoken_word_with_its_place_on_the_clock(built: Project) -> None:
    doc = built.cli("words", "--json").json
    rows = {row["key"]: row for row in doc["sections"]}
    assert sorted(rows) == list(SPOKEN)
    assert all(row["estimated"] for row in rows.values()), "an unvoiced build times its words by estimate"
    words = rows["01"]["words"]
    assert [word["word"] for word in words][:2] == ["A", "first"]
    assert all(word["end"] >= word["start"] for word in words)
    assert words == sorted(words, key=lambda word: word["start"])


def test_the_storyboard_is_one_page_of_every_slide_at_every_cue(built: Project) -> None:
    """The storyboard is the human checkpoint before any credit is spent, so `build` writes it too."""
    run = built.cli("storyboard", "--json")
    doc = run.json
    assert doc["storyboard"] == "build/storyboard.html"
    assert (built.build_dir / "storyboard.html").stat().st_size > 0
    panels = doc["panels"]
    assert panels, "a deck with three slides and seven cues has panels"
    assert {panel["slide"] for panel in panels} == {"1.1", "2.1", "3.1"}
    for panel in panels:
        assert (built.root / panel["image"]).stat().st_size > 0, panel


def test_the_storyboard_narrows_to_one_slide(built: Project) -> None:
    doc = built.cli("storyboard", "--json", "--slide", "3.1").json
    assert {panel["slide"] for panel in doc["panels"]} == {"3.1"}


def test_the_equation_typesets_from_the_deck_and_not_from_a_cdn(built: Project) -> None:
    """KaTeX is vendored into deck/katex, so section 4 records with no finding and loads no host."""
    log = built.recording_log("04")
    assert list(log.findings) == [] and list(log.external) == []
    assert any(name.startswith("deck/katex/") for name in log.assets), log.assets


def test_the_take_index_and_the_cue_times_agree_with_what_was_built(built: Project) -> None:
    """The two artifacts every later stage reads, checked as the models a caller reads them as."""
    takes = Takes.read(built.root / Artifact.TAKES.value)
    assert takes is not None
    assert [row.key for row in takes.sections] == list(SPOKEN)
    assert takes.estimated, "every take of an unvoiced build is a placeholder"
    cue_times = CueTimes.read(built.root / Artifact.CUE_TIMES.value)
    assert cue_times is not None
    resolved = [f"{section.section}:{cue.cue}" for section in cue_times.sections for cue in section.cues]
    assert resolved == list(CUES)
    assert all(cue.seconds is not None for section in cue_times.sections for cue in section.cues)


# ---- cue resolution on word timestamps that are not evenly spaced ---------------------------------------


UNEVEN = (
    ("A", 0.70), ("first", 0.81), ("block", 1.40), ("a", 2.60), ("second", 2.66),
    ("beside", 3.30), ("it", 3.92), ("a", 4.05), ("third", 4.10), ("below", 5.55),
)  # fmt: skip
"""Section 1's words with the spacing a real voice leaves, which is what a cue is resolved against."""

WORD_GAP_SECONDS = 0.05
"""How long the silence between two words is made here, so each word ends before the next begins."""

TRAILING_WORD_SECONDS = 0.4
"""How long the last word runs, because nothing after it says where it ends."""


def test_cues_resolve_by_occurrence_and_by_phrase_on_uneven_word_timestamps(built: Project, tmp_path: Path) -> None:
    """A cue names a spoken phrase, so resolution is about the words file and never about the page.

    The words file is not part of what a take's name is taken over, so rewriting it here re-resolves
    every cue and buys nothing, which is the property that lets an author fix a phrase for free.
    """
    root = tmp_path / HOSTILE_DIRECTORY / "sync"
    shutil.copytree(built.root, root, ignore=shutil.ignore_patterns("build"))
    shutil.copytree(built.build_dir, root / "build", ignore=shutil.ignore_patterns("final", "sections", "events"))
    project = Project(root=root, shim=built.shim, attempts=tmp_path / "attempts.txt")

    takes = Takes.read(root / Artifact.TAKES.value)
    assert takes is not None
    [first] = [row for row in takes.sections if row.key == "01"]
    words = []
    for index, (token, start) in enumerate(UNEVEN):
        after = UNEVEN[index + 1][1] if index + 1 < len(UNEVEN) else start + TRAILING_WORD_SECONDS
        words.append(Word(word=token, start=start, end=round(after - WORD_GAP_SECONDS, 3)))
    Words(words=tuple(words)).write(root / "build" / "narrate" / f"{first.hash}.words.json")

    cues = json.loads((root / "cues.json").read_text(encoding="utf-8"))
    cues["sections"]["1"]["cues"] = [
        {"cue": "1.1:first", "on": "first"},
        {"cue": "1.1:second", "on": "a", "occurrence": 3},
        {"cue": "1.1:third", "on": "third below", "offset": -0.2},
    ]
    (root / "cues.json").write_text(json.dumps(cues), encoding="utf-8")

    run = project.cli("cue", "--json", "--section", "1")
    doc = run.json
    assert run.code == FOUND_NOTHING, run.stderr
    [section] = [row for row in doc["sections"] if row["key"] == "01"]
    resolved = {row["cue"]: row["seconds"] for row in section["cues"]}
    lead = first.lead_seconds
    assert resolved["1.1:first"] == pytest.approx(0.81 + lead, abs=1e-2)
    assert resolved["1.1:second"] == pytest.approx(4.05 + lead, abs=1e-2), "the third `a` is the one named"
    assert resolved["1.1:third"] == pytest.approx(4.10 + lead - 0.2, abs=1e-2), "the phrase starts at `third`"


# ---- the rebuild of one section, last because it writes into the built project ---------------------------


def test_building_one_section_records_that_section_and_no_other(built: Project) -> None:
    recordings = built.build_dir / "recordings"
    before = {key: (recordings / f"{key}.webm").stat().st_mtime_ns for key in SPOKEN}
    length = ffmpeg.probe_duration(built.film)
    run = built.cli("build", "--no-voice", "--section", "4", "--json")
    assert run.certain() == [], run.stderr
    assert run.code in (FOUND_NOTHING, FOUND_SOMETHING), run.stderr
    after = {key: (recordings / f"{key}.webm").stat().st_mtime_ns for key in SPOKEN}
    assert after["01"] == before["01"] and after["02"] == before["02"], "only section 4 is recorded again"
    assert after["04"] > before["04"]
    assert abs(ffmpeg.probe_duration(built.film) - length) < SECOND_TOLERANCE
    assert built.network_attempts() == []
