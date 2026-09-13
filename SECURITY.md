# Security

DeckTalk reads an ElevenLabs API key from `.env` in the project directory or from the
environment. It never prints the key, never writes it under `build/`, and sends it only to
the ElevenLabs API base named in the configuration. Error messages replace the voice id
with a placeholder.

If you find a vulnerability, please report it privately through
[GitHub's private vulnerability reporting](https://github.com/jacobcbeaudin/decktalk/security/advisories/new)
rather than in a public issue. You can expect an acknowledgement within a week.
