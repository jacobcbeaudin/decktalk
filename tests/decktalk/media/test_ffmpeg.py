"""Finding the tools and refusing what they could not do, which is what keeps a failure out of a verdict."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from decktalk.errors import ToolError
from decktalk.media import ffmpeg
from decktalk.settings import ToolsConfig
from decktalk.toolchain.cache import cache_dir


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


def test_naming_one_half_of_the_build_is_refused_rather_than_ignored(tmp_path):
    """ffmpeg and ffprobe are one build, so naming one key would render with two of them."""
    binary = tmp_path / "ffmpeg"
    binary.write_text("")
    half = ToolsConfig(ffmpeg=str(binary))
    assert ffmpeg.unpaired_tool(half) == ["tools.ffprobe"]
    with ffmpeg.using_tools(half), pytest.raises(ToolError) as raised:
        ffmpeg.ffmpeg_paths()
    assert "tools.ffprobe" in str(raised.value)
    # `doctor` reports a machine rather than rendering on it, so it says there is no usable pair.
    assert ffmpeg.installed_paths(half) is None


def test_a_key_that_names_a_file_which_is_not_there_is_refused_rather_than_resolved(tmp_path):
    """A typo told `doctor` the machine was ready and then died inside the first render."""
    both = ToolsConfig(ffmpeg=str(tmp_path / "nope"), ffprobe=str(tmp_path / "also-nope"))
    assert ffmpeg.missing_tools(both) == ["tools.ffmpeg", "tools.ffprobe"]
    with ffmpeg.using_tools(both), pytest.raises(ToolError, match="tools.ffmpeg"):
        ffmpeg.ffmpeg_paths()


def test_naming_both_halves_is_the_build_this_machine_renders_with(tmp_path):
    ff, fp = tmp_path / "ffmpeg", tmp_path / "ffprobe"
    ff.write_text("")
    fp.write_text("")
    tools = ToolsConfig(ffmpeg=str(ff), ffprobe=str(fp))
    assert ffmpeg.unpaired_tool(tools) == [] and ffmpeg.missing_tools(tools) == []
    with ffmpeg.using_tools(tools):
        assert ffmpeg.ffmpeg_paths() == (str(ff), str(fp))
        assert ffmpeg.installed_paths() == (str(ff), str(fp))
    # A run that bound nothing is back to the pinned build, so one run never decides another's.
    assert ffmpeg.bound_tools() == ToolsConfig()


def test_binding_the_tools_also_binds_where_a_fetch_is_kept(tmp_path):
    """One call says which ffmpeg a run renders with, and a cache directory is part of that answer."""
    with ffmpeg.using_tools(ToolsConfig(cache_dir=str(tmp_path / "elsewhere"))):
        assert cache_dir() == tmp_path / "elsewhere"
    assert cache_dir() != tmp_path / "elsewhere"


def test_a_concat_line_quotes_a_path_a_person_could_actually_write():
    r"""An apostrophe inside a single-quoted path ends the quoting, so it is written as `'\''`."""
    assert ffmpeg.concat_line("/films/a.mp4") == "file '/films/a.mp4'\n"
    assert ffmpeg.concat_line("/jacob's films/a.mp4") == "file '/jacob'\\''s films/a.mp4'\n"
    assert ffmpeg.concat_list([Path("a.mp4"), Path("b.mp4")]) == "file 'a.mp4'\nfile 'b.mp4'\n"


@pytest.mark.media
def test_ffmpeg_concatenates_files_under_a_directory_with_an_apostrophe_in_its_name(tmp_path):
    """The live defect: a build under `jacob's films/` died at the concatenation step."""
    films = tmp_path / "jacob's films"
    films.mkdir()
    parts = []
    for name in ("a", "b"):
        part = films / f"{name}.mp4"
        ffmpeg.run(
            "-f", "lavfi", "-i", "color=c=black:s=64x64:r=25:d=0.4",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", str(part),
        )  # fmt: skip
        parts.append(part)
    listing = films / "parts.txt"
    listing.write_text(ffmpeg.concat_list(parts), encoding="utf-8")
    joined = films / "joined.mp4"
    ffmpeg.run("-f", "concat", "-safe", "0", "-i", str(listing), "-c", "copy", str(joined))
    assert ffmpeg.probe_duration(joined) == pytest.approx(0.8, abs=0.1)
