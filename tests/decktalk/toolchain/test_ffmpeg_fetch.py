"""The pinned ffmpeg build: its table, the verified download, the guarded unpack, and the fallbacks.

Nothing here touches the network. The archives are built in the test and served by a fake urlopen.
"""

from __future__ import annotations

import contextvars
import hashlib
import io
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

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


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


def test_a_download_nobody_is_listening_to_says_nothing_and_still_arrives(monkeypatch):
    """The listener is what a machine sets for a run, so a caller that sets none downloads as before."""
    pinned_tar(monkeypatch, "test-silent")
    assert fetch.fetch_ffmpeg(wait_seconds=WAIT) == fetch.installed_pinned()


def test_two_cold_fetches_take_turns_and_the_second_downloads_nothing(monkeypatch):
    """Two first builds in one process used to share one scratch directory and delete each other's download."""
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
    """A run that downloaded nothing because another had just installed the build used to say nothing."""
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
