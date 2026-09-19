"""Audio work on top of ffmpeg, so nothing above this module spells an audio filter by hand.

The narration stages build silence, click tracks, padding and joins here, `assemble` measures and
corrects loudness here, and `verify` reads the samples of one span here. Every call goes through
`ffmpeg.run`, which checks the return code, so a failed edit says what ffmpeg said.
"""

from __future__ import annotations

import math
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from . import ffmpeg


def write_silence(out: Path, seconds: float, *, sample_rate: int, bitrate: str) -> None:
    ffmpeg.run(
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
    err = ffmpeg.stderr(
        "-ss", f"{start:.3f}", "-t", f"{seconds:.3f}", "-i", str(path), "-vn",
        "-af", "astats=measure_perchannel=none:measure_overall=RMS_level", "-f", "null", "-",
    )  # fmt: skip
    m = re.findall(r"RMS level dB: (-?[0-9.]+|-inf)", err)
    if not m:
        return -120.0
    return -120.0 if m[-1] == "-inf" else float(m[-1])


SILENCE_END_TOLERANCE_SECONDS = 0.06  # A silence that ends this close to the end of the file runs to the end.
MP3_FRAME_SAMPLES = 1152  # Samples in one MPEG-1 Layer III frame.


def sound_end(path: Path, *, noise_db: int = -35, min_run: float = 0.05) -> float:
    """Where the sound in an audio file ends: the start of the silence that runs to its end, or its length.

    It reads the file and nothing else, so the same bytes always give the same answer. The container
    length includes the encoder padding, which decodes to nothing. For an mp3 that padding is up to
    about 50 ms, so silencedetect reports the last silence ending that far before the container end.
    A silence that ends within one audio frame or SILENCE_END_TOLERANCE_SECONDS of the end, whichever
    is longer, counts as running to the end, and where it starts is measured on the decoded audio,
    which the padding never reaches.
    """
    duration = ffmpeg.probe_duration(path)
    err = ffmpeg.stderr("-i", str(path), "-af", f"silencedetect=noise={noise_db}dB:d={min_run}", "-f", "null", "-")
    starts = re.findall(r"silence_start: ([0-9.]+)", err)
    ends = re.findall(r"silence_end: ([0-9.]+)", err)
    if not starts:
        return round(duration, 3)
    rate = re.search(r"Audio: [^\n]*?(\d+) Hz", err)
    frame = MP3_FRAME_SAMPLES / int(rate.group(1)) if rate and int(rate.group(1)) > 0 else 0.0
    tolerance = max(SILENCE_END_TOLERANCE_SECONDS, frame)
    if len(ends) < len(starts) or float(ends[-1]) >= duration - tolerance:
        return round(float(starts[-1]), 3)
    return round(duration, 3)


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
    ffmpeg.run("-i", str(wav), "-c:a", "libmp3lame", "-b:a", bitrate, str(path))
    wav.unlink()


def pcm_span(path: Path, start: float, seconds: float, *, sample_rate: int = 48000) -> list[int]:
    """Mono 16-bit samples of the audio between start and start + seconds."""
    out = subprocess.run(
        [ffmpeg.ffmpeg(), "-v", "error", "-ss", f"{start:.3f}", "-t", f"{seconds:.3f}", "-i", str(path), "-vn",
         "-ac", "1", "-ar", str(sample_rate), "-f", "s16le", "-"],
        capture_output=True, check=True,
    ).stdout  # fmt: skip
    import array

    a = array.array("h")
    a.frombytes(out[: len(out) - len(out) % 2])
    return list(a)


def concat_audio(
    files: list[Path],
    out: Path,
    *,
    bitrate: str,
    sample_rate: int,
    leads: list[float] | None = None,
    lengths: list[float] | None = None,
) -> None:
    """Join audio files back to back.

    `leads` gives each file seconds of silence before it, in whole milliseconds. `lengths` gives
    each file the seconds it runs for after its lead, to the sample: a longer file is cut there and a
    shorter one is followed by silence up to it, so where every file lands is arithmetic over the
    two lists and never depends on the files around it.
    """
    inputs: list[str] = []
    for f in files:
        inputs += ["-i", str(f)]
    delays = [int(round(x * 1000)) for x in (leads or [])] + [0] * len(files)
    samples = [int(round(x * sample_rate)) for x in (lengths or [])] + [None] * len(files)
    steps, shaped = [], set()
    for i in range(len(files)):
        chain = []
        if samples[i] is not None:
            chain += [f"aresample={sample_rate}", f"atrim=end_sample={samples[i]}", f"apad=whole_len={samples[i]}"]
        if delays[i] > 0:
            chain.append(f"adelay=delays={delays[i]}:all=1")
        if chain:
            steps.append(f"[{i}:a]{','.join(chain)}[l{i}];")
            shaped.add(i)
    pads = "".join(steps)
    labels = "".join(f"[l{i}]" if i in shaped else f"[{i}:a]" for i in range(len(files)))
    ffmpeg.run(
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
    ffmpeg.run(
        *inputs, "-filter_complex", chain.rstrip(";"),
        "-map", "[a]", "-c:a", "libmp3lame", "-b:a", bitrate, str(out),
    )  # fmt: skip


@dataclass(frozen=True)
class Loudness:
    i: float
    tp: float
    lra: float
    thresh: float
    offset: float


def measure_loudness(path: Path, *, i: float, tp: float, lra: float) -> Loudness:
    err = ffmpeg.stderr(
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
