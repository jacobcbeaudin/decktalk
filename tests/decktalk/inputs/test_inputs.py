"""Loading one project: the sections it declares, the keys it may not name, and what it refuses."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from decktalk.artifacts import PLACEHOLDER_PREFIX, CueTimes, Words
from decktalk.artifacts.words import words_file
from decktalk.errors import ErrorCode, InputError, NotBuiltError
from decktalk.inputs import PAID_FOLDERS as LOADED_PAID_FOLDERS
from decktalk.inputs import Inputs
from decktalk.inputs.document import ClipSection
from decktalk.inputs.env import reading_dotenv
from decktalk.results import CueTime, SectionCues, Word
from support.projects import MINIMAL_TOML, write_project

TITLED_CLIP_TOML = """
[project]
name = "t"

[[section]]
number = 1
chapter = "Open"
page = "deck/index.html"

[[section]]
number = 2
chapter = "The edit"
clip = "media/before.mov"
words = "media/before.words.json"

[[section]]
number = 3
chapter = "The edit"
page = "deck/index.html"

[[section]]
number = 4
chapter = "Close"
page = "deck/index.html"
"""

MID_CLIP_TOML = """
[project]
name = "t"

[[section]]
number = 1
page = "deck/index.html"

[[section]]
number = 2
clip = "media/broll.mp4"

[[section]]
number = 3
page = "deck/index.html"

[[section]]
number = 4
page = "deck/index.html"
"""


def test_project_loads_sections_in_order(tmp_path):
    p = Inputs.load(write_project(tmp_path), environ={})
    assert [s.number for s in p.document.sections] == [0, 1, 2]
    assert p.document.clip_numbers == {0}
    assert p.document.page_sections[0].scene == "1"
    assert p.document.page_sections[1].scene == "2"  # defaults to the section number
    assert p.document.name == "t" and p.workspace.film == tmp_path / "build" / "final" / "t.mp4"


@pytest.mark.parametrize(
    "toml, message",
    [
        ("[[section]]\nnumber = 1\n", "needs 'page'"),
        ("[[section]]\nnumber = 1\npage = 'deck/a.html'\nclip = 'b.mp4'\n", "either 'clip' or 'page'"),
        ("[[section]]\nnumber = 1\npage = 'deck/a.html'\n[[section]]\nnumber = 1\npage = 'deck/a.html'\n", "duplicate"),
        (
            "[[section]]\nnumber = 1\npage = 'deck/a.html'\nlead_seconds = -1\n",
            "'lead_seconds' must be 0 or more, got -1",
        ),
        ("[[section]]\nnumber = 1\npage = 'deck/a.html'\nhold_seconds = -0.5\n", "'hold_seconds' must be 0 or more"),
        ("[[section]]\nnumber = 1\npage = 'deck/a.html'\nrecord_margin_seconds = 'lots'\n", "must be"),
        ("[[section]]\nnumber = 1\npage = 'deck/a.html'\n[transition]\ndips = [[1, 9]]\n", "does not exist"),
        ("[[section]]\nnumber = 1\npage = 'deck/a.html'\n[bogus]\nx = 1\n", "unknown table"),
        (
            "[[section]]\nnumber = 1\npage = 'deck/a.html'\n[[mix.effects]]\nfile = 'x.mp3'\nsection = 1\n",
            "'cue' is required and is not there.",
        ),
    ],
)
def test_project_validation_messages(tmp_path, toml, message):
    with pytest.raises(InputError, match=re.escape(message) if message.endswith(".") else message):
        Inputs.load(write_project(tmp_path, toml), environ={})


def test_project_allows_clips_at_both_edges(tmp_path):
    toml = (
        "[[section]]\nnumber = 0\nclip = 'open.mp4'\n[[section]]\nnumber = 1\npage = 'deck/a.html'\n"
        "[[section]]\nnumber = 2\npage = 'deck/a.html'\n[[section]]\nnumber = 9\nclip = 'close.mp4'\n"
    )
    p = Inputs.load(write_project(tmp_path, toml), environ={})
    assert p.document.clip_numbers == {0, 9} and [s.number for s in p.document.page_sections] == [1, 2]


def test_project_allows_clips_between_page_sections(tmp_path):
    toml = (
        "[[section]]\nnumber = 1\npage = 'deck/a.html'\n[[section]]\nnumber = 2\nclip = 'broll.mp4'\n"
        "[[section]]\nnumber = 3\nclip = 'more.mp4'\n[[section]]\nnumber = 4\npage = 'deck/a.html'\n"
        "hold_seconds = 1\n"
    )
    p = Inputs.load(write_project(tmp_path, toml), environ={})
    assert [s.number for s in p.document.sections] == [1, 2, 3, 4]
    assert p.document.clip_numbers == {2, 3} and [s.number for s in p.document.page_sections] == [1, 4]


def test_project_missing_file_names_the_file_and_the_next_action(tmp_path):
    """The message is one sentence, and where to look and what to do next have slots of their own."""
    with pytest.raises(InputError) as raised:
        Inputs.load(tmp_path, environ={})
    error = raised.value
    assert str(error) == "decktalk.toml is not there."
    assert error.location is not None
    assert error.location.file == Path("decktalk.toml") and error.location.line is None
    assert "decktalk init" in (error.hint or "")


def test_project_tuning_tables_reach_settings(tmp_path):
    p = Inputs.load(write_project(tmp_path, MINIMAL_TOML + "\n[video]\npreset = 'ultrafast'\n"), environ={})
    assert p.settings.video.preset == "ultrafast"


def test_project_env_reads_dotenv_and_ignores_placeholders(tmp_path, monkeypatch):
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    root = write_project(tmp_path)
    (root / ".env").write_text("ELEVENLABS_API_KEY=<fill me>\nOTHER_VARIABLE='abc' # comment\n", encoding="utf-8")
    p = Inputs.load(root, environ={})
    with reading_dotenv(True):
        assert not p.env.get("ELEVENLABS_API_KEY")  # the placeholder counts as unset
        assert p.env.get("OTHER_VARIABLE").reveal() == "abc"
        # A secret is named by its variable and never by its value, in a repr as in an error.
        assert repr(p.env.get("OTHER_VARIABLE")) == "<secret OTHER_VARIABLE>"
        with pytest.raises(InputError, match="ELEVENLABS_API_KEY") as info:
            p.env.require("ELEVENLABS_API_KEY", "OTHER_VARIABLE")
    assert "abc" not in str(info.value)


def test_project_notes_every_unknown_key_and_suggests_the_closest(tmp_path, monkeypatch):
    monkeypatch.setenv("DECKTALK_CONFIG", str(tmp_path / "no-user-config.toml"))
    toml = (
        "[project]\nname = 't'\nscirpt = 'script.md'\n"
        "[voice]\nstabilty = 0.4\n"
        "[[section]]\nnumber = 1\npage = 'deck/a.html'\nscnee = 2\nslate_seconds = 3\n"
        "[[section]]\nnumber = 2\nclip = 'b.mp4'\nzebra = 1\n"
        "[mix]\nmusic_dbb = -20\n"
        "[score.music]\nprompt = 'calm'\nsecond = 60\n"
        "[video]\npresett = 'veryfast'\n"
    )
    p = Inputs.load(write_project(tmp_path, toml), environ={})
    page = "."
    # Each is a note the caller reports on its run, because the library never prints.
    assert p.notes == (
        f"decktalk.toml: [project]: ignoring unknown key 'scirpt' (did you mean 'script'?){page}",
        f"decktalk.toml: [[section]] number=1: ignoring unknown key 'scnee' (did you mean 'scene'?){page}",
        "decktalk.toml: [[section]] number=1: ignoring 'slate_seconds', which applies only to a clip section",
        f"decktalk.toml: [[section]] number=2: ignoring unknown key 'zebra'{page}",
        f"decktalk.toml: [mix]: ignoring unknown key 'music_dbb' (did you mean 'music_db'?){page}",
        f"decktalk.toml: [score.music]: ignoring unknown key 'second' (did you mean 'duration_seconds'?){page}",
        f"decktalk.toml: [video]: ignoring unknown key 'presett' (did you mean 'preset'?){page}",
        f"decktalk.toml: [voice]: ignoring unknown key 'stabilty' (did you mean 'elevenlabs.stability'?){page}",
    )
    # A warning, not an error: the load succeeds and every misspelled key keeps its default.
    assert p.settings.elevenlabs.stability == 0.55
    assert p.settings.video.preset == "medium"
    assert p.document.score.music is not None
    assert p.settings.score.music.duration_seconds == 360


def test_a_table_reads_every_key_its_dataclass_declares(tmp_path):
    """Each key is written once, as a field, so a table's reader and its class cannot drift apart."""
    toml = (
        "[project]\nname = 't'\nscript = 'script.md'\ncues = 'cues.json'\nbuild = 'build'\nlanguage = 'fr'\n"
        "[voice]\nprovider = 'elevenlabs'\nmodel = 'm'\n"
        "[elevenlabs]\nstability = 0.5\nprice_per_1000_characters = 0.3\n"
        "[transition]\ndips = [[1, 2]]\ndip_seconds = 0.2\npage_fades_in = true\n"
        "[[section]]\nnumber = 1\npage = 'deck/a.html'\n"
        "[[section]]\nnumber = 2\npage = 'deck/b.html'\n"
        "[mix]\nmusic_db = -20\n"
        "[audio]\ntarget_lufs = -16\ntrue_peak_max_dbtp = -1.5\nrange_max_lu = 9\n"
        "[[mix.effects]]\nfile = 'a.wav'\nsection = 1\ncue = '1.1'\ndb = -16\noffset = 0.1\ncaption = 'a chime'\n"
        "[score.effects.tap]\ntext = 'a tap'\nout = 'tap.mp3'\nduration_seconds = 0.5\n"
        "prompt_influence = 0.4\nmodel = 'sound'\n"
        "[score.music]\nprompt = 'calm'\nduration_seconds = 60\nforce_instrumental = true\nout = 'm.mp3'\n"
        "model = 'music'\n"
    )
    p = Inputs.load(write_project(tmp_path, toml), environ={})
    assert p.notes == ()
    assert p.document.language == "fr"
    # `[mix]` is shared, so the document reads its half and neither warns about the other.
    assert (p.settings.voice.provider, p.settings.voice.model) == ("elevenlabs", "m")
    assert p.settings.elevenlabs.price_per_1000_characters == 0.3
    assert p.settings.audio.range_max_lu == 9
    assert (p.settings.score.music.duration_seconds, p.settings.score.music.model) == (60, "music")
    assert p.document.mix.effects[0].caption == "a chime"


def test_a_section_with_no_chapter_is_titled_by_its_script_heading(tmp_path):
    """The author already wrote a heading, so the mp4's chapter carries it rather than a number."""
    toml = (
        "[project]\nname = 't'\n"
        "[[section]]\nnumber = 1\npage = 'deck/a.html'\n"
        "[[section]]\nnumber = 2\npage = 'deck/b.html'\nchapter = 'Its own'\n"
    )
    root = write_project(tmp_path, toml)
    (root / "script.md").write_text("## 1. What a cue is\n\nOne.\n\n## 2. The edit\n\nTwo.\n", encoding="utf-8")
    p = Inputs.load(root, environ={})
    assert p.chapters() == {1: "What a cue is", 2: "Its own"}
    (root / "script.md").unlink()
    assert Inputs.load(root, environ={}).chapters() == {1: "Section 1", 2: "Its own"}


def test_seamless_parses_on_any_section_but_the_first(tmp_path):
    toml = (
        "[[section]]\nnumber = 1\npage = 'deck/a.html'\n"
        "[[section]]\nnumber = 2\nclip = 'b.mp4'\nseamless = true\n"
        "[[section]]\nnumber = 3\npage = 'deck/a.html'\nseamless = true\n"
    )
    p = Inputs.load(write_project(tmp_path, toml), environ={})
    assert [s.seamless for s in p.document.sections] == [False, True, True] and not p.notes
    first = toml.replace("number = 1\npage = 'deck/a.html'\n", "number = 1\npage = 'deck/a.html'\nseamless = true\n")
    with pytest.raises(InputError, match="number=1: seamless is set on the first section"):
        Inputs.load(write_project(tmp_path, first), environ={})
    with pytest.raises(InputError, match="'seamless' must be bool"):
        Inputs.load(write_project(tmp_path, toml.replace("seamless = true", "seamless = 1")), environ={})


def test_a_clip_section_reads_its_words_key(tmp_path):
    titled = Inputs.load(write_project(tmp_path, TITLED_CLIP_TOML), environ={}).document.sections[1]
    assert isinstance(titled, ClipSection) and titled.words == "media/before.words.json"
    plain = Inputs.load(write_project(tmp_path, MID_CLIP_TOML), environ={}).document.sections[1]
    assert isinstance(plain, ClipSection) and plain.words is None


def test_section_lead_and_tail_keys_parse_on_page_sections_only(tmp_path):
    toml = (
        "[[section]]\nnumber = 1\npage = 'deck/a.html'\nlead_seconds = 1.5\ntail_seconds = 2\n"
        "[[section]]\nnumber = 2\nclip = 'c.mp4'\nlead_seconds = 1\n"
    )
    p = Inputs.load(write_project(tmp_path, toml), environ={})
    page = p.document.page_sections[0]
    assert (page.lead_seconds, page.tail_seconds) == (1.5, 2.0)
    assert p.lead_seconds(1) == 1.5 and p.lead_seconds(2) == 0.0
    said = "decktalk.toml: [[section]] number=2: ignoring 'lead_seconds', which applies only to a page section"
    assert p.notes == (said,)
    assert Inputs.load(write_project(tmp_path, MINIMAL_TOML), environ={}).document.page_sections[0].tail_seconds is None


def test_every_path_key_of_the_document_goes_through_the_one_check(tmp_path):
    """The rule is the method, so a key added later cannot miss it by being read the other way."""
    base = "[project]\nname = 't'\n\n[[section]]\nnumber = 1\npage = 'deck/index.html'\n"
    cases = {
        "clip": "[[section]]\nnumber = 2\nclip = '/etc/hosts'\n",
        "words": "[[section]]\nnumber = 2\nclip = 'a.mp4'\nwords = '/etc/hosts'\n",
        "music": "[mix]\nmusic = '/etc/hosts'\n",
        "ambience": "[mix]\nambience = '/etc/hosts'\n",
        "slate": "[mix]\nslate = '/etc/hosts'\n",
        "file": "[[mix.effects]]\nfile = '/etc/hosts'\nsection = 1\ncue = '1.1a'\n",
        "out": "[score.ambience]\ntext = 'x'\nout = '/etc/hosts'\n",
    }
    for index, (key, table) in enumerate(cases.items()):
        root = tmp_path / f"case{index}"
        root.mkdir()
        (root / "decktalk.toml").write_text(base + table, encoding="utf-8")
        (root / "script.md").write_text("## 1. A\n\nHi.\n", encoding="utf-8")
        with pytest.raises(InputError) as info:
            Inputs.load(root, environ={})
        assert f"'{key}' names a path outside the project" in str(info.value), key
        assert "/etc/hosts" not in str(info.value), key


def test_a_page_a_project_declares_is_refused_when_it_names_somewhere_else(tmp_path):
    """`page` is opened by the browser and read by the cue scan, so it is checked like the rest."""
    root = tmp_path / "project"
    root.mkdir()
    toml = "[project]\nname = 't'\n\n[[section]]\nnumber = 1\npage = '/etc/hosts'\n"
    (root / "decktalk.toml").write_text(toml, encoding="utf-8")
    (root / "script.md").write_text("## 1. A\n\nHi.\n", encoding="utf-8")
    with pytest.raises(InputError, match="'page' names a path outside the project"):
        Inputs.load(root, environ={})


def test_a_changed_page_touches_only_the_sections_that_play_it(tmp_path):
    """A watch loop rebuilds what moved, so this is the one reading it takes off a changed file."""
    toml = (
        "[project]\nname = 't'\n"
        "[[section]]\nnumber = 1\npage = 'deck/one.html'\n"
        "[[section]]\nnumber = 2\npage = 'deck/two.html'\n"
        "[[section]]\nnumber = 3\npage = 'deck/one.html'\n"
    )
    inputs = Inputs.load(write_project(tmp_path, toml), environ={})
    assert inputs.sections_touching(tmp_path / "deck" / "one.html") == (1, 3)
    assert inputs.sections_touching(tmp_path / "deck" / "nothing.html") == ()


def test_a_changed_script_or_cue_file_touches_every_section(tmp_path):
    inputs = Inputs.load(write_project(tmp_path, MINIMAL_TOML), environ={})
    for name in ("script.md", "cues.json", "decktalk.toml"):
        assert inputs.sections_touching(tmp_path / name) == (0, 1, 2), name


def test_a_changed_clip_touches_the_section_that_plays_it(tmp_path):
    inputs = Inputs.load(write_project(tmp_path, MINIMAL_TOML), environ={})
    assert inputs.sections_touching(tmp_path / "media" / "open.mp4") == (0,)


def test_the_artifacts_are_read_off_the_workspace_and_are_none_before_a_build(tmp_path):
    inputs = Inputs.load(write_project(tmp_path, MINIMAL_TOML), environ={})
    assert inputs.takes() is None
    assert inputs.cue_times() is None
    assert inputs.cuts() is None
    assert inputs.recording_log("01") is None


def test_a_take_words_are_shifted_by_their_own_section_lead(tmp_path):
    """A cue resolves against the section clock, which starts before the first word rather than on it."""
    toml = MINIMAL_TOML + "\n[narration]\nlead_seconds = 0.5\n"
    inputs = Inputs.load(write_project(tmp_path, toml), environ={})
    spoken = Words(words=(Word(word="hello", start=0.0, end=0.4),))
    spoken.write(inputs.workspace.takes / words_file("abc"))
    assert inputs.words(1, "abc") == (Word(word="hello", start=0.5, end=0.9),)
    assert inputs.words(0, "abc") == (Word(word="hello", start=0.0, end=0.4),)  # a clip has no lead
    assert inputs.words(1, "nothing") == ()


@pytest.mark.parametrize(
    ("digest", "refusal", "code"),
    [("abc", InputError, ErrorCode.INPUT), (f"{PLACEHOLDER_PREFIX}abc", NotBuiltError, ErrorCode.NOT_BUILT)],
)
def test_a_take_words_that_do_not_read_are_paid_exactly_when_the_take_is(tmp_path, digest, refusal, code):
    """Only voicing a take again gives its words back, and nobody paid for a placeholder's."""
    inputs = Inputs.load(write_project(tmp_path, MINIMAL_TOML), environ={})
    path = inputs.workspace.words_path(digest)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(refusal) as refused:
        inputs.words(1, digest)
    assert refused.value.code is code
    assert path.read_text(encoding="utf-8") == "{not json"


def test_a_path_is_published_relative_to_the_project(tmp_path):
    inputs = Inputs.load(write_project(tmp_path, MINIMAL_TOML), environ={})
    assert inputs.relative(tmp_path / "deck" / "index.html") == Path("deck/index.html")
    assert inputs.path("deck/index.html") == tmp_path / "deck" / "index.html"


def test_the_voice_never_reads_a_clip_section(tmp_path):
    root = write_project(tmp_path, MINIMAL_TOML)
    (root / "script.md").write_text("## 0. Open\n\nA.\n\n## 1. One\n\nB.\n\n## 2. Two\n\nC.\n", encoding="utf-8")
    inputs = Inputs.load(root, environ={})
    assert [s.index for s in inputs.script()] == [0, 1, 2]
    assert [s.index for s in inputs.spoken()] == [1, 2]


def test_the_preview_document_is_empty_before_the_cues_are_resolved(tmp_path):
    assert Inputs.load(write_project(tmp_path, MINIMAL_TOML), environ={}).preview_cues() == {"sections": []}


def test_the_preview_document_names_the_scene_each_section_plays(tmp_path):
    inputs = Inputs.load(write_project(tmp_path, MINIMAL_TOML), environ={})
    CueTimes(
        sections=(
            SectionCues(
                section=1,
                key="01",
                estimated=False,
                cues=(CueTime(cue="1.1:open", phrase="hello", seconds=1.5, offset=0.0),),
            ),
        )
    ).write(inputs.workspace.cue_times_path)
    assert inputs.preview_cues() == {
        "sections": [{"key": "01", "scene": "1", "cues": [{"cue": "1.1:open", "at": 1.5}]}]
    }


SERVED_TOML = """
[project]
name = "demo"

[[section]]
number = 1
page = "deck/one.html"

[[section]]
number = 2
clip = "media/broll.mp4"
words = "media/broll.words.json"

[mix]
music = "media/bed.mp3"
slate = "media/slate.png"

[[mix.effects]]
file = "media/chime.wav"
section = 1
cue = "1.1:open"
"""


def test_the_origin_serves_the_deck_and_the_files_the_document_declares(tmp_path):
    """The recorder and a preview an author leaves running read this one list, so it is asked once."""
    inputs = Inputs.load(write_project(tmp_path, SERVED_TOML), environ={})
    assert inputs.served_paths() == (
        "deck",
        "media/broll.mp4",
        "media/broll.words.json",
        "media/bed.mp3",
        "media/slate.png",
        "media/chime.wav",
    )


def test_the_origin_never_offers_the_project_file_the_credential_or_the_build(tmp_path):
    served = Inputs.load(write_project(tmp_path, SERVED_TOML), environ={}).served_paths()
    assert "decktalk.toml" not in served
    assert ".env" not in served
    assert "build" not in served


@pytest.mark.parametrize("page", ["index.html", "./index.html"])
def test_a_page_at_the_project_root_is_refused_because_its_directory_is_the_whole_project(tmp_path, page):
    """The origin serves a page's directory, and this one holds the script, the cues, `.env` and `build/`."""
    toml = SERVED_TOML.replace('page = "deck/one.html"', f'page = "{page}"')
    with pytest.raises(InputError, match="sits at the project root"):
        Inputs.load(write_project(tmp_path, toml), environ={})


@pytest.mark.parametrize(
    ("build", "page"),
    [
        ("deck", "deck/one.html"),
        ("deck/out", "deck/one.html"),
        ("build", "build/one.html"),
        ("out", "out/deck/one.html"),
    ],
)
def test_a_build_directory_that_shares_a_served_directory_is_refused(tmp_path, build, page):
    """A page reads what its origin serves, and the build holds the takes, the event lines and the recordings."""
    toml = SERVED_TOML.replace('name = "demo"', f'name = "demo"\nbuild = "{build}"').replace(
        'page = "deck/one.html"', f'page = "{page}"'
    )
    with pytest.raises(InputError, match="build directory") as refused:
        Inputs.load(write_project(tmp_path, toml), environ={})
    assert refused.value.code is ErrorCode.INPUT


def test_a_declared_name_that_folds_to_the_project_root_is_never_offered(tmp_path):
    toml = SERVED_TOML.replace('music = "media/bed.mp3"', 'music = "./"').replace(
        'slate = "media/slate.png"', 'slate = "./media/slate.png"'
    )
    served = Inputs.load(write_project(tmp_path, toml), environ={}).served_paths()
    assert served == ("deck", "media/broll.mp4", "media/broll.words.json", "media/slate.png", "media/chime.wav")


# ---- every path the project names stays inside it ----------------------------------------------

LINKED_TOML = """
[project]
name = "t"

[[section]]
number = 1
page = "deck/index.html"
"""


def _outside(tmp_path: Path) -> tuple[Path, Path]:
    """A project directory and a file beside it that no project file may reach."""
    root, secret = tmp_path / "project", tmp_path / "host.txt"
    root.mkdir()
    secret.write_text("## 1. Stolen\n\nSECRET-HOST-LINE\n", encoding="utf-8")
    return write_project(root, LINKED_TOML), secret


def test_a_script_linked_out_of_the_project_is_refused_before_a_line_is_read(tmp_path: Path) -> None:
    root, secret = _outside(tmp_path)
    (root / "script.md").symlink_to(secret)
    with pytest.raises(InputError, match="outside the project"):
        Inputs.load(root, environ={}).script()


def test_a_cue_file_linked_out_of_the_project_is_refused(tmp_path: Path) -> None:
    """The file it links to is a valid cue file, so the refusal is the link's and not the parser's."""
    root, _secret = _outside(tmp_path)
    elsewhere = tmp_path / "host-cues.json"
    elsewhere.write_text(json.dumps({"sections": {"1": {"cues": []}}}), encoding="utf-8")
    (root / "cues.json").symlink_to(elsewhere)
    with pytest.raises(InputError) as refused:
        Inputs.load(root, environ={}).cues()
    assert refused.value.location is not None and refused.value.location.file == Path("cues.json")


def test_a_page_linked_out_of_the_project_is_refused(tmp_path: Path) -> None:
    root, secret = _outside(tmp_path)
    (root / "deck").mkdir()
    (root / "deck" / "index.html").symlink_to(secret)
    inputs = Inputs.load(root, environ={})
    with pytest.raises(InputError, match="outside the project"):
        inputs.path(inputs.document.page_sections[0].page)


def test_a_build_directory_linked_out_of_the_project_is_refused_at_load(tmp_path: Path) -> None:
    root, _secret = _outside(tmp_path)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (root / "build").symlink_to(elsewhere, target_is_directory=True)
    with pytest.raises(InputError, match="outside the project"):
        Inputs.load(root, environ={})


# ---- the machine's take store --------------------------------------------------------------------


def store_named(named: str) -> dict[str, dict[str, str]]:
    """The machine's own tables naming its take store, as a machine file would."""
    return {"narration": {"store_dir": named}}


@pytest.mark.parametrize("named", ["shared-takes", "./takes", "../takes"])
def test_a_relative_take_store_is_refused_at_load(tmp_path: Path, named: str) -> None:
    """A relative store would name a different folder inside every project, which `git add .` then commits."""
    root = write_project(tmp_path)
    with pytest.raises(InputError, match=r"\[narration\] store_dir") as refused:
        Inputs.load(root, environ={}, machine=store_named(named))
    assert "relative" in str(refused.value)
    assert "~" in (refused.value.hint or "")


def test_a_take_store_that_starts_with_a_tilde_is_under_the_machines_home(tmp_path: Path) -> None:
    (tmp_path / "proj").mkdir()
    root = write_project(tmp_path / "proj")
    loaded = Inputs.load(root, environ={"HOME": str(tmp_path / "home")}, machine=store_named("~/dt-takes"))
    assert loaded.workspace.store == tmp_path / "home" / "dt-takes"


def test_a_take_store_inside_the_project_is_refused(tmp_path: Path) -> None:
    """Paid takes there would be a second takes directory with none of its rules, committed by `git add .`."""
    root = write_project(tmp_path)
    with pytest.raises(InputError, match="inside this project"):
        Inputs.load(root, environ={}, machine=store_named(str(tmp_path / "machine-takes")))


def test_a_project_reads_the_take_store_its_machine_keeps_unless_a_key_names_another(tmp_path: Path) -> None:
    (tmp_path / "proj").mkdir()
    root = write_project(tmp_path / "proj")
    standard = tmp_path / "data" / "takes"
    assert Inputs.load(root, environ={}, store=standard).workspace.store == standard
    named = Inputs.load(root, environ={}, machine=store_named(str(tmp_path / "named")), store=standard)
    assert named.workspace.store == tmp_path / "named"
    assert Inputs.load(root, environ={}).workspace.store is None


# ---- a paid folder and the build directory -------------------------------------------------------


def with_folder(table: str, key: str, named: str, *, build: str | None = None) -> str:
    """The minimal project with one paid folder named, and the build directory moved when `build` names one."""
    project = f'name = "t"\nbuild = "{build}"' if build is not None else 'name = "t"'
    return MINIMAL_TOML.replace('name = "t"', project) + f'\n[{table}]\n{key} = "{named}"\n'


PAID_FOLDERS = [("narration", "takes_dir", "takes"), ("score", "dir", "score")]
"""Every setting that names a folder of paid records, with its default name."""


def test_every_paid_folder_setting_goes_through_the_one_check() -> None:
    """The load reads each paid folder off one list, so these tests cover every key on it."""
    assert set(LOADED_PAID_FOLDERS) == {f"{table}.{key}" for table, key, _ in PAID_FOLDERS}


@pytest.mark.parametrize(("table", "key", "standard"), PAID_FOLDERS)
@pytest.mark.parametrize(("build", "under"), [(None, "build"), ("out", "out")])
@pytest.mark.parametrize("deeper", [True, False])
def test_a_paid_folder_inside_the_build_directory_is_refused_at_load(
    tmp_path: Path, table: str, key: str, standard: str, build: str | None, under: str, deeper: bool
) -> None:
    """The build directory is a cache that is deleted, so a paid record kept there would be bought again."""
    named = f"{under}/{standard}" if deeper else under
    root = write_project(tmp_path, with_folder(table, key, named, build=build))
    with pytest.raises(InputError) as refused:
        Inputs.load(root, environ={})
    assert refused.value.code is ErrorCode.INPUT
    message = str(refused.value)
    assert f"[{table}] {key} is {named}" in message
    assert f"[project] build is {under}" in message
    assert "deleted" in message
    assert "paid" in message


@pytest.mark.parametrize(("table", "key"), [(table, key) for table, key, _ in PAID_FOLDERS])
@pytest.mark.parametrize(
    ("build", "named"), [(None, "buildings"), (None, "builds/x"), ("out", "outtakes"), ("out", "build/x")]
)
def test_a_paid_folder_that_only_shares_letters_with_the_build_directory_is_accepted(
    tmp_path: Path, table: str, key: str, build: str | None, named: str
) -> None:
    root = write_project(tmp_path, with_folder(table, key, named, build=build))
    loaded = Inputs.load(root, environ={})
    held = {"narration": loaded.workspace.takes, "score": loaded.workspace.score_dir}[table]
    assert held == root / named


@pytest.mark.parametrize(("table", "key", "standard"), PAID_FOLDERS)
@pytest.mark.parametrize("deeper", [True, False])
def test_a_build_directory_inside_a_paid_folder_is_refused_at_load(
    tmp_path: Path, table: str, key: str, standard: str, deeper: bool
) -> None:
    """Every build file would land in the folder the project commits, among the paid records."""
    build = f"{standard}/build" if deeper else standard
    root = write_project(tmp_path, with_folder(table, key, standard, build=build))
    with pytest.raises(InputError) as refused:
        Inputs.load(root, environ={})
    assert refused.value.code is ErrorCode.INPUT
    message = str(refused.value)
    assert f"[{table}] {key} is {standard}" in message
    assert f"[project] build is {build}" in message


@pytest.mark.parametrize(("table", "key", "standard"), PAID_FOLDERS)
def test_a_build_directory_that_only_shares_letters_with_a_paid_folder_is_accepted(
    tmp_path: Path, table: str, key: str, standard: str
) -> None:
    build = f"{standard}helf"
    root = write_project(tmp_path, with_folder(table, key, standard, build=build))
    assert Inputs.load(root, environ={}).workspace.build == root / build


def test_the_default_paid_folders_sit_beside_the_build_directory_and_load(tmp_path: Path) -> None:
    loaded = Inputs.load(write_project(tmp_path), environ={})
    assert loaded.workspace.takes == loaded.root / "takes"
    assert loaded.workspace.score_dir == loaded.root / "score"
