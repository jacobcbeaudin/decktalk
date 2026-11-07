# The Pixi half of the installer, run for real whatever the channel holds. install.sh downloads the
# Pixi archive for this machine from its GitHub release, checks it against the SHA-256 it pins and
# unpacks it, and only then installs DeckTalk. While the channel holds no release, that last step
# fails, so this scenario judges the Pixi the run left rather than the run's exit: a Pixi that is
# there passed the check, because a download that fails the check is never unpacked.
#
# The run is root's, as in a Dockerfile, so the Pixi it left must be root's too and not the account
# that built the archive.
# The container's path is not this checkout's, so shellcheck is told where the file is.
# shellcheck source=scripts/shell/scenario.sh
. /shell/scenario.sh
with_tools curl
set +e
sh /install.sh
set -e
pixi="$HOME/.pixi/bin/pixi"
if [ ! -x "$pixi" ]; then
  echo "install.sh left no Pixi at $pixi, so the download, the check or the unpack failed"
  exit 1
fi
wanted="pixi $(sed -n 's/^PIXI_VERSION="\(.*\)"$/\1/p' "$INSTALL_SH")"
if [ "$("$pixi" --version)" != "$wanted" ]; then
  echo "install.sh pins $wanted and left $("$pixi" --version)"
  exit 1
fi
owner="$(stat -c %u "$pixi")"
if [ "$owner" != "$(id -u)" ]; then
  echo "the Pixi install.sh left belongs to uid $owner, the archive's, rather than to uid $(id -u), who ran it"
  exit 1
fi
