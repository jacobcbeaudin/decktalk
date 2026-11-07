# The docs say to run the installer again to update. `pixi global install` leaves an installed
# package where it is and keeps the pin it was given, so this installs the release before the
# current one with a pin, runs the installer again without one, and needs the current release.
# The container's path is not this checkout's, so shellcheck is told where the file is.
# shellcheck source=scripts/shell/scenario.sh
. /shell/scenario.sh
with_tools curl jq
needs_releases 2
older="$(printf '%s\n' "$RELEASED" | tail -2 | head -1)"
newest="$(printf '%s\n' "$RELEASED" | tail -1)"
DECKTALK_VERSION="$older" sh /install.sh
if [ "$(installed_version)" != "$older" ]; then
  echo "pinned $older, installed $(installed_version)"
  exit 1
fi
sh /install.sh
if [ "$(installed_version)" != "$newest" ]; then
  echo "a second run left $(installed_version) in place of $newest"
  exit 1
fi
