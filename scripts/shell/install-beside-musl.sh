# A glibc Debian that also carries musl's loader, which Debian's `musl` package installs for cross
# builds. The installer reads the C library the system runs, so it goes on here, and a dry run is
# enough to show that it did not refuse.
# The container's path is not this checkout's, so shellcheck is told where the file is.
# shellcheck source=scripts/shell/scenario.sh
. /shell/scenario.sh
with_tools musl
sh /install.sh --dry-run
