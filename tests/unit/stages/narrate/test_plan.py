"""The take plan: identity by content, what a run reuses, and what a voiced run would cost."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from decktalk.artifacts import Take, Takes, Word, write_words
from decktalk.cli import main
from decktalk.jsonio import dumps
from decktalk.model import Project
from decktalk.model.script import Segment
from decktalk.settings import Settings
from decktalk.speech import register_speech_provider
from decktalk.stages.narrate import plan_totals, voiced_plan
from decktalk.stages.narrate.plan import (
    CACHED,
    SYNTHESIZE,
    is_cached,
    silent_hash,
    silent_plan,
    take_name,
    text_hash,
    words_name,
)


def _segment(text: str, index: int = 1) -> Segment:
    return Segment(index=index, title="T", slug="t", text=text)


def _fill(project: Project, digest: str, words: list[Word] | None = None) -> None:
    """Put the take of this digest on disk, which is the whole of the narration cache."""
    project.narration_dir.mkdir(parents=True, exist_ok=True)
    (project.narration_dir / take_name(digest)).write_bytes(b"mp3")
    write_words(project.narration_dir / words_name(digest), words or [Word("a", 0.1, 0.4)])


def test_a_take_is_named_by_the_content_that_produced_it(project):
    cfg = project.settings.narration
    digest = text_hash(_segment("Hello there."), cfg, "test-voice", {})
    assert take_name(digest) == f"{digest}.mp3" and words_name(digest) == f"{digest}.words.json"
    # The section number and the heading are not in the payload, so neither can re-voice anything.
    assert text_hash(_segment("Hello there.", index=9), cfg, "test-voice", {}) == digest
    other = Segment(index=9, title="A wholly different title", slug="other", text="Hello there.")
    assert text_hash(other, cfg, "test-voice", {}) == digest
    assert not is_cached(digest, project.narration_dir)
    _fill(project, digest)
    assert is_cached(digest, project.narration_dir)
    assert silent_hash(_segment("Hello there."), cfg).startswith("silent-")


def test_inserting_or_retitling_a_section_plans_no_new_take(make_project, base, voice, monkeypatch):
    """Renumbering moves no file: every take is already on disk under the hash of its own words.

    The first run writes through the real writer, so the name the writer chooses and the name the
    planner looks for are checked against each other rather than against one helper.
    """
    from decktalk.media import audio, ffmpeg
    from decktalk.stages.narrate import narrate

    monkeypatch.setattr(audio, "trailing_silence", lambda path, **kw: 1.0)
    monkeypatch.setattr(audio, "concat_audio", lambda files, out, **kw: out.write_bytes(b"narration"))
    monkeypatch.setattr(ffmpeg, "probe_duration", lambda path: 2.0)
    monkeypatch.setattr(ffmpeg, "decoded_duration", lambda path, sample_rate=48000: 2.0)
    toml, script = base
    project = make_project()
    plans, note = voiced_plan(project, project.script_sections()[1], model="m")
    assert note is None and {p.status for p in plans} == {SYNTHESIZE}
    assert narrate(project).synthesized == ["01", "02", "03"]
    grown = script.replace("## 1. Open", "## 1. A brand new opening\n\nWords nobody has voiced.\n\n## 2. Open")
    grown = grown.replace("## 2. Middle", "## 3. Middle").replace("## 3. Close", "## 4. Close")
    moved = make_project(toml=toml + '\n[[section]]\nnumber = 4\npage = "deck/index.html"\n', script=grown)
    after = {p.segment.index: p.status for p in voiced_plan(moved, moved.script_sections()[1], model="m")[0]}
    assert after == {1: SYNTHESIZE, 2: CACHED, 3: CACHED, 4: CACHED}
    # Retitling alone changes no number and still voices nothing.
    retitled = make_project(toml=toml, script=script.replace("## 2. Middle", "## 2. A better heading"), name="proj")
    assert {p.status for p in voiced_plan(retitled, retitled.script_sections()[1], model="m")[0]} == {CACHED}


def test_a_plan_prices_what_it_would_send(project, voice):
    plans, _note = voiced_plan(project, project.script_sections()[1], model="m")
    totals = plan_totals(plans, project.settings.narration, project.voice.price_per_1000_characters)
    sent = sum(len(p.segment.text) for p in plans)
    assert totals[SYNTHESIZE] == 3 and totals["characters_sent"] == sent
    # The stitching context travels with the text, so it is counted apart rather than hidden.
    assert totals["characters_with_context"] > sent
    assert totals["price_per_1000_characters"] == 0.30
    assert totals["estimated_cost"] == round(sent / 1000 * 0.30, 2)
    # A project that names no rate is given no dollar figure rather than a free-looking zero.
    assert plan_totals(plans, project.settings.narration)["estimated_cost"] is None


def test_the_plan_carries_the_request_body_it_would_post(project, voice):
    (plan, *_rest) = voiced_plan(project, project.script_sections()[1], model="eleven_v3")[0]
    body = plan.to_dict(project.settings.narration)["request"]
    assert body["text"] == plan.segment.tts_text(project.settings.narration)
    assert body["model_id"] == "eleven_v3" and body["previous_text"] is None
    assert set(body) == {"text", "model_id", "voice_settings", "output_format", "previous_text", "next_text"}


def test_a_plan_without_a_voice_reports_what_it_cannot_check(make_project, base):
    """With no provider, a section already in the index is unknown and every other one needs a take."""
    toml, script = base
    project = make_project(toml=toml.replace('provider = "test-voice"', 'provider = "not-a-voice"'))
    project.narration_dir.mkdir(parents=True)
    index = Takes(script="script.md", model="m", output_format="mp3")
    index.sections["01"] = Take(1, "Open", "h.mp3", "h.words.json", "h", 2, 1.0, 2.0)
    index.save(project.takes_path)
    plans, note = voiced_plan(project, project.script_sections()[1], model="m")
    assert note is not None and "not registered" in note
    assert {p.segment.key: p.status for p in plans} == {"01": "unknown", "02": SYNTHESIZE, "03": SYNTHESIZE}


def test_a_run_without_voice_caches_its_placeholders_by_content_too(project):
    plans = silent_plan(project, project.script_sections()[1])
    assert {p.status for p in plans} == {SYNTHESIZE}
    for plan in plans:
        assert plan.digest is not None and plan.request is None
        _fill(project, plan.digest)
    assert {p.status for p in silent_plan(project, project.script_sections()[1])} == {CACHED}


def test_narrate_dry_run_json_prices_the_run_and_writes_nothing(project, voice, capsys):
    assert main(["narrate", "--dry-run", "--json", "-p", str(project.root)]) == 0
    doc = json.loads(capsys.readouterr().out)
    assert doc["command"] == "narrate" and doc["ok"] is True
    plan = doc["narrate"]
    assert plan["voice"]["provider"] == "test-voice" and plan["note"] is None and plan["takes"] is None
    assert [row["status"] for row in plan["sections"]] == [SYNTHESIZE] * 3
    assert plan["totals"]["estimated_cost"] == round(plan["totals"]["characters_sent"] / 1000 * 0.30, 2)
    assert not project.narration_dir.exists(), "a dry run wrote to build/narration"
    assert main(["narrate", "--dry-run", "-p", str(project.root)]) == 0
    out = capsys.readouterr().out
    assert f"{plan['totals']['characters_sent']} characters sent" in out
    assert f"About ${plan['totals']['estimated_cost']:.2f} at $0.30 per 1,000." in out


def test_estimated_seconds_are_the_words_at_the_configured_rate():
    segment = _segment("one two three four five six seven")
    assert segment.estimated_seconds(Settings().narration) == pytest.approx(3.0, abs=0.05)


def test_two_sections_with_the_same_words_share_one_take(project, voice, monkeypatch):
    """One digest is one take, so the second section of identical words is covered and never paid for."""
    from decktalk.media import audio, ffmpeg

    monkeypatch.setattr(audio, "trailing_silence", lambda path, **kw: 1.0)
    monkeypatch.setattr(audio, "concat_audio", lambda files, out, **kw: out.write_bytes(b"narration"))
    monkeypatch.setattr(ffmpeg, "probe_duration", lambda path: 2.0)
    monkeypatch.setattr(ffmpeg, "decoded_duration", lambda path, sample_rate=48000: 2.0)
    script = (project.root / "script.md").read_text(encoding="utf-8")
    (project.root / "script.md").write_text(script.replace("It steps down the bowl.", "A bowl. [beat] A ball."))
    twin = Project.load(project.root, environ={})
    plans, _note = voiced_plan(twin, twin.script_sections()[1], model="m")
    assert [p.digest for p in plans][0] == [p.digest for p in plans][1]
    assert [p.status for p in plans] == [SYNTHESIZE, CACHED, SYNTHESIZE]
    from decktalk.stages.narrate import narrate

    result = narrate(twin)
    assert len(voice.sent) == 2 and result.synthesized == ["01", "03"] and result.cached == ["02"]


def test_a_forced_run_voices_every_target_again_and_lifts_the_refusal(project, voice, monkeypatch):
    """`--force` is the one flag that deliberately spends on a take that is already on disk."""
    from decktalk.media import audio, ffmpeg
    from decktalk.stages.narrate import narrate

    monkeypatch.setattr(audio, "trailing_silence", lambda path, **kw: 1.0)
    monkeypatch.setattr(audio, "write_clicks", lambda path, *a, **kw: Path(path).write_bytes(b"clicks"))
    monkeypatch.setattr(audio, "concat_audio", lambda files, out, **kw: out.write_bytes(b"narration"))
    monkeypatch.setattr(ffmpeg, "probe_duration", lambda path: 2.0)
    monkeypatch.setattr(ffmpeg, "decoded_duration", lambda path, sample_rate=48000: 2.0)
    assert narrate(project).synthesized == ["01", "02", "03"] and len(voice.sent) == 3
    assert narrate(project).cached == ["01", "02", "03"] and len(voice.sent) == 3
    forced = narrate(project, force=True)
    assert forced.synthesized == ["01", "02", "03"] and len(voice.sent) == 6
    assert [p.reason for p in forced.plans] == ["forced"] * 3
    # Forcing also lifts the refusal that keeps a run without voice off a paid take.
    assert narrate(project, silent=True, force=True).synthesized == ["01", "02", "03"]


def test_a_rehearsal_plans_a_rehearsal_and_never_prices_a_voiced_run(project, voice):
    """`--dry-run --no-voice` describes the run it is the plan for, which spends nothing."""
    from decktalk.stages.narrate import narrate

    result = narrate(project, dry_run=True, silent=True)
    assert [p.digest for p in result.plans] == [p.digest for p in silent_plan(project, project.script_sections()[1])]
    totals = plan_totals(result.plans, project.settings.narration, result.rate)
    # A rehearsal sends nothing, so it is priced at nothing rather than at what a voiced run would cost.
    assert totals["characters_sent"] == 0 and totals["estimated_cost"] == 0.0
    assert all(p.request is None for p in result.plans) and result.note is None


def test_a_plan_without_a_voice_still_prices_what_it_would_send(make_project, base):
    """The approval stop must never read as free because the key is missing."""
    toml, script = base
    project = make_project(toml=toml.replace('provider = "test-voice"', 'provider = "not-a-voice"'))
    plans, note = voiced_plan(project, project.script_sections()[1], model="m")
    totals = plan_totals(plans, project.settings.narration, project.voice.price_per_1000_characters)
    assert note is not None and totals[SYNTHESIZE] == 3
    assert totals["characters_sent"] > 0 and totals["estimated_cost"] > 0
    assert all(p.request is not None for p in plans)


def test_a_section_whose_cache_cannot_be_checked_is_priced_apart_and_never_hidden(make_project, base, voice):
    """A project that has paid before must see what the run can cost, not only what it certainly costs."""
    from decktalk.report import plan_table

    toml, script = base
    paid = make_project()
    plans, _note = voiced_plan(paid, paid.script_sections()[1], model="m")
    index = Takes(script="script.md", model="m", output_format="mp3")
    for plan in plans:
        seg = plan.segment
        assert plan.digest is not None
        index.sections[seg.key] = Take(
            seg.index, seg.title, take_name(plan.digest), words_name(plan.digest), plan.digest,
            seg.word_count, 1.0, 2.0, spoken=seg.spoken,
        )  # fmt: skip
    index.save(paid.takes_path)
    # The same project read back with no provider, which is what a machine with no key sees.
    blind = make_project(toml=toml.replace('provider = "test-voice"', 'provider = "not-a-voice"'), name="proj")
    plans, note = voiced_plan(blind, blind.script_sections()[1], model="m")
    totals = plan_totals(plans, blind.settings.narration, blind.voice.price_per_1000_characters)
    assert note is not None and totals["unknown"] == 3 and totals[SYNTHESIZE] == 0
    assert totals["characters_sent"] == 0 and totals["characters_unchecked"] > 0
    assert totals["most_it_can_cost"] > 0
    line = plan_table(plans, blind.settings.narration, blind.voice.price_per_1000_characters)
    assert f"Up to ${totals['most_it_can_cost']:.2f} if the 3 unknown section(s) are voiced too." in line


def test_an_inserted_section_says_it_has_no_take_rather_than_that_its_text_changed(make_project, base, voice):
    """A row whose words moved to another section says nothing about the section that took its number."""
    toml, script = base
    project = make_project()
    plans, _note = voiced_plan(project, project.script_sections()[1], model="m")
    for plan in plans:
        assert plan.digest is not None
        _fill(project, plan.digest)
    index = Takes(script="script.md", model="m", output_format="mp3")
    for plan in plans:
        seg = plan.segment
        assert plan.digest is not None
        index.sections[seg.key] = Take(
            seg.index, seg.title, take_name(plan.digest), words_name(plan.digest), plan.digest,
            seg.word_count, 1.0, 2.0, spoken=seg.spoken,
        )  # fmt: skip
    index.save(project.takes_path)
    grown = script.replace("## 2. Middle", "## 2. Brand new\n\nWords nobody has voiced.\n\n## 3. Middle")
    grown = grown.replace("## 3. Close", "## 4. Close")
    moved = make_project(toml=toml + '\n[[section]]\nnumber = 4\npage = "deck/index.html"\n', script=grown)
    after = {
        p.segment.index: (p.status, p.reason) for p in voiced_plan(moved, moved.script_sections()[1], model="m")[0]
    }
    assert after[2] == (SYNTHESIZE, "no take yet")
    assert [after[n][0] for n in (1, 3, 4)] == [CACHED] * 3


def test_the_dry_run_payload_carries_no_value_read_from_the_environment(make_project, base):
    """A canary voice id read from .env reaches the digest and nothing else, which is the whole rule."""
    from decktalk.stages.narrate import narrate

    class Canary:
        name = "canary"

        def __init__(self, voice: str) -> None:
            self.voice = voice

        def speak(self, request):  # pragma: no cover - a dry run never sends
            raise AssertionError("a dry run must send nothing")

        def cache_key(self, request) -> str:
            return f"canary\n{self.voice}\n{request.model}\n{request.output_format}"

    register_speech_provider(
        "canary", lambda context: Canary(context.secrets.require("ELEVENLABS_VOICE_ID")[0].reveal())
    )
    toml, _script = base
    project = make_project(toml=toml.replace('provider = "test-voice"', 'provider = "canary"'))
    (project.root / ".env").write_text("ELEVENLABS_API_KEY=CANARY-KEY\nELEVENLABS_VOICE_ID=CANARY-VOICE-ID\n")
    from decktalk.model import Project

    with_env = Project.load(project.root, environ={})
    result = narrate(with_env, dry_run=True)
    payload = dumps(result.to_dict(with_env.root))
    assert all(plan.digest is not None for plan in result.plans), "the canary reached the digest"
    assert "CANARY-VOICE-ID" not in payload and "CANARY-KEY" not in payload


def test_a_refusal_built_while_the_provider_is_made_never_carries_its_value(make_project, base):
    """Every refusal raised while the voice is built becomes `narrate.note`, so none may quote a value."""
    from decktalk.model import Project
    from decktalk.stages.narrate import narrate

    toml, _script = base
    project = make_project(toml=toml.replace('provider = "test-voice"', 'provider = "elevenlabs"'))
    (project.root / ".env").write_text("ELEVENLABS_API_KEY=CANARY-KEY\nELEVENLABS_VOICE_ID=CANARY-VOICE-ID\n")
    environ = {"DECKTALK_ELEVENLABS_API_BASE": "https://evil.test/v1/CANARY-PATH-TOKEN"}
    result = narrate(Project.load(project.root, environ=environ), dry_run=True)
    payload = dumps(result.to_dict(project.root))
    assert result.note is not None and "api_base" in result.note
    assert "CANARY-PATH-TOKEN" not in payload and "evil.test" not in payload and "CANARY-KEY" not in payload


def test_the_hash_payload_shape_is_frozen_without_any_secret():
    """The order and the joins of the digest payload, pinned on a runner that holds no key.

    `tests/test_take_hash.py` holds the founder's own film against the same function and needs his
    voice id, so this is the net that runs everywhere and fails the moment the payload is reshaped.
    """
    segment = Segment(index=1, title="T", slug="t", text="Hello there.")
    assert text_hash(segment, Settings().narration, "k", {"a": 1}) == "84616982a3d332df"
