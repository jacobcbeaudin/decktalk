"""What the suites that drive `decktalk` as a subprocess share: the command, the hostile root and the environment.

Every suite under `tests/e2e/` runs the real command line and reads its `--json`, so how a command
is run, the contract every result is held to, the environment it is stripped of, the projects it
builds and how a video's frames are compared are written once here rather than in each file.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from decktalk.findings import Code, Severity
from decktalk.media import ffmpeg
from decktalk.results import SCHEMA
from decktalk.settings import MACHINE_FILE_VARIABLE, PROJECT_VARIABLE
from decktalk.speech import DECLARED
from decktalk.template import EXAMPLES, STARTER
from decktalk.tomlmap import variable
from support.tools import machine_tools

HOSTILE_DIRECTORY = "jacob's fïlms 2"
"""The name every temporary root of these suites sits under, because a path is an input like any other.

An apostrophe and a diacritic reach every shell quote, every ffmpeg concat list and every served URL
a build writes, and real projects live under names like this one.
"""

RESERVED_KEYS = ("schema", "ok", "findings", "error")
"""The four keys every result carries, which is the JSON contract."""


def flat(stdout: str, args: tuple[str, ...] = ()) -> dict[str, Any]:
    """The one flat object a command printed with `--json`, holding the four reserved keys."""
    doc = json.loads(stdout)
    assert isinstance(doc, dict), f"{args}: --json prints one object on stdout and nothing else"
    assert set(RESERVED_KEYS) <= set(doc), f"{args}: missing {sorted(set(RESERVED_KEYS) - set(doc))}"
    assert doc["schema"] == SCHEMA, doc["schema"]
    return doc


def codes(doc: dict[str, Any]) -> list[Code]:
    """Every finding of a result as the model's own member, for a test that asks which codes a run reported."""
    return [Code(row["code"]) for row in doc["findings"]]


def errors(doc: dict[str, Any]) -> list[dict[str, Any]]:
    """Every finding a result is sure about, which fails a deck on every runner."""
    return [row for row in doc["findings"] if row["severity"] == Severity.ERROR.value]


def clean_environ(config_dir: Path) -> dict[str, str]:
    """This process's environment without the credential, the voices, the project or the machine settings.

    A key or a settings file belonging to whoever runs the suite must not reach the build, because a
    project has to build on a machine that has never seen either.
    """
    env = dict(os.environ)
    keys = [declared.key_variable for declared in DECLARED.values() if declared.key_variable]
    voices = [name for name in env if name.startswith(variable("voices.")) or name == variable("narration.voice")]
    for name in (PROJECT_VARIABLE, *keys, *voices):
        env.pop(name, None)
    env[MACHINE_FILE_VARIABLE] = str(config_dir / "no-machine-config.toml")
    return env


RUNS_PROGRAMS = ("--set", "record.programs=run")
"""What a build of a project that names a program passes, since a machine refuses a project's programs by default.

The motion example names a program this suite wrote, so the build that runs it says so on its own
command line rather than the suite allowing every project's programs.
"""

PACKAGED = [example.name for example in EXAMPLES]
"""Every project `decktalk init` writes, the starter first."""

AUDIT_EVERY = 5
"""How often a build here captures a frame it would write again and compares the two.

Often enough that a still stretch of a fifth of a second is audited, and seldom enough that the
builds still take the skipping path they are judging.
"""

AUDITED = Path(__file__).with_name("audited.py")
"""The command line with the audit raised, which every command here is run through."""


def run(*args: str, cwd: Path, audit_every: int = AUDIT_EVERY, env: dict[str, str] | None = None
        ) -> subprocess.CompletedProcess[str]:  # fmt: skip
    """One `decktalk` command, run as the subprocess an author or an agent would run, with every skip audited.

    The environment is `clean_environ` unless the caller hands its own, so no key, voice variable or
    machine file of whoever runs the suite reaches the build.
    """
    return subprocess.run(
        [sys.executable, str(AUDITED), str(audit_every), *args],
        capture_output=True,
        text=True,
        check=False,
        env=clean_environ(cwd) if env is None else env,
        cwd=cwd,
    )


def decktalk(*args: str, cwd: Path, audit_every: int = AUDIT_EVERY) -> dict[str, Any]:
    """One `decktalk` command under `--json`, and the one object it printed."""
    ran = run(*args, "--json", cwd=cwd, audit_every=audit_every)
    assert ran.stdout.strip(), ran.stderr
    return json.loads(ran.stdout)


def written(home: Path, name: str) -> Path:
    """One packaged project, the starter or a shipped example by name, written by `init` into `home`."""
    root = home / name
    chosen = () if name == STARTER else ("--example", name)
    made = decktalk("init", str(root), "--no-skills", *chosen, cwd=home)
    assert made["ok"], made
    return root


def frame_digests(video: Path) -> list[str]:
    """One digest per decoded frame of `video`'s picture, taken over its rgb24 pixels."""
    with machine_tools():
        rows = ffmpeg.raw(*ffmpeg.source(video), "-map", "0:v", "-f", "framemd5", "-pix_fmt", "rgb24", "-").decode()
    return [line.rsplit(",", 1)[1].strip() for line in rows.splitlines() if line and not line.startswith("#")]
