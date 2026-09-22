"""Section starts, quiet cuts and the seam into a section that carries the picture before it."""

from __future__ import annotations

from decktalk.artifacts import Take, Takes, Word, write_words
from decktalk.cli import main
from decktalk.cli.schema import Seam, read_envelope
from decktalk.jsonio import read_as
from decktalk.media import audio
from decktalk.model import Project
from decktalk.stages.verify import verify
from decktalk.stages.verify.seams import cut_checks, seam_checks, start_checks
from decktalk.verdicts import Verdict


def test_verify_flags_a_pop_at_the_cut_into_a_seamless_section(verify_project, pages_toml, monkeypatch, capsys):
    from decktalk.media import frames as frames_module

    seamless_toml = pages_toml.replace(
        'number = 2\npage = "deck/index.html"\n', 'number = 2\npage = "deck/index.html"\nseamless = true\n'
    )
    seamless_toml = seamless_toml.replace(
        'number = 3\npage = "deck/index.html"\n', 'number = 3\npage = "deck/index.html"\nseamless = true\n'
    )
    p = verify_project({}, toml=seamless_toml)
    calls: list[tuple[float, float, dict]] = []
    share = [0.05]

    def changed(path, t1, t2, **kw):
        calls.append((round(t1, 3), round(t2, 3), kw))
        return share[0]

    monkeypatch.setattr(frames_module, "changed_pixels_percent", changed)
    result = verify(p)
    # Section 1 dips out over 0.16 s, so the last frame compared sits before the dip. Section 3 is not assembled.
    assert calls == [(4.78, 5.0, {"level": 40, "width": 480, "height": 270})]
    (row,) = result.seams
    assert (row.key, row.cut_at, row.verdict, row.ok) == ("02", 5.0, Verdict.OK, True) and result.ok

    share[0] = 0.5
    result = verify(p)
    assert [c.verdict for c in result.seams] == [Verdict.POP_AT_CUT] and not result.ok
    [seam_json] = result.to_dict(p.root)["seams"]
    seam = read_as(Seam, seam_json)
    assert (seam.key, seam.cut_at, seam.last_at, seam.first_at, seam.changed_percent, seam.verdict) == (
        "02", 5.0, 4.78, 5.0, 0.5, Verdict.POP_AT_CUT,
    )  # fmt: skip
    # A judged row carries the one sentence with its measured number, which the envelope lifts.
    assert seam.detail is not None and "0.50 percent" in seam.detail
    assert main(["-p", str(p.root), "verify"]) == 1
    assert Verdict.POP_AT_CUT.label in capsys.readouterr().out
    assert main(["-p", str(p.root), "verify", "--json"]) == 1
    doc = read_envelope(capsys.readouterr().out)
    assert (doc.findings.certain, doc.findings.uncertain) == (1, 0)
    assert doc.payload.seams[0].verdict is Verdict.POP_AT_CUT
    [row] = doc.findings.items
    assert row.verdict is Verdict.POP_AT_CUT and row.detail and row.section == 2

    # A straight cut compares the frame just before the cut, and a section without the key gets no row.
    calls.clear()
    (p.root / "decktalk.toml").write_text(seamless_toml + "\n[transition]\ndips = []\n", encoding="utf-8")
    assert [c.last_at for c in verify(Project.load(p.root, environ={})).seams] == [4.94]
    (p.root / "decktalk.toml").write_text(pages_toml, encoding="utf-8")
    assert verify(Project.load(p.root, environ={})).seams == []


def test_a_section_that_opens_on_black_is_a_certain_finding(verify_project, monkeypatch):
    from decktalk.media import frames as frames_module

    project = verify_project({"01": "a@1.0"})
    monkeypatch.setattr(frames_module, "luma_at", lambda path, t, crop=None: (1.0, 2.0))
    rows = start_checks(project, project.final, {"01": 0.0, "02": 5.0})
    assert [(r.key, r.verdict, r.ok) for r in rows] == [("01", Verdict.BLACK, False), ("02", Verdict.BLACK, False)]
    assert rows[0].to_dict()["ymax"] == 2.0


def _two_takes() -> Takes:
    """Section 01 sounds to the end of its 5.0 s take, and section 02 for 4.5 s with a 0.3 s tail."""
    takes = Takes(script="script.md", model="m", output_format="mp3")
    takes.sections["01"] = Take(1, "A", "h1.mp3", "h1.words.json", "h1", 1, 5.0, 5.0, speech_end_seconds=4.5)
    takes.sections["02"] = Take(
        2, "B", "h2.mp3", "h2.words.json", "h2", 1, 5.0, 5.0, speech_end_seconds=4.5, sound_end_seconds=4.5,
        tail_seconds=0.3,
    )  # fmt: skip
    return takes


def test_a_cut_is_quiet_when_the_narration_has_stopped(verify_project, monkeypatch):
    project = verify_project({"01": "a@1.0"})
    project.narration_path.write_bytes(b"x")
    write_words(project.takes_dir / "h1.words.json", [Word("Hi", 0.7, 1.0)])
    takes = _two_takes()
    windows: list[tuple[float, float]] = []

    def rms(level):
        return lambda path, start, length: windows.append((round(start, 3), round(length, 3))) or level

    monkeypatch.setattr(audio, "rms_db", rms(-60.0))
    # The picture of section 02 starts on a whole frame at 5.04 s, and its narration still plays from 5.0 s on.
    quiet = cut_checks(project, takes, {"01": 0.0, "02": 5.04})
    assert [(r.key, r.cut_at, r.verdict, r.ok) for r in quiet] == [
        ("01", 5.0, Verdict.QUIET, True),
        ("02", 9.8, Verdict.QUIET, True),
    ]
    # Each window is the last 0.15 s of the section's span, the samples that play just before its cut_at.
    assert windows == [(4.85, 0.15), (9.65, 0.15)]
    monkeypatch.setattr(audio, "rms_db", rms(-3.0))
    one, two = cut_checks(project, takes, {"01": 0.0, "02": 5.04})
    assert (one.verdict, one.ok, two.verdict) == (Verdict.SPEECH_AT_CUT, False, Verdict.SPEECH_AT_CUT)
    # The sentence names the cut out of the section, into the section that follows or the end of the film.
    assert one.detail == (
        "section 01's narration still sounds at -3.0 dBFS just before the cut into section 02 at 5.000s, "
        "so a word is cut off."
    )
    assert two.detail is not None and "just before the end of the film at 9.800s" in two.detail
    # With no narration track there is nothing to listen to, so there are no rows at all.
    assert cut_checks(project, None, {"01": 0.0}) == []


def test_a_hold_moves_the_narration_after_it_and_is_named_at_the_cut_before_it(verify_project, pages_toml, monkeypatch):
    """A hold pauses the narration, so the cut after it is placed on the next run and the one before it is the hold."""
    held = pages_toml.replace(
        'number = 1\npage = "deck/index.html"\n', 'number = 1\npage = "deck/index.html"\nhold_seconds = 1\n'
    )
    project = verify_project({"01": "a@1.0"}, toml=held)
    project.narration_path.write_bytes(b"x")
    monkeypatch.setattr(audio, "rms_db", lambda path, start, length: -3.0)
    one, two = cut_checks(project, _two_takes(), {"01": 0.0, "02": 6.0})
    # Section 02's narration resumes at 6.0 s on its own first frame, one second after the track holds it.
    assert (one.cut_at, one.held, two.cut_at, two.held) == (5.0, True, 10.8, False)
    assert one.detail is not None and "just before its hold at 5.000s" in one.detail


def test_a_section_that_sets_no_seamless_key_has_no_seam_row(verify_project):
    project = verify_project({"01": "a@1.0"})
    assert seam_checks(project, project.final, {"01": 0.0, "02": 5.0}) == []


def test_a_dark_section_start_carries_the_sentence_with_its_measured_luma():
    """A judged row says what is wrong with the number in it, which is the rule for all four shapes."""
    from decktalk.stages.verify.seams import StartCheck

    dark = StartCheck(key="02", start=14.48, probe_at=14.68, yavg=1.5, ymax=6.0, ok=False)
    assert dark.verdict is Verdict.BLACK
    detail = dark.detail
    assert detail is not None
    assert "section 02" in detail and "14.480" in detail and "1.5" in detail and "6.0" in detail
    row = dark.to_dict("build/out/t.mp4")
    assert row["detail"] == detail and row["where"] == "build/out/t.mp4"
    # A start that is fine carries no sentence, because it is not a finding.
    assert StartCheck(key="01", start=0.0, probe_at=0.2, yavg=100.0, ymax=200.0, ok=True).detail is None
