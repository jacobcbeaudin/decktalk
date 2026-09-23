#!/bin/sh
# DeckTalk installer. Read it before you run it; that is why it is served from a URL.
#
#   curl -LsSf https://decktalk.ai/install.sh | sh
#
# It installs uv if you do not have it, then `uv tool install decktalk`, then offers to run
# `decktalk install`, which fetches Chromium and ffmpeg.
#
# This script never calls sudo itself. `decktalk install` can: on Linux it runs
# `playwright install chromium --with-deps`, and Playwright uses sudo to add Chromium's system
# libraries. So it is never run without asking. On a terminal you get a y/N question that says the
# size and the sudo before anything happens, read from /dev/tty rather than stdin, because stdin is
# the pipe carrying this script. Without a terminal there is nobody to ask, so it is skipped and
# the one command to run later is printed. --browser and --no-browser decide it up front.
#
# Every step's output is kept in a log, whether or not it is shown. On failure the log's path is
# printed and the file is left behind; on success it is removed unless you asked to keep it.
#
# DECKTALK_VERSION=0.4.1 pins the version. Options go after `-s --`, for example
# `| sh -s -- --dry-run`; run with --help to see them.
#
# Everything is inside main(), which is called on the last line, so a download cut off part way
# through runs nothing at all instead of half an install.

set -eu

DECKTALK_VERSION="${DECKTALK_VERSION:-}"
UV_INSTALLER="https://astral.sh/uv/install.sh"

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
BROWSER=ask
SPIN_PID=''
LABEL=''
STEP_LOG=''
STEP_RC=''
TRANSCRIPT=''
TMP_UV=''
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
mark_skip() { if [ "$UNICODE" = 1 ]; then printf '%s' "·"; else printf '%s' "--"; fi; }

# ---- the log ------------------------------------------------------------------------------------
# One file for the whole run, written whether or not anything is shown, so a failure three steps
# back is still readable and so a bug report can carry one path instead of a scrollback.

now() { date '+%Y-%m-%dT%H:%M:%S' 2>/dev/null || echo "?"; }
epoch() { date +%s 2>/dev/null || echo 0; }

log() { [ -z "$TRANSCRIPT" ] || printf '%s %s\n' "$(now)" "$*" >>"$TRANSCRIPT"; }

log_open() {
	TRANSCRIPT="${DECKTALK_INSTALL_LOG:-${TMPDIR:-/tmp}/decktalk-install.$$.log}"
	: >"$TRANSCRIPT" 2>/dev/null || TRANSCRIPT=''
	log "install.sh starting"
	log "  uname: $(uname -a 2>/dev/null || echo '?')"
	log "  shell: ${0##*/}  tty=$TTY  unicode=$UNICODE  fancy=$FANCY"
	log "  args: $*"
	log "  DECKTALK_VERSION=${DECKTALK_VERSION:-<latest>}  browser=$BROWSER"
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
skip_line() { printf '  %s%s %s%s\n' "$DIM" "$(mark_skip)" "$1" "$RESET"; }

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
# For work that already reports real progress: uv prints the size of each wheel and a live bar, and
# a spinner over the top of that would replace a true number with a decorative one. So the child
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
	status=0
	{
		"$@" 2>&1
		echo $? >"$STEP_RC"
	} | {
		if [ -n "$TRANSCRIPT" ]; then
			tee -a "$TRANSCRIPT"
		else
			cat
		fi
	}
	status=$(cat "$STEP_RC" 2>/dev/null || echo 1)
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
	[ -z "$STEP_LOG" ] || rm -f "$STEP_LOG"
	[ -z "$STEP_RC" ] || rm -f "$STEP_RC"
	[ -z "$TMP_UV" ] || rm -f "$TMP_UV"
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
    --browser, -y  Run `decktalk install` without asking (Chromium and ffmpeg).
                   The only prompt in this script, so -y answers all of it.
    --no-browser   Skip it. `decktalk install` does the same thing later.
    --verbose      Show the output of every step, not just failing ones.
    --keep-log     Keep the log file even when everything works.
    --no-color     No colour and no spinner, even on a terminal.
    -h, --help     Print this.

Environment:
    DECKTALK_VERSION     Install this version instead of the latest.
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

download_uv_installer() {
	# Downloaded to a file rather than piped: a pipeline reports the last command's status, so
	# `fetch … | sh` would report sh succeeding on empty input and hide a failed download.
	TMP_UV="${TMPDIR:-/tmp}/decktalk-uv-install.$$.sh"
	if ! fetch "$UV_INSTALLER" >"$TMP_UV" || [ ! -s "$TMP_UV" ]; then
		err "could not download uv's installer from $UV_INSTALLER"
		return 1
	fi
}

ensure_uv() {
	# uv does the hard part, including fetching a Python when the machine has none. Installing it
	# here is the same trust the next line already assumes.
	if have uv; then
		say "uv $(uv --version 2>/dev/null | awk '{print $2}') is already installed."
		log "uv already present: $(uv --version 2>/dev/null || echo '?')"
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
	step "Downloading uv" download_uv_installer
	step "Installing uv" sh "$TMP_UV"
	rm -f "$TMP_UV"
	TMP_UV=''
	# uv's installer puts it in ~/.local/bin, which this shell may not have on PATH yet.
	if ! have uv; then
		PATH="$HOME/.local/bin:$PATH"
		export PATH
		log "added ~/.local/bin to PATH for this shell"
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
	# Streamed, not spun: uv prints the real size of each wheel and a live bar, and this is the one
	# step long enough for a number to be worth more than a spinner.
	step_stream "Installing $spec" uv tool install "$spec"
}

find_decktalk() {
	# "Installed" was a claim about the command that had just run, not about the command the reader
	# is about to type. uv puts the shim in ~/.local/bin, which this shell may still not have.
	if have decktalk; then
		DECKTALK=decktalk
	elif [ -x "$HOME/.local/bin/decktalk" ]; then
		DECKTALK="$HOME/.local/bin/decktalk"
	else
		err "decktalk was installed but will not run. Try: uv tool install --force decktalk"
		return 1
	fi
	INSTALLED="$("$DECKTALK" --version 2>/dev/null || echo decktalk)"
	log "verified: $INSTALLED at $DECKTALK"
}

ask_browser() {
	# stdin is the pipe carrying this script, so a prompt has to read the terminal directly. If
	# /dev/tty will not open there is nobody to ask, and the answer is no.
	[ "$TTY" = 1 ] || return 1
	[ -r /dev/tty ] || return 1
	say ""
	note "Chromium and ffmpeg are what record and assemble the film. A few hundred megabytes,"
	note "once per machine."
	case "$(uname -s)" in
	Linux) note "On Linux this asks for sudo, for Chromium's system libraries." ;;
	esac
	printf 'Fetch them now? %s[Y/n]%s ' "$DIM" "$RESET"
	# A bare Enter means yes, so the fast path is one keystroke. EOF is not a bare Enter: it means
	# the terminal handed us nothing and there is nobody to ask, which has to read as no rather
	# than as the default, or a closed stdin silently authorises a sudo.
	if read -r answer </dev/tty; then
		log "browser prompt answered: '$answer'"
		case "$answer" in
		"" | [Yy] | [Yy][Ee][Ss]) return 0 ;;
		*) return 1 ;;
		esac
	fi
	log "browser prompt: EOF, treating as no"
	say ""
	return 1
}

install_browser() {
	if [ "$DRY_RUN" = 1 ]; then
		say "would run: decktalk install"
		return 0
	fi
	case "$BROWSER" in
	no)
		BROWSER_DONE=0
		skip_line "Chromium and ffmpeg skipped."
		return 0
		;;
	yes) ;;
	*)
		if ! ask_browser; then
			BROWSER_DONE=0
			[ "$TTY" = 1 ] && say "" || true
			skip_line "Chromium and ffmpeg skipped."
			return 0
		fi
		;;
	esac
	say ""
	# Streamed and never behind a spinner: Playwright prints its own download progress, and on
	# Linux sudo needs to put a password prompt on the screen where a person can see it.
	step_stream "Chromium and ffmpeg" "$DECKTALK" install
	BROWSER_DONE=1
}

report() {
	say ""
	if [ "$DRY_RUN" = 1 ]; then
		say "Dry run: nothing was installed."
		return 0
	fi
	printf '%s%s is installed.%s Next:\n' "$BOLD" "$INSTALLED" "$RESET"
	say ""
	if [ "$BROWSER_DONE" != 1 ]; then
		printf '    %sdecktalk install%s      %s# Chromium and ffmpeg, once per machine.%s\n' "$GOLD" "$RESET" "$DIM" "$RESET"
	fi
	printf '    %sdecktalk init my-film%s\n' "$GOLD" "$RESET"
	printf '    %scd my-film && decktalk build --no-voice%s\n' "$GOLD" "$RESET"
	say ""
	note "That first build needs no account and spends nothing. For your own voice, put an"
	note "ElevenLabs key and voice id in .env and run: decktalk build"
	case ":$ORIGINAL_PATH:" in
	*":$HOME/.local/bin:"*) ;;
	*)
		say ""
		printf '%sAdd ~/.local/bin to your PATH to run decktalk from a new shell.%s\n' "$GOLD" "$RESET"
		;;
	esac
}

main() {
	DRY_RUN=0
	DECKTALK=decktalk
	INSTALLED="DeckTalk"
	BROWSER_DONE=0
	# Kept because ensure_uv may add ~/.local/bin to this shell's PATH. The advice at the end is
	# about the author's NEXT shell, so it has to read the PATH they actually have.
	ORIGINAL_PATH="$PATH"
	for arg in "$@"; do
		case "$arg" in
		--dry-run) DRY_RUN=1 ;;
		--browser | --yes | -y) BROWSER=yes ;;
		--no-browser) BROWSER=no ;;
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
	STEP_LOG="${TMPDIR:-/tmp}/decktalk-step.$$.log"
	STEP_RC="${TMPDIR:-/tmp}/decktalk-step.$$.rc"
	log_open "$@"
	# The trap is what puts the cursor back and keeps the log after a Ctrl-C in mid-spinner.
	trap 'on_error 1' EXIT
	trap 'say ""; err "interrupted"; on_error 130' INT
	trap 'on_error 143' TERM

	banner
	check_platform
	ensure_uv
	install_decktalk
	[ "$DRY_RUN" = 1 ] || find_decktalk
	install_browser
	report

	log "install.sh finished"
	# Reaching here means nothing failed, so the EXIT trap must not report one.
	trap - EXIT
	cleanup
}

main "$@"
