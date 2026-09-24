"""The artifact package: one module owns each file, and every file is one frozen model."""

from __future__ import annotations

import pkgutil

import pytest

from decktalk import artifacts
from decktalk.artifacts.stored import Stored

STORED = (artifacts.CueTimes, artifacts.Cuts, artifacts.RecordingLog, artifacts.Takes, artifacts.Words)
"""Every file under `build/` that a stage writes and a later reader opens."""


def test_every_name_the_package_publishes_is_reachable() -> None:
    for name in artifacts.__all__:
        assert hasattr(artifacts, name), name


@pytest.mark.parametrize("model", STORED, ids=lambda model: model.__name__)
def test_every_artifact_reads_and_writes_itself(model: type[Stored]) -> None:
    assert issubclass(model, Stored)
    assert model.model_config["frozen"]
    assert model.model_config["extra"] == "forbid"


def test_no_module_here_owns_two_files() -> None:
    """One module per file is what keeps a shape from being read two ways."""
    owners = {model.__module__ for model in STORED}
    assert len(owners) == len(STORED)


def test_nothing_here_carries_the_progress_file_the_event_stream_replaced() -> None:
    """A run says what it is doing on the stream, so `build/` holds no second account of it."""
    modules = {info.name for info in pkgutil.iter_modules(artifacts.__path__)}
    assert "progress" not in modules
