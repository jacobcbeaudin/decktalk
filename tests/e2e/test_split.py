"""The API key never shares a process with a page: voice in one job, render in another.

    uv run pytest -m e2e tests/e2e/test_split.py

A host that renders other people's pages runs `narrate` in a voice job that holds the key, and the
whole build in a render job whose environment holds none, with the project directory, build
directory included, handed from one to the other. The render job's build does not spend, so it plays
every take the voice job bought and never builds a voice. This drives exactly that split through the
public SDK, each job a real subprocess with an environment the test chose.

The voice job's provider is a stand-in that asks the machine for the key, checks it is the sentinel,
and answers with a real tone from the pinned ffmpeg, so no request leaves the machine and nothing is
spent. The render job records every environment a Chromium launch is handed and reports it, because
that environment, and the job's own, are the two places a key could reach a page. The key here is
an obvious sentinel and never a credential.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from decktalk.findings import Code
from decktalk.machine import Machine, init
from decktalk.pipeline import Outcome, Stage
from decktalk.settings import MACHINE_FILE_VARIABLE

pytestmark = pytest.mark.e2e

KEY = "ELEVENLABS_API_KEY"
"""The one credential a voiced run reads, which only the voice job is handed."""

SENTINEL = "sk-split-sentinel-not-a-credential"
"""What the voice job holds as the key, spelled so a match anywhere else is unmistakable."""

CARRIED = ("PATH", "HOME", "TMPDIR", "TEMP", "TMP", "LANG", "SYSTEMROOT", "LOCALAPPDATA", "USERPROFILE")
"""What both jobs keep from this process: enough to find the tools and a temporary directory, and no secret."""

JOB_SECONDS = 600
"""Calibration: many times what the starter's record, assemble and verify take, so only a hung job reaches it."""

VOICE_JOB = '''
import json, socket, sys
from pathlib import Path

import decktalk
from decktalk.machine import Machine
from decktalk.media import browser, ffmpeg
from decktalk.results import TakeOutcome, Word

root, cache, out = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
KEY, SENTINEL = "ELEVENLABS_API_KEY", "sk-split-sentinel-not-a-credential"


def refuse(*_args, **_kwargs):
    raise AssertionError("the voice job opened a connection or a browser")


socket.socket.connect = refuse
browser.chromium = refuse


class Tone:
    """A voice that holds the key it was handed and answers with a real tone, one third of a second a word."""

    def __init__(self, context):
        (key,) = context.secrets.require(KEY)
        assert key.reveal() == SENTINEL, "the voice job's provider did not get the voice job's key"

    def speak(self, request):
        text = " ".join(piece.text for piece in request.pieces)
        words = text.split()
        clip = out.parent / f"tone-{abs(hash(text))}.mp3"
        seconds = max(1.0, len(words) / 3)
        ffmpeg.run("-f", "lavfi", "-i", "sine=f=220:r=44100", "-t", f"{seconds:.2f}", "-c:a", "libmp3lame", str(clip))
        timed = [Word(word=w, start=i / 3, end=(i + 1) / 3) for i, w in enumerate(words)]
        return clip.read_bytes(), timed


import os
here = Machine.of(
    environ=dict(os.environ),
    machine_file=out.parent / "voice-machine.toml",
    cwd=root,
    cache_dir=cache,
    speech_providers={"elevenlabs": Tone},
)
result = decktalk.open(root, machine=here).narrate(spend=True)
voiced = [take.section for take in result.sections if take.outcome is TakeOutcome.VOICED]
out.write_text(json.dumps({"ok": result.ok, "voiced": voiced}), encoding="utf-8")
'''

RENDER_JOB = """
import json, os, sys
from dataclasses import replace
from pathlib import Path

import decktalk
from decktalk.artifacts import Takes
from decktalk.machine import Machine
from decktalk.media import browser

root, out = Path(sys.argv[1]), Path(sys.argv[2])
launched = []
chosen = browser.child_environment


def watched():
    given = chosen()
    launched.append(dict(given))
    return given


def refuse(_context):
    raise AssertionError("the render job built a voice")


browser.child_environment = watched
base = Machine.from_environment()
here = replace(base, speech_providers=replace(base.speech_providers, factories={"elevenlabs": refuse}))
built = decktalk.open(root, machine=here, threshold=decktalk.Threshold(stop_on=None)).build()
takes = Takes.read(root / "build" / "narrate" / "takes.json")
out.write_text(
    json.dumps(
        {
            "process": dict(os.environ),
            "launched": launched,
            "stages": {row.stage.value: row.outcome.value for row in built.stages},
            "codes": [found.code.value for found in built.findings],
            "voiced": [take.voiced for take in takes.sections],
            "film": built.film is not None,
        }
    ),
    encoding="utf-8",
)
"""


def job(script: str, args: list[str], environ: dict[str, str], cwd: Path) -> None:
    """Run one job as its own interpreter with exactly the environment the test chose for it."""
    done = subprocess.run(
        [sys.executable, "-c", script, *args],
        env=environ,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=JOB_SECONDS,
        check=False,
    )
    assert done.returncode == 0, f"the job failed:\n{done.stdout}\n{done.stderr}"


def carried(**extra: str) -> dict[str, str]:
    """This process's variables a job needs to find its tools, plus what the job is handed on purpose."""
    return {**{name: value for name, value in os.environ.items() if name.upper() in CARRIED}, **extra}


def test_the_api_key_never_reaches_the_render_job_or_a_page(tmp_path: Path) -> None:
    here = Machine.from_environment()
    voice_root = tmp_path / "voice" / "starter"
    voice_root.parent.mkdir()
    init(voice_root, machine=here, skills=False)

    voiced = tmp_path / "voice" / "result.json"
    voice_env = carried(**{KEY: SENTINEL, "DECKTALK_VOICE_ID": "house-voice"})
    job(VOICE_JOB, [str(voice_root), str(here.cache_dir), str(voiced)], voice_env, voice_root)
    said = json.loads(voiced.read_text(encoding="utf-8"))
    assert said["ok"] and said["voiced"], "the voice job voiced nothing"

    # The project directory is what moves between the jobs, with the build directory inside it.
    render_root = tmp_path / "render" / "starter"
    shutil.copytree(voice_root, render_root)
    rendered = tmp_path / "render" / "result.json"
    # The voice is a published name and travels with the project, so the render job is handed it and no key.
    render_env = carried(
        **{MACHINE_FILE_VARIABLE: str(tmp_path / "render" / "machine.toml"), "DECKTALK_VOICE_ID": "house-voice"}
    )
    assert KEY not in render_env
    job(RENDER_JOB, [str(render_root), str(rendered)], render_env, render_root)
    seen = json.loads(rendered.read_text(encoding="utf-8"))

    # The whole build runs without buying, so it plays every take the voice job bought and builds no voice.
    assert seen["stages"][Stage.NARRATE.value] == Outcome.RAN.value
    assert seen["voiced"] and all(seen["voiced"]), "the render job replaced a voiced take"
    assert Code.TAKE_MISSING.value not in seen["codes"]
    assert seen["film"]
    assert seen["launched"], "the render job never launched a browser, so it proved nothing about one"
    for environment in [seen["process"], *seen["launched"]]:
        assert KEY not in environment
        assert SENTINEL not in json.dumps(environment)
    assert (render_root / "build" / "final").is_dir()
