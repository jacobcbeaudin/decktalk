# The release before the current one on the channel. Pinning to the latest version would pass on an
# installer that dropped the pin on the floor and installed the latest anyway.
# The container's path is not this checkout's, so shellcheck is told where the file is.
# shellcheck source=scripts/shell/scenario.sh
. /shell/scenario.sh
with_tools curl jq
needs_releases 2
DECKTALK_VERSION="$(printf '%s\n' "$RELEASED" | tail -2 | head -1)"
export DECKTALK_VERSION
sh /install.sh
if [ "$(installed_version)" != "$DECKTALK_VERSION" ]; then
  echo "pinned $DECKTALK_VERSION, installed $(installed_version)"
  exit 1
fi
