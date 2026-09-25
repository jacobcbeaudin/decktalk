# Security

This file says how DeckTalk handles your ElevenLabs API key, how to check that a release is the one CI built, and how to report a vulnerability.

## How DeckTalk handles your API key

DeckTalk reads the ElevenLabs API key from the environment or from `.env` in the project directory.

- DeckTalk never prints the key.
- DeckTalk never writes the key under `build/`.
- DeckTalk sends the key only to the ElevenLabs API base in the configuration (`api_base`).
- Error messages show `<voice id>` in place of the voice id.

## Verify a release

Every release is published from CI by [trusted publishing](https://docs.pypi.org/trusted-publishers/), and every file on PyPI carries a [provenance attestation](https://docs.pypi.org/attestations/). The attestation names the repository and the workflow that built the file, and Sigstore signs it. This command downloads one file and its attestation from PyPI and checks both against this repository.

```console
uvx pypi-attestations verify pypi --repository https://github.com/jacobcbeaudin/decktalk pypi:decktalk-0.5.0rc1-py3-none-any.whl
```

It prints `OK:` and the file name when the file was built from this repository by `release.yml`. It exits 1 when the file was built anywhere else, or when the file does not match its attestation. Put any file name from the release's page on PyPI after `pypi:`, the wheel or the source archive.

## Report a vulnerability

Report a vulnerability privately through [GitHub's private vulnerability reporting](https://github.com/jacobcbeaudin/decktalk/security/advisories/new). Do not open a public issue. Expect a reply within a week.
