"""The programs a project names, and the machine's word on whether `record` starts them."""

from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path

import pytest

from decktalk.errors import ErrorCode, InputError
from decktalk.inputs import programs
from decktalk.settings import RecordConfig
from support.projects import MINIMAL_TOML, load_project

CLIP = '\n[[clip]]\nfile = "deck/clips/tone.mp4"\ncommand = ["{python}", "clips/make", "{out}"]\n'
DRAWN = '\n[[section]]\nnumber = 3\nrender_command = ["/usr/bin/true", "{out}"]\n'
DRAWER = programs.section_program(3, ("/usr/bin/true", "{out}"))


def record(**values: str) -> RecordConfig:
    return replace(RecordConfig(), **values)


def test_a_machine_with_no_say_refuses_every_program_and_names_the_key_the_section_and_the_words() -> None:
    assert RecordConfig().programs == programs.REFUSE
    with pytest.raises(InputError) as refused:
        programs.admitted(RecordConfig(), [DRAWER])
    said = str(refused.value)
    assert refused.value.code is ErrorCode.INPUT
    assert "record.programs is refuse" in said
    assert 'section 3\'s render_command runs `["/usr/bin/true", "{out}"]`' in said
    hint = refused.value.hint or ""
    assert "--set record.programs=run" in hint
    assert "DECKTALK_RECORD_PROGRAMS=run" in hint
    assert ".env" in hint


def test_ask_with_nobody_asked_refuses_and_says_so() -> None:
    with pytest.raises(InputError, match="nobody at a terminal allowed it"):
        programs.admitted(record(programs=programs.ASK), [DRAWER])


def test_run_starts_them_and_a_project_with_none_is_never_refused() -> None:
    programs.admitted(record(programs=programs.RUN), [DRAWER])
    programs.admitted(RecordConfig(), [])


def test_an_untrusted_machine_refuses_every_program_whatever_programs_says() -> None:
    with pytest.raises(InputError, match="untrusted"):
        programs.admitted(record(programs=programs.RUN, page_policy="untrusted"), [DRAWER])


def test_a_project_file_cannot_allow_its_own_programs(tmp_path: Path) -> None:
    with pytest.raises(InputError, match="'record.programs' is machine-scoped"):
        load_project(tmp_path, MINIMAL_TOML + '\n[record]\nprograms = "run"\n')


def test_the_programs_a_record_would_start_are_its_missing_clips_and_its_selected_drawn_sections(
    tmp_path: Path,
) -> None:
    inputs = load_project(tmp_path, MINIMAL_TOML + DRAWN + CLIP)
    clip = programs.clip_program("deck/clips/tone.mp4", ("{python}", "clips/make", "{out}"))
    assert inputs.programs() == (clip, DRAWER)
    assert inputs.programs([1]) == (clip,)
    (tmp_path / "deck" / "clips").mkdir(parents=True)
    (tmp_path / "deck" / "clips" / "tone.mp4").write_bytes(b"made")
    assert inputs.programs() == (DRAWER,)
    assert inputs.programs(force=True) == (clip, DRAWER)


def test_only_a_relative_word_naming_a_project_file_is_a_project_file(tmp_path: Path) -> None:
    (tmp_path / "render").mkdir()
    (tmp_path / "render" / "draw").write_text("print()")
    command = ("/usr/bin/python3", "-c", "import sys", "render/draw", "{out}", "missing/file", "--fps={fps}")
    assert programs.project_files(tmp_path, command) == {"render/draw": tmp_path / "render" / "draw"}


def test_a_word_that_links_out_of_the_project_is_refused(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.write_text("secret")
    root = tmp_path / "project"
    root.mkdir()
    os.symlink(outside, root / "draw")
    with pytest.raises(InputError, match="outside the project"):
        programs.project_files(root, ("draw",))
