"""`scripts/build_install_docs.py`, which writes every install line a reader meets from install.sh.

The generated row holds each page to install.sh on every pull request. These tests hold what that
check alone cannot: that a new channel in install.sh reaches every block, and that no file but
install.sh and the pages the generator writes spells the channel.
"""

from __future__ import annotations

import re
import subprocess

import pytest

import build_install_docs as docs
from support.paths import REPO


def test_every_value_is_read_from_install_sh() -> None:
    install = docs.read_installer()
    text = docs.INSTALLER.read_text(encoding="utf-8")
    assert f'CHANNEL="{install.channel}"' in text
    assert install.install_line == f"pixi global install -c {install.channel} -c conda-forge decktalk"
    assert install.install_line in install.windows_line
    assert install.upload_channel == install.channel.removeprefix("https://prefix.dev/")


def test_a_new_channel_in_install_sh_reaches_every_block(monkeypatch: pytest.MonkeyPatch) -> None:
    """A channel move is one line of install.sh and one `--write`, whatever page shows the install."""
    old = docs.read_installer().upload_channel
    moved = docs.INSTALLER.read_text(encoding="utf-8").replace(old, "elsewhere/moved")
    real = docs.Path.read_text

    def reading(path: docs.Path, encoding: str | None = None) -> str:
        return moved if path == docs.INSTALLER else real(path, encoding=encoding)

    monkeypatch.setattr(docs.Path, "read_text", reading)
    for path, text in docs.files().items():
        assert "elsewhere/moved" in text, path.name
        assert old not in text, path.name


def test_no_file_but_install_sh_and_the_written_pages_names_the_channel() -> None:
    """A page that spelled the channel by hand would keep the old one after a move."""
    channel = docs.read_installer().upload_channel
    listed = subprocess.run(
        ["git", "grep", "-l", "-F", channel], cwd=REPO, capture_output=True, text=True, check=False
    ).stdout.split()
    allowed = {"install.sh", *(path.relative_to(REPO).as_posix() for path in docs.pages())}
    assert set(listed) <= allowed, sorted(set(listed) - allowed)


def block(name: str, body: str, *, opens: str = "<!-- ", closes: str = " -->") -> str:
    """One block as a page writes it, with `body` between its two markers."""
    return f"{opens}{docs.MARKER}:{name}:start{closes}\n{body}{opens}{docs.MARKER}:{name}:end{closes}\n"


def test_a_block_nothing_writes_is_refused(monkeypatch: pytest.MonkeyPatch, tmp_path: docs.Path) -> None:
    monkeypatch.setattr(docs, "ROOT", tmp_path)
    page = tmp_path / "page.md"
    page.write_text(block("unknown", ""))
    with pytest.raises(SystemExit, match="nothing writes"):
        docs.filled(page, docs.read_installer())


def test_a_block_with_no_end_marker_is_refused(monkeypatch: pytest.MonkeyPatch, tmp_path: docs.Path) -> None:
    monkeypatch.setattr(docs, "ROOT", tmp_path)
    page = tmp_path / "page.md"
    page.write_text(block("uv-line", "stale\n").rsplit("<!--", 1)[0])
    with pytest.raises(SystemExit, match="does not close"):
        docs.filled(page, docs.read_installer())


def test_every_block_of_one_name_in_a_page_is_written(monkeypatch: pytest.MonkeyPatch, tmp_path: docs.Path) -> None:
    """A page may show the same lines twice, and a stale second copy is as wrong as a stale first."""
    monkeypatch.setattr(docs, "ROOT", tmp_path)
    page = tmp_path / "page.md"
    page.write_text(block("uv-line", "pipx install decktalk\n") + "Between.\n" + block("uv-line", "pipx again\n"))
    written = docs.filled(page, docs.read_installer())
    assert written.count(docs.UV_LINE) == 2, written
    assert "pipx" not in written and "Between.\n" in written, written


def test_a_page_is_written_because_it_holds_a_marker(monkeypatch: pytest.MonkeyPatch, tmp_path: docs.Path) -> None:
    """The markers are the only registration, so a new page needs no list to join."""
    monkeypatch.setattr(docs, "ROOT", tmp_path)
    faq = tmp_path / "docs" / "help" / "faq.mdx"
    faq.parent.mkdir(parents=True)
    faq.write_text(block("uv-line", "pipx install decktalk\n", opens="{/* ", closes=" */}"))
    unread = tmp_path / ".pixi" / "envs" / "README.md"
    unread.parent.mkdir(parents=True)
    unread.write_text(block("uv-line", "a tool's own file\n"))
    written = docs.files()
    assert list(written) == [faq]
    assert docs.UV_LINE in written[faq] and "pipx" not in written[faq], written[faq]


@pytest.mark.parametrize("where", ["help.yaml", ".changeset/note.mdx"])
def test_a_block_in_a_file_the_generator_does_not_read_is_refused(
    monkeypatch: pytest.MonkeyPatch, tmp_path: docs.Path, where: str
) -> None:
    """A block in a kind or a folder the walk passes over would stay stale while `--check` passes."""
    monkeypatch.setattr(docs, "ROOT", tmp_path)
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / "page.md").write_text(block("uv-line", docs.UV_LINE + "\n"))
    stray = tmp_path / where
    stray.parent.mkdir(exist_ok=True)
    stray.write_text(block("uv-line", "pipx install decktalk\n", opens="# ", closes=""))
    assert docs.pages() == [tmp_path / "page.md"]
    with pytest.raises(SystemExit, match=re.escape(where)):
        docs.files()
