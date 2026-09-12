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
# post-production checks on the final file: picture and sound both start at 0, every section
# boundary is a real frame, chapters mark the section starts, and captions were written
uv run python - "$T" <<'EOF'
import json
import subprocess
import sys
from pathlib import Path

from decktalk.media.ffmpeg import ffprobe, probe_duration

out = Path(sys.argv[1]) / "build" / "out"
final = out / "smoke.mp4"


def probe(*args: str) -> dict:
    cmd = [ffprobe(), "-v", "error", "-of", "json", *args, str(final)]
    return json.loads(subprocess.run(cmd, capture_output=True, text=True, check=True).stdout)


streams = probe("-show_entries", "stream=codec_type,start_time")["streams"]
starts = {s["codec_type"]: float(s["start_time"]) for s in streams if s["codec_type"] in ("video", "audio")}
assert starts == {"video": 0.0, "audio": 0.0}, f"streams do not start together: {starts}"
sections = sorted(out.glob("[0-9][0-9]-section.mp4"))
chapters = probe("-show_chapters")["chapters"]
assert len(chapters) == len(sections) == 4, (len(chapters), len(sections))
frames = probe("-select_streams", "v", "-show_entries", "frame=pts_time")["frames"]
pts = {round(float(f["pts_time"]), 3) for f in frames}
t = 0.0
for chapter, section in zip(chapters, sections, strict=True):
    assert abs(float(chapter["start_time"]) - t) < 1e-6, f"chapter {chapter['id']} at {chapter['start_time']}, want {t}"
    assert round(t, 3) in pts, f"no frame starts at {t}"
    t += probe_duration(section)
assert abs(t - probe_duration(final)) < 0.05, (t, probe_duration(final))
for name in ("smoke.srt", "smoke.vtt", "smoke.chapters.txt"):
    assert (out / name).stat().st_size > 0, name
print(f"post-production ok: video/audio start at 0, {len(chapters)} chapters on frame-exact section starts, captions written")
EOF
echo "smoke test OK: $T/build/out/smoke.mp4"
