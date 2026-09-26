# /// script
# requires-python = ">=3.12"
# ///
"""Print the release notes of a final version: every section its candidates and its own release wrote.

    python scripts/release_notes.py 0.5.0    # the notes GitHub shows for v0.5.0

release-please writes the notes of a release from the commits since the previous tag, and in the
candidate cycle the previous tag of a final release is its last candidate. The notes of 0.5.0 would
therefore list only what changed after 0.5.0-rc3. CHANGELOG.md keeps an entry per tag, and
`build_changelog.parse` already folds a version's candidates into one entry for the docs, so this
prints that entry for the release workflow, which sets it on a final release. A candidate keeps the
notes release-please wrote, which are what changed since the candidate before it.

It reads CHANGELOG.md alone and needs nothing but Python, because the release workflow runs it on a
checkout of the tag with no environment set up.
"""

from __future__ import annotations

import argparse
import re
import sys

from build_changelog import PREAMBLE, SOURCE, parse

FINAL = re.compile(r"\d+\.\d+\.\d+")
"""A final version, which is the only kind whose notes this prints."""


def notes(text: str, version: str) -> str:
    """The folded entry of `version` in `text`, with each section under a level-three heading."""
    if not FINAL.fullmatch(version):
        raise SystemExit(f"{version} is a candidate, and a candidate keeps the notes release-please wrote")
    release = next((release for release in parse(text) if release.base == version), None)
    if release is None:
        raise SystemExit(f"CHANGELOG.md has no entry for {version}")
    parts = []
    for name, chunks in release.sections.items():
        block = "\n".join(chunks).strip("\n")
        if name == PREAMBLE:
            parts.append(block)
        elif block:
            parts.append(f"### {name}\n\n{block}")
    return re.sub(r"\n{3,}", "\n\n", "\n\n".join(part for part in parts if part)).strip("\n") + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("version", help="a final version, such as 0.5.0")
    args = ap.parse_args()
    sys.stdout.write(notes(SOURCE.read_text(encoding="utf-8"), args.version.removeprefix("v")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
