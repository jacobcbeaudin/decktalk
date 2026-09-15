#!/usr/bin/env bash
# Upload one cut of the demo film to the private decktalk-media R2 bucket, or check a cut already there.
# A cut's key holds the UTC time of its upload and the first 12 hex digits of its SHA-256, such as
# decktalk-demo-20260914T151110Z-0810246f57b4.mp4. So a key is never reused, and the bytes behind a key
# can be checked against it at any time. The bucket has no public URL. The media Worker in workers/media
# serves a cut only through a signed link, which the landing page asks for. To ship a new cut, upload
# it, then put the key this script prints in the data-media attribute in site/index.html, in the same
# commit as the cut's poster and captions.
#
#   bash scripts/publish_demo.sh [--verbose] FILE.mp4     upload a cut, check it, and print its key
#   bash scripts/publish_demo.sh [--verbose] --check KEY  download a cut and check it against its key
#
# Progress goes to stderr and only the key goes to stdout, so key=$(bash scripts/publish_demo.sh ...)
# works. --verbose, or DEBUG=1, also prints each command as it runs. An upload that is interrupted
# leaves nothing behind, because R2 stores an object only once all of it has arrived.
#
# Log in once with the Cloudflare account that hosts decktalk.app: `npx wrangler login`.
set -Eeuo pipefail

BUCKET="decktalk-media"
MEDIA="https://media.decktalk.app"
SITE="https://decktalk.app"
KEY_RE='^decktalk-demo-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{12}\.mp4$'
USAGE="usage: bash scripts/publish_demo.sh [--verbose] FILE.mp4 | [--verbose] --check KEY"

log() { echo "publish_demo: $*" >&2; }
die() {
  log "error: $*"
  exit 1
}
trap 'log "error: failed at line $LINENO: $BASH_COMMAND"' ERR

verbose="${DEBUG:-}"
if [ "${1:-}" = "--verbose" ]; then
  verbose=1
  shift
fi
mode="upload"
if [ "${1:-}" = "--check" ]; then
  mode="check"
  shift
fi
if [ $# -ne 1 ]; then
  echo "$USAGE" >&2
  exit 2
fi
if [ -n "$verbose" ]; then
  set -x
fi

for tool in curl npx; do
  command -v "$tool" >/dev/null || die "$tool is not installed"
done
if command -v sha256sum >/dev/null; then
  sha256() { sha256sum | cut -d' ' -f1; }
elif command -v shasum >/dev/null; then
  sha256() { shasum -a 256 | cut -d' ' -f1; }
else
  die "neither sha256sum nor shasum is installed"
fi

# The signed link the landing page would get for a key.
signed_link() {
  local body
  body="$(curl -fsS -H "Origin: $SITE" "$MEDIA/sign/$1")" || return 1
  sed -nE 's/.*"url":"([^"]+)".*/\1/p' <<<"$body"
}

# Download a cut through the media Worker, as a visitor would, and check its SHA-256 against its key.
check_cut() {
  local key="$1" link code expected actual
  [[ "$key" =~ $KEY_RE ]] || die "not a cut's key: $key"
  expected="${key##*-}"
  expected="${expected%.mp4}"
  link="$(signed_link "$key")" || die "the media Worker at $MEDIA would not sign $key"
  code="$(curl -sS -o /dev/null -I -w '%{http_code}' "$link")"
  [ "$code" != 404 ] || die "$key is not in the bucket"
  [ "$code" = 200 ] || die "could not check $key: the media Worker answered HTTP $code"
  log "downloading $key through the media Worker"
  actual="$(curl -fsS "$link" | sha256)" || die "could not download $key"
  [ "${actual:0:12}" = "$expected" ] || die "$key does not match its key: its SHA-256 is $actual"
  log "$key matches its key (SHA-256 $actual)"
}

if [ "$mode" = "check" ]; then
  check_cut "$1"
  echo "$1"
  exit 0
fi

command -v file >/dev/null || die "file is not installed"
file="$1"
[ -f "$file" ] || die "no such file: $file"
# The Worker serves every cut as video/mp4, so only an mp4 goes in the bucket. -L reads through a symlink.
mime="$(file --brief --mime-type -L "$file")"
[ "$mime" = "video/mp4" ] || die "not an mp4: $file is $mime"
size="$(wc -c <"$file" | tr -d ' ')"
hash="$(sha256 <"$file")"
log "$file is an mp4 of $size bytes, SHA-256 $hash"

log "checking the Cloudflare login"
npx wrangler whoami 2>&1 | grep -q "You are logged in" || die "not logged in to Cloudflare. Run: npx wrangler login"

key="decktalk-demo-$(date -u +%Y%m%dT%H%M%SZ)-${hash:0:12}.mp4"
# The same file uploaded twice in one second gets the same key, so an existing key is refused.
link="$(signed_link "$key")" || die "the media Worker at $MEDIA would not sign $key"
code="$(curl -sS -o /dev/null -I -w '%{http_code}' "$link")"
case "$code" in
  404) ;;
  200) die "$key already exists. Wait a second and run this again." ;;
  *) die "could not check whether $key exists: the media Worker answered HTTP $code" ;;
esac

log "uploading to $BUCKET/$key"
npx wrangler r2 object put "$BUCKET/$key" --remote --file "$file" --content-type video/mp4 >&2

check_cut "$key"
log "done. Put this key in the data-media attribute in site/index.html:"
echo "$key"
