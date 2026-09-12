#!/usr/bin/env bash
# End-to-end smoke test with no API key: scaffold a project, render placeholder narration,
# record the template deck, assemble with a synthetic clip and underscore, verify.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
T=tests/out/smoke
rm -rf "$T"; mkdir -p tests/out
uv run decktalk init "$T" --name smoke
# a synthetic "on camera" clip: 3 s, 1280x720 (exercises scale/pad), a 440 Hz tone as the voice
FF="$(uv run python -c 'from decktalk.tools import ffmpeg; print(ffmpeg())')"
"$FF" -hide_banner -loglevel error -y -f lavfi -i "testsrc2=s=1280x720:r=30" -f lavfi -i "sine=f=440:r=48000" -t 3 -c:v libx264 -pix_fmt yuv420p -c:a aac "$T/media/open.mp4"
mkdir -p "$T/build/music"
"$FF" -hide_banner -loglevel error -y -f lavfi -i "sine=f=220:r=44100" -t 8 -af volume=0.5 -c:a libmp3lame "$T/build/music/underscore.mp3"
uv run decktalk -p "$T" build --silent --preset veryfast
uv run decktalk -p "$T" shots
uv run decktalk -p "$T" shots --section 2 --at 1 --at 6
uv run decktalk -p "$T" verify 2:2.2 3:3.1draw
uv run decktalk -p "$T" status
echo "smoke test OK: $T/build/out/smoke.mp4"
