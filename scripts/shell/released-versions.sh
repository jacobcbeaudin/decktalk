# The final releases of DeckTalk on its conda channel, read from the channel's own index, which is
# what `pixi global install` reads. scripts/check.py puts this text ahead of each scenario that
# installs from the channel. A scenario installs curl and jq, then calls `needs_releases N`, which
# stops the scenario with a pass and the reason when the channel holds fewer than N releases: the
# channel is empty until the first final release is published, and a scenario that failed on an
# empty channel would keep `main` red and so stop that first release from ever being cut.
CHANNEL="https://prefix.dev/jacobcbeaudin/jcbcodes"

# Every version of decktalk the channel's noarch index lists, oldest first. A channel nothing has
# been uploaded to has no index and answers 404, which is an empty list. Any other answer fails.
released_versions() {
  code="$(curl -sS -o /tmp/repodata.json -w '%{http_code}' "$CHANNEL/noarch/repodata.json")"
  case "$code" in
  200) jq -r '[(.packages // {})[], (."packages.conda" // {})[]] | map(select(.name == "decktalk") | .version) | unique | .[]' /tmp/repodata.json | sort -V ;;
  404) ;;
  *)
    echo "$CHANNEL answered HTTP $code for its index" >&2
    return 1
    ;;
  esac
}

needs_releases() {
  RELEASED="$(released_versions)"
  count="$(printf '%s\n' "$RELEASED" | grep -c . || true)"
  if [ "$count" -lt "$1" ]; then
    echo "skipped: $CHANNEL holds $count releases of decktalk and this scenario needs $1."
    echo "release.yml publishes each final release there, so the scenario runs once enough exist."
    exit 0
  fi
}
