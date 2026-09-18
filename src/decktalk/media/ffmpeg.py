"""ffmpeg and ffprobe: the pinned build and its fetch, invocation, probing, and the frame-analysis helpers.

`decktalk install` downloads one pinned GPL build of ffmpeg and ffprobe for this machine's platform
from the fixed URL in FFMPEG_BUILDS, checks each archive against the SHA-256 recorded there before
anything is unpacked, and keeps the two executables under DeckTalk's per-user cache. The same fetch
runs the first time a render needs ffmpeg, so every machine renders with the same build. The binary
is downloaded, never distributed in the wheel, and THIRD_PARTY_NOTICES.md names each build and its
licence. An ffmpeg on PATH is the fallback when the fetch cannot run, and DECKTALK_FFMPEG with
DECKTALK_FFPROBE override both.
"""

from __future__ import annotations

import hashlib
import logging
import math
import os
import platform
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import urllib.request
import zipfile
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from ..errors import ToolError
from ..scaffold import cache_dir

log = logging.getLogger(__name__)

# ---- the pinned build -------------------------------------------------------------------------

FFMPEG_VERSION = "8.1.2"
# An archive that grows past this is refused mid-download. The largest pinned archive is 169 MB.
MAX_ARCHIVE_BYTES = 400 * 1024 * 1024
# One host refuses urllib's default User-Agent, so every fetch names itself.
USER_AGENT = "decktalk"
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
    bin_dir: str = ""  # the archive directory that holds them, with its trailing slash; "" is the root


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
# that pad_tail feeds it ("inadequate AVFrame plane padding"), on every platform's build.
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


def _download_verified(asset: FfmpegAsset, into: Path) -> Path:
    """Stream the archive into `into`, hashing it as it arrives. A digest other than the pinned one is refused.

    The file is written under a temporary name and only takes its own name once the digest matches,
    so nothing that failed the check is ever left looking like an archive.
    """
    target = into / asset.url.rsplit("/", 1)[1]
    partial = target.with_name(f"{target.name}.part")
    digest = hashlib.sha256()
    total = 0
    log.info("   fetching %s", asset.url)
    request = urllib.request.Request(asset.url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=60) as resp, partial.open("wb") as fh:
        while chunk := resp.read(1 << 20):
            total += len(chunk)
            if total > MAX_ARCHIVE_BYTES:
                raise ToolError(f"{asset.url} is larger than {MAX_ARCHIVE_BYTES} bytes, so the download was refused")
            digest.update(chunk)
            fh.write(chunk)
    if digest.hexdigest() != asset.sha256:
        partial.unlink()
        raise ToolError(
            f"{asset.url} does not match the SHA-256 DeckTalk pins for it (expected {asset.sha256}, got "
            f"{digest.hexdigest()}), so the download was discarded. Nothing was installed. Set DECKTALK_FFMPEG "
            "and DECKTALK_FFPROBE to a build of your own until the pin is updated."
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
            "DECKTALK_FFMPEG and DECKTALK_FFPROBE."
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


def _env_paths() -> tuple[str, str] | None:
    env_ff, env_fp = os.environ.get("DECKTALK_FFMPEG"), os.environ.get("DECKTALK_FFPROBE")
    return (env_ff, env_fp) if env_ff and env_fp else None


def _path_pair() -> tuple[str, str] | None:
    ff, fp = shutil.which("ffmpeg"), shutil.which("ffprobe")
    return (ff, fp) if ff and fp else None


@lru_cache(maxsize=1)
def ffmpeg_paths() -> tuple[str, str]:
    """(ffmpeg, ffprobe) executables.

    The environment override wins. The pinned build comes next, fetched when it is not on disk yet,
    so every machine renders with the same ffmpeg. A build on PATH is the fallback when there is no
    pinned build for this platform or the download cannot run. A download whose digest does not
    match is never used and never falls back, because that is the one failure that must stop a run.
    """
    if env := _env_paths():
        return env
    if installed := installed_pinned():
        return installed
    on_path = _path_pair()
    if pinned_build() is None:
        if on_path:
            return on_path
        raise ToolError(
            f"ffmpeg/ffprobe not found: DeckTalk pins no build for {platform_key()} and none is on PATH. "
            "Install ffmpeg, or set DECKTALK_FFMPEG and DECKTALK_FFPROBE."
        )
    try:
        return fetch_ffmpeg()
    except OSError as exc:
        if on_path:
            log.warning("could not download the pinned ffmpeg (%s); using %s", exc, on_path[0])
            return on_path
        raise ToolError(
            f"ffmpeg/ffprobe not found: the pinned build could not be downloaded ({exc}) and none is on PATH. "
            "Run `decktalk install` with network access, or install ffmpeg."
        ) from exc


def installed_paths() -> tuple[str, str] | None:
    """The (ffmpeg, ffprobe) pair that ffmpeg_paths() would return without downloading anything.

    None means only a fetch could provide them. `decktalk doctor` reports on that instead of
    triggering it.
    """
    return _env_paths() or installed_pinned() or _path_pair()


def ffmpeg() -> str:
    return ffmpeg_paths()[0]


def ffprobe() -> str:
    return ffmpeg_paths()[1]


def run(*args: str) -> None:
    """ffmpeg -hide_banner -loglevel error -y ARGS. Raises ToolError with ffmpeg's message."""
    cmd = [ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", *args]
    log.debug("ffmpeg %s", " ".join(args))
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        tail = proc.stderr.strip().splitlines()[-6:]
        raise ToolError("ffmpeg failed: " + " | ".join(tail))


def stderr(*args: str) -> str:
    """ffmpeg run whose useful output is on stderr (metadata=print, silencedetect, loudnorm)."""
    cmd = [ffmpeg(), "-hide_banner", "-nostats", *args]
    return subprocess.run(cmd, capture_output=True, text=True).stderr


def probe_duration(path: Path | str) -> float:
    cmd = [
        ffprobe(),
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0 or not proc.stdout.strip():
        raise ToolError(f"ffprobe could not read {path}: {proc.stderr.strip()[-200:]}")
    return round(float(proc.stdout.strip()), 3)


def decoded_duration(path: Path | str, *, sample_rate: int = 48000) -> float:
    """The length of the audio as it decodes, in seconds.

    A container's reported duration can include encoder padding that the decoder trims,
    which for MP3 is 30 to 50 ms per file. Positions in a concatenated track add up from
    decoded lengths, so anything that maps section times onto that track must use these.
    """
    out = subprocess.run(
        [ffmpeg(), "-v", "error", "-i", str(path), "-vn", "-ac", "1", "-ar", str(sample_rate), "-f", "s16le", "-"],
        capture_output=True,
    )
    if out.returncode != 0:
        raise ToolError(f"ffmpeg could not decode {path}: {out.stderr.decode(errors='replace').strip()[-200:]}")
    return round(len(out.stdout) / 2 / sample_rate, 4)


def has_audio(path: Path | str) -> bool:
    cmd = [
        ffprobe(),
        "-v",
        "error",
        "-select_streams",
        "a",
        "-show_entries",
        "stream=codec_type",
        "-of",
        "csv=p=0",
        str(path),
    ]
    return bool(subprocess.run(cmd, capture_output=True, text=True).stdout.strip())


# ---- audio helpers -----------------------------------------------------------------


def write_silence(out: Path, seconds: float, *, sample_rate: int, bitrate: str) -> None:
    run(
        "-f",
        "lavfi",
        "-i",
        f"anullsrc=r={sample_rate}:cl=mono",
        "-t",
        f"{seconds:.3f}",
        "-c:a",
        "libmp3lame",
        "-b:a",
        bitrate,
        str(out),
    )


def rms_db(path: Path, start: float, seconds: float) -> float:
    """The RMS level in dBFS of the audio between start and start + seconds."""
    err = stderr(
        "-ss", f"{start:.3f}", "-t", f"{seconds:.3f}", "-i", str(path), "-vn",
        "-af", "astats=measure_perchannel=none:measure_overall=RMS_level", "-f", "null", "-",
    )  # fmt: skip
    m = re.findall(r"RMS level dB: (-?[0-9.]+|-inf)", err)
    if not m:
        return -120.0
    return -120.0 if m[-1] == "-inf" else float(m[-1])


SILENCE_END_TOLERANCE_SECONDS = 0.06  # A silence that ends this close to the end of the file runs to the end.
MP3_FRAME_SAMPLES = 1152  # Samples in one MPEG-1 Layer III frame.


def trailing_silence(path: Path, *, noise_db: int = -35, min_run: float = 0.05) -> float:
    """Seconds of silence at the end of an audio file.

    The container length includes the encoder padding, which decodes to nothing. For an mp3 that
    padding is up to about 50 ms, so silencedetect reports the last silence ending that far before
    the container end. A silence that ends within one audio frame or SILENCE_END_TOLERANCE_SECONDS
    of the end, whichever is longer, counts as running to the end.
    """
    duration = probe_duration(path)
    err = stderr("-i", str(path), "-af", f"silencedetect=noise={noise_db}dB:d={min_run}", "-f", "null", "-")
    starts = re.findall(r"silence_start: ([0-9.]+)", err)
    ends = re.findall(r"silence_end: ([0-9.]+)", err)
    if not starts:
        return 0.0
    rate = re.search(r"Audio: [^\n]*?(\d+) Hz", err)
    frame = MP3_FRAME_SAMPLES / int(rate.group(1)) if rate and int(rate.group(1)) > 0 else 0.0
    tolerance = max(SILENCE_END_TOLERANCE_SECONDS, frame)
    if len(ends) < len(starts) or float(ends[-1]) >= duration - tolerance:
        return round(duration - float(starts[-1]), 3)
    return 0.0


def write_clicks(
    path: Path, duration: float, times: list[float], *, sample_rate: int, bitrate: str, level_db: float = -24.0
) -> None:
    """A placeholder track for builds without voice: silence with a soft click at each word start.

    The clicks let `verify` measure the finished file's audio against its picture, and
    they make a silent draft reviewable for pacing.
    """
    import array
    import wave

    n = int(round(duration * sample_rate))
    samples = array.array("h", bytes(2 * n))
    amp = int(32767 * 10 ** (level_db / 20))
    click = int(0.008 * sample_rate)
    for t in times:
        start = int(round(t * sample_rate))
        for i in range(click):
            j = start + i
            if 0 <= j < n:
                env = math.sin(math.pi * i / click)
                samples[j] = int(amp * env * math.sin(2 * math.pi * 1000 * i / sample_rate))
    wav = path.with_suffix(".clicks.wav")
    with wave.open(str(wav), "wb") as fh:
        fh.setnchannels(1)
        fh.setsampwidth(2)
        fh.setframerate(sample_rate)
        fh.writeframes(samples.tobytes())
    run("-i", str(wav), "-c:a", "libmp3lame", "-b:a", bitrate, str(path))
    wav.unlink()


def pcm_span(path: Path, start: float, seconds: float, *, sample_rate: int = 48000) -> list[int]:
    """Mono 16-bit samples of the audio between start and start + seconds."""
    out = subprocess.run(
        [ffmpeg(), "-v", "error", "-ss", f"{start:.3f}", "-t", f"{seconds:.3f}", "-i", str(path), "-vn",
         "-ac", "1", "-ar", str(sample_rate), "-f", "s16le", "-"],
        capture_output=True, check=True,
    ).stdout  # fmt: skip
    import array

    a = array.array("h")
    a.frombytes(out[: len(out) - len(out) % 2])
    return list(a)


def pad_head(path: Path, seconds: float, *, bitrate: str) -> None:
    """Prepend silence, so the first section does not start on its first syllable."""
    tmp = path.with_suffix(".pad.mp3")
    ms = int(round(seconds * 1000))
    run("-i", str(path), "-af", f"adelay={ms}:all=1", "-c:a", "libmp3lame", "-b:a", bitrate, str(tmp))
    tmp.replace(path)


def pad_tail(path: Path, seconds: float, *, bitrate: str) -> None:
    tmp = path.with_suffix(".pad.mp3")
    run("-i", str(path), "-af", f"apad=pad_dur={seconds}", "-c:a", "libmp3lame", "-b:a", bitrate, str(tmp))
    tmp.replace(path)


def concat_audio(
    files: list[Path], out: Path, *, bitrate: str, sample_rate: int, leads: list[float] | None = None
) -> None:
    """Join audio files back to back. `leads` gives each file seconds of silence before it, in whole milliseconds."""
    inputs: list[str] = []
    for f in files:
        inputs += ["-i", str(f)]
    delays = [int(round(x * 1000)) for x in (leads or [])] + [0] * len(files)
    pads = "".join(f"[{i}:a]adelay=delays={delays[i]}:all=1[l{i}];" for i in range(len(files)) if delays[i] > 0)
    labels = "".join(f"[l{i}]" if delays[i] > 0 else f"[{i}:a]" for i in range(len(files)))
    run(
        *inputs,
        "-filter_complex",
        f"{pads}{labels}concat=n={len(files)}:v=0:a=1[a]",
        "-map",
        "[a]",
        "-c:a",
        "libmp3lame",
        "-b:a",
        bitrate,
        "-ar",
        str(sample_rate),
        str(out),
    )


def crossfade_join(parts: list[Path], out: Path, *, crossfade_seconds: float, bitrate: str) -> None:
    if len(parts) == 1:
        shutil.copyfile(parts[0], out)
        return
    inputs: list[str] = []
    for p in parts:
        inputs += ["-i", str(p)]
    chain, prev = "", "[0:a]"
    for i in range(1, len(parts)):
        label = "[a]" if i == len(parts) - 1 else f"[m{i}]"
        chain += f"{prev}[{i}:a]acrossfade=d={crossfade_seconds}:c1=tri:c2=tri{label};"
        prev = label
    run(*inputs, "-filter_complex", chain.rstrip(";"), "-map", "[a]", "-c:a", "libmp3lame", "-b:a", bitrate, str(out))


# ---- frame analysis ------------------------------------------------------------------


@dataclass(frozen=True)
class FrameStats:
    pts: float
    yavg: float
    ymax: float
    uavg: float
    vavg: float


def frame_stats(path: Path, seconds: float) -> list[FrameStats]:
    """Per-frame signalstats for the first `seconds` of the file."""
    out = stderr("-t", str(seconds), "-i", str(path), "-vf", "signalstats,metadata=print", "-f", "null", "-")
    frames: list[FrameStats] = []
    cur: dict[str, float] = {}
    for line in out.splitlines():
        m = re.search(r"pts_time:([0-9.]+)", line)
        if m:
            if "YAVG" in cur:
                frames.append(_stats(cur))
            cur = {"pts": float(m.group(1))}
            continue
        mm = re.search(r"lavfi\.signalstats\.(YAVG|UAVG|VAVG|YMAX)=([0-9.]+)", line)
        if mm and cur:
            cur[mm.group(1)] = float(mm.group(2))
    if "YAVG" in cur:
        frames.append(_stats(cur))
    return frames


def _stats(d: dict[str, float]) -> FrameStats:
    return FrameStats(
        pts=d["pts"],
        yavg=d.get("YAVG", 0.0),
        ymax=d.get("YMAX", 0.0),
        uavg=d.get("UAVG", 128.0),
        vavg=d.get("VAVG", 128.0),
    )


def luma_at(path: Path, t: float, *, crop: str | None = None) -> tuple[float, float]:
    """(YAVG, YMAX) of the frame at t, optionally after crop=w:h:x:y."""
    vf = (f"crop={crop}," if crop else "") + "signalstats,metadata=print"
    err = stderr("-ss", f"{t:.3f}", "-i", str(path), "-frames:v", "1", "-vf", vf, "-f", "null", "-")
    yavg = re.search(r"YAVG=([0-9.]+)", err)
    ymax = re.search(r"YMAX=([0-9.]+)", err)
    return (float(yavg.group(1)) if yavg else 0.0, float(ymax.group(1)) if ymax else 0.0)


def frame_seek(t: float) -> tuple[list[str], str]:
    """A coarse input seek and the exact output seek that follows it, for one frame at `t`.

    Seeking before the input is fast but some builds land on a keyframe rather than the
    frame asked for, and seeking after the input is exact but decodes from wherever the
    input starts. Jumping to a little before `t` on the input and then seeking the small
    remainder on the output is both quick and exact on every build.
    """
    coarse = max(0.0, t - 3.0)
    return ["-ss", f"{coarse:.3f}"], f"{t - coarse:.3f}"


def write_luma_frame(path: Path, t: float, target: Path, *, width: int, height: int) -> None:
    """Write the luma plane of the first frame at or after `t`, scaled to width x height, as a grayscale PNG.

    The comparisons below read luma only. An RGB image would carry the decoder's chroma
    upsampling and clipping, and comparing it with a frame that never left YUV reports
    changed pixels on colored edges that did not change.
    """
    pre, rest = frame_seek(t)
    run(*pre, "-i", str(path), "-ss", rest, "-frames:v", "1", "-vf", f"scale={width}:{height},format=gray", str(target))


def _changed_mask(level: int) -> str:
    """Filters that turn a luma difference into a mask of changed pixels and print its average."""
    return f"lut=c0='if(gt(val,{level}),255,0)',signalstats,metadata=print"


def changed_pixels_percent(path: Path, t1: float, t2: float, *, level: int, width: int, height: int) -> float:
    """Share (0-100) of pixels whose luma differs by more than `level` between the frames at t1 and t2.

    Each frame is extracted once as a grayscale image and the two images are compared, which
    every ffmpeg build handles the same way and costs two keyframe seeks.
    """
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        a, b = Path(tmp) / "a.png", Path(tmp) / "b.png"
        for t, target in ((t1, a), (t2, b)):
            write_luma_frame(path, t, target, width=width, height=height)
        err = stderr(
            "-i", str(a), "-i", str(b), "-filter_complex",
            f"[0:v]format=gray[a];[1:v]format=gray[b];[a][b]blend=all_mode=difference,{_changed_mask(level)}",
            "-frames:v", "1", "-f", "null", "-",
        )  # fmt: skip
    m = re.search(r"YAVG=([0-9.]+)", err)
    return (float(m.group(1)) / 255 * 100) if m else 0.0


def changed_images_percent(a: Path, b: Path, *, level: int, width: int, height: int) -> float:
    """Share (0-100) of pixels whose luma differs by more than `level` between two still images.

    Both images are scaled to width x height and read as luma, as the frames of changed_pixels_percent are.
    """
    err = stderr(
        "-i", str(a), "-i", str(b), "-filter_complex",
        f"[0:v]scale={width}:{height},format=gray[a];[1:v]scale={width}:{height},format=gray[b];"
        f"[a][b]blend=all_mode=difference,{_changed_mask(level)}",
        "-frames:v", "1", "-f", "null", "-",
    )  # fmt: skip
    m = re.search(r"YAVG=([0-9.]+)", err)
    return (float(m.group(1)) / 255 * 100) if m else 0.0


def changed_series(
    path: Path, ref_t: float, start: float, end: float, *, fps: int, level: int, width: int, height: int
) -> list[tuple[float, float]]:
    """Changed share against the frame at ref_t for every frame from start to end, as (time, percent) pairs.

    The reference frame is the first frame at or after ref_t. It is extracted once as a
    grayscale image and looped for the span, which every ffmpeg build handles the same way,
    and one run then compares the luma of each frame of the span with it. Times are the
    frames' own positions on the 1/fps grid. When start is ref_t, the first pair is the
    reference compared with itself, and its share is zero.
    """
    import tempfile

    span = max(end - start, 0.0)
    if span <= 0:
        return []
    pre_s, rest_s = frame_seek(start)
    with tempfile.TemporaryDirectory() as tmp:
        ref = Path(tmp) / "ref.png"
        write_luma_frame(path, ref_t, ref, width=width, height=height)
        fc = (
            f"[0:v]format=gray[r];"
            f"[1:v]trim=start={rest_s}:duration={span:.3f},setpts=PTS-STARTPTS,scale={width}:{height},format=gray[b];"
            f"[r][b]blend=all_mode=difference:shortest=1,{_changed_mask(level)}"
        )
        err = stderr(
            "-loop", "1", "-framerate", str(fps), "-t", f"{span + 0.2:.3f}", "-i", str(ref),
            *pre_s, "-i", str(path),
            "-filter_complex", fc, "-f", "null", "-",
        )  # fmt: skip
    first = math.ceil(start * fps - 1e-6) / fps
    out: list[tuple[float, float]] = []
    pts: float | None = None
    for line in err.splitlines():
        m = re.search(r"pts_time:([0-9.]+)", line)
        if m:
            pts = float(m.group(1))
            continue
        mm = re.search(r"lavfi\.signalstats\.YAVG=([0-9.]+)", line)
        if mm and pts is not None:
            out.append((round(first + pts, 3), round(float(mm.group(1)) / 255 * 100, 4)))
            pts = None
    return out


# ---- loudness --------------------------------------------------------------------------


@dataclass(frozen=True)
class Loudness:
    i: float
    tp: float
    lra: float
    thresh: float
    offset: float


def measure_loudness(path: Path, *, i: float, tp: float, lra: float) -> Loudness:
    err = stderr(
        "-i", str(path), "-map", "0:a", "-af", f"loudnorm=I={i}:TP={tp}:LRA={lra}:print_format=json", "-f", "null", "-"
    )

    def field(name: str) -> float:
        m = re.search(rf'"{name}"\s*:\s*"([-0-9.]+)"', err)
        return float(m.group(1)) if m else 0.0

    return Loudness(
        i=field("input_i"),
        tp=field("input_tp"),
        lra=field("input_lra"),
        thresh=field("input_thresh"),
        offset=field("target_offset"),
    )
