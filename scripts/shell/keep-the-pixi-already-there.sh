# Pixi's own image carries Pixi and no curl or wget, which also proves the installer needs no
# downloader of its own once Pixi is there. curl is installed only to read the channel, and removed
# again before the installer runs.
#
# Run as `sh -euc` inside the image after released-versions.sh, with install.sh mounted read only at
# /install.sh.
apt-get update -qq && apt-get install -y -qq curl jq >/dev/null
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
PATH="$HOME/.pixi/bin:$PATH"
export PATH
decktalk --version
