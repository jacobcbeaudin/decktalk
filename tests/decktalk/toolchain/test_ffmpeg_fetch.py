"""The pinned ffmpeg build: its table, the verified download, the guarded unpack, and the fallbacks.

Nothing here touches the network. The archives are built in the test and served by a fake urlopen.
"""

from __future__ import annotations

import contextvars
import hashlib
import http.server
import io
import os
import re
import sys
import tarfile
import threading
import time
import urllib.error
import zipfile
from pathlib import Path

import pytest
from filelock import FileLock

from decktalk.errors import Cancel, Cancelled, ToolError
from decktalk.media import ffmpeg as ff
from decktalk.results import sized
from decktalk.settings import ToolsConfig
from decktalk.toolchain import ffmpeg_fetch as fetch
from decktalk.toolchain.announce import announcing
from support.logs import data_of

HEX64 = re.compile(r"^[0-9a-f]{64}$")
WAIT = ToolsConfig().timeout_seconds
"""How long a fetch in these tests waits for another, which is the machine's default limit."""
PLATFORMS = ("linux-x86_64", "linux-arm64", "darwin-x86_64", "darwin-arm64", "win32-x86_64")


class Response(io.BytesIO):
    """What urlopen hands back, including the length header a download reads to say how far it is."""

    def __init__(self, data: bytes) -> None:
        super().__init__(data)
        self.headers = {"Content-Length": str(len(data))}


def serve(monkeypatch, archives: dict[str, bytes]) -> list[str]:
    """Answer urlopen from a dict of url -> bytes, recording the URLs asked for."""
    asked: list[str] = []

    def urlopen(request, timeout):  # noqa: ARG001  (urlopen's own signature, so a missing timeout shows)
        url = request.full_url
        assert request.get_header("User-agent") == fetch.USER_AGENT
        asked.append(url)
        if url not in archives:
            raise urllib.error.URLError(f"no route to {url}")
        return Response(archives[url])

    monkeypatch.setattr(fetch.urllib.request, "urlopen", urlopen)
    return asked


def zip_bytes(members: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return buf.getvalue()


def tar_xz_bytes(members: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:xz") as tf:
        for name, data in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def pin(monkeypatch, key: str, build: fetch.FfmpegBuild) -> None:
    monkeypatch.setitem(fetch.FFMPEG_BUILDS, key, build)
    monkeypatch.setattr(fetch, "platform_key", lambda: key)


def pinned(
    monkeypatch, key: str, archive: bytes, *, suffix: str = ".tar.xz", bin_dir: str = "bin/", sha256: str | None = None
) -> list[str]:
    """A pinned build of one archive at `https://example.test/<key><suffix>`, served, and the URLs asked for.

    The digest is the archive's own unless a test names another.
    """
    url = f"https://example.test/{key}{suffix}"
    digest = sha256 or hashlib.sha256(archive).hexdigest()
    asset = fetch.FfmpegAsset(url=url, sha256=digest, bin_dir=bin_dir)
    pin(monkeypatch, key, fetch.FfmpegBuild(builder="test", license="GPL-3.0-or-later", assets=(asset,)))
    return serve(monkeypatch, {url: archive})


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """A machine with nothing of its own: an empty cache, no build on PATH, and no resolution kept."""
    monkeypatch.setattr(ff.shutil, "which", lambda name: None)
    with ff.using_tools(ToolsConfig(cache_dir=str(tmp_path / "cache"))):
        yield


def is_lock(path: Path) -> bool:
    """Whether a file is the lock two fetches take turns under, which stays beside the build on purpose."""
    return path.name.endswith(".lock")


def scratch_left(dest: Path) -> list[str]:
    """Every scratch directory a fetch left beside the install directory, which must be none."""
    return [p.name for p in dest.parent.iterdir() if p.is_dir() and p != dest]


# ---- the table ---------------------------------------------------------------------------------


def test_the_table_pins_one_version_for_every_platform_with_a_digest_and_a_fixed_url():
    assert set(fetch.FFMPEG_BUILDS) == set(PLATFORMS)
    for key, build in fetch.FFMPEG_BUILDS.items():
        assert build.license.startswith("GPL"), key
        names = [name for asset in build.assets for name in asset.binaries]
        assert sorted(names) == ["ffmpeg", "ffprobe"], key
        for asset in build.assets:
            assert asset.url.startswith("https://"), asset.url
            assert "latest" not in asset.url, asset.url
            assert HEX64.match(asset.sha256), asset.url
            assert asset.url.endswith((".zip", ".tar.xz")), asset.url
            assert asset.bin_dir == "" or asset.bin_dir.endswith("/"), asset.url


def test_platform_key_normalizes_the_machine_name(monkeypatch):
    for machine, want in (("x86_64", "x86_64"), ("AMD64", "x86_64"), ("arm64", "arm64"), ("aarch64", "arm64")):
        monkeypatch.setattr(fetch.platform, "machine", lambda m=machine: m)
        assert fetch.platform_key() == f"{sys.platform}-{want}"


def test_install_dir_sits_under_the_decktalk_cache(tmp_path):
    assert fetch.install_dir("linux-arm64") == tmp_path / "cache" / "ffmpeg" / f"{fetch.FFMPEG_VERSION}-linux-arm64"


# ---- the fetch ---------------------------------------------------------------------------------


def test_fetch_verifies_each_archive_and_installs_both_executables(monkeypatch):
    tar = tar_xz_bytes(
        {"build/bin/ffmpeg": b"#!/bin/sh\necho ffmpeg\n", "build/bin/ffprobe": b"#!/bin/sh\necho ffprobe\n"}
    )
    asked = pinned(monkeypatch, "test-one", tar, bin_dir="build/bin/")
    assert fetch.installed_pinned() is None
    paths = fetch.fetch_ffmpeg(wait_seconds=WAIT)
    assert asked == ["https://example.test/test-one.tar.xz"]
    dest = fetch.install_dir("test-one")
    assert paths == (str(dest / fetch._exe("ffmpeg")), str(dest / fetch._exe("ffprobe")))
    assert sorted(p.name for p in dest.iterdir()) == sorted(fetch._exe(n) for n in ("ffmpeg", "ffprobe"))
    assert (dest / fetch._exe("ffprobe")).read_bytes() == b"#!/bin/sh\necho ffprobe\n"
    if sys.platform != "win32":
        assert (dest / "ffmpeg").stat().st_mode & 0o111 == 0o111
    assert fetch.installed_pinned() == paths
    assert scratch_left(dest) == []


def test_fetch_takes_one_executable_per_archive_from_zip_roots(monkeypatch):
    a, b = zip_bytes({fetch._exe("ffmpeg"): b"A"}), zip_bytes({fetch._exe("ffprobe"): b"B"})
    first = fetch.FfmpegAsset("https://example.test/ffmpeg.zip", hashlib.sha256(a).hexdigest(), binaries=("ffmpeg",))
    second = fetch.FfmpegAsset("https://example.test/ffprobe.zip", hashlib.sha256(b).hexdigest(), binaries=("ffprobe",))
    build = fetch.FfmpegBuild(builder="test", license="GPL-3.0-or-later", assets=(first, second))
    pin(monkeypatch, "test-two", build)
    serve(monkeypatch, {"https://example.test/ffmpeg.zip": a, "https://example.test/ffprobe.zip": b})
    ffm, ffp = fetch.fetch_ffmpeg(wait_seconds=WAIT)
    assert Path(ffm).read_bytes() == b"A" and Path(ffp).read_bytes() == b"B"


def test_a_digest_mismatch_discards_the_download_and_installs_nothing(tmp_path, monkeypatch):
    tar = tar_xz_bytes({"bin/ffmpeg": b"x", "bin/ffprobe": b"y"})
    pinned(monkeypatch, "test-bad", tar, sha256="0" * 64)
    with pytest.raises(ToolError, match="does not match the SHA-256"):
        fetch.fetch_ffmpeg(wait_seconds=WAIT)
    cache = tmp_path / "cache"
    assert not fetch.install_dir().exists()
    assert [p for p in cache.rglob("*") if p.is_file() and not is_lock(p)] == []
    # A mismatch is the one failure that never falls back to PATH, even when one exists.
    monkeypatch.setattr(ff.shutil, "which", lambda name: f"/usr/bin/{name}")
    with pytest.raises(ToolError, match="does not match the SHA-256"):
        ff.ffmpeg_paths()


def test_an_oversized_archive_is_refused_before_it_is_read_to_the_end(monkeypatch):
    monkeypatch.setattr(fetch, "MAX_ARCHIVE_BYTES", 10)
    pinned(monkeypatch, "test-huge", b"\0" * 64, suffix=".zip", bin_dir="", sha256="0" * 64)
    with pytest.raises(ToolError, match="larger than 10 bytes"):
        fetch.fetch_ffmpeg(wait_seconds=WAIT)
    assert not fetch.install_dir().exists()


def test_only_the_named_members_leave_the_archive(tmp_path, monkeypatch):
    """A member that names a path outside the install directory is never written anywhere."""
    exe = fetch._exe
    archive = zip_bytes({
        f"bin/{exe('ffmpeg')}": b"real", f"bin/{exe('ffprobe')}": b"real",
        "../escaped": b"evil", "bin/../../escaped2": b"evil", "/abs/escaped3": b"evil", "bin/extra.txt": b"noise",
    })  # fmt: skip
    pinned(monkeypatch, "test-escape", archive, suffix=".zip")
    fetch.fetch_ffmpeg(wait_seconds=WAIT)
    written = sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*") if p.is_file() and not is_lock(p))
    prefix = f"cache/ffmpeg/{fetch.FFMPEG_VERSION}-test-escape/"
    assert written == [f"{prefix}{exe('ffmpeg')}", f"{prefix}{exe('ffprobe')}"]


def test_an_archive_without_the_executable_is_a_tool_error(monkeypatch):
    archive = zip_bytes({"bin/README": b"no binaries here"})
    pinned(monkeypatch, "test-empty", archive, suffix=".zip")
    with pytest.raises(ToolError, match="holds no file bin/ffmpeg"):
        fetch.fetch_ffmpeg(wait_seconds=WAIT)
    assert not fetch.install_dir().exists()


# ---- resolution --------------------------------------------------------------------------------


def test_the_machines_own_build_wins_and_fetches_nothing(tmp_path, monkeypatch):
    ffmpeg, ffprobe = tmp_path / "ffmpeg", tmp_path / "ffprobe"
    for tool in (ffmpeg, ffprobe):
        tool.write_bytes(b"")
    monkeypatch.setattr(fetch, "fetch_ffmpeg", lambda key=None, **_: pytest.fail("must not fetch"))
    with ff.using_tools(ToolsConfig(ffmpeg=str(ffmpeg), ffprobe=str(ffprobe))):
        assert ff.ffmpeg_paths() == (str(ffmpeg), str(ffprobe))
        assert ff.installed_paths() == (str(ffmpeg), str(ffprobe))


def test_an_installed_build_is_used_without_a_fetch(monkeypatch):
    pin(monkeypatch, "test-installed", fetch.FFMPEG_BUILDS["linux-x86_64"])
    d = fetch.install_dir()
    d.mkdir(parents=True)
    for name in ("ffmpeg", "ffprobe"):
        (d / fetch._exe(name)).write_bytes(b"")
    monkeypatch.setattr(fetch, "fetch_ffmpeg", lambda key=None, **_: pytest.fail("must not fetch"))
    want = (str(d / fetch._exe("ffmpeg")), str(d / fetch._exe("ffprobe")))
    assert ff.ffmpeg_paths() == want
    assert ff.installed_paths() == want


def test_path_is_the_fallback_when_the_download_cannot_run(monkeypatch, caplog):
    pin(monkeypatch, "test-offline", fetch.FFMPEG_BUILDS["linux-x86_64"])
    serve(monkeypatch, {})
    monkeypatch.setattr(ff.shutil, "which", lambda name: f"/usr/bin/{name}")
    with caplog.at_level("WARNING", logger="decktalk.media.ffmpeg"):
        assert ff.ffmpeg_paths() == ("/usr/bin/ffmpeg", "/usr/bin/ffprobe")
    assert any("could not download the pinned ffmpeg" in r.getMessage() for r in caplog.records)


def test_no_download_and_no_path_is_a_tool_error_that_names_setup(monkeypatch):
    pin(monkeypatch, "test-offline", fetch.FFMPEG_BUILDS["linux-x86_64"])
    serve(monkeypatch, {})
    with pytest.raises(ToolError, match="decktalk install"):
        ff.ffmpeg_paths()


def test_a_run_resolves_its_pair_once_and_the_next_run_resolves_its_own(tmp_path, monkeypatch):
    """A process cache keyed on the settings answered a second machine with the first one's ffmpeg."""
    pin(monkeypatch, "test-once", fetch.FFMPEG_BUILDS["linux-x86_64"])
    asked: list[str] = []
    monkeypatch.setattr(ff.shutil, "which", lambda name: asked.append(name) or f"/first/{name}")
    monkeypatch.setattr(fetch, "fetch_ffmpeg", lambda key=None, **_: (_ for _ in ()).throw(OSError("offline")))
    with ff.using_tools(ToolsConfig(cache_dir=str(tmp_path / "one"))):
        assert ff.ffmpeg_paths() == ff.ffmpeg_paths() == ("/first/ffmpeg", "/first/ffprobe")
    assert asked == ["ffmpeg", "ffprobe"]
    monkeypatch.setattr(ff.shutil, "which", lambda name: f"/second/{name}")
    with ff.using_tools(ToolsConfig(cache_dir=str(tmp_path / "one"))):
        assert ff.ffmpeg_paths() == ("/second/ffmpeg", "/second/ffprobe")


def test_a_pair_the_machine_already_holds_is_used_without_resolving_anything(tmp_path, monkeypatch):
    monkeypatch.setattr(ff, "_resolve", lambda tools: pytest.fail("must not resolve"))
    held = (tmp_path / "ffmpeg", tmp_path / "ffprobe")
    with ff.using_tools(ToolsConfig(), paths=held):
        assert ff.ffmpeg_paths() == (str(held[0]), str(held[1]))


def test_an_unpinned_platform_uses_path_or_says_so(monkeypatch):
    monkeypatch.setattr(fetch, "platform_key", lambda: "plan9-mips")
    with pytest.raises(ToolError, match="pins no build for plan9-mips"):
        ff.ffmpeg_paths()
    monkeypatch.setattr(ff.shutil, "which", lambda name: f"/usr/bin/{name}")
    assert ff.ffmpeg_paths() == ("/usr/bin/ffmpeg", "/usr/bin/ffprobe")


def pinned_tar(monkeypatch, key: str) -> bytes:
    """A pinned build of one archive, served and ready to fetch, and the bytes the host will answer with."""
    tar = tar_xz_bytes({"bin/ffmpeg": b"#!/bin/sh\n", "bin/ffprobe": b"#!/bin/sh\n"})
    pinned(monkeypatch, key, tar)
    return tar


def test_a_download_says_what_is_arriving_and_how_much_of_it(monkeypatch):
    """A run that stops for a few hundred megabytes says so as it happens, through the one seam it has."""
    tar = pinned_tar(monkeypatch, "test-announce")
    heard: list[tuple[str, int, int | None]] = []
    with announcing(lambda tool, done_bytes, total_bytes: heard.append((tool, done_bytes, total_bytes))):
        fetch.fetch_ffmpeg(wait_seconds=WAIT)
    assert heard[0] == (fetch.TOOL, 0, len(tar)), heard
    assert heard[-1] == (fetch.TOOL, len(tar), len(tar)), heard


def test_a_download_whose_host_states_no_size_still_ends_with_the_size_that_arrived(monkeypatch):
    """A reader closes a download on the line that counts all of it, so a host that states no size
    must not leave the download open: its last line states the size that arrived."""
    tar = pinned_tar(monkeypatch, "test-unsized")
    monkeypatch.setattr(fetch, "_content_length", lambda _resp: None)
    heard: list[tuple[str, int, int | None]] = []
    with announcing(lambda tool, done_bytes, total_bytes: heard.append((tool, done_bytes, total_bytes))):
        fetch.fetch_ffmpeg(wait_seconds=WAIT)
    assert heard[0] == (fetch.TOOL, 0, None), heard
    assert heard[-1] == (fetch.TOOL, len(tar), len(tar)), heard


def test_each_archive_of_a_build_is_announced_under_the_executable_it_holds(monkeypatch):
    """macOS builds come as two archives, and two downloads both called ffmpeg read as one fetched twice."""
    a, b = zip_bytes({fetch._exe("ffmpeg"): b"A"}), zip_bytes({fetch._exe("ffprobe"): b"BB"})
    first = fetch.FfmpegAsset("https://example.test/ffmpeg.zip", hashlib.sha256(a).hexdigest(), binaries=("ffmpeg",))
    second = fetch.FfmpegAsset("https://example.test/ffprobe.zip", hashlib.sha256(b).hexdigest(), binaries=("ffprobe",))
    pin(
        monkeypatch, "test-named", fetch.FfmpegBuild(builder="test", license="GPL-3.0-or-later", assets=(first, second))
    )
    serve(monkeypatch, {"https://example.test/ffmpeg.zip": a, "https://example.test/ffprobe.zip": b})
    heard: list[tuple[str, int, int | None]] = []
    with announcing(lambda tool, done_bytes, total_bytes: heard.append((tool, done_bytes, total_bytes))):
        fetch.fetch_ffmpeg(wait_seconds=WAIT)
    assert [line for line in heard if line[1] == line[2]] == [("ffmpeg", len(a), len(a)), ("ffprobe", len(b), len(b))]


def test_a_download_nobody_is_listening_to_says_nothing_and_still_arrives(monkeypatch):
    """The listener is what a machine sets for a run, so a caller that sets none downloads as before."""
    pinned_tar(monkeypatch, "test-silent")
    assert fetch.fetch_ffmpeg(wait_seconds=WAIT) == fetch.installed_pinned()


def test_two_cold_fetches_take_turns_and_the_second_downloads_nothing(monkeypatch):
    """Two first builds in one process each get their own scratch directory, so neither deletes the other's download."""
    pinned_tar(monkeypatch, "test-race")
    real = fetch._download_verified
    downloads: list[str] = []
    both_started = threading.Barrier(2)

    def slow(asset, into):
        downloads.append(asset.url)
        time.sleep(0.05)
        return real(asset, into)

    monkeypatch.setattr(fetch, "_download_verified", slow)
    context = contextvars.copy_context()
    answers: list[tuple[str, str]] = []

    def one() -> None:
        both_started.wait()
        answers.append(context.copy().run(fetch.fetch_ffmpeg, wait_seconds=WAIT))

    threads = [threading.Thread(target=one) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(downloads) == 1
    assert answers[0] == answers[1] == fetch.installed_pinned()
    assert scratch_left(fetch.install_dir()) == []


def the_lock(key: str) -> FileLock:
    """A second holder of the lock a fetch of this build takes, as another process would hold it."""
    dest = fetch.install_dir(key)
    dest.parent.mkdir(parents=True, exist_ok=True)
    return FileLock(dest.with_name(f".{dest.name}.lock"))


def test_a_fetch_waiting_on_another_says_so_and_stops_when_the_run_is_cancelled(monkeypatch):
    """A run held behind a slow download on the same machine can still be stopped by its caller."""
    pinned_tar(monkeypatch, "test-held")
    cancel = Cancel()
    heard: list[tuple[str, int, int | None]] = []
    with the_lock("test-held"):
        stopper = threading.Timer(0.2, cancel.cancel)
        stopper.start()
        started = time.monotonic()
        with (
            announcing(lambda tool, done_bytes, total_bytes: heard.append((tool, done_bytes, total_bytes))),
            pytest.raises(Cancelled),
        ):
            fetch.fetch_ffmpeg(cancel=cancel, wait_seconds=WAIT)
        stopper.join()
    assert time.monotonic() - started < 2
    assert heard == [(fetch.TOOL, 0, None)]
    assert fetch.installed_pinned() is None


def test_a_fetch_waiting_on_another_gives_up_after_the_tools_timeout(monkeypatch):
    """A holder that is alive and wedged holds a waiting run for no longer than the machine allows."""
    pinned_tar(monkeypatch, "test-wedged")
    with the_lock("test-wedged"), pytest.raises(ToolError, match="tools.timeout_seconds"):
        fetch.fetch_ffmpeg(wait_seconds=0.3)
    assert fetch.installed_pinned() is None


def test_a_fetch_that_waited_says_how_long_and_what_it_found(monkeypatch, caplog):
    """A run that downloaded nothing because another had just installed the build says so."""
    pinned_tar(monkeypatch, "test-waited")
    held = threading.Event()

    def hold() -> None:
        # The lock belongs to the thread that took it, so the same thread gives it back.
        with the_lock("test-waited"):
            held.set()
            time.sleep(0.3)

    holder = threading.Thread(target=contextvars.copy_context().run, args=(hold,))
    holder.start()
    held.wait(timeout=5)
    with caplog.at_level("DEBUG", logger="decktalk"):
        fetch.fetch_ffmpeg(wait_seconds=5)
    holder.join()
    waited = [data_of(record) for record in caplog.records if "waited_seconds" in data_of(record)]
    assert len(waited) == 1 and waited[0]["waited_seconds"] >= 0.2
    assert waited[0]["found_installed"] is False
    verified = [data_of(record) for record in caplog.records if "sha256" in data_of(record)]
    assert verified and verified[0]["bytes"] > 0 and verified[0]["url"].startswith("https://")


# ---- a slow or broken host ---------------------------------------------------------------------


class Host:
    """A local HTTP host for one archive, which can trickle it, drop the connection or stall partway, and
    can honour a Range request or ignore it. Every request's Range header is recorded with the bytes sent.

    `drop_after` and `stall_after` break only the first answer, which is what a link that came back does.
    """

    PATH = "/archive.tar.xz"

    def __init__(self, data: bytes, *, ranges: bool = True) -> None:
        self.data = data
        self.ranges = ranges
        self.piece = len(data)
        self.pause = 0.0
        self.drop_after: int | None = None
        self.stall_after: int | None = None
        self.stall_seconds = 0.0
        self.asked: list[str | None] = []
        self.sent: list[int] = []
        host = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: object) -> None:  # a test host is quiet
                pass

            def do_GET(self) -> None:
                host.answer(self)

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_address[1]}{self.PATH}"

    def answer(self, handler: http.server.BaseHTTPRequestHandler) -> None:
        asked = handler.headers.get("Range")
        self.asked.append(asked)
        start = int(asked.removeprefix("bytes=").rstrip("-")) if asked and self.ranges else 0
        body = self.data[start:]
        handler.send_response(206 if start else 200)
        handler.send_header("Content-Length", str(len(body)))
        if self.ranges:
            handler.send_header("Accept-Ranges", "bytes")
        if start:
            handler.send_header("Content-Range", f"bytes {start}-{len(self.data) - 1}/{len(self.data)}")
        handler.end_headers()
        drop, stall = self.drop_after, self.stall_after
        self.drop_after = self.stall_after = None
        sent = 0
        for at in range(0, len(body), self.piece):
            if drop is not None and sent >= drop:
                break
            if stall is not None and sent >= stall:
                time.sleep(self.stall_seconds)
                break
            piece = body[at : at + self.piece]
            handler.wfile.write(piece)
            handler.wfile.flush()
            sent += len(piece)
            time.sleep(self.pause)
        self.sent.append(sent)

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def host(monkeypatch):
    """A pinned one-archive build served from a local host, whose archive is large enough to arrive in pieces."""
    tar = tar_xz_bytes({"bin/ffmpeg": os.urandom(64 * 1024), "bin/ffprobe": b"#!/bin/sh\n"})
    served = Host(tar)
    asset = fetch.FfmpegAsset(url=served.url, sha256=hashlib.sha256(tar).hexdigest(), bin_dir="bin/")
    pin(monkeypatch, "test-host", fetch.FfmpegBuild(builder="test", license="GPL-3.0-or-later", assets=(asset,)))
    yield served
    served.close()


def test_a_slow_download_is_announced_by_the_clock_and_not_only_by_the_bytes(host, monkeypatch):
    """A link far slower than the byte step still says how far it got at every announce interval."""
    monkeypatch.setattr(fetch, "ANNOUNCE_SECONDS", 0.05)
    host.piece, host.pause = len(host.data) // 8 + 1, 0.1
    heard: list[tuple[str, int, int | None]] = []
    with announcing(lambda tool, done_bytes, total_bytes: heard.append((tool, done_bytes, total_bytes))):
        fetch.fetch_ffmpeg(wait_seconds=WAIT)
    total = len(host.data)
    between = [done for _tool, done, _total in heard[1:-1]]
    assert heard[0] == (fetch.TOOL, 0, total) and heard[-1] == (fetch.TOOL, total, total), heard
    assert len(between) >= 4 and between == sorted(between) and all(0 < done <= total for done in between), heard


def test_a_dropped_download_keeps_what_arrived_and_the_next_fetch_asks_for_the_rest(host):
    """The partial archive stays beside its final name, and the next fetch resumes it with a Range request."""
    host.piece, host.drop_after = 4096, len(host.data) // 2
    with pytest.raises(OSError, match="next run resumes") as stopped:
        fetch.fetch_ffmpeg(wait_seconds=WAIT)
    # The sizes are said in the unit every progress line speaks, never as a raw count of bytes.
    assert f"of {sized(len(host.data))} (" in str(stopped.value) and " bytes" not in str(stopped.value), stopped.value
    kept = [p for p in fetch.install_dir().parent.rglob("*.part")]
    assert len(kept) == 1 and 0 < kept[0].stat().st_size < len(host.data)
    have = kept[0].stat().st_size
    heard: list[tuple[str, int, int | None]] = []
    with announcing(lambda tool, done_bytes, total_bytes: heard.append((tool, done_bytes, total_bytes))):
        assert fetch.fetch_ffmpeg(wait_seconds=WAIT) == fetch.installed_pinned()
    assert host.asked == [None, f"bytes={have}-"]
    assert host.sent[-1] == len(host.data) - have
    assert heard[0] == (fetch.TOOL, have, len(host.data)), heard
    assert list(fetch.install_dir().parent.rglob("*.part")) == []
    assert scratch_left(fetch.install_dir()) == []


def test_a_download_that_times_out_says_the_next_run_resumes(host, monkeypatch):
    """A host that stops answering ends the run with what to expect next, and through the media layer too."""
    monkeypatch.setattr(fetch, "DOWNLOAD_TIMEOUT_SECONDS", 0.2)
    host.piece, host.stall_after, host.stall_seconds = 4096, len(host.data) // 2, 1.0
    with pytest.raises(ToolError, match="next run resumes"):
        ff.ffmpeg_paths()
    assert len(list(fetch.install_dir().parent.rglob("*.part"))) == 1


def test_a_host_without_ranges_sends_the_whole_archive_again_and_it_still_verifies(host):
    """A host that answers a Range request with the whole archive is read from the start, never appended."""
    host.ranges = False
    host.piece, host.drop_after = 4096, len(host.data) // 2
    with pytest.raises(OSError, match="next run resumes"):
        fetch.fetch_ffmpeg(wait_seconds=WAIT)
    assert fetch.fetch_ffmpeg(wait_seconds=WAIT) == fetch.installed_pinned()
    assert host.asked[0] is None and host.asked[1] is not None
    assert host.sent[-1] == len(host.data)


@pytest.mark.usefixtures("host")
def test_a_partial_that_no_longer_matches_is_discarded_by_the_digest():
    """The pinned digest covers the whole file, so a partial with wrong bytes fails and is not kept."""
    dest = fetch.install_dir()
    downloads = dest.with_name(f".{dest.name}.download")
    downloads.mkdir(parents=True)
    (downloads / "archive.tar.xz.part").write_bytes(b"\0" * 1024)
    with pytest.raises(ToolError, match="does not match the SHA-256"):
        fetch.fetch_ffmpeg(wait_seconds=WAIT)
    assert list(dest.parent.rglob("*.part")) == []
    assert fetch.fetch_ffmpeg(wait_seconds=WAIT) == fetch.installed_pinned()
