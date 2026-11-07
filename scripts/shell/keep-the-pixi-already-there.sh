# Pixi's own image carries Pixi and no curl or wget, which also proves the installer needs no
# downloader of its own once Pixi is there. curl is installed only to read the channel, and removed
# again before the installer runs.
# The container's path is not this checkout's, so shellcheck is told where the file is.
# shellcheck source=scripts/shell/scenario.sh
. /shell/scenario.sh
with_tools curl jq
needs_releases 1
apt-get remove -y -qq curl >/dev/null
out="$(sh /install.sh)"
printf "%s\n" "$out"
case "$out" in
*"is already installed"*) ;;
*) echo "it did not recognise the Pixi that was already on PATH"; exit 1 ;;
esac
case "$out" in
*"Downloading Pixi"*) echo "it downloaded a second Pixi"; exit 1 ;;
esac
decktalk --version
