# Third-party notices

DeckTalk is licensed under Apache-2.0 (see LICENSE). This file lists the third-party software that DeckTalk depends on, downloads, or embeds, and the license of each.

| Component | How DeckTalk uses it | License |
|---|---|---|
| [Playwright](https://playwright.dev/python/) | Playwright is a Python dependency. It drives headless Chromium to record pages and take screenshots. | Apache-2.0 |
| Chromium (headless shell) | `decktalk install` downloads it through Playwright. | BSD-3-Clause and the licenses of its components |
| [ffmpeg](https://ffmpeg.org/) source | DeckTalk runs ffmpeg and ffprobe as separate processes to cut, mix, and analyze audio and video. DeckTalk does not link against ffmpeg. | LGPL-2.1-or-later |
| ffmpeg 8.1.2 builds for Linux (x86_64 and arm64) and Windows (x86_64) by [BtbN/FFmpeg-Builds](https://github.com/BtbN/FFmpeg-Builds) | `decktalk install` downloads the build for your platform from the release URL pinned in `src/decktalk/media/ffmpeg.py`, checks it against the SHA-256 recorded there, and keeps it in DeckTalk's cache. It is a GPL build: it includes libx264 and uses `--enable-gpl --enable-version3 --enable-libx264`. The wheel never contains it. | GPL-3.0-or-later |
| ffmpeg 8.1.2 build for Intel macOS by [evermeet.cx](https://evermeet.cx/ffmpeg/) | Downloaded, verified and kept the same way. It is a GPL build: it includes libx264 and uses `--enable-gpl --enable-version3 --enable-libx264`. The wheel never contains it. | GPL-3.0-or-later |
| ffmpeg 8.1.2 build for Apple silicon macOS by [Martin Riedl](https://ffmpeg.martin-riedl.de/) | Downloaded, verified and kept the same way. It is a GPL build: it includes libx264 and uses `--enable-gpl --enable-version3 --enable-libx264`. The wheel never contains it. | GPL-3.0-or-later |
| [Inter Tight](https://rsms.me/inter/) | The scaffold deck bundles the latin and greek woff2 subsets in `deck/fonts/`, with `InterTight-OFL.txt`. A subset is also embedded in the README graphics under `assets/`. | SIL Open Font License 1.1 |
| [Inter](https://rsms.me/inter/) | The scaffold deck bundles the latin and greek woff2 subsets in `deck/fonts/`, with `Inter-OFL.txt`. | SIL Open Font License 1.1 |
| [JetBrains Mono](https://www.jetbrains.com/lp/mono/) | The scaffold deck bundles the latin and greek woff2 subsets in `deck/fonts/`, with `JetBrainsMono-OFL.txt`. | SIL Open Font License 1.1 |
| [KaTeX](https://katex.org/) | KaTeX typesets equations in the scaffold deck. The wheel ships one release (`katex.min.js`, `katex.min.css` and the woff2 fonts) under `decktalk/katex/` with its licence file, and `decktalk init` copies it into `deck/katex/`, so no page loads it from a CDN. | MIT |
| [ElevenLabs](https://elevenlabs.io/) | ElevenLabs is a web API for speech, sound effects, and music. DeckTalk calls it with your own key, under your own account terms. | ElevenLabs terms of service |

Notes:

- The ffmpeg build is downloaded at install, or on the first render that needs it, and is never distributed in the wheel or in any DeckTalk package. DeckTalk uses an ffmpeg on your PATH only when the download cannot run or no build is pinned for your platform. To use a build of your own, set `DECKTALK_FFMPEG` and `DECKTALK_FFPROBE`.
- The wheel includes KaTeX and nothing is downloaded for it.
