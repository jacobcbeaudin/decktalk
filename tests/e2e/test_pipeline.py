"""The whole pipeline on the fixture project in tests/e2e/fixture, built once and read by every test here.

    uv run pytest -m e2e

The build is silent, so it needs no API key and spends nothing, and it runs with the network blocked. The
fixture is copied to tests/out/e2e/pipeline, which CI uploads when a test fails. Every test is one property
of the finished build, so a failure names what broke. The cue timing gate fails on OFF CUE on Linux and, on
macOS and Windows, only reports it while asserting the wider limits of four offset frames and five a/v frames,
because the hosted runners there present frames late. Pass --gate-timing to gate everywhere.
"""

from __future__ import annotations

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
from typing import Any

import pytest

from decktalk.artifacts import Manifest, Word, write_words
from decktalk.cli import main
from decktalk.media import ffmpeg
from decktalk.project import Project
from decktalk.providers.speech import SpeechRequest, get_provider
from decktalk.scaffold import katex_cached, update_runtime, vendor_katex
from decktalk.stages.narrate import build_timeline, script_segments, text_hash

pytestmark = [pytest.mark.e2e, pytest.mark.timeout(180)]

FIXTURE = Path(__file__).parent / "fixture"
OUT = Path(__file__).parent.parent / "out" / "e2e"
FPS = 25
SPOKEN = ("01", "02", "04")  # the page sections; 03 is a clip and 05 a slate
CUES = ("1:1.1first", "1:1.1second", "1:1.1third", "2:2.1fourth", "2:2.1fifth", "4:3.1eq", "4:3.1bar")


@dataclass
class Built:
    """The fixture project after `build --silent`, with the exit code and the verify JSON of that build."""

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
            dur = ffmpeg.probe_duration(self.out / f"{key}-section.mp4")
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
    ffmpeg.run("-f", "lavfi", "-i", "sine=f=220:r=44100", "-t", "8", "-af", "volume=0.5", str(media / "underscore.mp3"))
    ffmpeg.run("-f", "lavfi", "-i", "anoisesrc=c=pink:r=44100:a=0.2", "-t", "6", str(media / "ambience.mp3"))
    ffmpeg.run("-f", "lavfi", "-i", "sine=f=1000:r=44100", "-t", "0.1", str(media / "tick.mp3"))


@pytest.fixture(scope="session")
def built() -> Iterator[Built]:
    """Copy the fixture, add the runtime and KaTeX, generate the media, and build it silently offline."""
    if ffmpeg.installed_paths() is None:
        pytest.skip("ffmpeg is missing: run `decktalk setup` first")
    if not chromium_available():
        pytest.skip("Chromium is missing: run `decktalk setup` first")
    if katex_cached() is None:
        pytest.skip("KaTeX is not cached: run `decktalk setup` first")
    root = OUT / "pipeline"
    shutil.rmtree(root, ignore_errors=True)
    shutil.copytree(FIXTURE, root)
    update_runtime(root)
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
            built.exit_code, built.stdout = built.cli("build", "--silent")
            # One strict verify run over the sections with no slate. Its JSON is kept for the CI upload.
            doc = built.json("verify", "--json", "--strict", "--no-fail", "--only", "1", "--only", "2", "--only", "4")
        (root / "verify.json").write_text(json.dumps(doc, indent=2), encoding="utf-8")
        built.verify = doc
        yield built
    finally:
        for k, v in saved.items():
            os.environ.pop(k, None)
            if v is not None:
                os.environ[k] = v


def voiced_copy(built: Built, name: str, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A copy of the built project whose manifest reads as voiced takes with the hash a real run would compute.

    The dry run plans against that manifest and sends nothing, so a placeholder key is enough for it.
    """
    root = OUT / name
    shutil.rmtree(root, ignore_errors=True)
    shutil.copytree(built.root, root, ignore=shutil.ignore_patterns("rec", "out", "preflight", "shots", "*.mp4"))
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-key")
    monkeypatch.setenv("ELEVENLABS_VOICE_ID", "test-voice")
    project = Project.load(root)
    cfg = project.settings.narration
    model = project.voice.model or cfg.model
    settings = project.voice.api_settings()
    provider = get_provider(project)
    manifest = Manifest.load(project.manifest_path)
    assert manifest is not None and manifest.estimated
    manifest.estimated = False
    manifest.model = model
    for seg in script_segments(project)[1]:
        request = SpeechRequest(seg.tts_text(cfg), model, voice_settings=settings, output_format=cfg.output_format)
        manifest.segments[seg.key].hash = text_hash(seg, cfg, provider.cache_key(request), settings)
    manifest.save(project.manifest_path)
    return root


def statuses(doc: dict[str, Any]) -> dict[str, str]:
    return {s["key"]: s["status"] for s in doc["narrate"]["sections"]}


def srt_spans(path: Path) -> list[tuple[float, float]]:
    def seconds(stamp: str) -> float:
        hms, ms = stamp.split(",")
        h, m, s = hms.split(":")
        return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000

    spans: list[tuple[float, float]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if " --> " in line:
            a, b = line.split(" --> ")
            spans.append((seconds(a), seconds(b)))
    return spans


# ---- the build ---------------------------------------------------------------------------------


def test_build_exits_zero_with_the_network_blocked(built: Built) -> None:
    assert built.exit_code == 0, built.stdout
    assert built.network_attempts == []
    assert (built.out / "pipeline.mp4").exists()


def test_the_missing_optional_clip_plays_its_slate(built: Built) -> None:
    """Section 5 has no clip file, so a titled slate of slate_seconds plays there. Asserted apart from --strict."""
    assert (built.out / "slates" / "05-slate.png").stat().st_size > 0
    assert ffmpeg.probe_duration(built.out / "05-section.mp4") == pytest.approx(1.0, abs=1 / FPS)
    _yavg, ymax = ffmpeg.luma_at(built.out / "pipeline.mp4", built.spans()["05"][0] + 0.5)
    assert ymax > 60, "the slate frame is black"


def test_check_strict_reports_no_findings(built: Built) -> None:
    doc = built.json("check", "--json", "--strict", "--only", "1", "--only", "2", "--only", "4")
    assert doc["ok"], doc["findings"]
    rows = {r["key"]: r for r in doc["check"]["recordings"]}
    assert set(rows) == set(SPOKEN)
    assert {k: r["verdicts"] for k, r in rows.items()} == {k: [] for k in SPOKEN}
    assert all(r["page_errors"] == [] for r in rows.values())


def test_verify_strict_finds_nothing_but_timing(built: Built) -> None:
    """Every start is on screen, every cut is quiet, and every one of the seven cues changed the picture."""
    v = built.verify["verify"]
    assert [f"{c['section']}:{c['cue']}" for c in v["cues"]] == list(CUES)
    assert {s["key"]: s["verdict"] for s in v["starts"]} == {k: "ok" for k in ("01", "02", "03", "04", "05")}
    assert {c["key"]: c["verdict"] for c in v["cuts"]} == {k: "quiet" for k in SPOKEN}
    bad = [c for c in v["cues"] if c["verdict"] not in ("changed", "OFF CUE")]
    assert not bad, bad  # THIN CHANGE?, NO CHANGE, UNRESOLVED and skipped all fail here
    assert all(c["av_ms"] is not None for c in v["cues"]), "a silent build carries a click at every cued word"


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


def test_section_2_carries_section_1s_last_frame(built: Built) -> None:
    [carry] = built.verify["verify"]["carries"]
    assert (carry["key"], carry["verdict"]) == ("02", "ok"), carry
    assert carry["changed_percent"] <= 0.1


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
    captions = srt_spans(built.out / "pipeline.srt")
    assert captions
    over = [c for c in captions if c[0] < clip_end - 1e-3 and c[1] > clip_at + 1e-3]
    assert not over, f"captions over the clip at {clip_at:.2f}-{clip_end:.2f}: {over}"
    assert any(clip_end <= c[0] < spans["04"][1] for c in captions), "section 4 has no captions after the clip"
    assert "WEBVTT" in (built.out / "pipeline.vtt").read_text(encoding="utf-8")[:6]


def test_the_broll_clip_keeps_its_own_sound(built: Built) -> None:
    clip_at, clip_end = built.spans()["03"]
    assert clip_end - clip_at == pytest.approx(2.5, abs=1 / FPS)
    assert ffmpeg.rms_db(built.out / "pipeline.mp4", clip_at + 0.5, clip_end - clip_at - 1.0) > -30


def test_the_equation_typesets_offline_and_the_step_shot_shows_it(built: Built) -> None:
    """KaTeX comes from deck/katex, so section 4 records with no warning, and its step screenshot is written."""
    sidecar = json.loads((built.root / "build" / "rec" / "04-scene.json").read_text(encoding="utf-8"))
    assert sidecar["warnings"] == [] and sidecar["page_errors"] == []
    assert any(e["id"] == "3.1eq" for e in sidecar["cue_log"]), sidecar["cue_log"]
    code, _out = built.cli("shots", "--step", "3.1")
    assert code == 0
    shot = built.root / "build" / "shots" / "step-3.1.png"
    assert shot.stat().st_size > 0
    # The card with the equation is dark on a white page, so the frozen step is far from blank.
    blank = built.root / "build" / "shots" / "blank.png"
    ffmpeg.run("-f", "lavfi", "-i", "color=c=white:s=1920x1080", "-frames:v", "1", str(blank))
    assert ffmpeg.changed_images_percent(blank, shot, level=40, width=480, height=270) > 5


def test_preflight_plans_every_take_and_estimates_each_reveal(built: Built) -> None:
    doc = built.json("preflight", "--json", "--no-fail")
    p = doc["preflight"]
    assert [t["key"] for t in p["takes"]] == list(SPOKEN)
    assert p["totals"]["synthesize"] == len(p["takes"]) == 3, p["totals"]
    verdicts = {f"{c['section']}:{c['cue']}": c["verdict"] for c in p["cues"]}
    assert set(verdicts) == set(CUES)
    assert set(verdicts.values()) <= {"changed", "THIN CHANGE?", "skipped"}, verdicts
    assert [(k["key"], k["verdict"]) for k in p["carries"]] == [("02", "ok")]


def test_shots_write_a_frame_from_a_playing_section(built: Built) -> None:
    code, _out = built.cli("shots", "--section", "1", "--at", "1")
    assert code == 0
    assert (built.root / "build" / "shots" / "section-01-at-1s.png").stat().st_size > 0


def test_status_lists_every_output(built: Built) -> None:
    doc = built.json("status", "--json")
    s = doc["status"]
    assert s["final"]["exists"] and s["final"]["duration"] > 18
    assert all(o["exists"] for o in s["outputs"].values()) and set(s["outputs"]) == {"srt", "vtt", "chapters"}
    assert all(sec["cut"] for sec in s["sections"])
    assert [sec["key"] for sec in s["sections"] if sec["recorded"]] == list(SPOKEN)
    assert s["timeline"]["estimated"] and s["beats"]["exists"]


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
    assert doc["narrate"]["totals"]["characters_sent"] == take["characters_sent"] == len(take["text"])


def test_narrate_dry_run_plans_one_take_for_an_inserted_section(built: Built, monkeypatch: pytest.MonkeyPatch) -> None:
    root = voiced_copy(built, "inserted", monkeypatch)
    toml = root / "decktalk.toml"
    toml.write_text(
        toml.read_text(encoding="utf-8")
        + '\n[[section]]\nnumber = 6\ntitle = "Coda"\npage = "deck/index.html"\nscene = 3\n',
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
    manifest = project.manifest()
    assert manifest is not None
    entry = manifest.segments["01"]
    words = [
        Word(w, start, round((UNEVEN[i + 1][1] if i + 1 < len(UNEVEN) else start + 0.4) - 0.05, 3))
        for i, (w, start) in enumerate(UNEVEN)
    ]
    write_words(project.audio_dir / entry.words_file, words)
    build_timeline(project, manifest, script_segments(project)[1])
    (root / "cues.json").write_text(
        json.dumps(
            {
                "sections": {
                    "1": {
                        "cues": [
                            {"cue": "1.1first", "on": "first"},
                            {"cue": "1.1second", "on": "a", "occurrence": 3},
                            {"cue": "1.1third", "on": "third below", "offset": -0.2},
                        ]
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    run = Built(root, 0, "", {})
    beats = run.json("beats", "--json")
    assert beats["ok"], beats["findings"]
    [section] = beats["beats"]["sections"]
    assert section["key"] == "01" and section["notes"] == []
    assert section["cues"] == {"1.1first": 0.81, "1.1second": 4.05, "1.1third": pytest.approx(3.9)}
    # The words file is not part of the take hash, so the take stays cached, and --only keeps section 1 alone.
    plan = run.json("narrate", "--dry-run", "--json", "--only", "1")
    assert statuses(plan) == {"01": "cached"}
    spoken = run.json("words", "--json", "--only", "1")
    [row] = spoken["words"]["sections"]
    assert row["key"] == "01" and not row["estimated"]
    assert [(w["word"], w["start"]) for w in row["words"]] == UNEVEN
    assert [w["text"] for w in row["words"]][:3] == ["A", "first", "block,"]


# ---- the rebuild of one section, last because it writes into the built project -------------------


def test_build_only_rerecords_section_4(built: Built) -> None:
    rec = built.root / "build" / "rec"
    before = {k: (rec / f"{k}-scene.webm").stat().st_mtime_ns for k in SPOKEN}
    length = ffmpeg.probe_duration(built.out / "pipeline.mp4")
    with offline(built.network_attempts):
        code, out = built.cli("build", "--silent", "--only", "4")
    assert code == 0, out
    after = {k: (rec / f"{k}-scene.webm").stat().st_mtime_ns for k in SPOKEN}
    assert after["01"] == before["01"] and after["02"] == before["02"], "only section 4 should be recorded again"
    assert after["04"] > before["04"]
    assert abs(ffmpeg.probe_duration(built.out / "pipeline.mp4") - length) < 0.05
    assert built.network_attempts == []
