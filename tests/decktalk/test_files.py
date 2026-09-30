"""A file a person owns is replaced whole, and JSON text no digest reads is written by pydantic-core.

The second half holds every JSON text a digest or a content key is taken over to the standard
library's bytes. `json_text` puts no space between items, sorts no keys, escapes no non-ASCII
character and writes a small float as `1e-7`, so a key taken over it would move, and a moved key
re-records a section, rebuilds a cut or buys a paid take again. Each serialisation that names a take,
a recording, a cut, a still, a sound or a kept stage is fed the one value on which the two spellings
part, and its bytes are held to what `json.dumps` wrote before the helper existed.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import stat
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from decktalk.artifacts.stored import INDENT, engine_digest
from decktalk.artifacts.takes import Take, TakeInputs, Takes
from decktalk.files import json_text, replace_all
from decktalk.inputs import Inputs
from decktalk.results import SoundKind
from decktalk.settings import Settings
from decktalk.stages import status
from decktalk.stages.assemble import cut
from decktalk.stages.soundscape import Planned
from decktalk.stages.soundscape.ledger import DIGEST_DIGITS, request_digest


def test_every_file_is_replaced_and_keeps_the_mode_it_had(tmp_path: Path) -> None:
    (tmp_path / "kept.txt").write_text("old\n", encoding="utf-8")
    (tmp_path / "kept.txt").chmod(0o640)
    replace_all({tmp_path / "kept.txt": "new\n", tmp_path / "deep" / "made.bin": b"\x00\x01"})
    assert (tmp_path / "kept.txt").read_text(encoding="utf-8") == "new\n"
    assert stat.S_IMODE((tmp_path / "kept.txt").stat().st_mode) == 0o640
    assert (tmp_path / "deep" / "made.bin").read_bytes() == b"\x00\x01"
    assert [p.name for p in tmp_path.rglob("*.draft")] == []


def test_a_move_that_fails_puts_back_every_file_already_moved_and_removes_one_it_made(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "first.txt").write_text("one\n", encoding="utf-8")
    real = Path.replace
    moved: list[Path] = []

    def refuse_the_third(self: Path, target: Path) -> Path:
        moved.append(Path(target))
        if len(moved) == 3:
            raise OSError(28, "No space left on device", str(target))
        return real(self, target)

    monkeypatch.setattr(Path, "replace", refuse_the_third)
    texts = {tmp_path / "first.txt": "changed\n", tmp_path / "new.txt": "made\n", tmp_path / "third.txt": "x\n"}
    with pytest.raises(OSError, match="No space"):
        replace_all(texts)
    assert (tmp_path / "first.txt").read_text(encoding="utf-8") == "one\n"
    assert not (tmp_path / "new.txt").exists()
    assert sorted(p.name for p in tmp_path.iterdir()) == ["first.txt"]


def test_a_draft_is_never_written_through_a_link_already_under_its_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("decktalk.files.secrets.token_hex", lambda _size: "planted")
    (tmp_path / "notes.txt").write_text("one\n", encoding="utf-8")
    elsewhere = tmp_path / "elsewhere.txt"
    elsewhere.write_text("mine\n", encoding="utf-8")
    try:
        os.symlink(elsewhere, tmp_path / ".notes.txt.planted.draft")
    except OSError:  # pragma: no cover  (Windows makes a link only in developer mode)
        pytest.skip("this machine does not let an unprivileged user make a link")
    with pytest.raises(FileExistsError):
        replace_all({tmp_path / "notes.txt": "changed\n"})
    assert elsewhere.read_text(encoding="utf-8") == "mine\n"
    assert (tmp_path / ".notes.txt.planted.draft").is_symlink()


# ---- JSON text and the keys it must never feed --------------------------------------------------


PARTING: dict[str, Any] = {"voice": "Zoë", "style": 1e-07, "nested": {"b": 1, "a": [1.0, None, True]}}
"""A value with every trait on which the two spellings part: key order, spacing, a small float and non-ASCII."""


def test_the_helper_is_not_the_spelling_a_digest_reads() -> None:
    """If this ever passes the other way, the helper could stand in for a key, and every test below is moot."""
    assert json_text(PARTING) != json.dumps(PARTING)
    assert json_text(PARTING) != json.dumps(PARTING, sort_keys=True)


def test_the_take_settings_keep_their_bytes() -> None:
    made = TakeInputs.of(provider="p", voice="v", model="m", output_format="f", settings=PARTING, text="t")
    assert made.settings == '{"nested": {"a": [1.0, null, true], "b": 1}, "style": 1e-07, "voice": "Zo\\u00eb"}'


def test_the_sound_ledger_key_keeps_its_bytes() -> None:
    payload = 'sound\n{"nested": {"a": [1.0, null, true], "b": 1}, "style": 1e-07, "voice": "Zo\\u00eb"}'
    assert request_digest("sound", PARTING) == hashlib.sha256(payload.encode()).hexdigest()[:DIGEST_DIGITS]


def test_the_sound_request_the_ledger_keeps_keeps_its_bytes() -> None:
    planned = Planned(
        name="rain",
        kind=SoundKind.AMBIENCE,
        prompt="rain",
        out=Path("rain.mp3"),
        endpoint="e",
        bodies=(PARTING,),
        digest="d",
    )
    assert planned.request == json.dumps(PARTING, sort_keys=True)


def test_a_kept_artifact_keeps_its_bytes(tmp_path: Path) -> None:
    """Every file under build/ is hashed by the assemble key and by the kept record's intact check."""
    row = Take(
        section=1,
        key="01",
        chapter="Café",
        hash="0123456789abcdef",
        voiced=True,
        word_count=2,
        characters=10,
        estimated_seconds=1e-07,
        duration_seconds=2.0,
        spoken="Zoë speaks",
    )
    index = Takes(script="script.md", model="m", output_format="f", sections=(row,))
    written = index.write(tmp_path / "takes.json").read_text(encoding="utf-8")
    assert written == json.dumps(index.model_dump(mode="json"), indent=INDENT, allow_nan=False) + "\n"
    assert "Caf\\u00e9" in written and "1e-07" in written


def test_the_assemble_and_verify_keys_keep_their_bytes(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    taken: list[tuple[str, ...]] = []
    monkeypatch.setattr(status, "engine_digest", lambda *lines: taken.append(lines) or "k")
    monkeypatch.setattr(status, "_assemble_reads", lambda _inputs: ())
    (tmp_path / "film.mp4").write_bytes(b"film")
    inputs = SimpleNamespace(settings=Settings(), workspace=SimpleNamespace(film=tmp_path / "film.mp4"))
    status.assemble_key(inputs, PARTING)  # ty: ignore[invalid-argument-type]
    status.verify_key(inputs, "made", PARTING)  # ty: ignore[invalid-argument-type]
    (settings, options), (_made, _film, measured) = taken
    assert settings == json.dumps(dataclasses.asdict(Settings()), sort_keys=True, default=str)
    assert options == measured == json.dumps(PARTING, sort_keys=True)


def test_the_slate_still_key_keeps_its_bytes(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    keyed: list[tuple[str, ...]] = []
    monkeypatch.setattr(cut, "still_key", lambda parts: keyed.append(parts) or "k")
    settings = Settings()
    inputs = SimpleNamespace(
        settings=settings,
        chapters=lambda: {3: "Café"},
        stills=SimpleNamespace(find=lambda _key: tmp_path / "kept.png"),
    )
    section = SimpleNamespace(number=3, clip="media/clip.mp4")
    cut.section_slate(inputs, None, section)  # ty: ignore[invalid-argument-type]
    video = settings.video
    shown = [
        "Café",
        "Your clip goes here",
        "section 3: slate",
        "drop it at media/clip.mp4 and run `decktalk assemble`",
        video.width,
        video.height,
        video.slate_color,
    ]
    assert keyed[0][2] == json.dumps(shown)


def test_the_cue_times_a_preview_reads_keep_their_bytes() -> None:
    answered = Inputs.documents(SimpleNamespace(preview_cues=lambda: PARTING))  # ty: ignore[invalid-argument-type]
    assert list(answered.values()) == [json.dumps(PARTING).encode("utf-8")]


def test_an_engine_digest_is_still_taken_over_newline_joined_lines() -> None:
    """The keys above feed their text here, so the join is held too."""
    assert engine_digest("a", "b") == engine_digest("a\nb")
