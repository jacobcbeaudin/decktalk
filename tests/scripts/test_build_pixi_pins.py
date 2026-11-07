"""`scripts/build_pixi_pins.py`, which writes the Pixi version and its digests from `requires-pixi`.

The generated row runs it with `--check` on every pull request, which reaches no network. These
tests replace the two reads of GitHub and of the registry, so a write of a moved version is judged
offline too.
"""

from __future__ import annotations

import re
import urllib.error
from email.message import Message

import pytest

import build_pixi_pins as pins
import check


@pytest.fixture
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail any read of a release or of the registry, so a test proves the generator made none."""

    def refuse(*_args: object) -> str:
        raise AssertionError("the generator reached the network although the version had not moved")

    monkeypatch.setattr(pins, "archive_digest", refuse)
    monkeypatch.setattr(pins, "image_digest", refuse)


@pytest.mark.usefixtures("no_network")
def test_a_version_that_has_not_moved_is_checked_offline_and_already_holds() -> None:
    written = pins.files(fetch=False)
    for path, text in written.items():
        assert path.read_text(encoding="utf-8") == text, path.name


@pytest.mark.usefixtures("no_network")
def test_a_moved_version_fails_the_check_offline_with_the_command_that_writes_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The generated row declares no network, so a check of a moved version reads none."""
    monkeypatch.setattr(check, "pixi_version", lambda: "9.9.9")
    with pytest.raises(SystemExit, match=r"Pixi 9\.9\.9.*check --group generated --write"):
        pins.files(fetch=False)


@pytest.mark.parametrize(
    ("failure", "said"),
    [
        pytest.param(
            urllib.error.HTTPError("u", 404, "Not Found", Message(), None), "answered 404 Not Found", id="404"
        ),
        pytest.param(urllib.error.URLError("no route to host"), "could not be read", id="no network"),
    ],
)
def test_a_read_that_fails_names_the_address_and_why(
    monkeypatch: pytest.MonkeyPatch, failure: OSError, said: str
) -> None:
    """A Pixi Renovate proposes may not be published yet, and a traceback would not say so."""

    def refuse(*_args: object, **_kwargs: object) -> None:
        raise failure

    monkeypatch.setattr(pins.urllib.request, "urlopen", refuse)
    with pytest.raises(SystemExit, match=said) as stopped:
        pins.archive_digest("9.9.9", pins.TARGETS[0])
    assert "releases/download/v9.9.9" in str(stopped.value)


def test_a_moved_version_writes_the_new_version_and_the_digests_its_release_publishes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(check, "pixi_version", lambda: "9.9.9")
    monkeypatch.setattr(pins, "archive_digest", lambda version, target: f"{len(target):064x}")
    monkeypatch.setattr(pins, "image_digest", lambda version: "sha256:" + "e" * 64)
    written = pins.files()
    installer = written[pins.INSTALLER]
    assert re.findall(r'^PIXI_VERSION="(.+)"$', installer, re.M) == ["9.9.9"]
    for target in pins.TARGETS:
        assert f"{target}={len(target):064x}\n" in installer
    assert '"ghcr.io/prefix-dev/pixi", "9.9.9", "sha256:' + "e" * 64 in written[pins.TABLE]


def test_a_release_with_no_digest_for_an_archive_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pins, "read", lambda _url: (b"<html>not found</html>", {}))
    with pytest.raises(SystemExit, match="publishes no SHA-256"):
        pins.archive_digest("9.9.9", pins.TARGETS[0])
