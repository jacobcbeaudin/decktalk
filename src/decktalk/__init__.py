"""DeckTalk: narrated presentation videos, cut to the word.

A project is a directory. `decktalk.open(path)` returns a project, six verbs move it forward, and a
handful of calls report on it, cut a piece out of it or serve it. Every call returns a frozen result
whose findings are diagnostics with a code, a location, a severity and often a fix a caller can
apply. Every call opens a run and writes to one event stream, which any renderer subscribes to.
Nothing in this package prints, nothing reads the environment except `Machine.from_environment`, and
a path a result carries is always relative to the project root.

`__all__` is the root of the supported Python API: the entry points, the errors and the few types
every caller names. Every other public type is in a public module: `decktalk.results` for every
result and row, `decktalk.events` for every event, `decktalk.findings` for the fix and location
types, `decktalk.settings` for the settings tree, and `decktalk.speech` with `decktalk.speech.sound`
for the voice and sound protocols. A type a public name can hand you is a type you can import from
one of them. Everything else may move without notice.
"""

from __future__ import annotations

from . import events as events
from . import findings as findings
from . import results as results
from . import settings as settings
from . import speech as speech
from .artifacts.stored import ENGINE_VERSION as __version__
from .errors import (
    ApprovalRequired,
    Cancel,
    Cancelled,
    DeckTalkError,
    ErrorCode,
    InputError,
    NotBuiltError,
    ProjectLocked,
    ProviderError,
    ToolError,
)
from .events import Event, Events, JsonlSink
from .explain import explain
from .findings import Code, Finding, Severity, Threshold
from .machine import Machine, Toolchain, init
from .pipeline import Stage
from .project import Origin, Project, open, section_numbers
from .results import Cost, Result

__all__ = [
    "ApprovalRequired",
    "Cancel",
    "Cancelled",
    "Code",
    "Cost",
    "DeckTalkError",
    "ErrorCode",
    "Event",
    "Events",
    "Finding",
    "InputError",
    "JsonlSink",
    "Machine",
    "NotBuiltError",
    "Origin",
    "Project",
    "ProjectLocked",
    "ProviderError",
    "Result",
    "Severity",
    "Stage",
    "Threshold",
    "ToolError",
    "Toolchain",
    "__version__",
    "explain",
    "init",
    "open",
    "section_numbers",
]
