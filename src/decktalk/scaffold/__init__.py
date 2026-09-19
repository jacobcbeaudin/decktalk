"""Writing a project, installing the machine's tools, and reporting on both.

    init.py      `decktalk init DIR` writes a starter project, or one of the examples
    examples.py  the projects packaged in the wheel, which `--example` names
    skills.py    the packaged skills, and where a project keeps them
    install.py   `decktalk install` fetches Chromium and ffmpeg into the per-user cache
    doctor.py    `decktalk doctor` reports on every component a build needs

What ships in the wheel and where a fetched tool lives is `toolchain/`.
"""

from __future__ import annotations

from .doctor import DoctorRow, doctor
from .examples import EXAMPLES, Example, example, listed_names, reserved_message
from .init import InitResult, init, title_from
from .install import install
from .skills import SKILL_NAMES, install_skills, skills_dir

__all__ = [
    "EXAMPLES",
    "SKILL_NAMES",
    "DoctorRow",
    "Example",
    "InitResult",
    "doctor",
    "example",
    "init",
    "install",
    "install_skills",
    "listed_names",
    "reserved_message",
    "skills_dir",
    "title_from",
]
