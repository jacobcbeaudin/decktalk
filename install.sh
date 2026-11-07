#!/bin/sh
# DeckTalk installer. Read it before you run it; that is why it is served from a URL.
#
#   curl -LsSf https://decktalk.ai/install.sh | sh
#
# It installs Pixi if you do not have it, then `pixi global install decktalk` from DeckTalk's own
# conda channel. That is all it does.
#
# Pixi comes from its GitHub release, pinned below, and is checked against the SHA-256 written below
# beside the version before it is unpacked into ~/.pixi/bin (or $PIXI_HOME/bin). Pixi's own installer is
# not run, so nothing edits a shell startup file and nothing reports the install to anyone: the
# script prints the PATH line for you to add. Run it again to update DeckTalk.
#
# It never runs sudo, and it never fetches Chromium or ffmpeg: `decktalk install` does that,
# separately and visibly, because on Linux it reaches `playwright install chromium --with-deps` and
# Playwright uses sudo for Chromium's system libraries. An installer that ran it would be an
# installer that could ask for a root password, and the whole argument for reading a script before
# piping it into a shell is that it does not do things like that.
#
# Every step's output is kept in a log, whether or not it is shown. On failure the log's path is
# printed and the file is left behind; on success it is removed unless you asked to keep it.
#
# DECKTALK_VERSION=X.Y.Z pins a final release the channel holds, and any other value is refused
# before anything is downloaded. Options go after `-s --`, for example `| sh -s -- --dry-run`; run
# with --help to see them.
#
# Everything is inside main(), which is called on the last line, so a download cut off part way
# through runs nothing at all instead of half an install.

set -eu

DECKTALK_VERSION="${DECKTALK_VERSION:-}"
# The Pixi this script installs when the machine has none, and the SHA-256 of each archive it may
# download.
# pixi-pins:start
# Written by scripts/build_pixi_pins.py. The version is the `requires-pixi` floor in DeckTalk's
# pyproject.toml, which every CI leg runs, and each digest is the one the Pixi release published
# beside that archive when the version was chosen, so a replaced release asset is refused.
PIXI_VERSION="0.84.1"
PIXI_SHA256="
x86_64-unknown-linux-musl=c81a16595e9e925fac3b12fe50cedbf415619a421ad2b72c1bba2a6ba6c2454a
aarch64-unknown-linux-musl=0595613aedd95f84f90a55ba4b51fc1e4b86afb4eaa3e34ca3f3454d8f22c2e6
x86_64-apple-darwin=9f951f413bfeea118e5be4375d68ecf8ba38424019b74a5240a392e58df89908
aarch64-apple-darwin=3b351fe52f3d12147457b6e55e3ab617cf43a5011f92230a4c506f9676caff8f
"
# pixi-pins:end
PIXI_RELEASES="https://github.com/prefix-dev/pixi/releases/download"
# DeckTalk's conda package lives in its own channel, and its dependencies (Python, Node, Playwright)
# in conda-forge, so `pixi global` is given both, in that order.
# This line is the one home of the channel: scripts/build_install_docs.py writes it into every page
# that shows an install, into RELEASING.md and into release.yml's upload, and the container scenarios
# read it from this file.
CHANNEL="https://prefix.dev/jcbcodes/decktalk"
# The install this script runs, and the one a Windows reader runs in PowerShell instead, each written
# once and printed wherever a message names it.
INSTALL_LINE="pixi global install -c $CHANNEL -c conda-forge decktalk"
WINDOWS_LINE="powershell -ExecutionPolicy ByPass -c \"irm -useb https://pixi.sh/install.ps1 | iex; $INSTALL_LINE\""

# ---- presentation -------------------------------------------------------------------------------
# Colour and motion only when stdout is a terminal. Piping this script into sh leaves stdout on the
# terminal, because it is stdin that is the pipe, so `[ -t 1 ]` is true for the one-liner and false
# in CI, in a Dockerfile and under `| tee`. That is exactly the split we want: a person gets the
# spinner, a log gets plain lines in the order they happened.

BOLD='' DIM='' RED='' GREEN='' GOLD='' RESET='' HIDE_CURSOR='' SHOW_CURSOR=''
FANCY=0
UNICODE=0
TTY=0
VERBOSE=0
KEEP_LOG=0
SPIN_PID=''
LABEL=''
WORK=''
STEP_LOG=''
STEP_RC=''
TRANSCRIPT=''
TARGET=''
PIXI=''
FAILED=0

setup_style() {
	# Box drawing and braille need a UTF-8 locale; under LC_ALL=C they arrive as mojibake.
	case "${LC_ALL-}${LC_CTYPE-}${LANG-}" in
	*[Uu][Tt][Ff]8* | *[Uu][Tt][Ff]-8*) UNICODE=1 ;;
	*) UNICODE=0 ;;
	esac
	[ -t 1 ] && TTY=1
	# NO_COLOR is the convention (no-color.org): set at all, however empty, means colour off.
	[ "${NO_COLOR-}" = "" ] || return 0
	[ "${TERM-}" != dumb ] || return 0
	[ "$TTY" = 1 ] || return 0

	BOLD=$(printf '\033[1m')
	DIM=$(printf '\033[2m')
	RED=$(printf '\033[31m')
	GREEN=$(printf '\033[32m')
	GOLD=$(printf '\033[33m') # the voice colour, used only where the brand uses it
	RESET=$(printf '\033[0m')
	HIDE_CURSOR=$(printf '\033[?25l')
	SHOW_CURSOR=$(printf '\033[?25h')
	FANCY=1

	# POSIX only requires sleep to take whole seconds, and a one-second frame is not a spinner. If
	# this shell's sleep refuses a fraction, keep the colour and drop the motion.
	sleep 0.05 2>/dev/null || FANCY=0
}

say() { printf '%s\n' "$*"; }
err() { printf '%sinstall.sh:%s %s\n' "$RED" "$RESET" "$*" >&2; }
note() { printf '%s%s%s\n' "$DIM" "$*" "$RESET"; }

mark_ok() { if [ "$UNICODE" = 1 ]; then printf '%s' "✓"; else printf '%s' "ok"; fi; }
mark_bad() { if [ "$UNICODE" = 1 ]; then printf '%s' "✗"; else printf '%s' "!!"; fi; }

# ---- the log ------------------------------------------------------------------------------------
# One file for the whole run, written whether or not anything is shown, so a failure three steps
# back is still readable and so a bug report can carry one path instead of a scrollback.

now() { date '+%Y-%m-%dT%H:%M:%S' 2>/dev/null || echo "?"; }
epoch() { date +%s 2>/dev/null || echo 0; }

log() { [ -z "$TRANSCRIPT" ] || printf '%s %s\n' "$(now)" "$*" >>"$TRANSCRIPT"; }

log_open() {
	# A log the reader did not name is a new file of its own, made by mktemp, so no other user of a
	# shared /tmp can have planted it as a link to something this run would truncate.
	if [ -n "${DECKTALK_INSTALL_LOG-}" ]; then
		TRANSCRIPT="$DECKTALK_INSTALL_LOG"
		: >"$TRANSCRIPT" 2>/dev/null || TRANSCRIPT=''
	else
		TRANSCRIPT="$(mktemp "${TMPDIR:-/tmp}/decktalk-install.XXXXXX" 2>/dev/null)" || TRANSCRIPT=''
	fi
	log "install.sh starting"
	log "  uname: $(uname -a 2>/dev/null || echo '?')"
	log "  shell: ${0##*/}  tty=$TTY  unicode=$UNICODE  fancy=$FANCY"
	log "  args: $*"
	log "  DECKTALK_VERSION=${DECKTALK_VERSION:-<latest>}"
	log "  PATH=$PATH"
}

banner() {
	[ "$FANCY" = 1 ] || return 0
	# The mark itself: a screen, with the voice bar under it, which is the favicon in two colours.
	printf '\n'
	printf '   %s┌────────┐%s\n' "$DIM" "$RESET"
	printf '   %s│        │%s   %sDeckTalk%s\n' "$DIM" "$RESET" "$BOLD" "$RESET"
	printf '   %s└────────┘%s   %sNarrated video from a script, in your own voice.%s\n' "$DIM" "$RESET" "$DIM" "$RESET"
	printf '   %s▂▂▂▂%s\n\n' "$GOLD" "$RESET"
}

spin_start() {
	if [ "$FANCY" != 1 ]; then
		# The plain path prints the label up front, because the next thing on screen is the child's
		# own output and a reader needs to know whose it is.
		say "$LABEL"
		return 0
	fi
	printf '%s' "$HIDE_CURSOR"
	(
		# A subshell with the parent's traps cleared: it is killed on purpose, and it must not run
		# the parent's cleanup when it dies. It reads $LABEL rather than "$1" because the frame
		# list below is carried in the positional parameters.
		trap - EXIT INT TERM
		while :; do
			if [ "$UNICODE" = 1 ]; then
				set -- ⠋ ⠙ ⠹ ⠸ ⠼ ⠴ ⠦ ⠧ ⠇ ⠏
			else
				set -- '-' "\\" '|' '/'
			fi
			for frame in "$@"; do
				printf '\r  %s%s%s %s' "$GOLD" "$frame" "$RESET" "$LABEL"
				sleep 0.08
			done
		done
	) &
	SPIN_PID=$!
}

spin_stop() {
	[ -n "$SPIN_PID" ] || return 0
	kill "$SPIN_PID" 2>/dev/null || true
	wait "$SPIN_PID" 2>/dev/null || true
	SPIN_PID=''
	printf '\r\033[2K' # erase the spinner's line so the result can take it
	return 0
}

done_line() {
	# "$1" label, "$2" epoch when the step started (0 if this shell has no date).
	finished=$(epoch)
	if [ "$2" != 0 ] && [ "$finished" != 0 ] && [ "$((finished - $2))" -ge 2 ]; then
		printf '  %s%s%s %s %s(%ss)%s\n' "$GREEN" "$(mark_ok)" "$RESET" "$1" "$DIM" "$((finished - $2))" "$RESET"
	else
		printf '  %s%s%s %s\n' "$GREEN" "$(mark_ok)" "$RESET" "$1"
	fi
}

fail_line() { printf '  %s%s%s %s\n' "$RED" "$(mark_bad)" "$RESET" "$1"; }

# step "Label" cmd args...
#
# For work that is quiet or whose output is noise: spin on one line, then replace it with a tick
# and how long it took. Everything the child printed goes to the log either way, and is shown on
# screen only if the step failed or --verbose asked for it.
step() {
	LABEL="$1"
	shift
	started=$(epoch)
	log "step: $LABEL  \$ $*"
	spin_start
	# Captured on the failure arm of the || rather than after an if. After an `if` whose condition
	# is false and which has no else, `$?` is the if statement's own status, which is 0, so reading
	# it there reports every failed step as a success and the script runs on into the next one.
	status=0
	"$@" >"$STEP_LOG" 2>&1 || status=$?
	spin_stop
	[ -z "$TRANSCRIPT" ] || sed 's/^/    /' "$STEP_LOG" >>"$TRANSCRIPT"
	log "step: $LABEL exited $status after $(($(epoch) - started))s"
	if [ "$status" = 0 ]; then
		done_line "$LABEL" "$started"
		[ "$VERBOSE" != 1 ] || sed 's/^/    /' "$STEP_LOG"
		return 0
	fi
	fail_line "$LABEL"
	# The whole reason the output was held back: show every line of it now, indented so it reads as
	# evidence for the line above rather than as more of this script's own talking.
	sed 's/^/    /' "$STEP_LOG" >&2
	return "$status"
}

# step_stream "Label" cmd args...
#
# For work that already reports real progress: Pixi prints each package it fetches and a live bar,
# and a spinner over the top of that would replace a true number with a decorative one. So the child
# keeps the screen, and its output is teed into the log at the same time.
step_stream() {
	LABEL="$1"
	shift
	started=$(epoch)
	log "step: $LABEL  \$ $*"
	if [ "$FANCY" = 1 ]; then
		printf '  %s%s%s\n' "$DIM" "$LABEL" "$RESET"
	else
		say "$LABEL"
	fi
	# tee would report its own exit status, so the child's is written to a file inside the pipe.
	# The left side of a pipe is a subshell that inherits `set -e`, so the status is captured on the
	# failure arm of the || and written on every path. The file is removed first, so a step whose
	# status never arrived reads as a failure rather than as the step before it.
	rm -f "$STEP_RC"
	{
		rc=0
		"$@" 2>&1 || rc=$?
		echo "$rc" >"$STEP_RC"
	} | {
		if [ -n "$TRANSCRIPT" ]; then
			tee -a "$TRANSCRIPT"
		else
			cat
		fi
	}
	status=$(cat "$STEP_RC" 2>/dev/null || true)
	case "$status" in
	'' | *[!0-9]*) status=1 ;;
	esac
	log "step: $LABEL exited $status after $(($(epoch) - started))s"
	if [ "$status" = 0 ]; then
		done_line "$LABEL" "$started"
		return 0
	fi
	fail_line "$LABEL"
	return "$status"
}

cleanup() {
	spin_stop
	[ -z "$WORK" ] || rm -rf "$WORK"
	[ "$FANCY" != 1 ] || printf '%s' "$SHOW_CURSOR"
	if [ -n "$TRANSCRIPT" ]; then
		if [ "$FAILED" = 1 ] || [ "$KEEP_LOG" = 1 ]; then
			printf '%sFull log: %s%s\n' "$DIM" "$TRANSCRIPT" "$RESET" >&2
		else
			rm -f "$TRANSCRIPT"
		fi
	fi
	return 0
}

on_error() {
	# Dropped first, because `exit` from inside a trap fires the EXIT trap on the way out. Left in
	# place, cleanup ran twice (two "Full log" lines) and the EXIT arm's own status replaced the
	# one being reported, so an interrupt exited 1 instead of 130.
	trap - EXIT INT TERM
	FAILED=1
	cleanup
	exit "${1:-1}"
}

usage() {
	cat <<'USAGE'
DeckTalk installer.

    curl -LsSf https://decktalk.ai/install.sh | sh

Options, passed after `-s --`, for example `| sh -s -- --dry-run`:
    --dry-run      Print what would happen and change nothing.
    --verbose      Show the output of every step, not just failing ones.
    --keep-log     Keep the log file even when everything works.
    --no-color     No colour and no spinner, even on a terminal.
    -h, --help     Print this.

Environment:
    DECKTALK_VERSION     Install this version instead of the latest.
    PIXI_HOME            Where Pixi and DeckTalk go, instead of ~/.pixi.
    DECKTALK_INSTALL_LOG Write the log here instead of a temp file.
    NO_COLOR             Set to anything for the same effect as --no-color.
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

# Whether the C library this system runs is musl. The loader is asked first, because a glibc
# system may also carry musl's loader, which Debian's `musl` package installs for cross builds.
runs_on_musl() {
	said="$(ldd --version 2>&1 || true)"
	case "$said" in
	*GLIBC* | *"GNU libc"* | *"GNU C Library"*) return 1 ;;
	*musl*) return 0 ;;
	esac
	for loader in /lib/ld-musl-*; do
		[ -e "$loader" ] && return 0
	done
	return 1
}

# The channel holds final releases alone, each named by three numbers, so any other pin could only
# fail later, at the solver, after Pixi was downloaded.
check_version() {
	case "$DECKTALK_VERSION" in
	'') return 0 ;;
	*[!0-9.]* | .* | *. | *..* | *.*.*.*) ;;
	*.*.*) return 0 ;;
	esac
	err "DECKTALK_VERSION is \"$DECKTALK_VERSION\", and a pin is the number of a final release, such as 0.7.0."
	return 1
}

check_platform() {
	os="$(uname -s)"
	case "$os" in
	Linux)
		# conda-forge, which builds the Python, Node and Playwright that DeckTalk runs on, publishes
		# no musl builds, so nothing it installs can start on Alpine. Say so before anything is
		# downloaded rather than leaving a solver or loader error to read.
		if runs_on_musl; then
			err "this is a musl system (Alpine or similar), and DeckTalk needs glibc:"
			err "conda-forge, which builds its Python, Node and Playwright, publishes no musl builds."
			return 1
		fi
		# Pixi's Linux build is one static binary, so the same archive runs on every glibc.
		suffix="unknown-linux-musl"
		;;
	Darwin) suffix="apple-darwin" ;;
	MINGW* | MSYS* | CYGWIN* | Windows_NT)
		err "Windows: install with PowerShell instead."
		err "  $WINDOWS_LINE"
		return 1
		;;
	*)
		err "unsupported system: $os. With Pixi, install with: $INSTALL_LINE"
		return 1
		;;
	esac
	machine="$(uname -m)"
	case "$machine" in
	x86_64 | amd64) arch="x86_64" ;;
	aarch64 | arm64) arch="aarch64" ;;
	*)
		err "unsupported processor: $machine. conda-forge builds DeckTalk's dependencies for x86_64 and arm64."
		return 1
		;;
	esac
	TARGET="$arch-$suffix"
}

sha256_of() {
	# sha256sum is GNU coreutils and busybox, shasum is macOS. Either prints the digest first.
	if have sha256sum; then
		sha256sum "$1" | awk '{print $1}'
	elif have shasum; then
		shasum -a 256 "$1" | awk '{print $1}'
	else
		err "needs sha256sum or shasum to check the Pixi download"
		return 1
	fi
}

# Each step body below is one chain of `&&` or ends by testing its own result, because a function
# called on the left of `||`, as `step` calls it, runs with `set -e` off.

download_pixi() {
	# Downloaded to a file rather than piped, so a failed download is a failed step and the file can
	# be checked before anything runs it.
	url="$PIXI_RELEASES/v$PIXI_VERSION/pixi-$TARGET.tar.gz"
	if ! fetch "$url" >"$WORK/pixi.tar.gz" || [ ! -s "$WORK/pixi.tar.gz" ]; then
		err "could not download Pixi from $url"
		return 1
	fi
}

# The SHA-256 written above for one archive, or nothing when this script names none for it.
pinned_sha256() {
	for pair in $PIXI_SHA256; do
		case "$pair" in
		"$1="*) printf '%s\n' "${pair#*=}" ;;
		esac
	done
}

verify_pixi() {
	expected="$(pinned_sha256 "$TARGET")"
	found="$(sha256_of "$WORK/pixi.tar.gz")" || return 1
	log "pixi sha256 expected $expected found $found"
	if [ -z "$expected" ] || [ "$expected" != "$found" ]; then
		err "the Pixi download does not match the SHA-256 this script pins for it, so it was not installed"
		err "  expected: $expected"
		err "  found:    $found"
		return 1
	fi
}

unpack_pixi() {
	# Unpacked inside this run's own directory and then moved, so nothing outside it can change the
	# file between the check and the move. tar run by root keeps the owner the archive records, which
	# is the account that built it, so the file is given to the user who runs this script instead.
	tar --no-same-owner -xzf "$WORK/pixi.tar.gz" -C "$WORK" pixi &&
		chmod +x "$WORK/pixi" &&
		mkdir -p "$PIXI_HOME/bin" &&
		mv "$WORK/pixi" "$PIXI_HOME/bin/pixi"
}

# Whether version $1 is older than version $2, both dotted numbers.
older_than() {
	awk -v found="$1" -v wanted="$2" 'BEGIN {
		n = split(found, f, "."); m = split(wanted, w, ".")
		for (i = 1; i <= (n > m ? n : m); i++) {
			if (f[i] + 0 < w[i] + 0) exit 0
			if (f[i] + 0 > w[i] + 0) exit 1
		}
		exit 1
	}'
}

ensure_pixi() {
	# Pixi does the hard part, including fetching a Python and Node for DeckTalk alone.
	if have pixi; then
		PIXI="$(command -v pixi)"
	elif [ -x "$PIXI_HOME/bin/pixi" ]; then
		PIXI="$PIXI_HOME/bin/pixi"
	fi
	if [ -n "$PIXI" ]; then
		found="$("$PIXI" --version 2>/dev/null | awk '{print $2}')"
		log "pixi already present: ${found:-no version} at $PIXI"
		# The Pixi DeckTalk's CI runs is the oldest this script vouches for, so an older one is
		# refused rather than used. `pixi self-update` works only for a Pixi from pixi.sh, and a Pixi
		# from conda-forge or Homebrew refuses it, so the refusal names both ways to move it.
		if [ -z "$found" ] || older_than "$found" "$PIXI_VERSION"; then
			err "the Pixi at $PIXI is ${found:-a version that prints no version}, and DeckTalk needs Pixi $PIXI_VERSION or later."
			err "Update it with the package manager that installed it, or install the current Pixi from https://pixi.sh."
			return 1
		fi
		say "Pixi $found is already installed."
		return 0
	fi
	PIXI="$PIXI_HOME/bin/pixi"
	if [ "$DRY_RUN" = 1 ]; then
		say "would download Pixi $PIXI_VERSION for $TARGET, check its SHA-256 and unpack it into $PIXI_HOME/bin"
		return 0
	fi
	if ! have curl && ! have wget; then
		err "needs curl or wget to download Pixi. Install Pixi yourself (https://pixi.prefix.dev)"
		err "and run: $INSTALL_LINE"
		return 1
	fi
	step "Downloading Pixi $PIXI_VERSION" download_pixi
	step "Checking Pixi" verify_pixi
	step "Installing Pixi" unpack_pixi
}

install_decktalk() {
	spec="decktalk"
	[ -n "$DECKTALK_VERSION" ] && spec="decktalk==$DECKTALK_VERSION"
	# `pixi global install` leaves an installed package where it is, and writes the spec it was
	# given into Pixi's global manifest. So a run with a version moves to that version, and a run
	# without one first sets the spec back to any version and then updates to the newest.
	update=0
	[ -z "$DECKTALK_VERSION" ] && [ -d "$PIXI_HOME/envs/decktalk" ] && update=1
	if [ "$DRY_RUN" = 1 ]; then
		say "would run: pixi global install -c $CHANNEL -c conda-forge $spec"
		[ "$update" = 0 ] || say "would run: pixi global update decktalk"
		return 0
	fi
	# Streamed, not spun: Pixi prints each package it fetches, and this is the one step long enough
	# for a real line to be worth more than a spinner.
	step_stream "Installing $spec" "$PIXI" global install -c "$CHANNEL" -c conda-forge "$spec"
	[ "$update" = 0 ] || step_stream "Updating decktalk" "$PIXI" global update decktalk
}

find_decktalk() {
	# "Installed" was a claim about the command that had just run, not about the command the reader
	# is about to type. Pixi puts the command in $PIXI_HOME/bin, which this shell may not have, and
	# that copy is read first, because an older decktalk elsewhere on PATH is not the one installed.
	if [ -x "$PIXI_HOME/bin/decktalk" ]; then
		DECKTALK="$PIXI_HOME/bin/decktalk"
	elif have decktalk; then
		DECKTALK=decktalk
	else
		err "decktalk was installed but will not run. Try: $INSTALL_LINE"
		return 1
	fi
	# The version is read back from the command itself, and a command that cannot say its version
	# is a command that does not work, whatever the install step reported.
	if ! INSTALLED="$("$DECKTALK" --version 2>"$WORK/version.err")"; then
		err "decktalk was installed but \`$DECKTALK --version\` failed:"
		sed 's/^/    /' "$WORK/version.err" >&2
		return 1
	fi
	log "verified: $INSTALLED at $DECKTALK"
}

report() {
	say ""
	if [ "$DRY_RUN" = 1 ]; then
		say "Dry run: nothing was installed."
		return 0
	fi
	printf '%s%s is installed.%s Next:\n' "$BOLD" "$INSTALLED" "$RESET"
	say ""
	number=1
	case ":$ORIGINAL_PATH:" in
	*":$PIXI_HOME/bin:"*) ;;
	*)
		# Printed, never written: an installer that edits a startup file is one that does more than
		# it says. The reader's own $PATH is meant to stay a word in the line they copy, so it is not
		# expanded.
		# shellcheck disable=SC2016
		printf '    %s1.%s %sexport PATH="%s/bin:$PATH"%s   %s# and put this line in ~/.zshrc or ~/.bashrc for every new shell%s\n' "$DIM" "$RESET" "$GOLD" "$PIXI_HOME" "$RESET" "$DIM" "$RESET"
		number=2
		;;
	esac
	printf '    %s%s.%s %sdecktalk install%s   %s# Linux, or to fetch Chromium and ffmpeg now. On Linux it asks for sudo.%s\n' "$DIM" "$number" "$RESET" "$GOLD" "$RESET" "$DIM" "$RESET"
	printf '    %s%s.%s %sdecktalk init my-lesson%s\n' "$DIM" "$((number + 1))" "$RESET" "$GOLD" "$RESET"
	printf '    %s%s.%s %scd my-lesson && decktalk build --no-spend%s\n' "$DIM" "$((number + 2))" "$RESET" "$GOLD" "$RESET"
	say ""
	note "That first build needs no account and spends nothing. For your own voice, put an"
	note "ElevenLabs key in .env, set [voices.narrator] id in decktalk.toml, and run: decktalk build"
	say ""
	# One link, and the one that answers the next question. Installers that end in a paragraph get
	# skimmed; rustup, uv, bun and deno all end in commands and at most one URL.
	printf '%sDocs%s  https://docs.decktalk.ai/quickstart\n' "$DIM" "$RESET"
	# A pinned install writes its pin into Pixi's manifest, where `pixi global update` keeps it, so
	# the one update that works in both cases is this script run again.
	if [ -n "$DECKTALK_VERSION" ]; then
		note "This install is pinned to $DECKTALK_VERSION. To move the pin, run the installer again with another"
		note "DECKTALK_VERSION, or without one for the newest release."
	else
		note "To update DeckTalk, run the installer again."
	fi
}

main() {
	DRY_RUN=0
	DECKTALK=decktalk
	INSTALLED="DeckTalk"
	# The advice at the end is about the author's NEXT shell, so it reads the PATH they started with.
	ORIGINAL_PATH="$PATH"
	# Exported, so every `pixi global` this script runs reads the same home it reports.
	PIXI_HOME="${PIXI_HOME:-$HOME/.pixi}"
	export PIXI_HOME
	for arg in "$@"; do
		case "$arg" in
		--dry-run) DRY_RUN=1 ;;
		--verbose | -v) VERBOSE=1 ;;
		--keep-log) KEEP_LOG=1 ;;
		--no-color | --no-colour) NO_COLOR=1 ;;
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

	setup_style
	# One directory of this run's own for every file it writes but the log, made by mktemp, so no
	# other user of a shared /tmp can read, plant or replace any of them.
	if ! WORK="$(mktemp -d "${TMPDIR:-/tmp}/decktalk-install.XXXXXX")"; then
		err "could not make a private directory under ${TMPDIR:-/tmp}"
		return 1
	fi
	STEP_LOG="$WORK/step.log"
	STEP_RC="$WORK/step.rc"
	log_open "$@"
	# The trap is what puts the cursor back and keeps the log after a Ctrl-C in mid-spinner.
	trap 'on_error 1' EXIT
	trap 'say ""; err "interrupted"; on_error 130' INT
	trap 'on_error 143' TERM

	banner
	check_version
	check_platform
	ensure_pixi
	install_decktalk
	[ "$DRY_RUN" = 1 ] || find_decktalk
	report

	log "install.sh finished"
	# Reaching here means nothing failed, so the EXIT trap must not report one.
	trap - EXIT
	cleanup
}

main "$@"
