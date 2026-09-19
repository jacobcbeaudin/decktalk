"""The whole pipeline on the fixture project in tests/e2e/fixture, built once and read by every test here.

    uv run pytest -m e2e

The build is silent, so it needs no API key and spends nothing, and it runs with the network blocked. The
fixture is copied to tests/out/e2e/pipeline, which CI uploads when a test fails. That directory is one
per machine, so a session takes a lock on it and skips rather than deleting another session's build,
and DECKTALK_E2E_OUT names another directory for a second session. Every test is one property
of the finished build, so a failure names what broke. The cue timing gate fails on OFF CUE on Linux and, on
macOS and Windows, only reports it while asserting the wider limits of four offset frames and five a/v frames,
because the hosted runners there present frames late. Pass --gate-timing to gate everywhere.
"""

from __future__ import annotations

import importlib
import importlib.util
import io
import json
import os
import shutil
import socket
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager, redirect_stdout
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, Any

import pytest

from decktalk.artifacts import Takes, Word, write_words
from decktalk.cli import main
from decktalk.media import audio, ffmpeg, frames
from decktalk.model import Project
from decktalk.speech import SpeechRequest, VoiceContext, get_provider
from decktalk.stages.narrate import build_timeline, take_name, text_hash, words_name
from decktalk.toolchain.assets import RUNTIME_FILE, katex_missing, runtime_path, vendor_katex

# An advisory lock on the output directory, where the platform has one.
fcntl = importlib.util.find_spec("fcntl") and importlib.import_module("fcntl")

pytestmark = [pytest.mark.e2e, pytest.mark.timeout(180)]

FIXTURE = Path(__file__).parent / "fixture"
# One directory, so CI uploads it from a path it knows. DECKTALK_E2E_OUT moves it for a second
# session on one machine, and the lock below refuses to share it rather than corrupting it.
OUT = Path(os.environ.get("DECKTALK_E2E_OUT") or Path(__file__).parent.parent / "out") / "e2e"
FPS = 25
STAGE_ORDER = ("narrate", "align", "record", "assemble", "verify")
SPOKEN = ("01", "02", "04")  # the page sections, because 03 is a clip and 05 a slate
CUES = ("1:1.1first", "1:1.1second", "1:1.1third", "2:2.1fourth", "2:2.1fifth", "4:3.1eq", "4:3.1bar")


@dataclass
class Built:
    """The fixture project after `build --no-voice`, with the exit code and the verify JSON of that build."""

    root: Path
    exit_code: int
    stdout: str
    verify: dict[str, Any]
    network_attempts: list[str] = field(default_factory=list)

    @property
    def out(self) -> Path:
        return self.root / "build" / "out"

    def cli(self, *args: str) -> tuple[int, str]:
        """Run one decktalk command on the project in process, so coverage counts it."""
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = main(["-p", str(self.root), *args])
        return code, buf.getvalue()

    def json(self, *args: str) -> dict[str, Any]:
        code, out = self.cli(*args)
        doc = json.loads(out)
        assert doc["command"] == args[0], doc
        return doc

    def probe(self, *args: str, path: Path | None = None) -> dict[str, Any]:
        cmd = [ffmpeg.ffprobe(), "-v", "error", "-of", "json", *args, str(path or self.out / "pipeline.mp4")]
        return json.loads(subprocess.run(cmd, capture_output=True, text=True, check=True).stdout)

    def spans(self) -> dict[str, tuple[float, float]]:
        """(start, end) of every section in the final mp4, from the section files' lengths."""
        t = 0.0
        spans: dict[str, tuple[float, float]] = {}
        for key in ("01", "02", "03", "04", "05"):
            dur = ffmpeg.probe_duration(self.root / "build" / "sections" / f"{key}.mp4")
            spans[key] = (t, t + dur)
            t += dur
        return spans


@contextmanager
def offline(attempts: list[str]) -> Iterator[None]:
    """Refuse every socket connection except loopback, and record what was attempted."""
    real: Any = socket.socket.connect

    def connect(self: Any, address: Any) -> None:
        host = address[0] if isinstance(address, tuple) else ""
        if not isinstance(address, tuple) or host in ("127.0.0.1", "::1", "localhost"):
            return real(self, address)
        attempts.append(repr(address))
        raise OSError(f"the test blocks the network: {address!r}")

    patched: Any = socket.socket
    patched.connect = connect
    try:
        yield
    finally:
        patched.connect = real


def chromium_available() -> bool:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return False
    with sync_playwright() as pw:
        return Path(pw.chromium.executable_path).exists()


def generate_media(root: Path) -> None:
    """The clip and the beds, from ffmpeg's lavfi sources. Videos and audio never go in git."""
    media = root / "media"
    ffmpeg.run(
        "-f", "lavfi", "-i", "testsrc=s=1280x720:r=30", "-f", "lavfi", "-i", "sine=f=660:r=48000",
        "-t", "2.5", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(media / "broll.mp4"),
    )  # fmt: skip
    ffmpeg.run("-f", "lavfi", "-i", "sine=f=220:r=44100", "-t", "8", "-af", "volume=0.5", str(media / "music.mp3"))
    ffmpeg.run("-f", "lavfi", "-i", "anoisesrc=c=pink:r=44100:a=0.2", "-t", "6", str(media / "ambience.mp3"))
    ffmpeg.run("-f", "lavfi", "-i", "sine=f=1000:r=44100", "-t", "0.1", str(media / "tick.mp3"))


def hold(path: Path) -> IO[str] | None:
    """The lock file held for this session, or None when another session already holds it.

    The whole point is that two sessions never share one output directory, because the build deletes
    it and writes it again. Where no advisory lock exists, one session at a time is the rule instead.
    """
    handle = path.open("w", encoding="utf-8")
    if fcntl is None:  # Windows has no advisory lock, and its runner builds one session at a time.
        return handle
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return None
    return handle


@pytest.fixture(scope="session")
def built() -> Iterator[Built]:
    """Copy the fixture, add the runtime and KaTeX, generate the media, and build it without voice offline."""
    if ffmpeg.installed_paths() is None:
        pytest.skip("ffmpeg is missing: run `decktalk install` first")
    if not chromium_available():
        pytest.skip("Chromium is missing: run `decktalk install` first")
    assert not katex_missing(), "the packaged KaTeX copy is incomplete"
    root = OUT / "pipeline"
    OUT.mkdir(parents=True, exist_ok=True)
    lock = hold(OUT / "pipeline.lock")
    if lock is None:
        pytest.skip(f"another session is building {root}: set DECKTALK_E2E_OUT to build somewhere else")
    shutil.rmtree(root, ignore_errors=True)
    shutil.copytree(FIXTURE, root)
    shutil.copyfile(runtime_path(), root / "deck" / RUNTIME_FILE)
    assert vendor_katex(root / "deck")
    generate_media(root)
    # The user's own settings file and any key in the environment must not reach the build.
    env = {
        "DECKTALK_CONFIG": str(OUT / "no-user-config.toml"),
        "DECKTALK_PROJECT": None,
        "ELEVENLABS_API_KEY": None,
        "ELEVENLABS_VOICE_ID": None,
    }
    saved = {k: os.environ.get(k) for k in env}
    for k, v in env.items():
        os.environ.pop(k, None)
        if v is not None:
            os.environ[k] = v
    try:
        attempts: list[str] = []
        built = Built(root, 1, "", {}, attempts)
        with offline(attempts):
            built.exit_code, built.stdout = built.cli("build", "--no-voice")
            # One strict verify run over the sections with no slate. Its JSON is kept for the CI upload.
            doc = built.json("verify", "--json", "--strict", "--exit-zero", "--only", "1", "--only", "2", "--only", "4")
        (root / "verify.json").write_text(json.dumps(doc, indent=2), encoding="utf-8")
        built.verify = doc
        yield built
    finally:
        for k, v in saved.items():
            os.environ.pop(k, None)
            if v is not None:
                os.environ[k] = v
        lock.close()


def voiced_copy(built: Built, name: str, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A copy of the built project whose takes sit under the content hash a real voiced run would compute.

    A take is found by its content, so the placeholder files are renamed to the digests a voiced run
    would give them. The dry run then plans against real-looking takes and sends nothing, so a
    placeholder key is enough for it.
    """
    root = OUT / name
    shutil.rmtree(root, ignore_errors=True)
    shutil.copytree(
        built.root,
        root,
        ignore=shutil.ignore_patterns("recordings", "out", "sections", "preflight", "screenshots", "*.mp4"),
    )
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-key")
    monkeypatch.setenv("ELEVENLABS_VOICE_ID", "test-voice")
    project = Project.load(root)
    cfg = project.settings.narration
    model = project.voice.model or cfg.model
    settings = project.voice.api_settings()
    provider = get_provider(project.voice.provider, VoiceContext(settings=project.settings, secrets=project.env))
    take_index = Takes.load(project.takes_path)
    assert take_index is not None and take_index.estimated
    take_index.model = model
    for seg in project.script_sections()[1]:
        request = SpeechRequest(seg.tts_text(cfg), model, voice_settings=settings, output_format=cfg.output_format)
        row = take_index.sections[seg.key]
        digest = text_hash(seg, cfg, provider.cache_key(request), settings)
        for old, new in ((row.file, take_name(digest)), (row.words_file, words_name(digest))):
            (project.narration_dir / old).rename(project.narration_dir / new)
        row.file, row.words_file, row.hash, row.voiced = take_name(digest), words_name(digest), digest, True
    take_index.save(project.takes_path)
    assert not take_index.estimated
    return root


def statuses(doc: dict[str, Any]) -> dict[str, str]:
    return {s["key"]: s["status"] for s in doc["narrate"]["sections"]}


def srt_cues(path: Path) -> list[tuple[float, float, str]]:
    """Every caption of an SRT file as (start, end, text), in the order it plays."""
    rows: list[tuple[float, float, str]] = []
    blocks = path.read_text(encoding="utf-8").strip().split("\n\n")
    for block in blocks:
        lines = block.splitlines()
        [stamp] = [line for line in lines if " --> " in line]
        a, b = srt_times(stamp)
        rows.append((a, b, "\n".join(lines[lines.index(stamp) + 1 :])))
    return rows


def srt_times(stamp: str) -> tuple[float, float]:
    """The two times of one SRT timestamp line, in seconds."""

    def seconds(value: str) -> float:
        hms, ms = value.split(",")
        h, m, s = hms.split(":")
        return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000

    a, b = stamp.split(" --> ")
    return seconds(a), seconds(b)


# ---- the build ---------------------------------------------------------------------------------


def test_build_exits_zero_with_the_network_blocked(built: Built) -> None:
    """The build's last stage is the real verify, so exit 0 means every reveal landed on its word.

    A hosted runner off Linux presents frames late, which is the one failure this row tolerates, and
    `test_cue_timing_gate` measures how late. Every other fault still fails here, on every platform.
    """
    if built.exit_code != 0:
        v = built.verify["verify"]
        rows = [*v["cues"], *v["starts"], *v["cuts"], *v["seams"]]
        late = [r for r in rows if r["verdict"] == "OFF CUE"]
        others = [r for r in rows if r["verdict"] not in ("OFF CUE", "changed", "skipped", "ok", "quiet")]
        judged = [r for r in v["recordings"] if r["verdicts"]]
        assert sys.platform != "linux", built.stdout
        assert late and not others and not judged, built.stdout
    assert built.network_attempts == []
    assert (built.out / "pipeline.mp4").exists()


def test_the_build_writes_a_progress_log_an_agent_can_read(built: Built) -> None:
    lines = (built.root / "build" / "progress.jsonl").read_text(encoding="utf-8").splitlines()
    rows = [json.loads(line) for line in lines]
    assert {r["stage"] for r in rows} == {"narrate", "align", "record", "assemble", "verify"}
    assert all(r["stage_count"] == 5 for r in rows)
    stages = list(STAGE_ORDER)
    assert [r["stage"] for r in rows if r["event"] == "start" and r["section"] is None] == stages
    # Every stage closes its own row, so a log whose last stage says start alone is a run that died.
    assert [r["stage"] for r in rows if r["event"] == "done" and r["section"] is None] == stages
    # Every section opens and closes a row of its own, so a reader of a long run sees it go.
    opened = [r["section"] for r in rows if r["stage"] == "record" and r["event"] == "start" and r["section"]]
    closed = {r["section"] for r in rows if r["stage"] == "record" and r["event"] in ("done", "skip") and r["section"]}
    assert set(opened) == closed == {int(k) for k in SPOKEN}
    assert all(r["ts"].endswith("Z") and r["pid"] == os.getpid() for r in rows)
    assert all(r["detail"] and r["detail"].endswith(".") for r in rows), "a detail is one sentence"


def test_build_dry_run_prints_the_stage_plan(built: Built) -> None:
    doc = built.json("build", "--dry-run", "--json")
    assert doc["build"]["stages"] == list(STAGE_ORDER) and doc["build"]["missing"] == [] and doc["ok"]
    doc = built.json("build", "--dry-run", "--json", "--from", "record", "--to", "assemble")
    assert doc["build"]["stages"] == ["record", "assemble"]


def test_the_missing_optional_clip_plays_its_slate(built: Built) -> None:
    """Section 5 has no clip file, so a titled slate of slate_seconds plays there. Asserted apart from --strict."""
    assert (built.out / "slates" / "05-slate.png").stat().st_size > 0
    assert ffmpeg.probe_duration(built.root / "build" / "sections" / "05.mp4") == pytest.approx(1.0, abs=1 / FPS)
    _yavg, ymax = frames.luma_at(built.out / "pipeline.mp4", built.spans()["05"][0] + 0.5)
    assert ymax > 60, "the slate frame is black"


def test_every_recording_is_measured_and_checked_by_the_run_that_made_it(built: Built) -> None:
    """`record` writes one log per section with its own measurement and its own verdicts."""
    rows = {}
    for key in SPOKEN:
        rows[key] = json.loads((built.root / "build" / "recordings" / f"{key}.json").read_text(encoding="utf-8"))
    assert {k: r["checks"]["verdicts"] for k, r in rows.items()} == {k: [] for k in SPOKEN}
    assert all(r["page_errors"] == [] for r in rows.values())
    assert all(r["t0_seconds"] is not None and r["t0_method"].startswith("cover") for r in rows.values())
    assert all(r["url"].startswith("http://project.localhost/") for r in rows.values())
    # Every project file the page loaded is named, which is what the next run keys its skip on.
    assert "deck/index.html" in rows["01"]["assets"] and "deck/decktalk-runtime.js" in rows["01"]["assets"]


def test_a_second_record_run_keeps_every_section(built: Built) -> None:
    """Nothing the pages are recorded from has moved, so the run opens no browser and keeps every webm."""
    before = {k: (built.root / "build" / "recordings" / f"{k}.webm").stat().st_mtime_ns for k in SPOKEN}
    doc = built.json("record", "--json")
    assert doc["ok"], doc["findings"]
    rows = {r["key"]: r for r in doc["record"]["recordings"]}
    assert set(rows) == set(SPOKEN) and all(r["kept"] for r in rows.values())
    after = {k: (built.root / "build" / "recordings" / f"{k}.webm").stat().st_mtime_ns for k in SPOKEN}
    assert after == before


def test_verify_strict_finds_nothing_but_timing(built: Built) -> None:
    """Every start is on screen, every cut is quiet, and every one of the seven cues changed the picture."""
    v = built.verify["verify"]
    assert [f"{c['section']}:{c['cue']}" for c in v["cues"]] == list(CUES)
    assert {s["key"]: s["verdict"] for s in v["starts"]} == {k: "ok" for k in ("01", "02", "03", "04", "05")}
    assert {c["key"]: c["verdict"] for c in v["cuts"]} == {k: "quiet" for k in SPOKEN}
    bad = [c for c in v["cues"] if c["verdict"] not in ("changed", "OFF CUE")]
    assert not bad, bad  # THIN CHANGE?, NO CHANGE, UNRESOLVED and skipped all fail here
    assert all(c["av_ms"] is not None for c in v["cues"]), "a build without voice carries a click at every cued word"


def test_cue_timing_gate(built: Built, request: pytest.FixtureRequest) -> None:
    """OFF CUE fails on Linux. macOS and Windows report it and assert the wider limits instead."""
    gate = sys.platform == "linux" or request.config.getoption("--gate-timing")
    rows = built.verify["verify"]["cues"]
    offsets = {f"{c['section']}:{c['cue']}": (c["offset_ms"], c["av_ms"]) for c in rows}
    worst = max((abs(o) for o, _ in offsets.values() if o is not None), default=0)
    line = f"largest cue offset {worst} ms over {len(rows)} cues ({'gate' if gate else 'report'} on {sys.platform})"
    print(line)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        Path(summary).open("a", encoding="utf-8").write(f"- pipeline test: {line}\n")
    off = [c for c in rows if c["verdict"] == "OFF CUE"]
    if gate:
        assert not off, off
        assert built.verify["ok"], built.verify["findings"]
        return
    if off:
        print(f"{len(off)} OFF CUE row(s) reported and not gated: this platform's hosted runner presents frames late")
    offset_limit = 4 * 1000 / FPS + 0.5
    av_limit = 5 * 1000 / FPS + 0.5
    for check, (offset_ms, av_ms) in offsets.items():
        assert offset_ms is not None and abs(offset_ms) <= offset_limit, (check, offset_ms)
        assert av_ms is not None and abs(av_ms) <= av_limit, (check, av_ms)


def test_section_2_is_seamless_after_section_1(built: Built) -> None:
    [seam] = built.verify["verify"]["seams"]
    assert (seam["key"], seam["verdict"]) == ("02", "ok"), seam
    assert seam["changed_percent"] <= 0.1


def test_streams_start_together_and_sections_sum_to_the_film(built: Built) -> None:
    streams = built.probe("-show_entries", "stream=codec_type,start_time")["streams"]
    starts = {s["codec_type"]: float(s["start_time"]) for s in streams if s["codec_type"] in ("video", "audio")}
    assert starts == {"video": 0.0, "audio": 0.0}
    spans = built.spans()
    frames = built.probe("-select_streams", "v", "-show_entries", "frame=pts_time")["frames"]
    pts = {round(float(f["pts_time"]), 3) for f in frames}
    for key, (start, _end) in spans.items():
        assert round(start, 3) in pts, f"section {key} starts at {start}, between frames"
    total = spans["05"][1]
    assert abs(total - ffmpeg.probe_duration(built.out / "pipeline.mp4")) < 0.05
    assert 18 < total < 26, f"the fixture should run about twenty seconds, not {total:.1f}"


def test_chapters_merge_the_two_blocks_sections(built: Built) -> None:
    chapters = built.probe("-show_chapters")["chapters"]
    spans = built.spans()
    assert [c["tags"]["title"] for c in chapters] == ["Blocks", "B-roll", "Equation", "Missing"]
    want = [spans["01"][0], spans["03"][0], spans["04"][0], spans["05"][0]]
    for chapter, start in zip(chapters, want, strict=True):
        assert abs(float(chapter["start_time"]) - start) < 1e-6, (chapter, start)
    assert abs(float(chapters[0]["end_time"]) - spans["02"][1]) < 1e-6, "the merged chapter ends with section 2"


def test_captions_and_chapter_files_are_written(built: Built) -> None:
    for name in ("pipeline.srt", "pipeline.vtt", "pipeline.chapters.txt"):
        assert (built.out / name).stat().st_size > 0, name
    spans = built.spans()
    clip_at, clip_end = spans["03"]
    captions = [(a, b) for a, b, _text in srt_cues(built.out / "pipeline.srt")]
    assert captions
    over = [c for c in captions if c[0] < clip_end - 1e-3 and c[1] > clip_at + 1e-3]
    assert not over, f"captions over the clip at {clip_at:.2f}-{clip_end:.2f}: {over}"
    assert any(clip_end <= c[0] < spans["04"][1] for c in captions), "section 4 has no captions after the clip"
    assert "WEBVTT" in (built.out / "pipeline.vtt").read_text(encoding="utf-8")[:6]


def test_the_cut_list_records_where_every_section_plays(built: Built) -> None:
    cuts = json.loads((built.out / "cuts.json").read_text(encoding="utf-8"))
    spans = built.spans()
    assert cuts["fps"] == FPS and abs(cuts["total_seconds"] - spans["05"][1]) < 0.05
    rows = {f"{r['section']:02d}": r for r in cuts["sections"]}
    assert set(rows) == set(spans)
    for key, (start, end) in spans.items():
        assert (rows[key]["start"], rows[key]["end"]) == pytest.approx((start, end), abs=1e-3)
    assert rows["03"]["kind"] == "clip" and rows["03"]["source"] == "media/broll.mp4"
    assert rows["01"]["source"] == "build/recordings/01.webm"
    # Section 5's clip is missing on purpose, so the row says a slate stands in for it.
    assert rows["05"]["substitute"] == "slate" and [r for r in cuts["sections"] if r["substitute"]] == [rows["05"]]


def test_the_transcript_page_and_the_poster_are_written(built: Built) -> None:
    """The transcript is the media alternative, and the poster is a lossless PNG from the page."""
    page = (built.out / "pipeline-transcript.html").read_text(encoding="utf-8")
    assert page.startswith("<!doctype html>") and "<script" not in page
    for chapter in ("Blocks", "B-roll", "Equation", "Missing"):
        assert f"<h2>{chapter}</h2>" in page, chapter
    assert "A first block, a second beside it, a third below." in page
    assert "A clip plays here: media/broll.mp4." in page and "A placeholder slate frame plays here." in page

    # A player names the audio from this tag, and the container takes the three-letter code alone.
    streams = built.probe("-show_entries", "stream=codec_type:stream_tags=language")["streams"]
    tagged = {s["codec_type"]: s.get("tags", {}).get("language") for s in streams}
    assert tagged["video"] == "eng" and tagged["audio"] == "eng"

    poster = built.out / "pipeline-poster.png"
    assert poster.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n", "the poster is a lossless PNG"
    probe = built.probe("-show_entries", "stream=width,height,codec_name", path=poster)["streams"][0]
    assert (probe["codec_name"], probe["width"], probe["height"]) == ("png", 1920, 1080)
    # Drawn by the page with its reveals fired, so it is not the film's first frame with nothing on it.
    first = built.root / "build" / "first-frame.png"
    ffmpeg.run("-ss", "0.5", "-i", str(built.out / "pipeline.mp4"), "-frames:v", "1", "-y", str(first))
    assert frames.changed_images_percent(first, poster, level=40, width=480, height=270) > 1.0, (
        "the poster is drawn by the page, not taken from the mp4"
    )


def test_the_cued_sound_reaches_the_captions(built: Built) -> None:
    """A sound a viewer is meant to notice is written down where it plays, in both caption files."""
    srt = (built.out / "pipeline.srt").read_text(encoding="utf-8")
    vtt = (built.out / "pipeline.vtt").read_text(encoding="utf-8")
    assert "[a tick lands]" in srt and "[a tick lands]" in vtt
    spans = built.spans()
    cues = srt_cues(built.out / "pipeline.srt")
    [(at, end, text)] = [row for row in cues if "[a tick lands]" in row[2]]
    assert spans["01"][0] <= at < spans["01"][1], "the sound caption sits in the section that cues it"
    assert text.splitlines()[-1] == "[a tick lands]", "the sound is a line of its own"
    assert end - at >= 1.0, "no caption is on screen for under a second"


def test_no_two_captions_are_on_screen_at_once(built: Built) -> None:
    """Two overlapping cues are drawn twice or dropped, so a sound under speech joins the speech cue."""
    spans = [(a, b) for a, b, _text in srt_cues(built.out / "pipeline.srt")]
    assert spans == sorted(spans)
    assert all(b <= next_a for (_a, b), (next_a, _b) in zip(spans, spans[1:], strict=False))
    assert all(b - a >= 1.0 - 1e-3 for a, b in spans), "no caption is on screen for under a second"


def test_the_broll_clip_keeps_its_own_sound(built: Built) -> None:
    clip_at, clip_end = built.spans()["03"]
    assert clip_end - clip_at == pytest.approx(2.5, abs=1 / FPS)
    assert audio.rms_db(built.out / "pipeline.mp4", clip_at + 0.5, clip_end - clip_at - 1.0) > -30


def test_the_equation_typesets_offline_and_the_slide_screenshot_shows_it(built: Built) -> None:
    """KaTeX comes from deck/katex, so section 4 records with no warning, and its slide screenshot is written."""
    recording_log = json.loads((built.root / "build" / "recordings" / "04.json").read_text(encoding="utf-8"))
    assert recording_log["warnings"] == [] and recording_log["page_errors"] == []
    assert any(e["id"] == "3.1eq" for e in recording_log["cue_log"]), recording_log["cue_log"]
    code, _out = built.cli("screenshots", "--slide", "3.1")
    assert code == 0
    png = built.root / "build" / "screenshots" / "slide-3.1.png"
    assert png.stat().st_size > 0
    # The card with the equation is dark on a white page, so the frozen slide is far from blank.
    blank = built.root / "build" / "screenshots" / "blank.png"
    ffmpeg.run("-f", "lavfi", "-i", "color=c=white:s=1920x1080", "-frames:v", "1", str(blank))
    assert frames.changed_images_percent(blank, png, level=40, width=480, height=270) > 5


def test_preflight_plans_every_take_and_estimates_each_reveal(built: Built) -> None:
    doc = built.json("preflight", "--json", "--exit-zero")
    p = doc["preflight"]
    assert [t["key"] for t in p["takes"]] == list(SPOKEN)
    assert p["totals"]["synthesize"] == len(p["takes"]) == 3, p["totals"]
    verdicts = {f"{c['section']}:{c['cue']}": c["verdict"] for c in p["cues"]}
    assert set(verdicts) == set(CUES)
    assert set(verdicts.values()) <= {"changed", "THIN CHANGE?", "skipped"}, verdicts
    assert [(k["key"], k["verdict"]) for k in p["seams"]] == [("02", "ok")]


def test_screenshots_write_a_frame_from_a_playing_section(built: Built) -> None:
    code, _out = built.cli("screenshots", "--section", "1", "--at", "1")
    assert code == 0
    assert (built.root / "build" / "screenshots" / "section-01-at-1s.png").stat().st_size > 0


def test_status_lists_every_output(built: Built) -> None:
    doc = built.json("status", "--json")
    s = doc["status"]
    assert s["final"]["exists"] and s["final"]["duration"] > 18
    assert all(o["exists"] for o in s["outputs"].values()) and set(s["outputs"]) == {"srt", "vtt", "chapters"}
    assert all(sec["cut"] for sec in s["sections"])
    assert [sec["key"] for sec in s["sections"] if sec["recorded"]] == list(SPOKEN)
    assert s["timeline"]["estimated"] and s["cue_times"]["exists"]


# ---- what a voiced run would spend, with no key and no call --------------------------------------


def test_narrate_dry_run_voices_nothing_when_nothing_changed(built: Built, monkeypatch: pytest.MonkeyPatch) -> None:
    root = voiced_copy(built, "unchanged", monkeypatch)
    doc = json.loads(Built(root, 0, "", {}).cli("narrate", "--dry-run", "--json")[1])
    assert doc["narrate"]["note"] is None, doc["narrate"]["note"]
    assert statuses(doc) == {k: "cached" for k in SPOKEN}
    assert doc["narrate"]["totals"]["synthesize"] == 0 and doc["narrate"]["totals"]["characters_sent"] == 0


def test_narrate_dry_run_voices_only_the_changed_section(built: Built, monkeypatch: pytest.MonkeyPatch) -> None:
    root = voiced_copy(built, "changed", monkeypatch)
    script = root / "script.md"
    script.write_text(script.read_text(encoding="utf-8").replace("and then a bar under it", "and then a wide bar"))
    doc = json.loads(Built(root, 0, "", {}).cli("narrate", "--dry-run", "--json")[1])
    assert statuses(doc) == {"01": "cached", "02": "cached", "04": "synthesize"}
    [take] = [s for s in doc["narrate"]["sections"] if s["status"] == "synthesize"]
    assert take["reason"] == "the text, voice, model, or voice settings changed"
    assert doc["narrate"]["totals"]["characters_sent"] == take["characters_sent"] == len(take["request"]["text"])


def test_narrate_dry_run_plans_one_take_for_an_inserted_section(built: Built, monkeypatch: pytest.MonkeyPatch) -> None:
    root = voiced_copy(built, "inserted", monkeypatch)
    toml = root / "decktalk.toml"
    toml.write_text(
        toml.read_text(encoding="utf-8")
        + '\n[[section]]\nnumber = 6\nchapter = "Coda"\npage = "deck/index.html"\nscene = 3\n',
        encoding="utf-8",
    )
    script = root / "script.md"
    script.write_text(script.read_text(encoding="utf-8") + "\n## 6. Coda\n\nOne more line closes it.\n")
    doc = json.loads(Built(root, 0, "", {}).cli("narrate", "--dry-run", "--json")[1])
    assert statuses(doc) == {"01": "cached", "02": "cached", "04": "cached", "06": "synthesize"}
    assert doc["narrate"]["totals"]["synthesize"] == 1


# ---- word-level sync, with uneven timestamps made here ------------------------------------------


UNEVEN = [
    ("A", 0.70), ("first", 0.81), ("block", 1.40), ("a", 2.60), ("second", 2.66),
    ("beside", 3.30), ("it", 3.92), ("a", 4.05), ("third", 4.10), ("below", 5.55),
]  # fmt: skip


def test_cues_resolve_on_uneven_word_timestamps(built: Built, monkeypatch: pytest.MonkeyPatch) -> None:
    """A words file with real-looking spacing resolves by occurrence, keeps its cached take, and answers --only."""
    root = voiced_copy(built, "sync", monkeypatch)
    project = Project.load(root)
    take_index = project.takes()
    assert take_index is not None
    entry = take_index.sections["01"]
    words = [
        Word(w, start, round((UNEVEN[i + 1][1] if i + 1 < len(UNEVEN) else start + 0.4) - 0.05, 3))
        for i, (w, start) in enumerate(UNEVEN)
    ]
    write_words(project.narration_dir / entry.words_file, words)
    build_timeline(project, take_index, project.script_sections()[1])
    # Section 1's cues alone are rewritten, so every other section's elements stay cued.
    cues = json.loads((root / "cues.json").read_text(encoding="utf-8"))
    cues["sections"]["1"]["cues"] = [
        {"cue": "1.1first", "on": "first"},
        {"cue": "1.1second", "on": "a", "occurrence": 3},
        {"cue": "1.1third", "on": "third below", "offset": -0.2},
    ]
    (root / "cues.json").write_text(json.dumps(cues), encoding="utf-8")
    run = Built(root, 0, "", {})
    aligned = run.json("align", "--json")
    assert aligned["ok"], aligned["findings"]
    [section] = [s for s in aligned["align"]["sections"] if s["key"] == "01"]
    assert section["notes"] == []
    # Times count from the section start, which begins with [narration] opening_silence_seconds.
    lead = project.settings.narration.opening_silence_seconds
    assert section["cues"] == {
        "1.1first": round(0.81 + lead, 2),
        "1.1second": round(4.05 + lead, 2),
        "1.1third": pytest.approx(3.9 + lead),
    }
    # The words file is not part of the take hash, so the take stays cached, and --only keeps section 1 alone.
    plan = run.json("narrate", "--dry-run", "--json", "--only", "1")
    assert statuses(plan) == {"01": "cached"}
    spoken = run.json("words", "--json", "--only", "1")
    [row] = spoken["words"]["sections"]
    assert row["key"] == "01" and not row["estimated"]
    assert [(w["word"], w["start"]) for w in row["words"]] == [(w, round(s + lead, 3)) for w, s in UNEVEN]
    assert [w["text"] for w in row["words"]][:3] == ["A", "first", "block,"]


# ---- the rebuild of one section, last because it writes into the built project -------------------


def test_build_only_rerecords_section_4(built: Built) -> None:
    rec = built.root / "build" / "recordings"
    before = {k: (rec / f"{k}.webm").stat().st_mtime_ns for k in SPOKEN}
    length = ffmpeg.probe_duration(built.out / "pipeline.mp4")
    with offline(built.network_attempts):
        code, out = built.cli("build", "--no-voice", "--only", "4")
    assert code == 0, out
    after = {k: (rec / f"{k}.webm").stat().st_mtime_ns for k in SPOKEN}
    assert after["01"] == before["01"] and after["02"] == before["02"], "only section 4 should be recorded again"
    assert after["04"] > before["04"]
    assert abs(ffmpeg.probe_duration(built.out / "pipeline.mp4") - length) < 0.05
    assert built.network_attempts == []
