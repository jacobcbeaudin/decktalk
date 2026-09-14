# Security

This file says how DeckTalk handles your ElevenLabs API key, and how to report a vulnerability.

## How DeckTalk handles your API key

DeckTalk reads the ElevenLabs API key from the environment or from `.env` in the project directory.

- DeckTalk never prints the key.
- DeckTalk never writes the key under `build/`.
- DeckTalk sends the key only to the ElevenLabs API base in the configuration (`api_base`).
- Error messages show `<voice id>` in place of the voice id.

## Report a vulnerability

Report a vulnerability privately through [GitHub's private vulnerability reporting](https://github.com/jacobcbeaudin/decktalk/security/advisories/new). Do not open a public issue. Expect a reply within a week.
