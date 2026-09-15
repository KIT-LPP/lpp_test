from lpp_collector.reports import ReportAggregator


class FakeReport:
    def __init__(self, nodeid, when, outcome, longreprtext="", duration=0.1):
        self.nodeid = nodeid
        self.when = when
        self.outcome = outcome
        self.longreprtext = longreprtext
        self.longrepr = longreprtext or None
        self.duration = duration

    @property
    def passed(self):
        return self.outcome == "passed"

    @property
    def failed(self):
        return self.outcome == "failed"

    @property
    def skipped(self):
        return self.outcome == "skipped"


def test_a_passing_test_is_one_row_not_three():
    agg = ReportAggregator()
    for when in ("setup", "call", "teardown"):
        agg.add_test_report(FakeReport("t.py::test_a", when, "passed"))

    rows = agg.result()
    assert len(rows) == 1
    assert rows[0]["outcome"] == "passed"
    assert rows[0]["duration"] == pytest_approx(0.3)


def test_a_failure_in_setup_is_not_lost():
    """従来は when == "call" だけを数えていたので 1 件も残らなかった。"""
    agg = ReportAggregator()
    agg.add_test_report(FakeReport("t.py::test_a", "setup", "failed", "fixture exploded"))
    agg.add_test_report(FakeReport("t.py::test_a", "teardown", "passed"))

    rows = agg.result()
    assert len(rows) == 1
    assert rows[0]["outcome"] == "error"
    assert rows[0]["when"] == "setup"
    assert rows[0]["longrepr"] == "fixture exploded"


def test_a_failing_call_wins_over_a_passing_setup():
    agg = ReportAggregator()
    agg.add_test_report(FakeReport("t.py::test_a", "setup", "passed"))
    agg.add_test_report(FakeReport("t.py::test_a", "call", "failed", "assert 1 == 2"))
    agg.add_test_report(FakeReport("t.py::test_a", "teardown", "passed"))

    assert agg.result()[0]["outcome"] == "failed"
    assert agg.result()[0]["longrepr"] == "assert 1 == 2"


def test_a_skipped_test_is_kept_without_a_verdict():
    agg = ReportAggregator()
    agg.add_test_report(FakeReport("t.py::test_a", "setup", "skipped", "skipped by mark"))
    assert agg.result()[0]["outcome"] == "skipped"


def test_a_collection_failure_is_reported_instead_of_an_empty_result():
    agg = ReportAggregator()
    agg.add_collect_report(FakeReport("t.py", "collect", "failed", "SyntaxError"))

    rows = agg.result()
    assert rows == [
        {
            "nodeid": "t.py",
            "outcome": "error",
            "when": "collect",
            "longrepr": "SyntaxError",
        }
    ]


def pytest_approx(value):
    import pytest

    return pytest.approx(value)
