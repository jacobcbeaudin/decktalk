# Third-party notices

DeckTalk is licensed under Apache-2.0 (see LICENSE). It depends on, downloads, or embeds
the following third-party software, each under its own license.

| Component | How DeckTalk uses it | License |
|---|---|---|
| [Playwright](https://playwright.dev/python/) | A Python dependency. It drives headless Chromium to record pages and take screenshots. | Apache-2.0 |
| Chromium (headless shell) | Downloaded by `decktalk setup` through Playwright. | BSD-3-Clause and the licenses of its components |
| [static-ffmpeg](https://github.com/zackees/static_ffmpeg) | A Python dependency. It fetches ffmpeg and ffprobe binaries on first use when none are on PATH. | BSD-3-Clause |
| [ffmpeg](https://ffmpeg.org/) | Run as a separate process to cut, mix, and analyze audio and video. The builds that static-ffmpeg fetches include libx264 and are therefore GPL builds. DeckTalk does not link against them. | GPL-2.0-or-later (those builds); LGPL-2.1-or-later (ffmpeg itself) |
| [Inter Tight](https://rsms.me/inter/) | A subset of the variable font is embedded in the README graphics under `assets/`. | SIL Open Font License 1.1 |
| [KaTeX](https://katex.org/) | The scaffold deck loads it from a CDN to typeset equations. It is not bundled. | MIT |
| [ElevenLabs](https://elevenlabs.io/) | A web API for speech, sound effects, and music, called with your own key under your own account terms. | ElevenLabs terms of service |
