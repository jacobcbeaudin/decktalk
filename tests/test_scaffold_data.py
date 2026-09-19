"""What `decktalk init` writes: the starter, the packaged examples, the skills and AGENTS.md.

Every project in the wheel is checked here for the one rule that ties its four files together: the
section number, its scene and the prefix of its cue ids agree, every cue id is named in the page,
and every cue phrase is in its own section of the script. The browser tests play every slide of
every packaged page. `tests/e2e/test_scaffold_build.py` builds them.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from browser_pages import chromium_page

from decktalk.errors import ConfigError
from decktalk.model import Project
from decktalk.model.script import parse_script
from decktalk.scaffold import EXAMPLES, SKILL_NAMES, example, init
from decktalk.scaffold.examples import STARTER
from decktalk.scaffold.skills import LINK_DIR, SKILLS_DIR
from decktalk.toolchain.assets import RUNTIME_FILE, package_file, runtime_path

SHIPPED = [None, *[e.name for e in EXAMPLES if e.shipped]]
"""Every project `init` can write: the starter, then each example that has a project behind it."""


@pytest.fixture
def offline(tmp_path, monkeypatch):
    """A machine with no cache and no user configuration, so a project loads from its own files."""
    monkeypatch.setenv("DECKTALK_CACHE_DIR", str(tmp_path / "empty-cache"))
    monkeypatch.setenv("DECKTALK_CONFIG", str(tmp_path / "no-user-config.toml"))


def write(tmp_path: Path, example_name: str | None = None, **kw) -> Path:
    name = example_name or STARTER
    return init(tmp_path / name, name=name, example_name=example_name, **kw).root


# ---- the starter ---------------------------------------------------------------------


def test_the_starter_is_three_page_sections_with_one_equation(tmp_path, offline, caplog):
    """`init` with no example writes a small project: three sections, one page, one equation."""
    root = write(tmp_path)
    caplog.clear()
    with caplog.at_level("WARNING", logger="decktalk"):
        project = Project.load(root, environ={})
    assert [r.getMessage() for r in caplog.records] == []
    assert [(s.number, s.scene) for s in project.page_sections] == [(1, "1"), (2, "2"), (3, "3")]
    assert project.clip_sections == []
    assert [s.chapter for s in project.sections] == ["Open", "How it works", "Close"]
    page = (root / "deck" / "index.html").read_text(encoding="utf-8")
    assert page.count("data-tex=") == 1
    assert "data-tex-display" in page
    # Attributes only: the page declares its scenes in markup and runs no script of its own.
    assert "DeckTalk.scene(" not in page and "render:" not in page
    assert page.count("<template data-slide=") == 3
    # The whole starter is small enough to read in a sitting, which the reviews asked for.
    assert len(page.splitlines()) < 200


def test_the_starter_names_the_project_everywhere_it_should(tmp_path, offline):
    root = init(tmp_path / "p", name="my-first-deck").root
    assert 'name = "my-first-deck"' in (root / "decktalk.toml").read_text(encoding="utf-8")
    assert "My First Deck" in (root / "script.md").read_text(encoding="utf-8")
    assert "My First Deck" in (root / "deck" / "index.html").read_text(encoding="utf-8")
    for path in (root / "decktalk.toml", root / "script.md", root / "deck" / "index.html", root / "AGENTS.md"):
        assert "__NAME__" not in path.read_text(encoding="utf-8"), path
        assert "__TITLE__" not in path.read_text(encoding="utf-8"), path


# ---- every packaged project ----------------------------------------------------------


@pytest.mark.parametrize("example_name", SHIPPED)
def test_a_project_arrives_whole_with_the_runtime_and_katex(tmp_path, offline, example_name):
    """Every packaged file arrives, the two dotfiles get their dots, and nothing else rides along."""
    root = write(tmp_path, example_name)
    source = package_file(f"template/{example_name and f'examples/{example_name}' or STARTER}")
    wanted = {p.relative_to(source) for p in source.rglob("*") if p.is_file()}
    renamed = {Path(".gitignore"), Path(".env.example")} | {
        p for p in wanted if p.name not in ("gitignore", "env.example")
    }
    got = {p.relative_to(root) for p in root.rglob("*") if p.is_file()}
    assert renamed <= got, renamed - got
    assert (root / "deck" / RUNTIME_FILE).read_bytes() == runtime_path().read_bytes()
    assert (root / "deck" / "katex" / "katex.min.js").is_file()
    assert (root / "deck" / "katex" / "LICENSE").is_file()
    assert not (root / "gitignore").exists() and not (root / "env.example").exists()


@pytest.mark.parametrize("example_name", SHIPPED)
def test_the_four_files_of_a_project_agree(tmp_path, offline, example_name):
    """Each cue id is named in its page, each phrase is in its own section, and the prefixes agree."""
    from decktalk.artifacts.words import Word
    from decktalk.model.cues import find_phrase
    from decktalk.stages.align import unknown_cue_ids

    root = write(tmp_path, example_name)
    project = Project.load(root, environ={})
    specs = project.cue_specs()
    assert unknown_cue_ids(project, specs) == []
    scenes = {s.number: s.scene for s in project.page_sections}
    segments = {s.index: s for s in parse_script((root / "script.md").read_text(encoding="utf-8"))}
    for section in specs:
        assert section.cues, section.number
        for cue in section.cues:
            # The section number, the scene and the cue id prefix agree, which is the rule a project holds.
            assert cue.cue.split(".")[0] == scenes[section.number], cue.cue
            if cue.on.startswith("$"):
                continue
            # Every phrase names the occurrence it means, so a repeat never moves a picture by accident.
            assert cue.occurrence_set, cue.cue
            words = [Word(w, i, i + 1) for i, w in enumerate(segments[section.number].spoken.split())]
            assert find_phrase(words, cue.on, occurrence=cue.occurrence) is not None, (
                f"cue {cue.cue}: {cue.on!r} is not in section {section.number}"
            )


@pytest.mark.parametrize("example_name", SHIPPED)
def test_a_project_loads_and_lists_every_cue_of_every_slide(tmp_path, offline, example_name):
    """`cues.json` parses, and no slide of a page section is left without a cue."""
    root = write(tmp_path, example_name)
    data = json.loads((root / "cues.json").read_text(encoding="utf-8"))
    ids = [c["cue"] for s in data["sections"].values() for c in s["cues"]]
    assert len(ids) == len(set(ids)), "a cue id is listed twice"
    page_text = "\n".join(p.read_text(encoding="utf-8") for p in (root / "deck").glob("*.html"))
    for cue in ids:
        assert cue in page_text, cue


# ---- the examples --------------------------------------------------------------------


def test_the_lesson_example_is_one_section_with_a_drawn_figure(tmp_path, offline):
    root = write(tmp_path, "lesson")
    project = Project.load(root, environ={})
    assert [(s.number, s.scene) for s in project.page_sections] == [(1, "1")]
    page = (root / "deck" / "lesson.html").read_text(encoding="utf-8")
    # The lesson is the example of a page that needs code: it builds one figure and moves it on cues.
    assert "DeckTalk.scene(1," in page
    assert "DeckTalk.waitFor(" in page and "window.__decktalk.ready =" not in page
    assert (root / "deck" / "fonts" / "Inter-latin.woff2").is_file()


@pytest.mark.parametrize("name", [ex.name for ex in EXAMPLES if not ex.shipped])
def test_a_reserved_example_says_what_it_is_waiting_for(tmp_path, offline, name):
    with pytest.raises(ConfigError) as exc:
        write(tmp_path, name)
    assert "reserved name" in str(exc.value) and example(name).summary in str(exc.value)
    assert "decktalk init --example lesson" in str(exc.value)
    assert not (tmp_path / name).exists()


def test_an_unknown_example_names_the_ones_there_are(tmp_path, offline):
    with pytest.raises(ConfigError) as exc:
        write(tmp_path, "nosuch")
    assert "unknown example 'nosuch'" in str(exc.value)
    # Every name is offered, and a name with no project behind it is offered as a reserved one.
    for ex in EXAMPLES:
        assert ex.name in str(exc.value)
    assert "product (reserved)" in str(exc.value)


# ---- the skills and AGENTS.md ---------------------------------------------------------


def test_init_installs_the_six_skills_with_the_claude_code_link(tmp_path, offline):
    root = write(tmp_path)
    installed = sorted(p.name for p in (root / SKILLS_DIR).iterdir())
    assert installed == sorted(SKILL_NAMES)
    for name in SKILL_NAMES:
        body = (root / SKILLS_DIR / name / "SKILL.md").read_text(encoding="utf-8")
        assert body.startswith("---\n") and f"name: {name}" in body
        assert "no body yet" not in body
        assert "AGENTS.md" in body and "--no-voice" in body
    link = root / LINK_DIR
    assert (link / "decktalk-build" / "SKILL.md").is_file()
    # One folder of skills, linked rather than copied. A filesystem that refuses a link gets a copy,
    # and this machine is not one, so a copy here would mean the link stopped being written.
    assert link.is_symlink()
    assert link.resolve() == (root / SKILLS_DIR).resolve()


def test_no_skills_leaves_both_folders_out(tmp_path, offline):
    root = write(tmp_path, skills=False)
    assert not (root / ".agents").exists()
    assert not (root / ".claude").exists()
    assert (root / "AGENTS.md").is_file()


def test_agents_md_is_written_only_when_the_directory_has_none(tmp_path, offline):
    target = tmp_path / "p"
    target.mkdir()
    (target / "AGENTS.md").write_text("mine\n", encoding="utf-8")
    root = init(target, name="p", force=True).root
    assert (root / "AGENTS.md").read_text(encoding="utf-8") == "mine\n"
    written = init(tmp_path / "q", name="q").written
    assert (tmp_path / "q" / "AGENTS.md") in written
    agents = (tmp_path / "q" / "AGENTS.md").read_text(encoding="utf-8")
    assert len(agents.splitlines()) <= 11
    assert "--no-voice" in agents


def test_the_result_lists_every_file_it_wrote(tmp_path, offline):
    result = init(tmp_path / "p", name="p")
    assert result.example == STARTER and result.skills is True
    assert result.root / "decktalk.toml" in result.written
    assert result.root / "deck" / RUNTIME_FILE in result.written
    assert all(p.exists() for p in result.written)


def test_init_refuses_a_non_empty_directory_without_force(tmp_path, offline):
    target = tmp_path / "p"
    target.mkdir()
    (target / "notes.txt").write_text("hi", encoding="utf-8")
    with pytest.raises(ConfigError, match="not empty"):
        init(target, name="p")
    assert init(target, name="p", force=True).root == target.resolve()


# ---- every packaged page, in a real browser -------------------------------------------


@pytest.fixture(scope="module")
def browser_page():
    """A page opened the way a DeckTalk command opens one, with decktalk-probe.js injected.

    These tests read the measured boxes and freeze a slide at one cue, which are the probe's, so
    the fixture uses the recorder's own hook rather than a copy of it.
    """
    from decktalk.media.browser import instrument

    yield from chromium_page(instrument)


def pages_of(root: Path) -> list[tuple[Path, str]]:
    """(page, scene) for every page section of a project's decktalk.toml, in order."""
    import tomllib

    doc = tomllib.loads((root / "decktalk.toml").read_text(encoding="utf-8"))
    return [((root / s["page"]).resolve(), str(s["scene"])) for s in doc["section"] if "page" in s]


def cues_of(root: Path, scene: str) -> list[str]:
    """The cue ids a project lists for one scene, in order."""
    data = json.loads((root / "cues.json").read_text(encoding="utf-8"))
    return [c["cue"] for s in data["sections"].values() for c in s["cues"] if c["cue"].split(".")[0] == scene]


@pytest.mark.browser
@pytest.mark.parametrize("example_name", SHIPPED)
def test_every_packaged_scene_plays_its_whole_cue_list(tmp_path, offline, browser_page, example_name):
    """Each page section in cue mode, with every cue of its scene, warns nothing and logs no error."""
    page = browser_page
    root = write(tmp_path, example_name)
    logged: list[str] = []
    page.on("console", lambda msg: logged.append(msg.text) if msg.type == "error" else None)
    for path, scene in pages_of(root):
        cues = cues_of(root, scene)
        assert cues, scene
        listed = ",".join(f"{cue}@{0.1 + i * 0.05:.2f}" for i, cue in enumerate(cues))
        page.goto(f"{path.as_uri()}?scene={scene}&t0=0&cues={listed}")
        page.evaluate("() => window.__decktalk.ready")
        page.wait_for_function(f"() => window.__decktalk.fired.length >= {len(cues)}", timeout=15000)
        assert page.evaluate("() => window.__decktalk.fired") == cues, scene
        assert page.evaluate("() => window.__decktalk.warnings") == [], scene
    assert logged == []
    assert not page.errors


@pytest.mark.browser
@pytest.mark.parametrize("example_name", SHIPPED)
def test_every_packaged_slide_freezes_without_warnings(tmp_path, offline, browser_page, example_name):
    """Every cue a project lists reveals an element or runs a handler, so no slide warns when frozen."""
    page = browser_page
    root = write(tmp_path, example_name)
    for url in dict.fromkeys(p.as_uri() for p, _ in pages_of(root)):
        page.goto(url)
        page.evaluate("() => window.__decktalk.ready")
        catalog = page.evaluate("() => window.__decktalk.catalog")
        assert catalog, url
        for entry in catalog:
            for slide in entry["slides"]:
                page.goto(f"{url}?slide={slide}")
                page.evaluate("() => window.__decktalk.ready")
                assert page.evaluate("() => window.__decktalk.warnings") == [], slide
    assert not page.errors


@pytest.mark.browser
def test_the_starter_keeps_every_reveal_out_of_the_caption_band_and_on_the_stage(tmp_path, offline, browser_page):
    """Each measured reveal sits inside the 1920 by 1080 stage and above the bottom 15 percent."""
    page = browser_page
    root = write(tmp_path)
    path, _ = pages_of(root)[0]
    page.goto(path.as_uri())
    page.evaluate("() => window.__decktalk.ready")
    rows = [r for entry in page.evaluate("() => window.__decktalk.catalog") for r in entry["elements"].values()]
    cued = [r for slide in rows for r in slide if r["cue"]]
    assert len(cued) == 11
    for row in cued:
        box = row["box"]
        assert 0 <= box["x"] and box["x"] + box["w"] <= 1920, row
        assert 0 <= box["y"] and box["y"] + box["h"] <= 918, row  # above the caption band
        assert row["describe"], row
        # A reveal has to be big enough for the frame check to see it: about 0.3 percent of the frame.
        assert box["w"] * box["h"] >= 0.003 * 1920 * 1080, row
    # Measuring left nothing on the stage, not even the typeset equation it laid out.
    assert page.evaluate("() => document.querySelector('.katex')") is None


@pytest.mark.browser
def test_the_starter_typesets_its_one_equation_from_the_copy_beside_it(tmp_path, offline, browser_page):
    page = browser_page
    root = write(tmp_path)
    page.goto(f"{(root / 'deck' / 'index.html').resolve().as_uri()}?slide=2.1")
    page.evaluate("() => window.__decktalk.ready")
    assert page.evaluate("() => document.querySelectorAll('[data-tex][data-typeset] .katex').length") == 1
    assert page.evaluate("() => !document.querySelector('.katex-error')")
    assert page.evaluate("() => window.__decktalk.warnings") == []
    assert not page.errors
