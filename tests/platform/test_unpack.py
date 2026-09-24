"""The platform fact: the pinned ffmpeg arrives in a different archive on every platform, and runs anyway.

Windows gets one zip whose executables sit under a `bin/` directory inside it and are named with
`.exe`. macOS gets two zips whose single executable sits at the root. Linux gets one tar.xz with a
`bin/` directory. The download is faked in every other test, and a faked `urlopen` proves the URL and
never that what comes out of the archive is a file this machine can run.

So this file builds an archive of exactly the shape the table says this platform's build has, runs
the real unpack over it and then runs what comes out. The bytes inside are a shell stub rather than
ffmpeg, because what is under test is the unpack and the executable bit and not the encoder.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest

from decktalk.toolchain.cache import cache_dir
from decktalk.toolchain.ffmpeg_fetch import _exe, _unpack, install_dir, pinned_build, platform_key

pytestmark = pytest.mark.platform
"""Every test here is about this machine, so only the group that names this platform runs one."""


WINDOWS = os.name == "nt"
"""Whether this is the platform that names an executable with a suffix and cannot run a shell stub."""

STUB = "#!/bin/sh\necho stub\n"
"""What stands in for the encoder, which is the smallest file a machine can be asked to run."""


def build_archive(asset, into: Path) -> Path:
    """One archive of exactly the shape the pinned table says this platform's build arrives in."""
    if asset.url.endswith(".zip"):
        archive = into / "pinned.zip"
        with zipfile.ZipFile(archive, "w") as zf:
            for name in asset.binaries:
                zf.writestr(f"{asset.bin_dir}{_exe(name)}", STUB)
        return archive
    archive = into / "pinned.tar.xz"
    member = into / "member"
    member.write_text(STUB, encoding="utf-8")
    with tarfile.open(archive, "w:xz") as tf:
        for name in asset.binaries:
            tf.add(member, arcname=f"{asset.bin_dir}{_exe(name)}")
    return archive


def test_this_platform_has_a_pinned_build_at_all() -> None:
    """A platform with no row fetches nothing, so every other test here would be about nothing."""
    assert pinned_build() is not None, f"{platform_key()} has no pinned ffmpeg build."


def test_the_unpack_writes_an_executable_this_machine_can_run(tmp_path: Path) -> None:
    build = pinned_build()
    assert build is not None
    for asset in build.assets:
        source = tmp_path / "archive"
        source.mkdir(exist_ok=True)
        archive = build_archive(asset, source)
        out = tmp_path / "bin"
        out.mkdir(exist_ok=True)
        _unpack(archive, asset, out)
        for name in asset.binaries:
            landed = out / _exe(name)
            assert landed.is_file(), landed
            assert landed.name.endswith(".exe") == WINDOWS, "an executable is named the way this platform names one"
            if not WINDOWS:
                assert os.access(landed, os.X_OK), f"{landed} came out of the archive without the executable bit"
                assert (
                    subprocess.run([str(landed)], capture_output=True, text=True, check=True).stdout.strip() == "stub"
                )


def test_an_archive_that_holds_no_member_the_table_names_is_refused(tmp_path: Path) -> None:
    """A build whose layout moved is a tool error with the member named, never a silent empty file."""
    build = pinned_build()
    assert build is not None
    asset = build.assets[0]
    archive = tmp_path / "pinned.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("somewhere/else", STUB)
    out = tmp_path / "bin"
    out.mkdir()
    if not asset.url.endswith(".zip"):
        pytest.skip(f"{platform_key()} pins a tar.xz, whose missing-member path is the same branch")
    with pytest.raises(Exception, match="holds no file"):
        _unpack(archive, asset, out)


def test_the_cache_sits_where_this_platform_keeps_a_per_user_cache() -> None:
    """A tool fetched into the wrong place is fetched again on every run, once per platform."""
    root = cache_dir()
    assert root.name == "decktalk"
    assert install_dir().is_relative_to(root)
    if sys.platform == "darwin":
        assert root.parent == Path.home() / "Library" / "Caches"
    elif WINDOWS:
        assert root.parent.is_absolute()
    else:
        assert root.parent == Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
