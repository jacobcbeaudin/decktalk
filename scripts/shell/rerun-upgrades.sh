# The docs say to run the installer again to update. `pixi global install` leaves an installed
# package where it is and keeps the pin it was given, so this installs the release before the
# current one with a pin, runs the installer again without one, and needs the current release.
#
# Run as `sh -euc` inside the image after released-versions.sh, with install.sh mounted read only at
# /install.sh.
apt-get update -qq && apt-get install -y -qq curl jq >/dev/null
needs_releases 2
older="$(printf '%s\n' "$RELEASED" | tail -2 | head -1)"
newest="$(printf '%s\n' "$RELEASED" | tail -1)"
PATH="$HOME/.pixi/bin:$PATH"
export PATH
installed() {
  got="$(decktalk --version)"
  echo "${got#decktalk }"
}
DECKTALK_VERSION="$older" sh /install.sh
if [ "$(installed)" != "$older" ]; then
  echo "pinned $older, installed $(installed)"
  exit 1
fi
sh /install.sh
if [ "$(installed)" != "$newest" ]; then
  echo "a second run left $(installed) in place of $newest"
  exit 1
fi
