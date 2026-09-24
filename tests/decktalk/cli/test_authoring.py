"""The commands an author runs around a build, and the files each one reports writing."""

from __future__ import annotations

from decktalk.cli import authoring, main
from decktalk.cli.schema import ScreenshotsPayload, ServePayload, StatusPayload, read_envelope
from decktalk.pipeline import SectionKind, SoundscapeStatus
from decktalk.stages.soundscape import SoundscapeItem, SoundscapeResult


def test_screenshots_lists_every_file_it_wrote(fake_project, stage, monkeypatch, capsys, tmp_path):
    png = tmp_path / "build" / "screenshots" / "3.1.png"
    png.parent.mkdir(parents=True)
    png.write_bytes(b"png")
    from decktalk.stages.screenshots import Screenshot

    monkeypatch.setattr(
        stage("screenshots"),
        "screenshot_slides",
        lambda project, pages=None, slides=None, cues=None, before=None: [
            Screenshot(path=png, page="deck/index.html", slide="3.1")
        ],
    )
    assert main(["screenshots", "--slide", "3.1", "--after", "3.1eq", "--json"]) == 0
    doc = read_envelope(capsys.readouterr().out)
    assert isinstance(doc.payload, ScreenshotsPayload)
    assert [row.file for row in doc.payload.files] == ["build/screenshots/3.1.png"]
    assert doc.written == ["build/screenshots/3.1.png"] and doc.summary == {"files": 1}
    # `--before` is the other half of the pair, and it reaches the stage the same way.
    capsys.readouterr()
    assert main(["screenshots", "--slide", "3.1", "--before", "3.1eq", "--json"]) == 0
    assert read_envelope(capsys.readouterr().out).summary == {"files": 1}
    # The table a person reads is the same list, so the two can never disagree.
    assert main(["screenshots", "--slide", "3.1", "--after", "3.1eq"]) == 0
    assert capsys.readouterr().out.splitlines()[-1] == "build/screenshots/3.1.png"


def test_status_on_a_scaffold_reads_the_four_input_files(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("DECKTALK_CACHE_DIR", str(tmp_path / "empty-cache"))
    from decktalk.scaffold import init

    root = init(tmp_path / "lesson", name="lesson").root
    assert main(["status", "-p", str(root), "--json"]) == 0
    doc = read_envelope(capsys.readouterr().out)
    assert doc.command == "status" and doc.ok is True and doc.written == []
    report = doc.payload
    assert isinstance(report, StatusPayload)
    assert report.project.name == "lesson"
    assert report.project.script == "script.md" and report.project.script_exists is True
    # The starter is three page sections, and each one plays a scene of the one deck page.
    assert [s.kind for s in report.sections] == [SectionKind.PAGE] * 3
    assert (report.final.path, report.final.exists, report.final.duration) == ("build/out/lesson.mp4", False, None)
    assert doc.summary == {"sections": 3, "cut": 0, "final": False}
    # The table reads the same report, and the summary leads it.
    assert main(["status", "-p", str(root)]) == 0
    out = capsys.readouterr().out
    assert out.splitlines()[0] == "sections 3, cut 0, final False"
    assert "final     not built" in out and "captions  build/out/lesson.srt  not built" in out


def test_soundscape_lists_only_what_it_generated(fake_project, monkeypatch, capsys):
    """A file the run left alone is not a file it wrote, so a caller opens what actually changed."""
    made = fake_project.root / "build" / "music" / "music.mp3"
    made.parent.mkdir(parents=True)
    made.write_bytes(b"mp3")
    rows = [
        SoundscapeItem(
            name="ambience",
            out=fake_project.root / "media" / "ambience.mp3",
            status=SoundscapeStatus.UNCHANGED,
            duration_seconds=0,
            endpoint="https://example.invalid/sound",
            requests=[],
        ),
        SoundscapeItem(
            name="music",
            out=made,
            status=SoundscapeStatus.GENERATED,
            duration_seconds=8,
            endpoint="https://example.invalid/music",
            requests=[{"prompt": "a hum"}],
        ),
    ]
    monkeypatch.setattr(authoring, "generate", lambda project, **kw: SoundscapeResult(items=rows))
    assert main(["soundscape", "--json"]) == 0
    doc = read_envelope(capsys.readouterr().out)
    assert doc.written == ["build/music/music.mp3"]
    assert doc.summary == {"items": 2, "generated": 1}
    assert [(item.name, item.out) for item in doc.payload.items] == [
        ("ambience", "media/ambience.mp3"),
        ("music", "build/music/music.mp3"),
    ]
    # The table a person reads names each item, where it went and whether it was made.
    assert main(["soundscape"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[1].startswith("== ambience -> ") and "[unchanged]" in lines[1]
    assert "[generated (8s)]" in lines[3] and lines[5] == "   {'prompt': 'a hum'}"


def test_serve_prints_the_url_and_holds_the_server_only_after_the_envelope(fake_project, monkeypatch, capsys):
    """A caller starts `serve` to open a page, so the address has to reach it before the server blocks."""
    from importlib import import_module

    module = import_module("decktalk.cli.authoring")
    held: list[str] = []

    class FakeServer:
        server_address = ("127.0.0.1", 8123)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def serve_forever(self):
            held.append("served")

    monkeypatch.setattr(module, "open_server", lambda root, host, port: FakeServer())
    monkeypatch.setattr(module, "served_urls", lambda server, pages: ["http://127.0.0.1:8123/deck/index.html"])
    monkeypatch.setattr(module, "reachable_warning", lambda server: "")
    assert main(["serve", "--host", "127.0.0.1", "--port", "0", "--json"]) == 0
    out, err = capsys.readouterr()
    doc = read_envelope(out)
    assert doc.payload == ServePayload(urls=["http://127.0.0.1:8123/deck/index.html"])
    # The summary is empty, because the one stdout line a person reads in text mode is the URL.
    assert doc.summary == {} and doc.ok is True
    assert held == ["served"], "the server was not held open after the envelope was printed"


def test_serve_warns_on_stderr_when_the_bind_is_reachable_from_another_machine(fake_project, monkeypatch, capsys):
    """An author reads the warning to know who can reach the page, and it never touches stdout."""
    from importlib import import_module

    module = import_module("decktalk.cli.authoring")

    class FakeServer:
        server_address = ("0.0.0.0", 8123)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def serve_forever(self):
            pass

    monkeypatch.setattr(module, "open_server", lambda root, host, port: FakeServer())
    monkeypatch.setattr(module, "served_urls", lambda server, pages: ["http://10.0.0.2:8123/deck/index.html"])
    monkeypatch.setattr(module, "reachable_warning", lambda server: "anyone on this network can read this project")
    assert main(["serve", "--host", "0.0.0.0", "--port", "0"]) == 0
    out, err = capsys.readouterr()
    assert "anyone on this network" in err and "anyone on this network" not in out
    assert out.strip() == "http://10.0.0.2:8123/deck/index.html"
