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
import http.client
import logging
import platform
import re
import shutil
import stat
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass
from http import HTTPStatus
from pathlib import Path

from filelock import FileLock, Timeout

from ..errors import Cancel, ToolError
from ..results import sized
from .announce import announce
from .cache import cache_dir

log = logging.getLogger(__name__)

TOOL = "ffmpeg"
"""What a `fetch` line calls this download, which is the name `doctor` and `install` print too."""

ANNOUNCE_SECONDS = 1.0
"""Calibration: how long a download goes between two counts of what arrived, whatever the byte rate. A tenth
of the ten seconds a pipe allows between its lines, so its printer always has a fresh count to print."""

FFMPEG_VERSION = "8.1.2"
MAX_ARCHIVE_BYTES = 400 * 1024 * 1024
"""Calibration: well over the largest pinned archive at 169 MB, so only a wrong answer grows past it."""
# One host refuses urllib's default User-Agent, so every fetch names itself.
USER_AGENT = "decktalk"
CHUNK_BYTES = 1 << 20
"""Truth: at most a megabyte at a time, which is large enough that hashing keeps up with the socket. A read
takes what has arrived rather than waiting for the whole megabyte, so a slow link is counted as it trickles."""

LOCK_POLL_SECONDS = 0.1
"""Calibration: how often a waiting fetch looks at the cancel token and the clock, which nobody notices."""

DOWNLOAD_TIMEOUT_SECONDS = 60
"""Calibration: longer than any read of a healthy host takes, so only one that stopped answering hits it."""

CONTENT_RANGE = re.compile(r"^bytes (?P<start>\d+)-\d+/(?P<total>\d+|\*)$")
"""The `Content-Range` of a 206 answer: where the bytes it sends start, and the whole file's size when stated."""
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

    @property
    def name(self) -> str:
        """What a `fetch` line calls this archive: its first executable, so ffprobe's own archive is not ffmpeg."""
        return self.binaries[0]


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


def download_dir(key: str | None = None) -> Path:
    """Where the archives of one build arrive, beside its install directory, which a stopped download leaves
    with its `.part` file so the next fetch resumes it, and an install removes."""
    dest = install_dir(key)
    return dest.with_name(f".{dest.name}.download")


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


class DownloadStopped(ConnectionError):
    """A download that ended before its last byte, whose partial file is kept for the next run to resume.

    It is a ConnectionError, so a caller that falls back to a build on PATH when the network fails
    still does, and one that cannot says what this says.
    """


def _hashed(path: Path) -> hashlib._Hash:
    """The SHA-256 of a file on disk so far, which a resumed download carries on from."""
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while block := fh.read(CHUNK_BYTES):
            digest.update(block)
    return digest


def _open(asset: FfmpegAsset, have: int) -> tuple[http.client.HTTPResponse, int, int | None]:
    """Ask the host for the archive from byte `have` on, and say where its answer really starts and the size.

    A host that honours the Range request answers 206 with a `Content-Range` starting at `have`, and
    the download carries on from there. Any other answer is the whole archive from its first byte, so
    the partial file is started again. A host that refuses the range outright (416, which a partial
    as long as the whole archive draws) is asked once more for all of it.
    """
    headers = {"User-Agent": USER_AGENT}
    if have:
        headers["Range"] = f"bytes={have}-"
    request = urllib.request.Request(asset.url, headers=headers)
    try:
        resp = urllib.request.urlopen(request, timeout=DOWNLOAD_TIMEOUT_SECONDS)
    except urllib.error.HTTPError as exc:
        if have and exc.code == HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE:
            return _open(asset, 0)
        raise
    stated = _content_length(resp)
    ranged = CONTENT_RANGE.match(resp.headers.get("Content-Range", "") or "") if have else None
    if have and getattr(resp, "status", HTTPStatus.OK) == HTTPStatus.PARTIAL_CONTENT:
        if ranged and int(ranged["start"]) == have:
            whole = ranged["total"]
            return resp, have, int(whole) if whole.isdigit() else (have + stated if stated is not None else None)
        resp.close()
        return _open(asset, 0)
    return resp, 0, stated


def _download_verified(asset: FfmpegAsset, into: Path) -> Path:
    """Stream the archive into `into`, hashing it as it arrives. A digest other than the pinned one is refused.

    The file is written under a `.part` name beside its own, and only takes its own name once the
    digest of the whole file matches, so nothing that failed the check is ever left looking like an
    archive. A download that stops partway, because the link dropped or the host stopped answering
    for `DOWNLOAD_TIMEOUT_SECONDS`, keeps its `.part` file and raises DownloadStopped, and the next
    fetch asks for the rest with a Range request. A host that sends the whole archive instead starts
    the file again. An archive already here under its own name passed the check when it took that
    name and is checked again rather than fetched.

    The download is announced under the archive's name with the size its host states, at its start
    (counting what a resumed file already holds), every `ANNOUNCE_SECONDS` while bytes arrive, and
    last with every byte as its size too, which is what tells a reader it ended even when the host
    stated none.
    """
    target = into / asset.url.rsplit("/", 1)[1]
    partial = target.with_name(f"{target.name}.part")
    started = time.monotonic()
    if target.is_file() and _hashed(target).hexdigest() == asset.sha256:
        return target
    have = partial.stat().st_size if partial.is_file() else 0
    resp, total, expected = _open(asset, have)
    with resp, partial.open("ab" if total else "wb") as fh:
        digest = _hashed(partial) if total else hashlib.sha256()
        resumed_from = total
        announce(asset.name, total, expected)
        said = time.monotonic()
        try:
            while chunk := resp.read1(CHUNK_BYTES):
                total += len(chunk)
                if total > MAX_ARCHIVE_BYTES:
                    fh.close()
                    partial.unlink()
                    raise ToolError(
                        f"{asset.url} is larger than {MAX_ARCHIVE_BYTES} bytes, so the download was refused"
                    )
                digest.update(chunk)
                fh.write(chunk)
                if time.monotonic() - said >= ANNOUNCE_SECONDS:
                    announce(asset.name, total, expected)
                    said = time.monotonic()
            if expected is not None and total < expected:
                raise ConnectionError("the host closed the connection early")
        except OSError as exc:
            raise DownloadStopped(
                f"{asset.name} stopped after {sized(total)} of {sized(expected) if expected else 'its size'} ({exc}). "
                "What arrived is kept, and the next run resumes from there"
            ) from exc
    announce(asset.name, total, total)
    if digest.hexdigest() != asset.sha256:
        partial.unlink()
        raise ToolError(
            f"{asset.url} does not match the SHA-256 DeckTalk pins for it (expected {asset.sha256}, got "
            f"{digest.hexdigest()}), so the download was discarded. Nothing was installed. Set `[tools] ffmpeg` "
            "and `ffprobe` to a build of your own until the pin is updated."
        )
    partial.replace(target)
    seconds = round(time.monotonic() - started, 3)
    log.debug(
        "%s arrived and matched its pinned digest in %.2f seconds.",
        target.name,
        seconds,
        extra={
            "data": {
                "url": asset.url,
                "sha256": asset.sha256,
                "bytes": total,
                "resumed_from": resumed_from,
                "seconds": seconds,
            }
        },
    )
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


def _take_turn(lock: FileLock, cancel: Cancel | None, wait_seconds: float) -> float | None:
    """Acquire the lock of one build, waiting on another fetch of it for no longer than `wait_seconds`.

    The wait is a run of short attempts rather than one blocking call, because a holder that is alive
    and slow or wedged would otherwise hold every other run on the machine where no cancel reaches
    it. A waiting run announces the fetch it stopped for once, with nothing counted, because the
    bytes arrive in another fetch that this one cannot see. The seconds it waited are given back, or
    None when the lock was free, so the caller can say what it found once its turn came.
    """
    started = time.monotonic()
    deadline = started + wait_seconds
    waiting = False
    while True:
        try:
            lock.acquire(timeout=LOCK_POLL_SECONDS)
            return round(time.monotonic() - started, 3) if waiting else None
        except Timeout:
            if not waiting:
                announce(TOOL, 0, None)
                waiting = True
            if cancel is not None:
                cancel.check()
            if time.monotonic() > deadline:
                raise ToolError(
                    f"Another fetch of ffmpeg held {lock.lock_file} for longer than the {wait_seconds:g} seconds "
                    "that tools.timeout_seconds allows, so this run stopped waiting for it.",
                    hint="Let the other fetch finish, or stop it, then run the command again.",
                ) from None


def fetch_ffmpeg(key: str | None = None, *, cancel: Cancel | None = None, wait_seconds: float) -> tuple[str, str]:
    """Download the pinned build for a platform into install_dir() and return its (ffmpeg, ffprobe).

    Every archive arrives in download_dir() and is verified before it is opened, the executables are
    unpacked into a scratch directory of this fetch's own, and that directory replaces the install
    directory only once both are in place, which is when the downloads go. A download that stopped
    leaves its partial file there for the next fetch to resume, and a refused archive leaves nothing.
    Two fetches of one build take turns under an operating system lock beside it, which the system
    releases if its holder dies, and the second finds the build the first installed and downloads
    nothing, so two cold first builds in one process or in two can never delete each other's
    download. A fetch waiting for its turn stops when `cancel` is set and gives up after
    `wait_seconds`, which a run takes from `tools.timeout_seconds`.
    """
    key = key or platform_key()
    build = pinned_build(key)
    if build is None:
        raise ToolError(
            f"DeckTalk pins no ffmpeg build for {key}. Install ffmpeg and ffprobe on PATH, or set "
            "`[tools] ffmpeg` and `ffprobe`."
        )
    dest = install_dir(key)
    dest.parent.mkdir(parents=True, exist_ok=True)
    lock = FileLock(dest.with_name(f".{dest.name}.lock"))
    waited = _take_turn(lock, cancel, wait_seconds)
    try:
        installed = installed_pinned(key)
        if waited is not None:
            log.info(
                "Waited %.1f seconds for another fetch of ffmpeg, which %s.",
                waited,
                "installed it" if installed else "left nothing installed, so this run fetches it",
                extra={
                    "data": {"lock": str(lock.lock_file), "waited_seconds": waited, "found_installed": bool(installed)}
                },
            )
        if installed:
            return installed
        downloads = download_dir(key)
        downloads.mkdir(exist_ok=True)
        scratch = Path(tempfile.mkdtemp(prefix=f".{dest.name}.", dir=dest.parent))
        try:
            for asset in build.assets:
                _unpack(_download_verified(asset, downloads), asset, scratch)
            shutil.rmtree(dest, ignore_errors=True)
            scratch.replace(dest)
            shutil.rmtree(downloads, ignore_errors=True)
        except ToolError:
            # A refused archive is never resumed, so nothing of this build's downloads is kept.
            shutil.rmtree(downloads, ignore_errors=True)
            raise
        finally:
            shutil.rmtree(scratch, ignore_errors=True)
    finally:
        lock.release()
    return str(dest / _exe("ffmpeg")), str(dest / _exe("ffprobe"))
