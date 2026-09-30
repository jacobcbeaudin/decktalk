"""`.env` read once, every value handed back as a `Secret`, and no value ever printed."""

from __future__ import annotations

import contextvars
import dataclasses
from pathlib import Path

import pytest

from decktalk.errors import InputError
from decktalk.inputs.env import DOTENV, Env, read_dotenv, reading_dotenv


@pytest.fixture(autouse=True)
def author_machine():
    """Every test here reads `.env` as the author's own machine does, inside a run that allows it."""
    with reading_dotenv(True):
        yield


def write_env(tmp_path: Path, text: str) -> Path:
    path = tmp_path / ".env"
    path.write_text(text, encoding="utf-8")
    return path


def test_a_variable_set_to_nothing_is_unset_and_never_a_traceback(tmp_path):
    """Blanking a key is how an author turns it off, so it reads as unset rather than crashing."""
    env = Env(write_env(tmp_path, "ELEVENLABS_API_KEY=\nELEVENLABS_VOICE_ID=abc\n"), environ={})
    assert not env.get("ELEVENLABS_API_KEY")
    assert env.get("ELEVENLABS_VOICE_ID").reveal() == "abc"


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("K=plain", "plain"),
        ("K='single'", "single"),
        ('K="double"', "double"),
        ("export K=exported", "exported"),
        ("K=value # trailing", "value"),
        ("K='value' # trailing", "value"),
        ("K=", ""),
        ("K=''", ""),
        ("K=it's", "it's"),
    ],
)
def test_each_line_form_is_read_as_its_author_wrote_it(tmp_path, line, expected):
    """The reader is tiny, so every form it claims to accept is held here rather than in prose."""
    assert read_dotenv(write_env(tmp_path, f"{line}\n")).get("K", "") == expected


def test_a_comment_a_blank_line_and_a_line_with_no_equals_are_skipped(tmp_path):
    env = write_env(tmp_path, "# a comment\n\nnot a pair\nK=v\n")
    assert read_dotenv(env) == {"K": "v"}


def test_a_placeholder_counts_as_unset(tmp_path):
    """`init` writes `<your key>`, and a project that still holds it has no key."""
    env = Env(write_env(tmp_path, "ELEVENLABS_API_KEY=<your key>\n"), environ={})
    assert not env.get("ELEVENLABS_API_KEY")


def test_a_real_environment_variable_wins_over_the_file(tmp_path):
    env = Env(write_env(tmp_path, "K=from-file\n"), environ={"K": "from-environment"})
    assert env.get("K").reveal() == "from-environment"


def test_the_file_is_read_once_however_many_variables_are_asked_for(tmp_path, monkeypatch):
    """One file is read once, which is the rule every module in this layer follows."""
    path = write_env(tmp_path, "A=1\nB=2\n")
    env, reads = Env(path, environ={}), []
    real = Path.read_text
    monkeypatch.setattr(Path, "read_text", lambda self, **kw: (reads.append(self), real(self, **kw))[1])
    env.get("A"), env.get("B"), env.get("A")
    assert reads.count(path) == 1


def test_a_missing_variable_names_itself_the_file_and_the_next_action(tmp_path):
    """The error slot is filled by the raiser that knows, and no value reaches the message."""
    path = write_env(tmp_path, "ELEVENLABS_VOICE_ID=abc\n")
    env = Env(path, environ={})
    with pytest.raises(InputError) as info:
        env.require("ELEVENLABS_API_KEY", "ELEVENLABS_VOICE_ID")
    error = info.value
    assert str(error) == "ELEVENLABS_API_KEY is not set."
    assert error.hint is not None and ".env.example" in error.hint
    assert error.location is not None and error.location.file == path
    assert "abc" not in str(error) and "abc" not in (error.hint or "")


def test_the_environment_is_not_a_field_so_no_walker_can_reach_it(tmp_path):
    """A walker over `fields(Env)` must never be able to print a whole machine's environment."""
    env = Env(write_env(tmp_path, "K=v\n"), environ={"SECRET_TOKEN": "sk_live_0"})
    assert [f.name for f in dataclasses.fields(env)] == ["file"]
    assert "sk_live_0" not in repr(dataclasses.asdict(env))


def test_a_byte_order_mark_does_not_hide_the_first_key(tmp_path):
    """An editor that writes a mark would otherwise leave a correctly pasted key reading as unset."""
    path = tmp_path / ".env"
    path.write_bytes(b"\xef\xbb\xbfELEVENLABS_API_KEY=sk_real\nELEVENLABS_VOICE_ID=abc\n")
    values = read_dotenv(path)
    assert values == {"ELEVENLABS_API_KEY": "sk_real", "ELEVENLABS_VOICE_ID": "abc"}
    assert Env(path, environ={}).get("ELEVENLABS_API_KEY").reveal() == "sk_real"


def test_a_caller_outside_any_run_reads_no_env_file(tmp_path):
    """A host that reaches this layer without a machine gets the host's rule, which leaves `.env` unread."""
    env = Env(write_env(tmp_path, "ELEVENLABS_API_KEY=sk_tenant\n"), environ={})
    with reading_dotenv(False):
        assert not env.get("ELEVENLABS_API_KEY")


def test_the_default_is_to_leave_env_unread():
    """The default is what a fresh thread or a bare call sees, so it is the closed one."""
    assert contextvars.Context().run(DOTENV.get) is False
