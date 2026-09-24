"""Finding the tools and refusing what they could not do, which is what keeps a failure out of a verdict."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from decktalk.errors import ToolError
from decktalk.media import ffmpeg


@pytest.fixture
def tools(monkeypatch: pytest.MonkeyPatch) -> None:
    """The pair resolved, so a test about running a tool never looks for one on this machine."""
    monkeypatch.setattr(ffmpeg, "ffmpeg_paths", lambda: ("ffmpeg", "ffprobe"))


def answer(monkeypatch: pytest.MonkeyPatch, *, code: int, out: bytes = b"", err: bytes = b"") -> list[list[str]]:
    """Replace the subprocess seam with one fixed answer, and give back the commands it was asked to run."""
    seen: list[list[str]] = []

    def fake(cmd: list[str], **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
        seen.append(cmd)
        return subprocess.CompletedProcess(cmd, code, out, err)

    monkeypatch.setattr(ffmpeg.subprocess, "run", fake)
    return seen


COMPLAINT = b"\n".join(b"line %d" % n for n in range(1, 10)) + b"\nno such file or directory\n"


@pytest.mark.usefixtures("tools")
def test_every_call_raises_a_tool_error_carrying_the_tail_of_what_the_tool_said(monkeypatch):
    """A failed run is never a measurement, so each of the five calls refuses rather than answering."""
    calls = (
        lambda: ffmpeg.run("-i", "a.mp3", "out.mp3"),
        lambda: ffmpeg.stderr("-i", "a.mp3", "-f", "null", "-"),
        lambda: ffmpeg.raw("-i", "a.mp3", "-f", "s16le", "-"),
        lambda: ffmpeg.probe_duration(Path("a.mp3")),
        lambda: ffmpeg.has_audio(Path("a.mp3")),
    )
    for call in calls:
        answer(monkeypatch, code=1, err=COMPLAINT)
        with pytest.raises(ToolError) as raised:
            call()
        message = str(raised.value)
        assert "no such file or directory" in message, message
        # The tail is the last few lines and never the whole log, so the first lines stay out of it.
        assert "line 1" not in message, message


@pytest.mark.usefixtures("tools")
def test_a_probe_that_exits_zero_with_nothing_to_say_is_still_a_refusal(monkeypatch):
    """An empty answer from ffprobe is a file it could not read, and a length of zero would be a lie."""
    answer(monkeypatch, code=0, out=b"  \n", err=b"moov atom not found")
    with pytest.raises(ToolError) as raised:
        ffmpeg.probe_duration(Path("build/recordings/01.webm"))
    assert raised.value.location is not None and raised.value.location.file == Path("build/recordings/01.webm")


@pytest.mark.usefixtures("tools")
def test_a_probe_that_answers_is_read_as_the_measurement_it_is(monkeypatch):
    answer(monkeypatch, code=0, out=b"12.34567\n")
    assert ffmpeg.probe_duration(Path("a.mp3")) == 12.346
    answer(monkeypatch, code=0, out=b"audio\n")
    assert ffmpeg.has_audio(Path("a.mp3")) is True
    answer(monkeypatch, code=0, out=b"")
    assert ffmpeg.has_audio(Path("a.mp3")) is False


@pytest.mark.usefixtures("tools")
def test_stderr_hands_back_the_report_and_raw_hands_back_the_bytes(monkeypatch):
    seen = answer(monkeypatch, code=0, out=b"\x01\x02", err=b"silence_start: 1.5")
    assert ffmpeg.stderr("-i", "a.mp3") == "silence_start: 1.5"
    assert ffmpeg.raw("-i", "a.mp3") == b"\x01\x02"
    assert seen[0][:3] == ["ffmpeg", "-hide_banner", "-nostats"]
    assert seen[1][:3] == ["ffmpeg", "-v", "error"]


def test_naming_one_half_of_the_override_is_refused_rather_than_ignored(monkeypatch, tmp_path):
    """ffmpeg and ffprobe are one build, so half an override would render with two of them."""
    binary = tmp_path / "ffmpeg"
    binary.write_text("")
    monkeypatch.setenv(ffmpeg.FFMPEG_VARIABLE, str(binary))
    monkeypatch.delenv(ffmpeg.FFPROBE_VARIABLE, raising=False)
    ffmpeg.ffmpeg_paths.cache_clear()
    assert ffmpeg.env_unpaired() == [ffmpeg.FFPROBE_VARIABLE]
    with pytest.raises(ToolError) as raised:
        ffmpeg.ffmpeg_paths()
    assert ffmpeg.FFPROBE_VARIABLE in str(raised.value)
    # `doctor` reports a machine rather than rendering on it, so it says there is no usable pair.
    assert ffmpeg.installed_paths() is None
    ffmpeg.ffmpeg_paths.cache_clear()


def test_setting_both_halves_is_the_override_it_was_written_to_be(monkeypatch, tmp_path):
    ff, fp = tmp_path / "ffmpeg", tmp_path / "ffprobe"
    ff.write_text("")
    fp.write_text("")
    monkeypatch.setenv(ffmpeg.FFMPEG_VARIABLE, str(ff))
    monkeypatch.setenv(ffmpeg.FFPROBE_VARIABLE, str(fp))
    ffmpeg.ffmpeg_paths.cache_clear()
    assert ffmpeg.env_unpaired() == []
    assert ffmpeg.ffmpeg_paths() == (str(ff), str(fp))
    ffmpeg.ffmpeg_paths.cache_clear()
