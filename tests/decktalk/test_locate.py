"""Finding a key's line by reading the text, which is what makes a refusal openable."""

from __future__ import annotations

import tomllib

import pytest

from decktalk.locate import locate, locate_table, refused_line

FILE = """\
# The project file a person wrote.
[video]
width = 1920

[verify]
# How late a reveal may land.
cue_offset_max_ms = 120

[mix.loudness]
target_lufs = -16.0

[[section]]
title = "one"
"""


@pytest.mark.parametrize(
    ("key", "line"),
    [("video.width", 3), ("verify.cue_offset_max_ms", 7), ("mix.loudness.target_lufs", 10), ("section.title", 13)],
)
def test_a_key_is_found_on_the_line_it_was_written_on(key: str, line: int) -> None:
    assert locate(FILE, key) == line


def test_a_key_the_file_never_states_has_no_line() -> None:
    assert locate(FILE, "video.height") is None
    assert locate(FILE, "verify.width") is None


def test_a_dotted_key_at_the_top_of_the_file_is_the_same_key() -> None:
    assert locate("video.width = 1920\n", "video.width") == 1


def test_a_quoted_name_and_spacing_around_the_dot_do_not_hide_a_key() -> None:
    assert locate('[video]\n"width"  =  1920\n', "video.width") == 2
    assert locate("mix . loudness . target_lufs = -16.0\n", "mix.loudness.target_lufs") == 1


def test_a_table_header_is_found_by_its_own_name() -> None:
    assert locate_table(FILE, "verify") == 5
    assert locate_table(FILE, "mix.loudness") == 9
    assert locate_table(FILE, "audio") is None


def test_a_comment_that_looks_like_an_assignment_is_not_one() -> None:
    assert locate("[video]\n# width = 4096\nwidth = 1920\n", "video.width") == 3


def test_the_line_a_parser_refused_reaches_the_reader() -> None:
    with pytest.raises(tomllib.TOMLDecodeError) as caught:
        tomllib.loads('[video]\nwidth = 1920\npreset = "open\n')
    assert refused_line(caught.value) == 3
