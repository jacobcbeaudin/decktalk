"""The suite mirrors the source tree, and this is the rule that keeps it mirrored.

One test file per source module, at the same path: `src/decktalk/media/audio.py` is tested by
`tests/decktalk/media/test_audio.py` and by nothing else, and a package's `__init__.py` is tested by
`tests/decktalk/<package>/test_<package>.py`. A test that belongs to no module goes in
`tests/contract/`, which is a closed allow-list so that it cannot become the next grab-bag, and
`tests/support/` holds what several modules share and collects nothing.

The two walks are equal in both directions, so a module with no test file is named here and a test
file with no module is named here too. `NO_UNIT_TEST` is the one escape, it names the track that owes
each entry, and it only ever shrinks.
"""

from __future__ import annotations

import ast
import tomllib
from pathlib import Path

import pytest

from support.paths import REPO, TESTS

SRC = REPO / "src" / "decktalk"
MIRROR = TESTS / "decktalk"

NO_UNIT_TEST = {
    "__init__.py": "T1 owes it. The package is the re-export surface, held by contract/test_api.py.",
    "__main__.py": "T8 owes it. It is the two lines `python -m decktalk` runs, held by the wheel smoke.",
    "artifacts/__init__.py": "T1 owes it. The package re-exports the artifact types and defines none.",
    "artifacts/words.py": "T4 owes it. The word file is read and written by every artifact test and judged by none.",
    "captions/__init__.py": "T6 owes it. The package re-exports the caption types and defines none.",
    "media/__init__.py": "T6 owes it. The package re-exports the media modules and defines none.",
    "media/ffmpeg.py": "T6 owes it. The return-code rule of must 4 is an AST walk with no file of its own yet.",
    "speech/__init__.py": "T6 owes it. The package is the provider registry, exercised by every voiced test.",
    "speech/elevenlabs.py": "T6 owes it. The one paid path, and the coverage floor names it by row.",
    "stages/__init__.py": "T5 owes it. The package re-exports the six stage callables and defines none.",
    "toolchain/__init__.py": "T6 owes it. The package re-exports the cache and the fetchers and defines none.",
    "toolchain/cache.py": "T6 owes it. The cache directory is asserted through the fetchers that write it.",
}
"""Every module with no mirrored test file, the track that owes it, and the one sentence saying why."""

EXCUSED_CEILING = 20
"""How many modules were excused when the mirror was first enforced.

The list is a ratchet: a module that gains its test leaves the list, and a module that loses its test
has to be argued for in review. Raising this number is the edit a reviewer refuses.
"""

ALLOW = {
    "contract/test_api.py": "`decktalk.__all__`, which is the whole supported Python API.",
    "contract/test_discoverable.py": (
        "The founder's thesis: every command, key, code and attribute reachable from --help and the schemas."
    ),
    "contract/test_docs_claims.py": "The claims in `docs/` that a reader can act on.",
    "contract/test_env_ignore.py": "`.gitignore` and the wheel's exclude list, judged with git itself.",
    "contract/test_homepage.py": "`site/`, opened in a browser rather than read as files.",
    "contract/test_imports.py": "The layer rank of every module in `src/decktalk`.",
    "contract/test_installer.py": "`site/install.sh`, the one-line installer.",
    "contract/test_layout.py": "This mirror rule, which belongs to the suite rather than to a module.",
    "contract/test_numbers.py": "`tests/contract/numbers-baseline.json`, every number written outside settings.py.",
    "contract/test_probe.py": "`src/decktalk/runtime/decktalk-probe.js`, read as the compiled bundle.",
    "contract/test_prose.py": "Every tracked text file, judged by the two prose rules that are mechanical.",
    "contract/test_results.py": "Every result a command returns, driven through its real stage.",
    "contract/test_runtime.py": "`src/decktalk/runtime/decktalk-runtime.js`, in a real Chromium.",
    "contract/test_selection.py": "The collection hook in `tests/conftest.py`.",
    "contract/test_site.py": "The committed files under `site/`.",
    "contract/test_take_hash.py": "`tests/data/take_hash.json`, the golden digests of the founder's film.",
    "contract/test_timing_policy.py": "`tests/support/timing_policy.py`, the suite's own timing rule.",
    "contract/test_vocabulary.py": (
        "`tests/contract/vocabulary-baseline.json`, every literal still spelling a closed vocabulary."
    ),
    "contract/test_wheel.py": "The built wheel's file list.",
}
"""Every test file that belongs to no source module, and the repository artifact it holds instead."""

SUITE_MARKERS = {"browser", "media", "e2e", "scaffold", "platform"}
"""The markers that name what a run needs. `tests/conftest.py` selects by them and nothing else is one."""

PLATFORM_BRANCH_EXEMPT = {
    "decktalk/toolchain/test_ffmpeg_fetch.py": "T6 owes the pair: the unpack policy on Linux, the run on the platform.",
}
"""Every file that still branches on the platform outside `tests/platform/`, and the track that owes the pair."""


def mirrored_path(module: Path) -> Path:
    """The one test file that mirrors `module`, whether it is a module or a package."""
    rel = module.relative_to(SRC)
    if module.name == "__init__.py":
        return MIRROR / rel.parent / f"test_{rel.parent.name or 'decktalk'}.py"
    return MIRROR / rel.parent / f"test_{rel.stem}.py"


def mirrored_module(test: Path) -> str:
    """The dotted name of the source module `test` mirrors."""
    rel = test.relative_to(MIRROR)
    subject = rel.stem.removeprefix("test_")
    packages = list(rel.parent.parts)
    if subject == (packages[-1] if packages else "decktalk"):
        return ".".join(["decktalk", *packages])
    return ".".join(["decktalk", *packages, subject])


def named_modules(test: Path) -> set[str]:
    """Every module the file names, whether by an import statement or by `importlib.import_module`.

    A literal string inside `import_module` counts, because a package that exports a function of its
    own name is imported that way and names its subject as plainly as an import statement does.
    """
    names: set[str] = set()
    for node in ast.walk(ast.parse(test.read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
            names.update(f"{node.module}.{alias.name}" for alias in node.names)
        elif isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr == "import_module" and node.args and isinstance(node.args[0], ast.Constant):
                names.add(str(node.args[0].value))
    return names


def source_modules() -> list[Path]:
    return sorted(SRC.rglob("*.py"))


def mirrored_tests() -> list[Path]:
    return sorted(MIRROR.rglob("test_*.py"))


def test_every_source_module_has_the_test_file_that_mirrors_it():
    for module in source_modules():
        key = module.relative_to(SRC).as_posix()
        if key in NO_UNIT_TEST:
            continue
        want = mirrored_path(module)
        assert want.exists(), (
            f"src/decktalk/{key} has no test file. Create {want.relative_to(REPO).as_posix()}, or add "
            f"{key!r} to NO_UNIT_TEST in tests/contract/test_layout.py with the track that owes it and "
            "the one sentence saying why."
        )


def test_every_mirrored_test_file_has_the_source_module_it_mirrors():
    for test in mirrored_tests():
        want = mirrored_module(test)
        module = SRC.parent / Path(*want.split("."))
        assert module.with_suffix(".py").exists() or (module / "__init__.py").exists(), (
            f"{test.relative_to(REPO).as_posix()} mirrors {want}, which is not a module. Move it to "
            "tests/contract/ and give it a sentence in ALLOW, or name it after the module it is about."
        )


def test_every_mirrored_test_file_names_the_module_it_mirrors():
    """Read from the AST, so a placeholder that mentions the module in a docstring does not satisfy it."""
    for test in mirrored_tests():
        want = mirrored_module(test)
        assert want in named_modules(test), (
            f"{test.relative_to(REPO).as_posix()} never imports {want}, so it mirrors a module it does not test."
        )


def test_no_test_file_sits_outside_a_directory_that_means_something():
    loose = sorted(p.name for p in TESTS.glob("test_*.py"))
    assert loose == [], (
        f"these files sit at the root of tests/: {loose}. A test belongs under decktalk/ beside its module, "
        "under contract/ with a sentence in ALLOW, under platform/ or under e2e/."
    )


def test_every_contract_file_names_the_repository_artifact_it_holds():
    files = sorted(p.relative_to(TESTS).as_posix() for p in (TESTS / "contract").glob("test_*.py"))
    assert files == sorted(ALLOW), (
        "tests/contract/ is a closed list so that it cannot become the next grab-bag. Add the file to ALLOW "
        "in tests/contract/test_layout.py with the one sentence naming the repository artifact it holds."
    )
    for name, sentence in ALLOW.items():
        assert sentence.endswith("."), name


def test_the_excused_list_only_ever_shrinks():
    assert len(NO_UNIT_TEST) <= EXCUSED_CEILING, (
        f"{len(NO_UNIT_TEST)} modules are excused from the mirror and the ceiling is {EXCUSED_CEILING}. "
        "Write the test rather than raising the ceiling."
    )
    for key, sentence in NO_UNIT_TEST.items():
        assert (SRC / key).exists(), f"{key} is excused from the mirror and is not a module any more."
        assert sentence.startswith("T") and sentence.endswith("."), f"{key} names no track that owes it."


def test_nothing_under_support_collects():
    files = sorted(p.name for p in (TESTS / "support").glob("test_*.py"))
    assert files == [], f"tests/support/ holds what several modules share and collects nothing, but holds {files}."


def test_the_project_registers_exactly_the_markers_that_name_what_a_run_needs():
    config = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))
    rows = config["tool"]["pytest"]["ini_options"]["markers"]
    assert {row.split(":", 1)[0] for row in rows} == SUITE_MARKERS
    for row in rows:
        name, description = row.split(":", 1)
        assert description.strip().startswith("needs "), (
            f"the {name} marker must say what it needs first, so `pytest --markers` answers what a table used to."
        )


def applied_markers(path: Path) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Attribute):
            if node.value.attr == "mark" and isinstance(node.value.value, ast.Name):
                names.add(node.attr)
    return names


def every_test_file() -> list[Path]:
    return sorted(p for p in TESTS.rglob("test_*.py") if "fixture" not in p.parts)


def test_no_marker_is_a_second_spelling_of_a_directory(pytestconfig):
    """A marker that named a path would let a file that forgets its marker fall into no group at all."""
    known = {row.split(":", 1)[0].split("(", 1)[0] for row in pytestconfig.getini("markers")}
    for path in every_test_file():
        unknown = applied_markers(path) - known
        assert unknown == set(), (
            f"{path.relative_to(REPO).as_posix()} applies {sorted(unknown)}, which is registered nowhere. "
            "A marker names what a test needs, and a directory already answers what a test is about."
        )


def platform_branches(path: Path) -> int:
    """How many times the file decides something from `sys.platform`."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    reads = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare):
            continue
        for operand in [node.left, *node.comparators]:
            if isinstance(operand, ast.Attribute) and operand.attr == "platform":
                reads += 1
            if isinstance(operand, ast.Attribute) and operand.attr == "name" and isinstance(operand.value, ast.Name):
                reads += operand.value.id == "os"
    return reads


def test_no_test_branches_on_the_platform_outside_the_platform_directory():
    """A platform skip is where a suite quietly stops proving anything, so the branch lives in one place."""
    for path in [*(TESTS / "decktalk").rglob("test_*.py"), *(TESTS / "e2e").glob("test_*.py")]:
        key = path.relative_to(TESTS).as_posix()
        if key in PLATFORM_BRANCH_EXEMPT:
            continue
        assert platform_branches(path) == 0, (
            f"{key} decides something from the platform. A policy test injects the environment and holds "
            "everywhere, and the environment test belongs in tests/platform/."
        )


def test_every_platform_file_names_the_platform_fact_it_holds():
    for path in sorted((TESTS / "platform").glob("test_*.py")) if (TESTS / "platform").is_dir() else []:
        docstring = ast.get_docstring(ast.parse(path.read_text(encoding="utf-8")))
        assert docstring, (
            f"{path.name} exists only because there are three platforms, so its module docstring names the "
            "platform fact it holds and the pair it belongs to."
        )


@pytest.mark.parametrize("key", sorted(PLATFORM_BRANCH_EXEMPT))
def test_every_platform_exemption_names_a_file_that_is_still_there(key):
    assert (TESTS / key).exists(), f"{key} is exempt from the platform rule and is not there any more."
