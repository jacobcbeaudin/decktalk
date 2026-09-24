"""The pinned ffmpeg build: its table, the verified download, the guarded unpack, and the fallbacks.

Nothing here touches the network. The archives are built in the test and served by a fake urlopen.
"""

from __future__ import annotations

import hashlib
import io
import re
import sys
import tarfile
import urllib.error
import zipfile
from pathlib import Path

import pytest

from decktalk.errors import ToolError
from decktalk.media import ffmpeg as ff
from decktalk.settings import ToolsConfig
from decktalk.toolchain import ffmpeg_fetch as fetch
from decktalk.toolchain.announce import announcing

HEX64 = re.compile(r"^[0-9a-f]{64}$")
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


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """A machine with nothing of its own: an empty cache, no build on PATH, and no resolution kept."""
    monkeypatch.setattr(ff.shutil, "which", lambda name: None)
    ff._resolve.cache_clear()
    with ff.using_tools(ToolsConfig(cache_dir=str(tmp_path / "cache"))):
        yield
    ff._resolve.cache_clear()


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
    url = "https://example.test/ffmpeg-9.tar.xz"
    build = fetch.FfmpegBuild(
        builder="test", license="GPL-3.0-or-later",
        assets=(fetch.FfmpegAsset(url=url, sha256=hashlib.sha256(tar).hexdigest(), bin_dir="build/bin/"),),
    )  # fmt: skip
    pin(monkeypatch, "test-one", build)
    asked = serve(monkeypatch, {url: tar})
    assert fetch.installed_pinned() is None
    paths = fetch.fetch_ffmpeg()
    assert asked == [url]
    dest = fetch.install_dir("test-one")
    assert paths == (str(dest / fetch._exe("ffmpeg")), str(dest / fetch._exe("ffprobe")))
    assert sorted(p.name for p in dest.iterdir()) == sorted(fetch._exe(n) for n in ("ffmpeg", "ffprobe"))
    assert (dest / fetch._exe("ffprobe")).read_bytes() == b"#!/bin/sh\necho ffprobe\n"
    if sys.platform != "win32":
        assert (dest / "ffmpeg").stat().st_mode & 0o111 == 0o111
    assert fetch.installed_pinned() == paths
    assert not dest.with_name(f".{dest.name}.tmp").exists()


def test_fetch_takes_one_executable_per_archive_from_zip_roots(monkeypatch):
    a, b = zip_bytes({fetch._exe("ffmpeg"): b"A"}), zip_bytes({fetch._exe("ffprobe"): b"B"})
    first = fetch.FfmpegAsset("https://example.test/ffmpeg.zip", hashlib.sha256(a).hexdigest(), binaries=("ffmpeg",))
    second = fetch.FfmpegAsset("https://example.test/ffprobe.zip", hashlib.sha256(b).hexdigest(), binaries=("ffprobe",))
    build = fetch.FfmpegBuild(builder="test", license="GPL-3.0-or-later", assets=(first, second))
    pin(monkeypatch, "test-two", build)
    serve(monkeypatch, {"https://example.test/ffmpeg.zip": a, "https://example.test/ffprobe.zip": b})
    ffm, ffp = fetch.fetch_ffmpeg()
    assert Path(ffm).read_bytes() == b"A" and Path(ffp).read_bytes() == b"B"


def test_a_digest_mismatch_discards_the_download_and_installs_nothing(tmp_path, monkeypatch):
    tar = tar_xz_bytes({"bin/ffmpeg": b"x", "bin/ffprobe": b"y"})
    url = "https://example.test/ffmpeg.tar.xz"
    build = fetch.FfmpegBuild(
        builder="test", license="GPL-3.0-or-later",
        assets=(fetch.FfmpegAsset(url=url, sha256="0" * 64, bin_dir="bin/"),),
    )  # fmt: skip
    pin(monkeypatch, "test-bad", build)
    serve(monkeypatch, {url: tar})
    with pytest.raises(ToolError, match="does not match the SHA-256"):
        fetch.fetch_ffmpeg()
    cache = tmp_path / "cache"
    assert not fetch.install_dir().exists()
    assert [p for p in cache.rglob("*") if p.is_file()] == []
    # A mismatch is the one failure that never falls back to PATH, even when one exists.
    monkeypatch.setattr(ff.shutil, "which", lambda name: f"/usr/bin/{name}")
    with pytest.raises(ToolError, match="does not match the SHA-256"):
        ff.ffmpeg_paths()


def test_an_oversized_archive_is_refused_before_it_is_read_to_the_end(monkeypatch):
    url = "https://example.test/huge.zip"
    build = fetch.FfmpegBuild(
        builder="test", license="GPL-3.0-or-later",
        assets=(fetch.FfmpegAsset(url=url, sha256="0" * 64),),
    )  # fmt: skip
    pin(monkeypatch, "test-huge", build)
    monkeypatch.setattr(fetch, "MAX_ARCHIVE_BYTES", 10)
    serve(monkeypatch, {url: b"\0" * 64})
    with pytest.raises(ToolError, match="larger than 10 bytes"):
        fetch.fetch_ffmpeg()
    assert not fetch.install_dir().exists()


def test_only_the_named_members_leave_the_archive(tmp_path, monkeypatch):
    """A member that names a path outside the install directory is never written anywhere."""
    exe = fetch._exe
    archive = zip_bytes({
        f"bin/{exe('ffmpeg')}": b"real", f"bin/{exe('ffprobe')}": b"real",
        "../escaped": b"evil", "bin/../../escaped2": b"evil", "/abs/escaped3": b"evil", "bin/extra.txt": b"noise",
    })  # fmt: skip
    url = "https://example.test/ffmpeg.zip"
    build = fetch.FfmpegBuild(
        builder="test", license="GPL-3.0-or-later",
        assets=(fetch.FfmpegAsset(url=url, sha256=hashlib.sha256(archive).hexdigest(), bin_dir="bin/"),),
    )  # fmt: skip
    pin(monkeypatch, "test-escape", build)
    serve(monkeypatch, {url: archive})
    fetch.fetch_ffmpeg()
    written = sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*") if p.is_file())
    prefix = f"cache/ffmpeg/{fetch.FFMPEG_VERSION}-test-escape/"
    assert written == [f"{prefix}{exe('ffmpeg')}", f"{prefix}{exe('ffprobe')}"]


def test_an_archive_without_the_executable_is_a_tool_error(monkeypatch):
    archive = zip_bytes({"bin/README": b"no binaries here"})
    url = "https://example.test/ffmpeg.zip"
    build = fetch.FfmpegBuild(
        builder="test", license="GPL-3.0-or-later",
        assets=(fetch.FfmpegAsset(url=url, sha256=hashlib.sha256(archive).hexdigest(), bin_dir="bin/"),),
    )  # fmt: skip
    pin(monkeypatch, "test-empty", build)
    serve(monkeypatch, {url: archive})
    with pytest.raises(ToolError, match="holds no file bin/ffmpeg"):
        fetch.fetch_ffmpeg()
    assert not fetch.install_dir().exists()


# ---- resolution --------------------------------------------------------------------------------


def test_the_machines_own_build_wins_and_fetches_nothing(tmp_path, monkeypatch):
    ffmpeg, ffprobe = tmp_path / "ffmpeg", tmp_path / "ffprobe"
    for tool in (ffmpeg, ffprobe):
        tool.write_bytes(b"")
    monkeypatch.setattr(fetch, "fetch_ffmpeg", lambda key=None: pytest.fail("must not fetch"))
    with ff.using_tools(ToolsConfig(ffmpeg=str(ffmpeg), ffprobe=str(ffprobe))):
        assert ff.ffmpeg_paths() == (str(ffmpeg), str(ffprobe))
        assert ff.installed_paths() == (str(ffmpeg), str(ffprobe))


def test_an_installed_build_is_used_without_a_fetch(monkeypatch):
    pin(monkeypatch, "test-installed", fetch.FFMPEG_BUILDS["linux-x86_64"])
    d = fetch.install_dir()
    d.mkdir(parents=True)
    for name in ("ffmpeg", "ffprobe"):
        (d / fetch._exe(name)).write_bytes(b"")
    monkeypatch.setattr(fetch, "fetch_ffmpeg", lambda key=None: pytest.fail("must not fetch"))
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


def test_an_unpinned_platform_uses_path_or_says_so(monkeypatch):
    monkeypatch.setattr(fetch, "platform_key", lambda: "plan9-mips")
    with pytest.raises(ToolError, match="pins no build for plan9-mips"):
        ff.ffmpeg_paths()
    ff._resolve.cache_clear()
    monkeypatch.setattr(ff.shutil, "which", lambda name: f"/usr/bin/{name}")
    assert ff.ffmpeg_paths() == ("/usr/bin/ffmpeg", "/usr/bin/ffprobe")


def pinned_tar(monkeypatch, key: str) -> bytes:
    """A pinned build of one archive, served and ready to fetch, and the bytes the host will answer with."""
    tar = tar_xz_bytes({"bin/ffmpeg": b"#!/bin/sh\n", "bin/ffprobe": b"#!/bin/sh\n"})
    url = f"https://example.test/{key}.tar.xz"
    build = fetch.FfmpegBuild(
        builder="test", license="GPL-3.0-or-later",
        assets=(fetch.FfmpegAsset(url=url, sha256=hashlib.sha256(tar).hexdigest(), bin_dir="bin/"),),
    )  # fmt: skip
    pin(monkeypatch, key, build)
    serve(monkeypatch, {url: tar})
    return tar


def test_a_download_says_what_is_arriving_and_how_much_of_it(monkeypatch):
    """A run that stops for a few hundred megabytes says so as it happens, through the one seam it has."""
    tar = pinned_tar(monkeypatch, "test-announce")
    heard: list[tuple[str, int, int | None]] = []
    with announcing(lambda tool, done_bytes, total_bytes: heard.append((tool, done_bytes, total_bytes))):
        fetch.fetch_ffmpeg()
    assert heard[0] == (fetch.TOOL, 0, len(tar)), heard
    assert heard[-1] == (fetch.TOOL, len(tar), len(tar)), heard


def test_a_download_nobody_is_listening_to_says_nothing_and_still_arrives(monkeypatch):
    """The listener is what a machine sets for a run, so a caller that sets none downloads as before."""
    pinned_tar(monkeypatch, "test-silent")
    assert fetch.fetch_ffmpeg() == fetch.installed_pinned()
