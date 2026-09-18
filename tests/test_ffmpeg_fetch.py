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

HEX64 = re.compile(r"^[0-9a-f]{64}$")
PLATFORMS = ("linux-x86_64", "linux-arm64", "darwin-x86_64", "darwin-arm64", "win32-x86_64")


class Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def serve(monkeypatch, archives: dict[str, bytes]) -> list[str]:
    """Answer urlopen from a dict of url -> bytes, recording the URLs asked for."""
    asked: list[str] = []

    def urlopen(request, timeout):
        url = request.full_url
        assert request.get_header("User-agent") == ff.USER_AGENT
        asked.append(url)
        if url not in archives:
            raise urllib.error.URLError(f"no route to {url}")
        return Response(archives[url])

    monkeypatch.setattr(ff.urllib.request, "urlopen", urlopen)
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


def pin(monkeypatch, key: str, build: ff.FfmpegBuild) -> None:
    monkeypatch.setitem(ff.FFMPEG_BUILDS, key, build)
    monkeypatch.setattr(ff, "platform_key", lambda: key)


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("DECKTALK_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.delenv("DECKTALK_FFMPEG", raising=False)
    monkeypatch.delenv("DECKTALK_FFPROBE", raising=False)
    monkeypatch.setattr(ff.shutil, "which", lambda name: None)
    ff.ffmpeg_paths.cache_clear()
    yield
    ff.ffmpeg_paths.cache_clear()


# ---- the table ---------------------------------------------------------------------------------


def test_the_table_pins_one_version_for_every_platform_with_a_digest_and_a_fixed_url():
    assert set(ff.FFMPEG_BUILDS) == set(PLATFORMS)
    for key, build in ff.FFMPEG_BUILDS.items():
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
        monkeypatch.setattr(ff.platform, "machine", lambda m=machine: m)
        assert ff.platform_key() == f"{sys.platform}-{want}"


def test_install_dir_sits_under_the_decktalk_cache(tmp_path):
    assert ff.install_dir("linux-arm64") == tmp_path / "cache" / "ffmpeg" / f"{ff.FFMPEG_VERSION}-linux-arm64"


# ---- the fetch ---------------------------------------------------------------------------------


def test_fetch_verifies_each_archive_and_installs_both_executables(tmp_path, monkeypatch):
    tar = tar_xz_bytes(
        {"build/bin/ffmpeg": b"#!/bin/sh\necho ffmpeg\n", "build/bin/ffprobe": b"#!/bin/sh\necho ffprobe\n"}
    )
    url = "https://example.test/ffmpeg-9.tar.xz"
    build = ff.FfmpegBuild(
        builder="test", license="GPL-3.0-or-later",
        assets=(ff.FfmpegAsset(url=url, sha256=hashlib.sha256(tar).hexdigest(), bin_dir="build/bin/"),),
    )  # fmt: skip
    pin(monkeypatch, "test-one", build)
    asked = serve(monkeypatch, {url: tar})
    assert ff.installed_pinned() is None
    paths = ff.fetch_ffmpeg()
    assert asked == [url]
    dest = ff.install_dir("test-one")
    assert paths == (str(dest / ff._exe("ffmpeg")), str(dest / ff._exe("ffprobe")))
    assert sorted(p.name for p in dest.iterdir()) == sorted(ff._exe(n) for n in ("ffmpeg", "ffprobe"))
    assert (dest / ff._exe("ffprobe")).read_bytes() == b"#!/bin/sh\necho ffprobe\n"
    if sys.platform != "win32":
        assert (dest / "ffmpeg").stat().st_mode & 0o111 == 0o111
    assert ff.installed_pinned() == paths
    assert not dest.with_name(f".{dest.name}.tmp").exists()


def test_fetch_takes_one_executable_per_archive_from_zip_roots(tmp_path, monkeypatch):
    a, b = zip_bytes({ff._exe("ffmpeg"): b"A"}), zip_bytes({ff._exe("ffprobe"): b"B"})
    first = ff.FfmpegAsset("https://example.test/ffmpeg.zip", hashlib.sha256(a).hexdigest(), binaries=("ffmpeg",))
    second = ff.FfmpegAsset("https://example.test/ffprobe.zip", hashlib.sha256(b).hexdigest(), binaries=("ffprobe",))
    build = ff.FfmpegBuild(builder="test", license="GPL-3.0-or-later", assets=(first, second))
    pin(monkeypatch, "test-two", build)
    serve(monkeypatch, {"https://example.test/ffmpeg.zip": a, "https://example.test/ffprobe.zip": b})
    ffm, ffp = ff.fetch_ffmpeg()
    assert Path(ffm).read_bytes() == b"A" and Path(ffp).read_bytes() == b"B"


def test_a_digest_mismatch_discards_the_download_and_installs_nothing(tmp_path, monkeypatch):
    tar = tar_xz_bytes({"bin/ffmpeg": b"x", "bin/ffprobe": b"y"})
    url = "https://example.test/ffmpeg.tar.xz"
    build = ff.FfmpegBuild(
        builder="test", license="GPL-3.0-or-later",
        assets=(ff.FfmpegAsset(url=url, sha256="0" * 64, bin_dir="bin/"),),
    )  # fmt: skip
    pin(monkeypatch, "test-bad", build)
    serve(monkeypatch, {url: tar})
    with pytest.raises(ToolError, match="does not match the SHA-256"):
        ff.fetch_ffmpeg()
    cache = tmp_path / "cache"
    assert not ff.install_dir().exists()
    assert [p for p in cache.rglob("*") if p.is_file()] == []
    # A mismatch is the one failure that never falls back to PATH, even when one exists.
    monkeypatch.setattr(ff.shutil, "which", lambda name: f"/usr/bin/{name}")
    with pytest.raises(ToolError, match="does not match the SHA-256"):
        ff.ffmpeg_paths()


def test_an_oversized_archive_is_refused_before_it_is_read_to_the_end(tmp_path, monkeypatch):
    url = "https://example.test/huge.zip"
    build = ff.FfmpegBuild(
        builder="test", license="GPL-3.0-or-later",
        assets=(ff.FfmpegAsset(url=url, sha256="0" * 64),),
    )  # fmt: skip
    pin(monkeypatch, "test-huge", build)
    monkeypatch.setattr(ff, "MAX_ARCHIVE_BYTES", 10)
    serve(monkeypatch, {url: b"\0" * 64})
    with pytest.raises(ToolError, match="larger than 10 bytes"):
        ff.fetch_ffmpeg()
    assert not ff.install_dir().exists()


def test_only_the_named_members_leave_the_archive(tmp_path, monkeypatch):
    """A member that names a path outside the install directory is never written anywhere."""
    exe = ff._exe
    archive = zip_bytes({
        f"bin/{exe('ffmpeg')}": b"real", f"bin/{exe('ffprobe')}": b"real",
        "../escaped": b"evil", "bin/../../escaped2": b"evil", "/abs/escaped3": b"evil", "bin/extra.txt": b"noise",
    })  # fmt: skip
    url = "https://example.test/ffmpeg.zip"
    build = ff.FfmpegBuild(
        builder="test", license="GPL-3.0-or-later",
        assets=(ff.FfmpegAsset(url=url, sha256=hashlib.sha256(archive).hexdigest(), bin_dir="bin/"),),
    )  # fmt: skip
    pin(monkeypatch, "test-escape", build)
    serve(monkeypatch, {url: archive})
    ff.fetch_ffmpeg()
    written = sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*") if p.is_file())
    prefix = f"cache/ffmpeg/{ff.FFMPEG_VERSION}-test-escape/"
    assert written == [f"{prefix}{exe('ffmpeg')}", f"{prefix}{exe('ffprobe')}"]


def test_an_archive_without_the_executable_is_a_tool_error(tmp_path, monkeypatch):
    archive = zip_bytes({"bin/README": b"no binaries here"})
    url = "https://example.test/ffmpeg.zip"
    build = ff.FfmpegBuild(
        builder="test", license="GPL-3.0-or-later",
        assets=(ff.FfmpegAsset(url=url, sha256=hashlib.sha256(archive).hexdigest(), bin_dir="bin/"),),
    )  # fmt: skip
    pin(monkeypatch, "test-empty", build)
    serve(monkeypatch, {url: archive})
    with pytest.raises(ToolError, match="holds no file bin/ffmpeg"):
        ff.fetch_ffmpeg()
    assert not ff.install_dir().exists()


# ---- resolution --------------------------------------------------------------------------------


def test_the_environment_override_wins_and_fetches_nothing(monkeypatch):
    monkeypatch.setenv("DECKTALK_FFMPEG", "/opt/ff/ffmpeg")
    monkeypatch.setenv("DECKTALK_FFPROBE", "/opt/ff/ffprobe")
    monkeypatch.setattr(ff, "fetch_ffmpeg", lambda key=None: pytest.fail("must not fetch"))
    assert ff.ffmpeg_paths() == ("/opt/ff/ffmpeg", "/opt/ff/ffprobe")
    assert ff.installed_paths() == ("/opt/ff/ffmpeg", "/opt/ff/ffprobe")


def test_an_installed_build_is_used_without_a_fetch(tmp_path, monkeypatch):
    pin(monkeypatch, "test-installed", ff.FFMPEG_BUILDS["linux-x86_64"])
    d = ff.install_dir()
    d.mkdir(parents=True)
    for name in ("ffmpeg", "ffprobe"):
        (d / ff._exe(name)).write_bytes(b"")
    monkeypatch.setattr(ff, "fetch_ffmpeg", lambda key=None: pytest.fail("must not fetch"))
    want = (str(d / ff._exe("ffmpeg")), str(d / ff._exe("ffprobe")))
    assert ff.ffmpeg_paths() == want
    assert ff.installed_paths() == want


def test_path_is_the_fallback_when_the_download_cannot_run(monkeypatch, caplog):
    pin(monkeypatch, "test-offline", ff.FFMPEG_BUILDS["linux-x86_64"])
    serve(monkeypatch, {})
    monkeypatch.setattr(ff.shutil, "which", lambda name: f"/usr/bin/{name}")
    with caplog.at_level("WARNING", logger="decktalk.media.ffmpeg"):
        assert ff.ffmpeg_paths() == ("/usr/bin/ffmpeg", "/usr/bin/ffprobe")
    assert any("could not download the pinned ffmpeg" in r.getMessage() for r in caplog.records)


def test_no_download_and_no_path_is_a_tool_error_that_names_setup(monkeypatch):
    pin(monkeypatch, "test-offline", ff.FFMPEG_BUILDS["linux-x86_64"])
    serve(monkeypatch, {})
    with pytest.raises(ToolError, match="decktalk setup"):
        ff.ffmpeg_paths()


def test_an_unpinned_platform_uses_path_or_says_so(monkeypatch):
    monkeypatch.setattr(ff, "platform_key", lambda: "plan9-mips")
    with pytest.raises(ToolError, match="pins no build for plan9-mips"):
        ff.ffmpeg_paths()
    ff.ffmpeg_paths.cache_clear()
    monkeypatch.setattr(ff.shutil, "which", lambda name: f"/usr/bin/{name}")
    assert ff.ffmpeg_paths() == ("/usr/bin/ffmpeg", "/usr/bin/ffprobe")


def test_doctor_reports_missing_ffmpeg_without_fetching(tmp_path, monkeypatch):
    from decktalk import scaffold

    monkeypatch.setattr(ff, "fetch_ffmpeg", lambda key=None: pytest.fail("doctor must not download ffmpeg"))
    monkeypatch.setitem(sys.modules, "playwright.sync_api", None)  # keeps the test free of Chromium
    rows = {name: (ok, detail) for name, ok, detail in scaffold.doctor()}
    assert rows["ffmpeg"] == (False, "not fetched yet and none on PATH  -> run `decktalk setup`")
    assert "ffprobe" not in rows
    # Executables already on disk are reported without fetching either.
    d = ff.install_dir()
    d.mkdir(parents=True)
    for name in ("ffmpeg", "ffprobe"):
        (d / ff._exe(name)).write_bytes(b"")
    rows = {name: (ok, detail) for name, ok, detail in scaffold.doctor()}
    assert rows["ffmpeg"] == (True, str(d / ff._exe("ffmpeg")))
    assert rows["ffprobe"] == (True, str(d / ff._exe("ffprobe")))
