"""The two-scene page the recorder opens and the report a page gives back, shared by the record tests."""

from __future__ import annotations

from decktalk.media.pagereport import PageReport

PAGE = """<!doctype html><html><body>
<div data-scene="1"><template data-slide="1.1"><p data-in="open">one</p></template></div>
<div data-scene="2"><template data-slide="2.1"><p data-in="open">two</p></template></div>
</body></html>
"""


def a_report(**fields: object) -> PageReport:
    """What the page says about one scene, with every field a case names in place of the default."""
    return PageReport.model_validate({"version": "0.5.0", "mode": "cue", "scene": "1", "slide": "1.1", **fields})
