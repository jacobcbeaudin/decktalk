#!/usr/bin/env bash
# End-to-end smoke test with no API key: scaffold a project, render placeholder narration,
# record the template deck, assemble with a synthetic clip and underscore, verify every cue.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
T=tests/out/smoke
rm -rf "$T"; mkdir -p tests/out
uv run decktalk init "$T" --name smoke
FF="$(uv run python -c 'from decktalk.media.ffmpeg import ffmpeg; print(ffmpeg().replace(chr(92), chr(47)))')"
# a synthetic "on camera" clip: 3 s, 1280x720 (exercises scale/pad), a 440 Hz tone as the voice
"$FF" -hide_banner -loglevel error -y -f lavfi -i "testsrc2=s=1280x720:r=30" -f lavfi -i "sine=f=440:r=48000" -t 3 -c:v libx264 -pix_fmt yuv420p -c:a aac "$T/media/open.mp4"
mkdir -p "$T/build/music"
"$FF" -hide_banner -loglevel error -y -f lavfi -i "sine=f=220:r=44100" -t 8 -af volume=0.5 -c:a libmp3lame "$T/build/music/underscore.mp3"
DECKTALK_VIDEO_PRESET=veryfast uv run decktalk -p "$T" build --silent
uv run decktalk -p "$T" shots
uv run decktalk -p "$T" shots --section 2 --at 1 --at 6
uv run decktalk -p "$T" verify 1:1b 1:1c 2:2.1a 2:2.1b 2:2.1c 2:2.1d 2:2.2 3:3.1draw 3:3.1eq
uv run decktalk -p "$T" status
uv run python -c "import decktalk; p = decktalk.Project.load('$T'); print('python api ok:', p.name, len(p.sections), 'sections')"
echo "smoke test OK: $T/build/out/smoke.mp4"
