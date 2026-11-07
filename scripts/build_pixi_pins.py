"""Write the Pixi version and its digests wherever Pixi is downloaded outside a workflow.

    pixi run python scripts/build_pixi_pins.py --write    # move every copy to `requires-pixi`
    pixi run python scripts/build_pixi_pins.py --check    # exit 1 if a copy names another Pixi

The `requires-pixi` floor in pyproject.toml is the one home of the Pixi version. Every workflow
reads it through `check.py --pixi-version`. Two copies cannot read it, so this generator writes them:

- install.sh, which is served as one file to a machine that has nothing else, carries the version
  and the SHA-256 of each of the four archives it may download. The digests come from the `.sha256`
  file the Pixi release publishes beside each archive, read once when the version moves, so a
  release asset replaced afterwards no longer matches what install.sh checks.
- scripts/check.py carries Pixi's own container image, by its tag and by the digest the registry
  names for that tag, read once when the version moves.

When the version has not moved, the digests are read back from the two files. `--check` reaches no
network: when the version has moved, it fails with the one command that writes the new version, and
only `--write` reads the new digests, failing with the address it could not read.
"""

from __future__ import annotations

import functools
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

import check
import generated

ROOT = Path(__file__).resolve().parent.parent
INSTALLER = ROOT / "install.sh"
TABLE = ROOT / "scripts" / "check.py"

INSTALLER_BLOCK = ("# pixi-pins:start\n", "# pixi-pins:end\n")
TABLE_BLOCK = ("# pixi-image:start\n", "# pixi-image:end\n")

TARGETS = (
    "x86_64-unknown-linux-musl",
    "aarch64-unknown-linux-musl",
    "x86_64-apple-darwin",
    "aarch64-apple-darwin",
)
"""The four archives install.sh chooses from: Pixi's static Linux build and its macOS build, on x86_64 and arm64."""

RELEASES = "https://github.com/prefix-dev/pixi/releases/download"
IMAGE = "ghcr.io/prefix-dev/pixi"
REGISTRY_TOKEN = "https://ghcr.io/token?scope=repository:prefix-dev/pixi:pull"
MANIFESTS = "https://ghcr.io/v2/prefix-dev/pixi/manifests/{tag}"
INDEX_TYPES = "application/vnd.oci.image.index.v1+json, application/vnd.docker.distribution.manifest.list.v2+json"
"""The manifest kinds that name every platform of an image, whose digest is the one `docker run` pulls by."""

TIMEOUT_SECONDS = 30
"""How long one request to GitHub or to the registry may take before the generator fails rather than hangs."""

DIGEST = re.compile(r"[0-9a-f]{64}")
VERSION_LINE = re.compile(r'^PIXI_VERSION="(?P<version>[^"]+)"$', re.M)
PAIR = re.compile(r"^(?P<target>[\w.-]+)=(?P<digest>[0-9a-f]{64})$", re.M)


MOVED = (
    "requires-pixi names Pixi {version}, and install.sh and the check table name Pixi {held}. The digests of "
    "a new Pixi are read from its release, so run `pixi run check --group generated --write` with the network."
)
"""Why `--check` fails when the version has moved, with the command that moves every copy."""


def read(url: str, headers: dict[str, str] | None = None) -> tuple[bytes, dict[str, str]]:
    """The body and the headers one HTTPS request answers with, following redirects.

    A refusal or a network that cannot reach the address stops the generator with the address and
    the reason, because a new Pixi release may not be published yet when Renovate proposes it.
    """
    request = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            return response.read(), dict(response.headers.items())
    except urllib.error.HTTPError as refused:
        raise SystemExit(f"{url} answered {refused.code} {refused.reason}, so no digest was written.") from None
    except OSError as unreachable:
        raise SystemExit(f"{url} could not be read ({unreachable}), so no digest was written.") from None


def archive_digest(version: str, target: str) -> str:
    """The SHA-256 the Pixi release publishes beside the archive for `target`, as its `.sha256` file says it."""
    body, _ = read(f"{RELEASES}/v{version}/pixi-{target}.tar.gz.sha256")
    digest = body.decode().split()[0] if body.split() else ""
    if not DIGEST.fullmatch(digest):
        raise SystemExit(f"Pixi {version} publishes no SHA-256 for {target}, so install.sh cannot check it.")
    return digest


def image_digest(version: str) -> str:
    """The digest the registry names for Pixi's image at `version`, over every platform it was built for."""
    body, _ = read(REGISTRY_TOKEN)
    token = json.loads(body)["token"]
    _, headers = read(MANIFESTS.format(tag=version), {"Authorization": f"Bearer {token}", "Accept": INDEX_TYPES})
    found = {name.lower(): value for name, value in headers.items()}.get("docker-content-digest", "")
    if not found.startswith("sha256:"):
        raise SystemExit(f"the registry names no digest for {IMAGE}:{version}.")
    return found


def installer_block(text: str) -> str:
    start, end = INSTALLER_BLOCK
    return text.partition(start)[2].partition(end)[0]


def held_version(text: str) -> str:
    """The Pixi version install.sh pins, or an empty string when it pins none."""
    held = VERSION_LINE.search(installer_block(text))
    return held["version"] if held else ""


def archive_digests(version: str, text: str) -> dict[str, str]:
    """The digest of each archive at `version`: read back from install.sh when it already names `version`."""
    if held_version(text) == version:
        return {match["target"]: match["digest"] for match in PAIR.finditer(installer_block(text))}
    return {target: archive_digest(version, target) for target in TARGETS}


def render_installer(version: str, digests: dict[str, str]) -> str:
    pairs = "\n".join(f"{target}={digests[target]}" for target in TARGETS)
    return (
        "# Written by scripts/build_pixi_pins.py. The version is the `requires-pixi` floor in DeckTalk's\n"
        "# pyproject.toml, which every CI leg runs, and each digest is the one the Pixi release published\n"
        "# beside that archive when the version was chosen, so a replaced release asset is refused.\n"
        f'PIXI_VERSION="{version}"\n'
        f'PIXI_SHA256="\n{pairs}\n"\n'
    )


def render_table(version: str) -> str:
    image = check.PIXI_IMAGE
    digest = image.digest if image.tag == version else image_digest(version)
    return f'PIXI_IMAGE = Image(\n    "{IMAGE}", "{version}", "{digest}"\n)\n'


def files(*, fetch: bool = True) -> dict[Path, str]:
    """install.sh and the check table at the Pixi `requires-pixi` names, reading new digests only when `fetch`."""
    version = check.pixi_version()
    installer = INSTALLER.read_text(encoding="utf-8")
    table = TABLE.read_text(encoding="utf-8")
    held = {held_version(installer), check.PIXI_IMAGE.tag}
    if not fetch and held != {version}:
        raise SystemExit(MOVED.format(version=version, held=" and ".join(sorted(held - {version}))))
    written = render_installer(version, archive_digests(version, installer))
    return {
        INSTALLER: generated.splice(installer, INSTALLER_BLOCK, written, where=INSTALLER),
        TABLE: generated.splice(table, TABLE_BLOCK, render_table(version), where=TABLE),
    }


if __name__ == "__main__":
    sys.exit(generated.run(files, checked=functools.partial(files, fetch=False)))
