# What `install.sh` has to survive: an image with nothing but curl on it. The installer's own
# promise is that a machine that has never had DeckTalk ends with `decktalk --version` printing one,
# so the whole check is that line, run in a shell the installer did not write.
#
# Run as `sh -euc` inside the image, with install.sh mounted read only at /install.sh.
if command -v apt-get >/dev/null 2>&1; then
  apt-get update -qq && apt-get install -y -qq curl >/dev/null
else
  dnf install -y -q curl >/dev/null
fi
sh /install.sh
PATH="$HOME/.local/bin:$PATH"
export PATH
decktalk --version
