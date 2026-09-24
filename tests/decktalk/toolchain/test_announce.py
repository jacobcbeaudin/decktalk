"""The seam a download reports through, which is what lets a layer below the events stream speak."""

from __future__ import annotations

import contextvars
import threading

from decktalk.toolchain.announce import announce, announcing


def heard_by(seen: list[tuple[str, int, int | None]]):
    """A listener of the shape the machine wires to the `fetch` event, which records what it hears."""
    return lambda tool, done_bytes, total_bytes: seen.append((tool, done_bytes, total_bytes))


def test_a_download_with_nobody_listening_says_nothing_and_raises_nothing():
    """A script that never opened a run still fetches its tools, so the default listener does nothing."""
    assert announce("ffmpeg", 0, None) is None


def test_the_listener_hears_every_line_while_its_context_is_open():
    seen: list[tuple[str, int, int | None]] = []
    with announcing(heard_by(seen)):
        announce("ffmpeg", 0, 1000)
        announce("ffmpeg", 1000, 1000)
    announce("ffmpeg", 0, None)
    assert seen == [("ffmpeg", 0, 1000), ("ffmpeg", 1000, 1000)]


def test_one_listener_inside_another_gives_the_first_one_back():
    outer: list[tuple[str, int, int | None]] = []
    inner: list[tuple[str, int, int | None]] = []
    with announcing(heard_by(outer)):
        with announcing(heard_by(inner)):
            announce("chromium", 0, None)
        announce("ffmpeg", 0, None)
    assert [tool for tool, _done, _total in inner] == ["chromium"]
    assert [tool for tool, _done, _total in outer] == ["ffmpeg"]


def test_a_thread_carries_the_listener_when_it_carries_the_context():
    """A thread starts in a fresh context, so a stage that fetches on one carries its run across."""
    seen: list[tuple[str, int, int | None]] = []
    with announcing(heard_by(seen)):
        bare = threading.Thread(target=announce, args=("ffmpeg", 5, 10))
        bare.start()
        bare.join()
        assert seen == [], "a bare thread heard a listener it was never given"
        carried = contextvars.copy_context()
        worker = threading.Thread(target=carried.run, args=(announce, "ffmpeg", 5, 10))
        worker.start()
        worker.join()
    assert seen == [("ffmpeg", 5, 10)]
