"""Write every install line a reader meets from the one place each is written: install.sh.

    pixi run python scripts/build_install_docs.py --write    # bring every install block up to date
    pixi run python scripts/build_install_docs.py --check    # exit 1 if a block names another install

install.sh is served as one file to a machine that has nothing else, so it is the one home of the
conda channel, of the `pixi global install` line it runs and of the PowerShell line it gives a
Windows reader. Every page that shows an install carries the same lines between two markers that
name the block, and this generator writes them from install.sh, so a new channel is one line of
install.sh and one `--write`. It writes the channel into RELEASING.md and into the upload of
release.yml too, and the install line into the example workflow of the CI guide. That example names
each action by its release tag, as a reader's own workflow would, so a new release of an action
DeckTalk's CI pins leaves the guide true. The uv line pins the newest final release CHANGELOG.md
heads, so the one version pin a reader copies names a version PyPI serves as a release.

A block starts at a line holding `install-docs:<name>:start` and ends at the next line holding
`install-docs:<name>:end`, in whatever comment the file writes, and its lines take the start
marker's indentation, so a block inside an MDX tab reads as the tab does. The markers are the only
registration: every page, workflow and Markdown file of the checkout that holds one is written, and
every block in it. A start marker in any other file that git sees is refused, so a block cannot sit
where no run of this generator reads it.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import build_changelog
import generated

ROOT = Path(__file__).resolve().parent.parent
INSTALLER = ROOT / "install.sh"

MARKER = "install-docs"
START = re.compile(rf"^.*{MARKER}:(?P<name>[a-z-]+):start.*$", re.M)
BLOCK = re.compile(
    rf"^(?P<indent>[ \t]*)\S.*{MARKER}:(?P<name>[a-z-]+):start.*\n(?P<body>(?:.*\n)*?).*{MARKER}:(?P=name):end.*$",
    re.M,
)
"""One block: its start marker, every line up to the next end marker of the same name, and that marker."""

KINDS = (".md", ".mdx", ".yml")
"""The kinds of file a block may sit in: the README and the guides, the docs pages, and the workflows."""

UNREAD = ("node_modules",)
"""The one folder without a leading dot that holds files of those kinds the checkout did not write."""

PREFIX_DEV = "https://prefix.dev/"


@dataclass(frozen=True)
class Install:
    """The values every block is written from: what install.sh holds, and the newest final release."""

    one_liner: str
    channel: str
    install_line: str
    windows_line: str
    bin_dir: str
    release: str

    @property
    def upload_channel(self) -> str:
        """The channel as `pixi upload prefix` names it, which is its URL's path after the host."""
        return self.channel.removeprefix(PREFIX_DEV)

    @property
    def page(self) -> str:
        namespace, name = self.upload_channel.split("/")
        return f"{PREFIX_DEV}channels/@{namespace}/{name}"


def assigned(text: str, name: str) -> str:
    """The value one `NAME="..."` line of install.sh assigns, with its escaped quotes read as quotes."""
    found = re.findall(rf'^{name}="(.*)"$', text, re.M)
    if len(found) != 1:
        raise SystemExit(f"install.sh assigns {name} {len(found)} times, and the docs are written from one.")
    return found[0].replace('\\"', '"')


def read_installer() -> Install:
    text = INSTALLER.read_text(encoding="utf-8")
    [one_liner] = re.findall(r"^#   (curl \S+ \S+ \| sh)$", text, re.M)
    channel = assigned(text, "CHANNEL")
    install_line = assigned(text, "INSTALL_LINE").replace("$CHANNEL", channel)
    windows_line = assigned(text, "WINDOWS_LINE").replace("$INSTALL_LINE", install_line)
    home = re.findall(r'^\tPIXI_HOME="\$\{PIXI_HOME:-(.+)\}"$', text, re.M)
    if len(home) != 1:
        raise SystemExit("install.sh names no one default PIXI_HOME, so no page can name its bin folder.")
    release = build_changelog.newest_final(build_changelog.SOURCE.read_text(encoding="utf-8"))
    return Install(one_liner, channel, install_line, windows_line, f"{home[0]}/bin", release)


def first_lines(install: Install) -> str:
    return "\n".join(
        (
            "```console",
            install.one_liner,
            f'export PATH="{install.bin_dir}:$PATH"',
            "decktalk init my-lesson && cd my-lesson",
            "decktalk build --no-spend",
            "```",
        )
    )


def pixi_line(install: Install) -> str:
    return f"```console\n{install.install_line}\n```"


def windows_line(install: Install) -> str:
    return f"```powershell\n{install.windows_line}\n```"


def uv_line(install: Install) -> str:
    """The second path, in the one wording every page gives it, pinned to a version PyPI serves as a release.

    This line is the one pin a reader can copy. It names the newest final release, so a candidate
    branch, whose version PyPI serves only as a prerelease, never names a version that is not
    released.
    """
    return f"`uv tool install decktalk=={install.release}` installs the newest release from PyPI on every platform."


def other_ways(install: Install) -> str:
    return (
        f"With Pixi already on the machine, `{install.install_line}` is the whole install, on Windows "
        "too. The one-line installer stops on Windows, because it is a POSIX shell script, so in "
        f"PowerShell `{install.windows_line}` installs Pixi with its own installer and then runs that "
        f"line. {uv_line(install)}"
    )


def channel(install: Install) -> str:
    return (
        f"The channel is `{install.upload_channel}`. Its URL, which `install.sh` and `pixi global "
        f"install` read, is `{install.channel}`, and its page is `{install.page}`."
    )


def upload_channel(install: Install) -> str:
    return f"PREFIX_CHANNEL: {install.upload_channel}"


GUIDE_WORKFLOW = """```yaml .github/workflows/video.yml
name: video

on:
  push:
    branches: [main]
  pull_request:

permissions:
  contents: read

jobs:
  video:
    runs-on: ubuntu-latest
    env:
      DECKTALK_PROJECT: lessons/derivatives
    steps:
      - uses: actions/checkout@v7
        with:
          persist-credentials: false
      - uses: prefix-dev/setup-pixi@v0.11.3
        with:
          run-install: false
      - run: @INSTALL_LINE@
      - id: decktalk
        run: echo "version=$(decktalk --version)" >> "$GITHUB_OUTPUT"
      - name: Cache Chromium and ffmpeg
        uses: actions/cache@v6
        with:
          path: ~/.cache/decktalk
          key: decktalk-tools-${{ runner.os }}-${{ runner.arch }}-${{ steps.decktalk.outputs.version }}
      - run: decktalk install --no-input
      - run: mkdir -p reports
      - run: decktalk doctor --no-input --json > reports/doctor.json
      - run: decktalk check --no-input --json > reports/check.json
      - run: decktalk build --no-spend --no-input --json > reports/build.json
        env:
          DECKTALK_VIDEO_PRESET: veryfast
      - name: Keep the film, its files and the reports
        uses: actions/upload-artifact@v7
        if: always()
        with:
          name: film
          path: |
            ${{ env.DECKTALK_PROJECT }}/build/final/
            reports/
      - name: Keep the film on its own, so it plays without unzipping
        uses: actions/upload-artifact@v7
        if: always()
        with:
          path: ${{ env.DECKTALK_PROJECT }}/build/final/*.mp4
          archive: false
```"""
"""The CI guide's example workflow, whose install line is filled in from install.sh."""


def ci_workflow(install: Install) -> str:
    return GUIDE_WORKFLOW.replace("@INSTALL_LINE@", install.install_line)


RENDERERS: dict[str, Callable[[Install], str]] = {
    "first-lines": first_lines,
    "pixi-line": pixi_line,
    "windows-line": windows_line,
    "uv-line": uv_line,
    "other-ways": other_ways,
    "channel": channel,
    "upload-channel": upload_channel,
    "ci-workflow": ci_workflow,
}
"""Every block a page may carry, by the name its markers give it."""


def pages() -> list[Path]:
    """Every file of the checkout that holds a start marker, found by walking the tree.

    The tree is walked rather than asked of git, because the rehearsal runs this generator in a copy
    of the checkout that is not a repository. A folder whose name starts with a dot is left out but
    `.github`, because each of the others is a tool's own state, such as `.git` and `.pixi`.
    """
    found = []
    for folder, children, names in os.walk(ROOT):
        children[:] = sorted(
            child for child in children if child not in UNREAD and (not child.startswith(".") or child == ".github")
        )
        for name in sorted(names):
            path = Path(folder) / name
            if path.suffix in KINDS and START.search(path.read_text(encoding="utf-8")):
                found.append(path)
    return found


def filled(path: Path, install: Install) -> str:
    """`path` with every block it carries written from install.sh, indented as its start marker is."""
    text = path.read_text(encoding="utf-8")
    where = path.relative_to(ROOT)
    blocks = list(BLOCK.finditer(text))
    if len(blocks) != len(START.findall(text)):
        raise SystemExit(f"{where} opens an install block it does not close with an end marker of the same name.")
    written, at = [], 0
    for match in blocks:
        name, indent = match["name"], match["indent"]
        if name not in RENDERERS:
            raise SystemExit(f"{where} carries a block named {name}, which nothing writes.")
        body = "".join(f"{indent}{line}\n" if line else "\n" for line in RENDERERS[name](install).splitlines())
        written += [text[at : match.start("body")], body]
        at = match.end("body")
    return "".join([*written, text[at:]])


def unread(written: list[Path]) -> list[str]:
    """Every file git sees, of any kind and in any folder, that holds a start marker and is not in `written`.

    Outside a repository, as in the rehearsal's copy, git lists nothing and nothing is refused. The
    copy holds the files git sees in the checkout, whose own `generated` row has judged them.
    """
    listed = subprocess.run(
        ("git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"),
        cwd=ROOT,
        capture_output=True,
        check=False,
    )
    if listed.returncode != 0:
        return []
    known = {path.relative_to(ROOT).as_posix() for path in written}
    names = [name for name in listed.stdout.decode().split("\0") if name and name not in known]
    marker = re.compile(START.pattern.encode(), re.M)
    return [name for name in names if (ROOT / name).is_file() and marker.search((ROOT / name).read_bytes())]


def files() -> dict[Path, str]:
    install = read_installer()
    written = pages()
    if stray := unread(written):
        raise SystemExit(
            f"{', '.join(stray)} carries an install block, and this generator writes blocks only in a "
            f"{', '.join(KINDS)} file outside a dot folder other than .github."
        )
    return {path: filled(path, install) for path in written}


if __name__ == "__main__":
    sys.exit(generated.run(files))
