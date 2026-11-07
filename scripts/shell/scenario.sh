# The setup every installer scenario shares. Each scenario starts with `. /shell/scenario.sh`, and
# scripts/check.py runs it with install.sh mounted read only at /install.sh and this directory at
# /shell, and nothing else of the checkout.
#
# The conda channel is read from /install.sh, which is its one home. `needs_releases N` stops the
# scenario with a pass and the reason when the channel holds fewer than N final releases: the
# channel is empty until the first final release is published, and a scenario that failed on an
# empty channel would keep `main` red and so stop that first release from ever being cut.

INSTALL_SH=/install.sh

# Where `pixi global` puts the command, which a later line of a scenario runs by name.
PATH="$HOME/.pixi/bin:$PATH"
export PATH

# The channel install.sh installs from, read from its one `CHANNEL=` line.
channel_of() {
  sed -n 's/^CHANNEL="\(.*\)"$/\1/p' "$1"
}

# Every version of decktalk the channel's noarch index lists, oldest first. prefix.dev answers the
# index with a redirect to a signed download, so curl follows it. An index with no decktalk entry
# is an empty list. A channel that answers no index at all is a failure, because a wrong channel
# name would otherwise skip every scenario for ever.
released_versions() {
  index="$(mktemp)"
  if ! curl -fsSL -o "$index" "$1/noarch/repodata.json"; then
    rm -f "$index"
    echo "$1 answered no index of its releases" >&2
    return 1
  fi
  jq -r '[(.packages // {})[], (."packages.conda" // {})[]] | map(select(.name == "decktalk") | .version) | unique | .[]' "$index" | sort -V
  rm -f "$index"
}

# Install the named tools with whichever package manager the image has, with no prompt and no
# progress output.
with_tools() {
  if command -v apt-get >/dev/null 2>&1; then
    DEBIAN_FRONTEND=noninteractive apt-get update -qq
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "$@" >/dev/null
  else
    dnf install -y -q "$@" >/dev/null
  fi
}

# Stop the scenario with a pass when the channel holds fewer than $1 releases, and leave the
# releases in $RELEASED, oldest first.
needs_releases() {
  channel="$(channel_of "$INSTALL_SH")"
  RELEASED="$(released_versions "$channel")"
  count="$(printf '%s\n' "$RELEASED" | grep -c . || true)"
  if [ "$count" -lt "$1" ]; then
    echo "skipped: $channel holds $count releases of decktalk and this scenario needs $1."
    echo "release.yml publishes each final release there, so the scenario runs once enough exist."
    exit 0
  fi
}

# The version the installed command reports, without the name an older release prints before it.
installed_version() {
  got="$(decktalk --version)"
  echo "${got#decktalk }"
}
