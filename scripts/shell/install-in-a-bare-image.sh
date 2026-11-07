# What `install.sh` has to survive: an image with nothing but its package manager on it. The
# installer's own promise is that a machine that has never had DeckTalk ends with `decktalk
# --version` printing one, so the whole check is that line, run in a shell the installer did not
# write. curl fetches and jq reads the channel's index.
# The container's path is not this checkout's, so shellcheck is told where the file is.
# shellcheck source=scripts/shell/scenario.sh
. /shell/scenario.sh
with_tools curl jq
needs_releases 1
sh /install.sh
decktalk --version
