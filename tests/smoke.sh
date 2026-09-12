#!/usr/bin/env bash
# End-to-end smoke test with no API key: scaffold a project, add the optional clip section the
# way the comments in decktalk.toml describe, render placeholder narration, record the template
# deck, assemble with a synthetic clip and underscore, and verify every cue.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
T=tests/out/smoke
rm -rf "$T"; mkdir -p tests/out
uv run decktalk init "$T" --name smoke
# Uncomment the clip section exactly as the template's comment block instructs.
uv run python - "$T" <<'EOF'
import sys
from pathlib import Path

root = Path(sys.argv[1])
toml = root / "decktalk.toml"
clip_lines = ("# [[section]]", "# number = 0", '# title = "On camera"', "# clip = ", "# slate_seconds = ")
lines = [line[2:] if line.startswith(clip_lines) else line for line in toml.read_text().splitlines(keepends=True)]
toml.write_text("".join(lines).replace("dips = []", "dips = [[0, 1]]", 1))
script = root / "script.md"
heading = "## 0. On camera\n\n[Your clip at media/open.mp4 plays here, with its own sound.]\n\n## 1. Open"
script.write_text(script.read_text().replace("## 1. Open", heading, 1))
EOF
FF="$(uv run python -c 'from decktalk.media.ffmpeg import ffmpeg; print(ffmpeg().replace(chr(92), chr(47)))')"
# A synthetic "on camera" clip: 3 s, 1280x720 (exercises scale/pad), a 440 Hz tone as the voice.
"$FF" -hide_banner -loglevel error -y -f lavfi -i "testsrc2=s=1280x720:r=30" -f lavfi -i "sine=f=440:r=48000" -t 3 -c:v libx264 -pix_fmt yuv420p -c:a aac "$T/media/open.mp4"
mkdir -p "$T/build/music"
"$FF" -hide_banner -loglevel error -y -f lavfi -i "sine=f=220:r=44100" -t 8 -af volume=0.5 -c:a libmp3lame "$T/build/music/underscore.mp3"
DECKTALK_VIDEO_PRESET=veryfast uv run decktalk -p "$T" build --silent
uv run decktalk -p "$T" shots
uv run decktalk -p "$T" shots --section 2 --at 1 --at 6
uv run decktalk -p "$T" verify \
  1:1.1b 1:1.1c \
  2:2.1a 2:2.1b 2:2.1c 2:2.1d 2:2.2 2:2.2edit \
  3:3.1draw 3:3.1a 3:3.1b 3:3.1think 3:3.1c \
  4:4.1ask 4:4.1r1 4:4.1r2 4:4.1r3 4:4.2 4:4.2ask 4:4.2reply
uv run decktalk -p "$T" status
uv run python -c "import decktalk; p = decktalk.Project.load('$T'); print('python api ok:', p.name, len(p.sections), 'sections')"
echo "smoke test OK: $T/build/out/smoke.mp4"
