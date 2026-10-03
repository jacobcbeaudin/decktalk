"""The environment a launched browser is given, which carries the machine's paths and locale and no secret."""

from __future__ import annotations

import tempfile

from decktalk.media.environment import CHILD_KEYS, child_environment, children_see


def test_a_browser_sees_the_machines_paths_and_locale_and_nothing_else():
    machine = {
        "HOME": "/home/author",
        "LANG": "fr_FR.UTF-8",
        "TZ": "Europe/Paris",
        "TMPDIR": "/scratch",
        "ELEVENLABS_API_KEY": "sk-secret",
        "AWS_SECRET_ACCESS_KEY": "also-secret",
        "DECKTALK_ELEVENLABS_BASE_URL": "http://127.0.0.1:9/v1",
    }
    with children_see(machine):
        seen = child_environment()
    assert seen == {"HOME": "/home/author", "LANG": "fr_FR.UTF-8", "TZ": "Europe/Paris", "TMPDIR": "/scratch"}


def test_a_trusted_page_reaches_the_network_through_the_machines_own_proxy():
    """Chromium on Linux reads its proxy from the environment, so scrubbing it cut a trusted deck off."""
    with children_see({"https_proxy": "http://proxy.corp:3128", "NO_PROXY": "localhost"}):
        assert child_environment()["https_proxy"] == "http://proxy.corp:3128"


def test_a_name_windows_spells_its_own_way_is_still_carried():
    with children_see({"SystemRoot": r"C:\Windows", "Path": r"C:\Windows\system32"}):
        assert child_environment()["SystemRoot"] == r"C:\Windows"


def test_a_launch_outside_any_run_gets_a_temporary_directory_and_nothing_of_the_process():
    seen = child_environment()
    assert set(seen) == {"TMPDIR", "TMP", "TEMP"}
    assert set(seen.values()) == {tempfile.gettempdir()}


def test_no_name_a_browser_may_see_is_one_a_credential_is_kept_under():
    assert not [key for key in CHILD_KEYS if any(word in key for word in ("KEY", "TOKEN", "SECRET", "PASS"))]
