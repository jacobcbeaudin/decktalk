"""The speech layer's registry, whose voices travel on the run and never in a context variable.

A context variable is read by whatever thread asks, so one whose default is a provider table hands
the shipped paid voice to any thread a pool started without copying the context. The speech layer
therefore keeps no such variable, and these tests read its source to hold that.
"""

from __future__ import annotations

import ast
from pathlib import Path

import decktalk.speech

SPEECH = Path(decktalk.speech.__file__).parent


def module_level_context_vars(path: Path) -> list[str]:
    """Every name a module binds at its top level to a `ContextVar`, by annotation or by value."""
    found: list[str] = []
    for node in ast.parse(path.read_text(encoding="utf-8")).body:
        if isinstance(node, ast.AnnAssign):
            spelled = ast.unparse(node.annotation) + (ast.unparse(node.value) if node.value else "")
            targets = [node.target]
        elif isinstance(node, ast.Assign):
            spelled, targets = ast.unparse(node.value), node.targets
        else:
            continue
        if "ContextVar" in spelled:
            found += [f"{path.name}:{ast.unparse(target)}" for target in targets]
    return found


def test_no_module_in_the_speech_layer_holds_a_context_variable() -> None:
    """A provider table reaches a stage on its run, so no thread can be handed one it was never given."""
    held = [name for path in sorted(SPEECH.glob("*.py")) for name in module_level_context_vars(path)]
    assert held == []
