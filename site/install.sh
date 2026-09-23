#!/bin/sh
# DeckTalk installer. Read it before you run it; that is why it is served from a URL.
#
#   curl -LsSf https://decktalk.ai/install.sh | sh
#
# It installs uv if you do not have it, then `uv tool install decktalk`. It never runs sudo, and it
# never installs Chromium or ffmpeg: `decktalk install` does that, separately and visibly, because
# on Linux it needs root for Chromium's system libraries.
#
# DECKTALK_VERSION=0.4.1 pins the version. --dry-run prints what would happen and changes nothing.
#
# Everything is inside main(), which is called on the last line, so a download cut off part way
# through runs nothing at all instead of half an install.

set -eu

DECKTALK_VERSION="${DECKTALK_VERSION:-}"
UV_INSTALLER="https://astral.sh/uv/install.sh"

say() { printf '%s\n' "$*"; }
err() { printf 'install.sh: %s\n' "$*" >&2; }

usage() {
	cat <<'USAGE'
DeckTalk installer.

    curl -LsSf https://decktalk.ai/install.sh | sh

Options, passed after `-s --`, for example `| sh -s -- --dry-run`:
    --dry-run    Print what would happen and change nothing.
    -h, --help   Print this.

Environment:
    DECKTALK_VERSION   Install this version instead of the latest.
USAGE
}

have() { command -v "$1" >/dev/null 2>&1; }

fetch() {
	# One downloader, whichever the machine has, failing loudly on an HTTP error.
	if have curl; then
		curl -LsSf "$1"
	elif have wget; then
		wget -qO- "$1"
	else
		err "needs curl or wget to download $1"
		return 1
	fi
}

check_platform() {
	os="$(uname -s)"
	case "$os" in
	Linux)
		# Playwright, which records the slides, publishes no musllinux wheels, so DeckTalk cannot
		# resolve on Alpine at all. Say so here rather than leaving a resolver trace to read.
		if (ldd --version 2>&1 | grep -qi musl) || [ -e /lib/ld-musl-x86_64.so.1 ] || [ -e /lib/ld-musl-aarch64.so.1 ]; then
			err "this is a musl system (Alpine or similar), and DeckTalk needs glibc:"
			err "Playwright, which records the slides, publishes no musl wheels."
			return 1
		fi
		;;
	Darwin) ;;
	MINGW* | MSYS* | CYGWIN* | Windows_NT)
		err "Windows: install with PowerShell instead."
		err '  powershell -c "irm https://astral.sh/uv/install.ps1 | iex; uv tool install decktalk"'
		return 1
		;;
	*)
		err "unsupported system: $os. Install with: uv tool install decktalk"
		return 1
		;;
	esac
}

ensure_uv() {
	# uv does the hard part, including fetching a Python when the machine has none. Installing it
	# here is the same trust the next line already assumes.
	if have uv; then
		say "uv $(uv --version 2>/dev/null | awk '{print $2}') is already installed."
		return 0
	fi
	if [ "$DRY_RUN" = 1 ]; then
		say "would install uv from $UV_INSTALLER"
		return 0
	fi
	if ! have curl && ! have wget; then
		err "needs curl or wget to install uv. Install uv yourself (https://docs.astral.sh/uv/)"
		err "and run: uv tool install decktalk"
		return 1
	fi
	say "Installing uv from $UV_INSTALLER"
	# Downloaded to a file rather than piped: a pipeline reports the last command's status, so
	# `fetch … | sh` would report sh succeeding on empty input and hide a failed download.
	tmp="${TMPDIR:-/tmp}/decktalk-uv-install.$$.sh"
	if ! fetch "$UV_INSTALLER" >"$tmp" || [ ! -s "$tmp" ]; then
		rm -f "$tmp"
		err "could not download uv's installer from $UV_INSTALLER"
		return 1
	fi
	sh "$tmp" || { rm -f "$tmp"; err "uv's installer failed"; return 1; }
	rm -f "$tmp"
	# uv's installer puts it in ~/.local/bin, which this shell may not have on PATH yet.
	if ! have uv; then
		PATH="$HOME/.local/bin:$PATH"
		export PATH
	fi
	have uv || {
		err "uv installed but is not on PATH. Open a new shell and run: uv tool install decktalk"
		return 1
	}
}

install_decktalk() {
	spec="decktalk"
	[ -n "$DECKTALK_VERSION" ] && spec="decktalk==$DECKTALK_VERSION"
	if [ "$DRY_RUN" = 1 ]; then
		say "would run: uv tool install $spec"
		return 0
	fi
	say "Installing $spec"
	uv tool install "$spec"
}

report() {
	say ""
	if [ "$DRY_RUN" = 1 ]; then
		say "Dry run: nothing was installed."
		return 0
	fi
	say "DeckTalk is installed. Next:"
	say ""
	say "    decktalk install      # Chromium and ffmpeg, once per machine. On Linux it asks for sudo."
	say "    decktalk init my-film"
	say "    cd my-film && decktalk build --no-voice"
	say ""
	say "That first build needs no account and spends nothing. For your own voice, put an"
	say "ElevenLabs key and voice id in .env and run: decktalk build"
	case ":$ORIGINAL_PATH:" in
	*":$HOME/.local/bin:"*) ;;
	*) say "" && say "Add ~/.local/bin to your PATH to run decktalk from a new shell." ;;
	esac
}

main() {
	DRY_RUN=0
	# Kept because ensure_uv may add ~/.local/bin to this shell's PATH. The advice at the end is
	# about the author's NEXT shell, so it has to read the PATH they actually have.
	ORIGINAL_PATH="$PATH"
	for arg in "$@"; do
		case "$arg" in
		--dry-run) DRY_RUN=1 ;;
		-h | --help)
			usage
			return 0
			;;
		*)
			err "unknown option: $arg"
			usage >&2
			return 2
			;;
		esac
	done

	check_platform
	ensure_uv
	install_decktalk
	report
}

main "$@"
