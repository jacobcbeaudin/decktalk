# Security

This file says how DeckTalk handles your ElevenLabs API key, how to check that a release is the one CI built, and how to report a vulnerability.

## How DeckTalk handles your API key

DeckTalk reads the ElevenLabs API key from the environment or from `.env` in the project directory.

- DeckTalk never prints the key.
- DeckTalk never writes the key under `build/`.
- DeckTalk sends the key only to the ElevenLabs base URL this machine names (`[elevenlabs] base_url`), which no
  project file can set.
- The voice id is a published name rather than a secret, so it is committed in `decktalk.toml` and
  may appear in a message.

## A project's programs can read your key

A project can name programs: a section's `render_command` and a `[[clip]]` table's `command`. A
program runs with your user's rights and can read every file you can, the `.env` that holds your key
included. DeckTalk refuses a project's programs by default: `record` and `build` stop before any of
them starts, and `decktalk check` lists each one. A machine allows them with `[record] programs`
(`ask` or `run`), which no project file can set, and `record.page_policy = "untrusted"` refuses them
whatever it says. A program gets the scrubbed environment, with no key and no proxy variable, and runs
in an empty scratch folder, but DeckTalk hides no file from it, so allow a project's programs only
after you have read them.

## Verify a release

Every release is published from CI by [trusted publishing](https://docs.pypi.org/trusted-publishers/), and every file on PyPI carries a [provenance attestation](https://docs.pypi.org/attestations/). The attestation names the repository and the workflow that built the file, and Sigstore signs it. This command downloads one file and its attestation from PyPI and checks both against this repository.

```console
uvx pypi-attestations verify pypi --repository https://github.com/jacobcbeaudin/decktalk pypi:decktalk-0.5.0-py3-none-any.whl
```

It prints `OK:` and the file name when the file was built from this repository by `release.yml`. It exits 1 when the file was built anywhere else, or when the file does not match its attestation. Put any file name from the release's page on PyPI after `pypi:`, the wheel or the source archive.

## Supported versions

Only the latest release receives security fixes while DeckTalk is alpha. A fix ships in the next
release, and its advisory names that version.

## Report a vulnerability

Report a vulnerability privately through [GitHub's private vulnerability reporting](https://github.com/jacobcbeaudin/decktalk/security/advisories/new). Do not open a public issue. Expect a reply within a week.
