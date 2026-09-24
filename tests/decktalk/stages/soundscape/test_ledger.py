"""The record of what a project has already bought from the sound service.

Every row stands between a request and a charge, so what these tests hold is that a row survives a
round trip whole, that a file nobody can read is refused rather than taken for a project that has
bought nothing, and that the digest moves whenever the request moves and not when its keys are
written in another order.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from decktalk.errors import ErrorCode, NotBuiltError
from decktalk.results import SoundKind
from decktalk.stages.soundscape.ledger import (
    DIGEST_DIGITS,
    UNFINISHED_DIGEST,
    Ledger,
    SoundEntry,
    request_digest,
)

ENDPOINT = "https://api.elevenlabs.io/v1/sound-generation"
"""The endpoint a sound request goes to, which is half of what a row is keyed by."""


def an_entry(name: str = "chime", digest: str = "abc123", **fields: object) -> SoundEntry:
    return SoundEntry(
        name=name,
        kind=SoundKind.EFFECT,
        digest=digest,
        file=Path("build/soundscape/chime.mp3"),
        seconds=1.5,
        request='{"text":"a bright chime"}',
        **fields,  # type: ignore[arg-type]  (a test names the field it is judging)
    )


def test_a_row_survives_a_round_trip_through_its_own_file(tmp_path: Path) -> None:
    path = tmp_path / "ledger.json"
    Ledger(items=(an_entry(),)).write(path)
    assert Ledger.read(path) == Ledger(items=(an_entry(),))


def test_a_ledger_that_was_never_written_reads_as_nothing(tmp_path: Path) -> None:
    assert Ledger.read(tmp_path / "ledger.json") is None


def test_a_ledger_that_will_not_parse_is_refused_rather_than_read_as_an_empty_record(tmp_path: Path) -> None:
    """A corrupt ledger read as empty would buy every item in it again, which is a charge."""
    path = tmp_path / "ledger.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(NotBuiltError) as refused:
        Ledger.read(path)
    assert refused.value.code is ErrorCode.NOT_BUILT
    assert "ledger.json" in (refused.value.hint or "")


def test_a_row_is_found_by_its_name_and_a_name_nobody_bought_is_none() -> None:
    ledger = Ledger(items=(an_entry(),))
    assert ledger.of("chime") is not None
    assert ledger.of("thunder") is None


def test_writing_one_row_replaces_the_row_of_that_name_and_keeps_the_others() -> None:
    ledger = Ledger(items=(an_entry("chime"), an_entry("thunder")))
    grown = ledger.updated(an_entry("chime", digest="moved"))
    assert {row.name for row in grown.items} == {"chime", "thunder"}
    assert grown.of("chime") is not None
    assert grown.of("chime").digest == "moved"  # type: ignore[union-attr]  (asserted above)


def test_a_row_still_being_bought_carries_a_digest_no_request_can_match() -> None:
    assert UNFINISHED_DIGEST != request_digest(ENDPOINT, {"text": "x"})


def test_the_digest_moves_with_the_body_and_with_the_endpoint() -> None:
    first = request_digest(ENDPOINT, {"text": "a bright chime"})
    assert first != request_digest(ENDPOINT, {"text": "a dull chime"})
    assert first != request_digest(ENDPOINT.replace("sound-generation", "music"), {"text": "a bright chime"})


def test_the_digest_does_not_move_when_the_keys_are_written_in_another_order() -> None:
    body = {"text": "a bright chime", "model_id": "sound_v2"}
    shuffled = dict(reversed(list(body.items())))
    assert request_digest(ENDPOINT, body) == request_digest(ENDPOINT, shuffled)


def test_a_digest_is_as_long_as_the_module_declares() -> None:
    assert len(request_digest(ENDPOINT, {"text": "x"})) == DIGEST_DIGITS
