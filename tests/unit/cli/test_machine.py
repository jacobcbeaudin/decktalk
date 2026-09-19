"""`doctor`: the table, the envelope and the block a person pastes into a bug report."""

from __future__ import annotations

import sys

from decktalk.cli import machine, main
from decktalk.cli.schema import DoctorPayload, InitPayload, InstallPayload, read_envelope
from decktalk.verdicts import Verdict


def test_a_command_prints_one_object_and_nothing_else_on_stdout(tmp_path, monkeypatch, capsys):
    monkeypatch.setitem(sys.modules, "playwright.sync_api", None)  # keeps the test free of Chromium
    monkeypatch.setenv("DECKTALK_CACHE_DIR", str(tmp_path / "empty-cache"))
    code = main(["doctor", "--json"])
    doc = read_envelope(capsys.readouterr().out)  # the whole of stdout parses, so nothing else was there
    assert doc.command == "doctor" and doc.schema == 1 and isinstance(doc.payload, DoctorPayload)
    names = {c.name: c for c in doc.payload.components}
    assert names["python"].ok is True and names["katex"].ok is True
    assert names["config"].optional is True  # a build runs without it, so its absence is no finding
    assert doc.findings.certain >= 1 and doc.findings.uncertain == 0
    assert [r.verdict for r in doc.findings.items] == [Verdict.MISSING] * doc.findings.certain
    assert doc.ok is False and doc.exit_code == 1 and code == 1
    assert main(["doctor", "--json", "--exit-zero"]) == 0
    relaxed = read_envelope(capsys.readouterr().out)
    assert relaxed.ok is False and relaxed.exit_code == 0  # --exit-zero never hides what was found


def test_doctor_report_is_pasteable_and_carries_no_secret(tmp_path, monkeypatch, capsys):
    monkeypatch.setitem(sys.modules, "playwright.sync_api", None)
    monkeypatch.setenv("DECKTALK_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("ELEVENLABS_API_KEY", "sk-not-in-the-report")
    monkeypatch.setenv("DECKTALK_VIDEO_PRESET", "veryfast")
    main(["doctor", "--report", "--exit-zero"])
    block = capsys.readouterr().out
    assert block.count("```") == 2 and block.splitlines()[1].startswith("```text")
    for line in ("decktalk ", "platform ", "python ", "katex ", "cache "):
        assert line in block, line
    # No environment variable's value reaches it, because a variable may hold a key.
    assert "sk-not-in-the-report" not in block and "veryfast" not in block


def test_init_reports_every_file_it_wrote(tmp_path, monkeypatch, capsys):
    """`written` is the list a caller opens next, so it is the project and not a count of it."""
    monkeypatch.setenv("DECKTALK_CACHE_DIR", str(tmp_path / "empty-cache"))
    assert main(["init", str(tmp_path / "deck"), "--json"]) == 0
    doc = read_envelope(capsys.readouterr().out)
    for name in ("decktalk.toml", "script.md", "cues.json"):
        assert name in doc.written, name
    assert doc.summary == {"files": len(doc.written)}
    assert isinstance(doc.payload, InitPayload)
    assert doc.payload.name == "deck" and doc.payload.files == doc.written
    assert (doc.findings.certain, doc.findings.uncertain, doc.findings.items) == (0, 0, []) and doc.error is None


def test_the_doctor_table_names_each_component_and_marks_the_missing_one(tmp_path, monkeypatch, capsys):
    """The six rows are the test's own state, so the table is the same on a fresh clone as on a built machine."""
    monkeypatch.setitem(sys.modules, "playwright.sync_api", None)
    monkeypatch.setenv("DECKTALK_CACHE_DIR", str(tmp_path / "empty-cache"))
    ffmpeg, ffprobe = tmp_path / "ffmpeg", tmp_path / "ffprobe"
    for tool in (ffmpeg, ffprobe):
        tool.write_text("#!/bin/sh\n", encoding="utf-8")
        tool.chmod(0o755)
    monkeypatch.setenv("DECKTALK_FFMPEG", str(ffmpeg))
    monkeypatch.setenv("DECKTALK_FFPROBE", str(ffprobe))
    assert main(["doctor", "--exit-zero"]) == 0
    rows = {line.split()[0]: line for line in capsys.readouterr().out.splitlines()[1:]}
    assert set(rows) == {"python", "chromium", "ffmpeg", "ffprobe", "config", "katex"}
    assert Verdict.MISSING.label in rows["chromium"] and Verdict.OK.label in rows["python"]


def test_an_ffmpeg_a_variable_names_and_the_disk_does_not_have_is_reported_missing(tmp_path, monkeypatch, capsys):
    """A typo in the override told a managed machine everything was installed, and the run then died."""
    monkeypatch.setitem(sys.modules, "playwright.sync_api", None)
    monkeypatch.setenv("DECKTALK_CACHE_DIR", str(tmp_path / "empty-cache"))
    monkeypatch.setenv("DECKTALK_FFMPEG", str(tmp_path / "not-there"))
    monkeypatch.setenv("DECKTALK_FFPROBE", str(tmp_path / "also-not-there"))
    assert main(["doctor", "--exit-zero"]) == 0
    rows = {line.split()[0]: line for line in capsys.readouterr().out.splitlines()[1:]}
    assert Verdict.MISSING.label in rows["ffmpeg"] and "DECKTALK_FFMPEG" in rows["ffmpeg"]
    assert Verdict.MISSING.label in rows["ffprobe"] and "DECKTALK_FFPROBE" in rows["ffprobe"]


def test_a_broken_ffprobe_variable_blames_ffprobe_and_still_reports_the_ffmpeg_that_works(
    tmp_path, monkeypatch, capsys
):
    """A report that names the wrong tool sends an author debugging something that is not broken."""
    monkeypatch.setitem(sys.modules, "playwright.sync_api", None)
    monkeypatch.setenv("DECKTALK_CACHE_DIR", str(tmp_path / "empty-cache"))
    real = tmp_path / "ffmpeg"
    real.write_text("#!/bin/sh\n", encoding="utf-8")
    real.chmod(0o755)
    monkeypatch.setenv("DECKTALK_FFMPEG", str(real))
    monkeypatch.setenv("DECKTALK_FFPROBE", str(tmp_path / "not-there"))
    monkeypatch.setattr(machine, "installed_paths", lambda: None)
    assert main(["doctor", "--exit-zero"]) == 0
    rows = {line.split()[0]: line for line in capsys.readouterr().out.splitlines()[1:]}
    assert "DECKTALK_FFPROBE" in rows["ffprobe"] and Verdict.MISSING.label in rows["ffprobe"]
    # The tool that is fine is still a row of its own, and it never carries the other's variable.
    assert "DECKTALK_FFPROBE" not in rows["ffmpeg"]


def test_install_reports_the_binaries_a_run_would_use(tmp_path, monkeypatch, capsys):
    """`install` fetches once per machine, so its envelope names what the machine now has."""
    ffmpeg, ffprobe = (tmp_path / "ffmpeg").as_posix(), (tmp_path / "ffprobe").as_posix()
    monkeypatch.setattr(machine, "fetch", lambda: None)
    monkeypatch.setattr(machine, "installed_paths", lambda: (ffmpeg, ffprobe))
    assert main(["install", "--json"]) == 0
    doc = read_envelope(capsys.readouterr().out)
    assert doc.payload == InstallPayload(ffmpeg=ffmpeg, ffprobe=ffprobe)
    assert doc.written == [] and doc.ok is True and doc.error is None
