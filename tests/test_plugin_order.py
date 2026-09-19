"""プラグインが結果を出す順序。

学生は結果を見る前に通信の完了を待たされてはならない。一方で、提出の控えは
送った**後**に書かなければならない。試行の id はサーバの応答で決まるので、
先に書くと「まだサーバに届いていません」と言われて提出できなくなる。
"""

import types

import pytest

import lpp_collector
from lpp_collector import student_report


class FakeDevice:
    device_id = "dev-1"
    device_token = None

    def is_bound(self):
        return True


class FakeUploader:
    def __init__(self, device, events):
        self.events = events
        self.attempt_ids = {}
        self.errors = []
        self.enqueued = []

    def start_background_retry(self):
        pass

    def stop_background_retry(self):
        pass

    def enqueue(self, record, tar):
        self.events.append("enqueue")
        self.enqueued.append(record)

    def flush(self, newest_first=False, deadline=None):
        self.events.append("flush")
        for record in self.enqueued:
            self.attempt_ids[record["idempotencyKey"]] = "attempt-1"
        return len(self.enqueued)

    def pending(self, newest_first=False):
        return []


class FakeReporter:
    def __init__(self, events):
        self.events = events
        self.lines = []

    def write_line(self, line, **markup):
        self.events.append("write")
        self.lines.append(line)


@pytest.fixture
def collector(monkeypatch, tmp_path):
    events = []
    monkeypatch.setenv("LPP_TESTSUITE", "03test")
    monkeypatch.setenv("LPP_SESSION_ID", "session-1")
    monkeypatch.setattr(student_report, "LPP_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(lpp_collector, "LppDevice", lambda: FakeDevice())
    monkeypatch.setattr(
        lpp_collector, "Uploader", lambda device: FakeUploader(device, events)
    )
    monkeypatch.setattr(lpp_collector, "snapshot", lambda path: b"")
    monkeypatch.setattr(lpp_collector, "env_labels", lambda: {})

    saved = []

    def fake_save_attempt(state, state_dir=None):
        events.append("save_attempt")
        saved.append(state)

    monkeypatch.setattr(lpp_collector, "save_attempt", fake_save_attempt)

    config = types.SimpleNamespace(
        option=types.SimpleNamespace(tbstyle="long"),
        getoption=lambda name, default=None: False,
        pluginmanager=types.SimpleNamespace(getplugin=lambda name: None),
    )
    collector = lpp_collector.LppCollector(config)
    return collector, events, saved, config


def a_report(nodeid, outcome="passed"):
    return types.SimpleNamespace(
        nodeid=nodeid,
        when="call",
        outcome=outcome,
        passed=outcome == "passed",
        failed=outcome == "failed",
        skipped=outcome == "skipped",
        duration=0.1,
        longreprtext="出力が期待と違います",
        longrepr="出力が期待と違います",
    )


def test_the_result_is_shown_before_the_upload(collector):
    instance, events, _saved, _config = collector
    instance.pytest_runtest_logreport(a_report("t.py::test_a", "failed"))

    instance.pytest_sessionfinish(session=None, exitstatus=1)
    assert "flush" not in events  # ここではまだ送らない

    instance.pytest_terminal_summary(FakeReporter(events), 1, None)
    assert events.index("write") < events.index("flush")


def test_the_receipt_is_written_after_the_upload(collector):
    """控えに試行の id が入るのは、送った後に書くからである。"""
    instance, events, saved, _config = collector
    instance.pytest_runtest_logreport(a_report("t.py::test_a"))

    instance.pytest_sessionfinish(session=None, exitstatus=0)
    instance.pytest_terminal_summary(FakeReporter(events), 0, None)

    assert events.index("flush") < events.index("save_attempt")
    assert saved[0]["attempt_id"] == "attempt-1"
    assert saved[0]["session_id"] == "session-1"


def test_nothing_is_uploaded_twice_when_the_summary_never_runs(collector):
    """--no-summary などでまとめが出なくても、送信と控えは落とさない。"""
    instance, events, saved, _config = collector
    instance.pytest_runtest_logreport(a_report("t.py::test_a"))

    instance.pytest_sessionfinish(session=None, exitstatus=0)
    instance.pytest_unconfigure(None)
    instance.pytest_unconfigure(None)

    assert events.count("flush") == 1
    assert len(saved) == 1


def test_pytest_own_failure_sections_are_silenced_by_default(collector):
    instance, _events, _saved, config = collector
    instance.pytest_sessionfinish(session=None, exitstatus=1)
    assert config.option.tbstyle == "no"


def test_full_keeps_pytest_own_output(collector, monkeypatch):
    instance, _events, _saved, config = collector
    instance.full = True
    instance.pytest_sessionfinish(session=None, exitstatus=1)
    assert config.option.tbstyle == "long"


def test_the_outcome_is_kept_for_the_next_run(collector, tmp_path):
    instance, _events, _saved, _config = collector
    instance.pytest_runtest_logreport(a_report("t.py::test_a"))
    instance.pytest_sessionfinish(session=None, exitstatus=0)

    assert student_report.load_previous("03test") == {"t.py::test_a": "passed"}
