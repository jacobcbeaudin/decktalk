#!/usr/bin/env bash
# The cue timing gate, with no API key. It builds the small deck in tests/timing silently and fails on any
# verify finding, OFF CUE included. Its pages are still and its reveals snap in, so a hosted runner draws
# little while a cue lands. The scaffold's smoke test reports its OFF CUE rows without failing.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
T=tests/out/timing
rm -rf "$T"; mkdir -p tests/out
cp -R tests/timing "$T"
uv run decktalk -p "$T" runtime
DECKTALK_VIDEO_PRESET=veryfast uv run decktalk -p "$T" build --silent
uv run decktalk -p "$T" check --strict
# One verify run: the JSON is kept for the CI upload, and --strict also fails on THIN CHANGE?.
uv run decktalk -p "$T" verify --json --strict --no-fail > "$T/verify.json"
uv run python - "$T/verify.json" <<'EOF'
import json
import sys

doc = json.load(open(sys.argv[1], encoding="utf-8"))
v = doc["verify"]
print(f"{'check':<14} {'cue':>6} {'offset':>8} {'a/v':>7}  result")
for c in v["cues"]:
    offset = "-" if c["offset_ms"] is None else f"{c['offset_ms']:+d}ms"
    av = "-" if c["av_ms"] is None else f"{c['av_ms']:+d}ms"
    print(f"{c['section']}:{c['cue']:<12} {c['cue_seconds']:6.2f} {offset:>8} {av:>7}  {c['verdict']}")
offsets = [abs(c["offset_ms"]) for c in v["cues"] if c["offset_ms"] is not None]
print(f"largest offset {max(offsets, default=0)} ms over {len(offsets)} cues")
bad = [r for k in ("starts", "cuts", "cues") for r in v[k] if r["verdict"] not in ("ok", "quiet", "changed")]
assert len(v["cues"]) == 7, f"the timing deck has 7 cues, verify checked {len(v['cues'])}"
assert not bad, bad
assert doc["ok"], doc["findings"]
EOF
echo "timing test OK: $T/build/out/timing.mp4"
