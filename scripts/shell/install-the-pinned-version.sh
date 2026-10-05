# The release before the current one. Pinning to the latest version would pass on an installer that
# dropped the pin on the floor and installed the latest anyway.
#
# Run as `sh -euc` inside the image, with install.sh mounted read only at /install.sh.
apt-get update -qq && apt-get install -y -qq curl jq >/dev/null
DECKTALK_VERSION="$(curl -LsSf https://pypi.org/pypi/decktalk/json | jq -r '.releases | keys_unsorted[]' | sort -V | tail -2 | head -1)"
export DECKTALK_VERSION
if [ -z "$DECKTALK_VERSION" ]; then
  echo "no released version to pin to" >&2
  exit 1
fi
sh /install.sh
PATH="$HOME/.local/bin:$PATH"
export PATH
# 0.4 prints `decktalk 0.4.1` and 0.5 prints `0.5.0rc2`, and the pin is whichever release is
# second newest, so the name is dropped before the two versions are compared.
got="$(decktalk --version)"
got="${got#decktalk }"
if [ "$got" != "$DECKTALK_VERSION" ]; then
  echo "pinned $DECKTALK_VERSION, installed $got"
  exit 1
fi
