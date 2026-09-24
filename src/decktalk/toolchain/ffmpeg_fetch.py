"""The pinned ffmpeg build: one fixed URL per platform, verified against its SHA-256 before it is opened.

`decktalk install` downloads one pinned GPL build of ffmpeg and ffprobe for this machine's platform
from the fixed URL in FFMPEG_BUILDS, checks each archive against the digest recorded there before
anything is unpacked, and keeps the two executables under DeckTalk's per-user cache. The same fetch
runs the first time a render needs ffmpeg, so every machine renders with the same build. The binary
is downloaded, never distributed in the wheel, and THIRD_PARTY_NOTICES.md names each build and its
licence.
"""

from __future__ import annotations

import hashlib
import logging
import platform
import shutil
import stat
import sys
import tarfile
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path

from ..errors import ToolError
from .announce import announce
from .cache import cache_dir

log = logging.getLogger(__name__)

TOOL = "ffmpeg"
"""What a `fetch` line calls this download, which is the name `doctor` and `install` print too."""

ANNOUNCE_STEP_BYTES = 4 * 1024 * 1024
"""Calibration: often enough that a download looks alive, and rare enough to cost a run nothing."""

FFMPEG_VERSION = "8.1.2"
MAX_ARCHIVE_BYTES = 400 * 1024 * 1024
"""Calibration: well over the largest pinned archive at 169 MB, so only a wrong answer grows past it."""
# One host refuses urllib's default User-Agent, so every fetch names itself.
USER_AGENT = "decktalk"
CHUNK_BYTES = 1 << 20
"""Truth: a megabyte at a time, which is large enough that hashing keeps up with the socket."""

DOWNLOAD_TIMEOUT_SECONDS = 60
"""Calibration: longer than any read of a healthy host takes, so only one that stopped answering hits it."""
_BTBN = "https://github.com/BtbN/FFmpeg-Builds/releases/download/autobuild-2026-08-31-13-27"
_BTBN_DIR = "ffmpeg-n8.1.2-50-g1a748fe2cd"
_EVERMEET = "https://evermeet.cx/ffmpeg"
_RIEDL = "https://ffmpeg.martin-riedl.de/download/macos/arm64/1783011502_8.1.2"


@dataclass(frozen=True)
class FfmpegAsset:
    """One archive of a pinned build: its fixed URL, its SHA-256, and where the executables sit inside it."""

    url: str
    sha256: str
    binaries: tuple[str, ...] = ("ffmpeg", "ffprobe")  # the executables this archive holds, without .exe
    bin_dir: str = ""  # the archive directory that holds them, with its trailing slash. "" is the root.


@dataclass(frozen=True)
class FfmpegBuild:
    """The pinned build for one platform: who built it, its licence, and its archives."""

    builder: str
    license: str
    assets: tuple[FfmpegAsset, ...]


# One row per platform key (see platform_key). Every URL is a versioned path that its host keeps,
# never a "latest" alias, and every digest was computed from the archive as downloaded on
# 2026-09-17. A new version changes the URL, the digest and the THIRD_PARTY_NOTICES.md row together.
# 8.1.2 is pinned rather than 9.0.1 because the 9.0.1 libmp3lame encoder refuses the padded frames
# that the narration join pads with silence ("inadequate AVFrame plane padding"), on every platform's build.
FFMPEG_BUILDS: dict[str, FfmpegBuild] = {
    "linux-x86_64": FfmpegBuild(
        builder="BtbN/FFmpeg-Builds",
        license="GPL-3.0-or-later",
        assets=(
            FfmpegAsset(
                url=f"{_BTBN}/{_BTBN_DIR}-linux64-gpl-8.1.tar.xz",
                sha256="c733b4b2951e5957e15505f788b2c65a7a41b6da4b289e295852cc38079b4d2b",
                bin_dir=f"{_BTBN_DIR}-linux64-gpl-8.1/bin/",
            ),
        ),
    ),
    "linux-arm64": FfmpegBuild(
        builder="BtbN/FFmpeg-Builds",
        license="GPL-3.0-or-later",
        assets=(
            FfmpegAsset(
                url=f"{_BTBN}/{_BTBN_DIR}-linuxarm64-gpl-8.1.tar.xz",
                sha256="ae5da4f51b9052390f414005f8ab26c1eed1268f327cce7cb79aa076b29bd66e",
                bin_dir=f"{_BTBN_DIR}-linuxarm64-gpl-8.1/bin/",
            ),
        ),
    ),
    "darwin-x86_64": FfmpegBuild(
        builder="evermeet.cx",
        license="GPL-3.0-or-later",
        assets=(
            FfmpegAsset(
                url=f"{_EVERMEET}/ffmpeg-{FFMPEG_VERSION}.zip",
                sha256="e91df72a1ee7c26606f90dd2dd4dcccc6a75140ff9ea6fdd50faae828b82ba69",
                binaries=("ffmpeg",),
            ),
            FfmpegAsset(
                url=f"{_EVERMEET}/ffprobe-{FFMPEG_VERSION}.zip",
                sha256="399b93f0b9862f69767afa343e90c2f48d7e7958cadbb6deb76a012d0e3b7ce3",
                binaries=("ffprobe",),
            ),
        ),
    ),
    "darwin-arm64": FfmpegBuild(
        builder="ffmpeg.martin-riedl.de",
        license="GPL-3.0-or-later",
        assets=(
            FfmpegAsset(
                url=f"{_RIEDL}/ffmpeg.zip",
                sha256="ef1aa60006c7b77ce170c1608c08d8e4ba1c30c5746f2ac986ded932d0ac2c3c",
                binaries=("ffmpeg",),
            ),
            FfmpegAsset(
                url=f"{_RIEDL}/ffprobe.zip",
                sha256="c39787f4af7a3932502d2d48db6f6feaaa836b48a73ef78c32cc3285df61dfaf",
                binaries=("ffprobe",),
            ),
        ),
    ),
    "win32-x86_64": FfmpegBuild(
        builder="BtbN/FFmpeg-Builds",
        license="GPL-3.0-or-later",
        assets=(
            FfmpegAsset(
                url=f"{_BTBN}/{_BTBN_DIR}-win64-gpl-8.1.zip",
                sha256="273abb45f3f9f76c303e35ff39f5bb6c23c163ae65f6244a32b7d4a7f6cf0616",
                bin_dir=f"{_BTBN_DIR}-win64-gpl-8.1/bin/",
            ),
        ),
    ),
}

_ARCH = {"x86_64": "x86_64", "amd64": "x86_64", "arm64": "arm64", "aarch64": "arm64"}


def platform_key() -> str:
    """This machine's key into FFMPEG_BUILDS: `<os>-<arch>`, such as darwin-arm64 or win32-x86_64."""
    machine = platform.machine().lower()
    return f"{sys.platform}-{_ARCH.get(machine, machine)}"


def pinned_build(key: str | None = None) -> FfmpegBuild | None:
    """The pinned build for a platform, or None when DeckTalk pins none for it."""
    return FFMPEG_BUILDS.get(key or platform_key())


def install_dir(key: str | None = None) -> Path:
    """Where the pinned executables live: `<cache>/ffmpeg/<version>-<platform>`, beside the other tools."""
    return cache_dir() / "ffmpeg" / f"{FFMPEG_VERSION}-{key or platform_key()}"


def _exe(name: str) -> str:
    return f"{name}.exe" if sys.platform == "win32" else name


def installed_pinned(key: str | None = None) -> tuple[str, str] | None:
    """The (ffmpeg, ffprobe) pair of the pinned build when both are on disk, else None."""
    d = install_dir(key)
    ff, fp = d / _exe("ffmpeg"), d / _exe("ffprobe")
    if ff.is_file() and fp.is_file():
        return str(ff), str(fp)
    return None


def _content_length(resp: object) -> int | None:
    """How large the host says the archive is, or None when it did not say, which some hosts do not."""
    stated = getattr(resp, "headers", {}).get("Content-Length")
    return int(stated) if stated and stated.isdigit() else None


def _download_verified(asset: FfmpegAsset, into: Path) -> Path:
    """Stream the archive into `into`, hashing it as it arrives. A digest other than the pinned one is refused.

    The file is written under a temporary name and only takes its own name once the digest matches,
    so nothing that failed the check is ever left looking like an archive.
    """
    target = into / asset.url.rsplit("/", 1)[1]
    partial = target.with_name(f"{target.name}.part")
    digest = hashlib.sha256()
    total = 0
    request = urllib.request.Request(asset.url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=DOWNLOAD_TIMEOUT_SECONDS) as resp, partial.open("wb") as fh:
        expected = _content_length(resp)
        announce(TOOL, 0, expected)
        said = 0
        while chunk := resp.read(CHUNK_BYTES):
            total += len(chunk)
            if total > MAX_ARCHIVE_BYTES:
                raise ToolError(f"{asset.url} is larger than {MAX_ARCHIVE_BYTES} bytes, so the download was refused")
            digest.update(chunk)
            fh.write(chunk)
            if total - said >= ANNOUNCE_STEP_BYTES:
                announce(TOOL, total, expected)
                said = total
    announce(TOOL, total, expected)
    if digest.hexdigest() != asset.sha256:
        partial.unlink()
        raise ToolError(
            f"{asset.url} does not match the SHA-256 DeckTalk pins for it (expected {asset.sha256}, got "
            f"{digest.hexdigest()}), so the download was discarded. Nothing was installed. Set `[tools] ffmpeg` "
            "and `ffprobe` to a build of your own until the pin is updated."
        )
    partial.replace(target)
    return target


def _unpack(archive: Path, asset: FfmpegAsset, into: Path) -> None:
    """Copy the named executables out of the archive and mark them executable.

    Only the members the table names are read, and each is written to a file name chosen here, so no
    member path inside the archive decides where anything lands.
    """
    for name in asset.binaries:
        member, out = f"{asset.bin_dir}{_exe(name)}", into / _exe(name)
        try:
            with out.open("wb") as fh:
                if archive.suffix == ".zip":
                    with zipfile.ZipFile(archive) as zf, zf.open(member) as src:
                        shutil.copyfileobj(src, fh)
                else:
                    with tarfile.open(archive, "r:xz") as tf:
                        info = tf.getmember(member)
                        src = tf.extractfile(info) if info.isfile() else None
                        if src is None:
                            raise KeyError(member)
                        shutil.copyfileobj(src, fh)
        except KeyError as exc:
            raise ToolError(f"{archive.name} holds no file {member}, which the pinned build should contain") from exc
        out.chmod(out.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def fetch_ffmpeg(key: str | None = None) -> tuple[str, str]:
    """Download the pinned build for a platform into install_dir() and return its (ffmpeg, ffprobe).

    Every archive is verified before it is opened, the executables are unpacked into a temporary
    directory, and that directory replaces the install directory only once both are in place.
    """
    key = key or platform_key()
    build = pinned_build(key)
    if build is None:
        raise ToolError(
            f"DeckTalk pins no ffmpeg build for {key}. Install ffmpeg and ffprobe on PATH, or set "
            "`[tools] ffmpeg` and `ffprobe`."
        )
    dest = install_dir(key)
    tmp = dest.with_name(f".{dest.name}.tmp")
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    try:
        for asset in build.assets:
            archive = _download_verified(asset, tmp)
            _unpack(archive, asset, tmp)
            archive.unlink()
        shutil.rmtree(dest, ignore_errors=True)
        tmp.replace(dest)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return str(dest / _exe("ffmpeg")), str(dest / _exe("ffprobe"))
