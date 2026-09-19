from decktalk.report import mmss


def test_mmss_rounds_before_splitting_minutes():
    assert mmss(179.6) == "3:00"
    assert mmss(59.5) == "1:00"
    assert mmss(197.96) == "3:18"
    assert mmss(0.0) == "0:00"
    assert mmss(None) == "  --  "
