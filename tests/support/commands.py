"""What the suites that drive `decktalk` as a subprocess share: the hostile root, the key contract and the environment.

`tests/e2e/test_pipeline.py` and `tests/e2e/test_scaffold_build.py` both run the real command line
and read its `--json`, so the contract they hold every result to and the environment they strip are
written once here rather than twice with lists that drift apart.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from decktalk.findings import Code
from decktalk.project import PROJECT_VARIABLE
from decktalk.results import SCHEMA
from decktalk.settings import MACHINE_FILE_VARIABLE
from decktalk.speech import DECLARED
from decktalk.stages.narrate.plan import VOICE_ID_VARIABLE

HOSTILE_DIRECTORY = "jacob's fïlms 2"
"""The name every temporary root of these suites sits under, because a path is an input like any other.

An apostrophe and a diacritic reach every shell quote, every ffmpeg concat list and every served URL
a build writes, and the founder's own films live under a name like this one.
"""

RESERVED_KEYS = ("schema", "ok", "findings", "error")
"""The four keys every result carries, which is the founder's decided JSON contract."""

FOUND_NOTHING, FOUND_SOMETHING = 0, 1
"""What the CLI exits when it judged nothing and when it judged something, from the CLI design."""


def flat(stdout: str, args: tuple[str, ...] = ()) -> dict[str, Any]:
    """The one flat object a command printed with `--json`, holding the four reserved keys."""
    doc = json.loads(stdout)
    assert isinstance(doc, dict), f"{args}: --json prints one object on stdout and nothing else"
    assert set(RESERVED_KEYS) <= set(doc), f"{args}: missing {sorted(set(RESERVED_KEYS) - set(doc))}"
    assert doc["schema"] == SCHEMA, doc["schema"]
    return doc


def codes(doc: dict[str, Any]) -> list[Code]:
    """Every finding as the model's own member, which is what the timing policy judges."""
    return [Code(row["code"]) for row in doc["findings"]]


def clean_environ(config_dir: Path) -> dict[str, str]:
    """This process's environment without the credential, the voice, the project or the machine settings.

    A key or a settings file belonging to whoever runs the suite must not reach the build, because a
    project has to build on a machine that has never seen either.
    """
    env = dict(os.environ)
    keys = [declared.key_variable for declared in DECLARED.values() if declared.key_variable]
    for name in (PROJECT_VARIABLE, *keys, VOICE_ID_VARIABLE):
        env.pop(name, None)
    env[MACHINE_FILE_VARIABLE] = str(config_dir / "no-machine-config.toml")
    return env
