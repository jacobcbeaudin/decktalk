# The release before the current one on the channel. Pinning to the latest version would pass on an
# installer that dropped the pin on the floor and installed the latest anyway.
#
# Run as `sh -euc` inside the image after released-versions.sh, with install.sh mounted read only at
# /install.sh.
apt-get update -qq && apt-get install -y -qq curl jq >/dev/null
needs_releases 2
DECKTALK_VERSION="$(printf '%s\n' "$RELEASED" | tail -2 | head -1)"
export DECKTALK_VERSION
sh /install.sh
PATH="$HOME/.pixi/bin:$PATH"
export PATH
# An older release may print `decktalk 0.4.1` where a newer one prints `0.6.0`, so the name is
# dropped before the two versions are compared.
got="$(decktalk --version)"
got="${got#decktalk }"
if [ "$got" != "$DECKTALK_VERSION" ]; then
  echo "pinned $DECKTALK_VERSION, installed $got"
  exit 1
fi
