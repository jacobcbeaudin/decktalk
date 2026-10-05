# uv's own image carries uv and no curl, which also proves the installer needs no downloader of its
# own once uv is there.
#
# Run as `sh -euc` inside the image, with install.sh mounted read only at /install.sh.
out="$(sh /install.sh)"
printf "%s\n" "$out"
case "$out" in
*"is already installed"*) ;;
*) echo "it did not recognise the uv that was already on PATH"; exit 1 ;;
esac
PATH="$HOME/.local/bin:$PATH"
export PATH
decktalk --version
