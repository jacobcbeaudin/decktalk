"""A value that may be used and never shown, attacked by every way a value usually escapes."""

from __future__ import annotations

import base64
import copy
import json
import logging
import pickle
import pprint
import re
import secrets
import subprocess
import traceback
from dataclasses import asdict, dataclass
from pathlib import Path

import pytest
from pydantic_core import PydanticSerializationError
from pytest_httpserver import HTTPServer
from werkzeug import Request, Response

import decktalk
from decktalk.artifacts import Stored
from decktalk.cli.app import main
from decktalk.errors import DeckTalkError, ErrorCode, ErrorInfo, ProviderError
from decktalk.findings import Applicability, Code, CommandFix, Finding, Location
from decktalk.machine import Machine
from decktalk.media import audio
from decktalk.secret import Secret, redact, redacted, register, register_environment, secret_name
from decktalk.settings import MACHINE_FILE_VARIABLE
from support.service import Service
from support.speech import alignment

VALUE = "sk_sentinel_key_that_must_never_print"


@dataclass(frozen=True)
class Holder:
    """A dataclass that holds one, which is how every real holder holds one."""

    name: str
    key: Secret


class Leak(Stored):
    """An artifact that tried to carry one, which is the one door every file under `build/` goes through."""

    name: str
    key: object


def test_the_value_is_readable_only_through_reveal():
    """One call reads a secret, so every place a secret leaves DeckTalk is one call a reader finds."""
    secret = Secret(VALUE, "ELEVENLABS_API_KEY")
    assert secret.reveal() == VALUE
    assert secret.name == "ELEVENLABS_API_KEY"


def test_a_secret_prints_as_the_variable_it_came_from():
    """A secret is named by its variable name and never by its value."""
    secret = Secret(VALUE, "ELEVENLABS_API_KEY")
    assert repr(secret) == "<secret ELEVENLABS_API_KEY>"
    assert str(secret) == "<secret ELEVENLABS_API_KEY>"
    assert repr(Secret(VALUE)) == "<secret>"


@pytest.mark.parametrize(
    "show",
    [
        repr,
        str,
        lambda s: f"{s}",
        lambda s: f"{s!r}",
        lambda s: f"{s!s}",
        lambda s: "%s" % (s,),  # noqa: UP031  (the spelling a log call uses is the point)
        lambda s: "%r" % (s,),  # noqa: UP031
        "{}".format,
        pprint.pformat,
        lambda s: json.dumps(s, default=str),
        lambda s: repr(Holder("eleven", s)),
        lambda s: json.dumps({"name": "eleven", "key": s}, default=str),
    ],
)
def test_no_ordinary_way_of_showing_a_value_shows_this_one(show):
    """These are the ways a value reaches a terminal, a log or a payload, and none of them works."""
    assert VALUE not in show(Secret(VALUE, "ELEVENLABS_API_KEY"))


def test_a_secret_refuses_every_copy_protocol():
    """`pickle` carries a value between processes, so a worker pool added later carries none."""
    secret = Secret(VALUE, "ELEVENLABS_API_KEY")
    attempts = (
        secret.__getstate__,
        lambda: secret.__reduce_ex__(2),
        lambda: pickle.dumps(secret),
        lambda: copy.copy(secret),
        lambda: copy.deepcopy(secret),
    )
    for attempt in attempts:
        with pytest.raises(TypeError, match="not serializable"):
            attempt()
    with pytest.raises(TypeError):
        asdict(Holder("eleven", secret))  # asdict deep-copies, so it refuses as well


def test_a_secret_is_not_json_and_is_refused_rather_than_written(tmp_path):
    """The artifact writer is how every build file reaches the disk, so one holding a secret raises instead."""
    with pytest.raises(TypeError):
        json.dumps({"key": Secret(VALUE)})
    out = tmp_path / "leak.json"
    with pytest.raises(PydanticSerializationError):
        Leak(name="eleven", key=Secret(VALUE)).write(out)
    assert not out.exists()
    assert not list(tmp_path.iterdir())


def test_a_traceback_of_a_failure_holding_one_shows_no_value():
    """An error reaches a log and a terminal, and `-v` prints the whole traceback with its locals."""
    secret = Secret(VALUE, "ELEVENLABS_API_KEY")
    try:
        raise RuntimeError(f"could not reach the provider with {secret}")
    except RuntimeError as exc:
        text = "".join(traceback.format_exception(exc))
    assert VALUE not in text and "<secret ELEVENLABS_API_KEY>" in text


def test_a_logging_call_shows_no_value(caplog):
    """Every log line goes to stderr, where a CI job keeps it."""
    with caplog.at_level(logging.WARNING, logger="decktalk"):
        logging.getLogger("decktalk").warning("key %s is set", Secret(VALUE, "ELEVENLABS_API_KEY"))
    assert VALUE not in caplog.text and "<secret ELEVENLABS_API_KEY>" in caplog.text


def test_an_unset_variable_is_falsy_and_a_set_one_is_not():
    """A caller checks whether a variable is set before it spends, and never by reading the value."""
    assert not Secret("", "ELEVENLABS_API_KEY")
    assert Secret(VALUE, "ELEVENLABS_API_KEY")
    assert len(Secret(VALUE)) == len(VALUE)


def test_two_secrets_are_equal_by_value_and_a_plain_string_is_never_one():
    """Comparing against a plain string would be the leak the type exists to stop."""
    assert Secret(VALUE, "A") == Secret(VALUE, "B")
    assert Secret(VALUE) != Secret("other")
    assert Secret(VALUE) != VALUE
    assert len({Secret(VALUE), Secret(VALUE)}) == 1


# ---- the registry every line and every error is built through -----------------------------------

REGISTERED = "sk_registry_canary_4f1e9b2c7d"


def test_a_secret_registers_its_value_so_a_sentence_holding_it_is_redacted():
    Secret(REGISTERED, "ELEVENLABS_API_KEY")
    assert redact(f"sent {REGISTERED} to the host") == "sent <secret ELEVENLABS_API_KEY> to the host"


def test_a_published_name_read_beside_the_key_is_held_and_not_registered():
    """A name a reader needs in every URL it is in is never redacted, even when it is held as a `Secret`."""
    Secret("voice-canary-7f3b21", "DECKTALK_VOICE_ID")
    assert redact("/v1/text-to-speech/voice-canary-7f3b21") == "/v1/text-to-speech/voice-canary-7f3b21"


def test_a_short_value_is_never_registered_so_ordinary_words_survive():
    Secret("dog", "PET_KEY")
    assert redact("the dog barked") == "the dog barked"


@pytest.mark.parametrize(
    ("name", "held"),
    [
        ("ELEVENLABS_API_KEY", True),
        ("GITHUB_TOKEN", True),
        ("AWS_SECRET", True),
        ("HOST_DB_PASSWORD", True),
        ("password_file", True),
        ("PATH", False),
        ("DECKTALK_VOICE_ID", False),
        ("KEYBOARD", False),
    ],
)
def test_a_variable_is_a_credential_when_its_name_says_so(name: str, held: bool):
    assert secret_name(name) is held


def test_a_hosts_environment_registers_every_credential_it_names():
    register_environment({"HOST_DB_PASSWORD": "hunter2-canary-77aa", "HOME": "/home/host-canary-home"})
    assert redact("hunter2-canary-77aa") == "<secret HOST_DB_PASSWORD>"
    assert redact("/home/host-canary-home") == "/home/host-canary-home"


def test_a_value_inside_a_longer_one_is_replaced_after_the_longer_one():
    register("canary-outer-canary-inner-9c", "OUTER_TOKEN")
    register("canary-inner-9c", "INNER_TOKEN")
    assert redact("x canary-outer-canary-inner-9c y") == "x <secret OUTER_TOKEN> y"


def test_every_string_of_a_nested_value_is_redacted():
    Secret(REGISTERED, "ELEVENLABS_API_KEY")
    given = {"a": [REGISTERED, (f"x{REGISTERED}",)], "b": 3}
    assert redacted(given) == {"a": ["<secret ELEVENLABS_API_KEY>", ("x<secret ELEVENLABS_API_KEY>",)], "b": 3}


def test_a_refusal_quoting_a_secret_holds_only_its_name():
    Secret(REGISTERED, "ELEVENLABS_API_KEY")
    refused = ProviderError(f"HTTP 401: bad key {REGISTERED}", hint=f"check {REGISTERED}")
    assert REGISTERED not in str(refused) and REGISTERED not in (refused.hint or "")
    assert REGISTERED not in ErrorInfo.of(refused).model_dump_json()


# ---- the canary run: no path of a run lets a key out ----------------------------------------------

CANARY_TOML = """
[project]
name = "canary"

[voice]
provider = "elevenlabs"
dollars_per_1000_characters = 0.30

[[section]]
number = 1
page = "deck/index.html"
scene = "1"

[[section]]
number = 2
page = "deck/index.html"
scene = "2"
"""

CANARY_SCRIPT = "# Notes\n\n## 1. Open\n\nA bowl and a ball.\n\n## 2. Close\n\nThe ball rests.\n"


class HostileVoice:
    """A voice service on a local socket, which answers from a script of hostile replies and then speaks.

    Every reply that can quote the key does: a refusal echoes it, a busy answer and a gateway page carry
    it in their bodies, and a redirect sends the request to another origin, where the key must not go.
    """

    def __init__(self, server: HTTPServer, key: str) -> None:
        self.server, self.key = server, key
        self.script: list[str] = []
        self.landed: list[dict[str, str]] = []
        server.expect_request(re.compile(r"/v1/text-to-speech/.*")).respond_with_handler(self.speak)
        server.expect_request("/landed").respond_with_handler(self.land)

    def spoken(self, request: Request) -> Response:
        # A redirect turns the POST into a GET with no body, and the landing still speaks.
        text = (request.get_json(silent=True) or {}).get("text", "moved")
        body = {"audio_base64": base64.b64encode(b"mp3").decode(), "alignment": alignment(text)}
        return Response(json.dumps(body), 200, content_type="application/json")

    def speak(self, request: Request) -> Response:
        said = request.headers.get("xi-api-key", "")
        reply = self.script.pop(0) if self.script else "speak"
        if reply == "refuse":
            return Response(json.dumps({"detail": f"invalid api key {said}"}), 401, content_type="application/json")
        if reply == "busy":
            return Response(f"slow down, {said}", 429, {"Retry-After": "1"})
        if reply == "gateway":
            return Response(f"<html>{said}</html>", 200)
        if reply == "redirect":
            port = self.server.port
            return Response(status=302, headers={"Location": f"http://localhost:{port}/landed"})
        return self.spoken(request)

    def land(self, request: Request) -> Response:
        self.landed.append({name.lower(): value for name, value in request.headers.items()})
        return self.spoken(request)


def _canary_project(root: Path, base: str, key: str) -> Path:
    """A project whose key is in `.env`, on a machine whose own file sends the voice to `base`."""
    (root / "deck").mkdir(parents=True)
    (root / "deck" / "index.html").write_text("<p>deck</p>", encoding="utf-8")
    (root / "decktalk.toml").write_text(CANARY_TOML, encoding="utf-8")
    (root.parent / "machine.toml").write_text(f'[elevenlabs]\nbase_url = "{base}"\n', encoding="utf-8")
    (root / "script.md").write_text(CANARY_SCRIPT, encoding="utf-8")
    (root / ".env").write_text(f"ELEVENLABS_API_KEY={key}\n", encoding="utf-8")
    return root


def _chain(error: BaseException | None) -> str:
    """Every sentence an exception and its causes carry, which is what a traceback would print."""
    said: list[str] = []
    while error is not None:
        said.append(str(error))
        if isinstance(error, DeckTalkError):
            said.append(ErrorInfo.of(error).model_dump_json())
        error = error.__cause__ or error.__context__
    return "\n".join(said)


def _refused(project: decktalk.Project, voice: HostileVoice, script: list[str]) -> ProviderError:
    """The refusal a paid narrate raises when the voice answers from `script`."""
    voice.script = script
    with pytest.raises(ProviderError) as refused:
        project.narrate(spend=True)
    return refused.value


@pytest.mark.usefixtures("fake_ffmpeg", "waits")
def test_no_path_of_a_run_lets_a_key_reach_a_log_a_file_an_error_or_a_terminal(
    tmp_path: Path,
    service: Service,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The end-to-end proof: a local fake voice answers with every hostile reply, and no canary survives.

    The key is handed over twice, in the machine's environment and in the project's `.env`, beside a
    host credential of the machine's own. No request leaves this machine and no real key exists.
    """
    monkeypatch.setattr(audio, "sound_end", lambda _path, **_levels: 0.8)
    key = f"sk_canary_{secrets.token_hex(12)}"
    host = f"pw_canary_{secrets.token_hex(8)}"
    voice = HostileVoice(service, key)
    root = _canary_project(tmp_path / "canary", service.url_for("/v1"), key)
    environ = {"ELEVENLABS_API_KEY": key, "DECKTALK_VOICE_ID": "voice-canary", "HOST_DB_PASSWORD": host}
    here = Machine.of(
        environ=environ,
        machine_file=tmp_path / "machine.toml",
        cwd=root,
        cache_dir=tmp_path / "cache",
        dotenv=True,
    )
    project = decktalk.open(root, machine=here)
    raised: list[BaseException] = []
    lines: list[str] = []
    here.events.subscribe(lambda event: lines.append(event.model_dump_json()))

    with caplog.at_level("DEBUG", logger="decktalk"):
        project.check(pages=False, frames=False)
        raised.append(_refused(project, voice, ["refuse"]))
        # A gateway page arrived where speech was paid for, so it is raised and never asked for again.
        raised.append(_refused(project, voice, ["gateway"]))
        assert "possibly charged" in str(raised[-1])
        voice.script = ["busy", "redirect"]
        assert project.narrate(spend=True).ok
        # A fix whose command fails and says the key and the host's password on its way out.
        failing = subprocess.CompletedProcess([], 2, b"", f"Traceback\nKeyError: {key} {host}\n".encode())
        monkeypatch.setattr("decktalk.machine.fixes.subprocess.run", lambda argv, **_: failing)
        fix = CommandFix(title="t", applicability=Applicability.SAFE, command=("decktalk", "install"))
        found = Finding(code=Code.FILE_MISSING, message="m", location=Location(where="ffmpeg"), fix=fix)
        applied = project.apply(found)
        assert not applied.fixes[0].applied
        # A run that fails on something DeckTalk did not mean to raise, with the key in its message.
        with pytest.raises(RuntimeError) as broke, here._run(root=root, events_dir=root / "build" / "events"):
            raise RuntimeError(f"a bug holding {key} and {host}")
        # The exception is the test's own and keeps its words, and what DeckTalk makes of it does not.
        internal = ErrorInfo.of_failure(broke.value).model_dump_json()

    # The command line in every mode a caller reads it in, against the same fake and the same key.
    monkeypatch.setenv("ELEVENLABS_API_KEY", key)
    monkeypatch.setenv("DECKTALK_VOICE_ID", "voice-canary")
    monkeypatch.setenv("HOST_DB_PASSWORD", host)
    monkeypatch.setenv(MACHINE_FILE_VARIABLE, str(tmp_path / "machine.toml"))
    printed: list[str] = []
    for mode in (["--json"], ["--events"], ["-v"], []):
        voice.script = ["refuse"]
        capsys.readouterr()
        code = main(["-p", str(root), *mode, "narrate", "--spend", "--replace-voiced"])
        out, err = capsys.readouterr()
        assert code == ErrorCode.PROVIDER.exit_code, (mode, out, err)
        printed.append(out + err)

    assert voice.landed and all("xi-api-key" not in headers for headers in voice.landed)
    files = "".join(path.read_text(encoding="utf-8") for path in (root / "build" / "events").glob("*.jsonl"))
    logged = "".join(f"{record.getMessage()} {getattr(record, 'data', '')}" for record in caplog.records)
    for where, text in {
        "events files": files,
        "stream": "".join(lines),
        "logging": logged,
        "exceptions": "".join(_chain(error) for error in raised) + internal,
        "command line": "".join(printed),
        "fix outcome": applied.model_dump_json(),
    }.items():
        assert key not in text, where
        assert host not in text, where
    assert "<secret HOST_DB_PASSWORD>" in files and '"code":"PROVIDER"' in files
