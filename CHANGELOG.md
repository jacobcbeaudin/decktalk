# Changelog

## 0.1.0 (2026-09-12)

First release. DeckTalk turns a markdown script, plain HTML slides, and an ElevenLabs
voice into one mp4 in which every reveal lands on the word that introduces it. It records
the slides in headless Chromium, cuts each section to the narration frame-exactly, mixes an
optional soundscape, normalizes loudness, and verifies the result. Chromium and ffmpeg
arrive through Python packages, and `--silent` renders a full draft without an API key.
