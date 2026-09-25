"""提出の経路。

テストがすべて通ったときの確認と、`lppsubmit` の手動の提出を見る。
提出は取り消せないので、「尋ねずに送らない」「今回の試行以外を送らない」
の 2 つを重点的に確かめる。
"""

from pathlib import Path

import pytest

from lpp_collector.api import ApiError
from lpp_collector.device import LppDevice
from lpp_collector.submit import (
    attempt_state,
    is_full_suite_run,
    known_assignments,
    latest_attempt,
    load_attempt,
    manual_submit,
    offer_after_test,
    save_attempt,
    submit,
)
from lpp_collector.uploader import Uploader


class FakeApi:
    def __init__(self, error=None):
        self.error = error
        self.submissions = []
        self.sent = []

    def post_submission(self, attempt_id, auto=False):
        self.submissions.append((attempt_id, auto))
        if self.error is not None:
            raise self.error
        return {"submissionId": "s1", "submittedAt": "2026-04-01T10:00:00.000Z"}

    def post_attempt(self, record, source_tar):
        self.sent.append((record, source_tar))
        if self.error is not None:
            raise self.error
        return {"attemptId": "a1", "duplicate": False, "fileCount": 1, "bound": True}

    def close(self):
        pass


def bound_device(tmp_path: Path) -> LppDevice:
    device = LppDevice(str(tmp_path / "device.json"))
    device.bind("s1", "tok", "b1")
    return device


def unbound_device(tmp_path: Path) -> LppDevice:
    return LppDevice(str(tmp_path / "device.json"))


def passing_state(tmp_path: Path, **overrides):
    state = attempt_state(
        assignment="01test",
        idempotency_key="k1",
        device_time="2026-04-01T09:00:00+09:00",
        result=[{"nodeid": "t1", "outcome": "passed"}],
        attempt_id="a1",
        session_id="sess",
    )
    state.update(overrides)
    save_attempt(state, tmp_path / "attempts")
    return state


# ---------------------------------------------------------------------------
# 控え
# ---------------------------------------------------------------------------
def test_a_failing_run_is_not_all_passed():
    state = attempt_state(
        "01test",
        "k1",
        "2026-04-01T09:00:00+09:00",
        [{"outcome": "passed"}, {"outcome": "failed"}],
    )
    assert state["all_passed"] is False
    assert state["failed"] == 1


def test_a_run_with_no_results_is_not_all_passed():
    """収集の段で落ちると結果は空になる。何も動いていないものは通過ではない。"""
    assert attempt_state("01test", "k1", "t", [])["all_passed"] is False


def test_the_state_is_kept_per_assignment(tmp_path):
    """課題をまたいで走らせても、どちらを出すのか決められるようにする。"""
    state_dir = tmp_path / "attempts"
    save_attempt(attempt_state("01test", "k1", "t1", [{"outcome": "passed"}]), state_dir)
    save_attempt(attempt_state("02test", "k2", "t2", [{"outcome": "passed"}]), state_dir)

    assert known_assignments(state_dir) == ["01test", "02test"]
    assert load_attempt("01test", state_dir)["idempotency_key"] == "k1"
    assert latest_attempt(state_dir)["assignment"] == "02test"


# ---------------------------------------------------------------------------
# テストの直後の確認
# ---------------------------------------------------------------------------
def test_only_a_whole_suite_counts_as_all_passed():
    """一部だけを走らせたものを「すべて通った」と言ってはならない。

    集めた結果は走らせた分のものなので、コンパイルのテストだけを通しても
    その中では全件が通っている。
    """
    assert is_full_suite_run("all", [])
    assert is_full_suite_run(None, ["-v"])
    assert not is_full_suite_run("00_tc_compile_test.py", [])
    assert not is_full_suite_run("all", ["-k", "compile"])
    assert not is_full_suite_run("all", ["--lf"])
    assert not is_full_suite_run("all", ["--deselect=01test/01_tc_run_test.py"])



def test_all_passing_and_a_yes_submits(tmp_path, capsys):
    passing_state(tmp_path)
    api = FakeApi()

    assert offer_after_test(
        "sess",
        device=bound_device(tmp_path),
        api_factory=lambda token: api,
        ask=lambda q: "y",
        state_dir=tmp_path / "attempts",
        interactive=True,
        auto_submit="prompt",
    )
    assert api.submissions == [("a1", True)]
    # 提出したことは控えにも残す
    assert load_attempt("01test", tmp_path / "attempts")["submission"]["submissionId"] == "s1"


@pytest.mark.parametrize("answer", ["", "n", "no", "いいえ", "  "])
def test_anything_but_yes_does_not_submit(tmp_path, answer):
    """既定は「いいえ」。提出は取り消せない。"""
    passing_state(tmp_path)
    api = FakeApi()

    assert not offer_after_test(
        "sess",
        device=bound_device(tmp_path),
        api_factory=lambda token: api,
        ask=lambda q: answer,
        state_dir=tmp_path / "attempts",
        interactive=True,
        auto_submit="prompt",
    )
    assert api.submissions == []


def test_eof_does_not_submit(tmp_path):
    """端末が閉じただけで提出してはならない。"""
    passing_state(tmp_path)
    api = FakeApi()

    def ask(_):
        raise EOFError

    assert not offer_after_test(
        "sess",
        device=bound_device(tmp_path),
        api_factory=lambda token: api,
        ask=ask,
        state_dir=tmp_path / "attempts",
        interactive=True,
        auto_submit="prompt",
    )
    assert api.submissions == []


def test_a_failed_run_is_not_offered(tmp_path):
    passing_state(tmp_path, all_passed=False, failed=1)
    api = FakeApi()

    assert not offer_after_test(
        "sess",
        device=bound_device(tmp_path),
        api_factory=lambda token: api,
        ask=lambda q: "y",
        state_dir=tmp_path / "attempts",
        interactive=True,
        auto_submit="prompt",
    )
    assert api.submissions == []


def test_a_previous_run_is_not_offered_as_this_one(tmp_path):
    """pytest が途中で殺されると控えは前回のまま残る。

    今回の実行の印が合わないものを提出に持ちかけてはならない。
    """
    passing_state(tmp_path, session_id="前回")
    api = FakeApi()

    assert not offer_after_test(
        "sess",
        device=bound_device(tmp_path),
        api_factory=lambda token: api,
        ask=lambda q: "y",
        state_dir=tmp_path / "attempts",
        interactive=True,
        auto_submit="prompt",
    )
    assert api.submissions == []


def test_an_unbound_device_is_told_to_set_up(tmp_path, capsys):
    """提出は身元を伴う。サーバは端末トークンなしでは受け付けない。"""
    passing_state(tmp_path)

    assert not offer_after_test(
        "sess",
        device=unbound_device(tmp_path),
        ask=lambda q: "y",
        state_dir=tmp_path / "attempts",
        interactive=True,
        auto_submit="prompt",
    )
    assert "lppsetup" in capsys.readouterr().out


def test_an_undelivered_attempt_points_at_the_manual_command(tmp_path, capsys):
    passing_state(tmp_path, attempt_id=None)
    api = FakeApi()

    assert not offer_after_test(
        "sess",
        device=bound_device(tmp_path),
        api_factory=lambda token: api,
        ask=lambda q: "y",
        state_dir=tmp_path / "attempts",
        interactive=True,
        auto_submit="prompt",
    )
    assert api.submissions == []
    assert "lppsubmit" in capsys.readouterr().out


def test_without_a_terminal_it_prints_the_command_instead_of_asking(tmp_path, capsys):
    """パイプや CI、エージェントの下では答えが返らない。黙って流さない。"""
    passing_state(tmp_path)
    api = FakeApi()

    def ask(_):
        raise AssertionError("端末が無いのに尋ねてはならない")

    assert not offer_after_test(
        "sess",
        device=bound_device(tmp_path),
        api_factory=lambda token: api,
        ask=ask,
        state_dir=tmp_path / "attempts",
        interactive=False,
        auto_submit="prompt",
    )
    assert "lppsubmit 01test" in capsys.readouterr().out


@pytest.mark.parametrize("mode", ["off", "unavailable", "on", None])
def test_it_asks_only_when_the_server_says_prompt(tmp_path, capsys, monkeypatch, mode):
    """サーバのフラグが prompt のときだけ尋ねる。

    届かなかったとき (unavailable)、知らない値、何も渡されなかったときも
    尋ねない。授業の開始前に提出を尋ねるほうが、尋ねるべきときに尋ねないより困る。
    案内も出さない。`lppsubmit` を勧めると尋ねないようにした意味が薄れる。
    """
    monkeypatch.delenv("LPP_AUTO_SUBMIT", raising=False)
    passing_state(tmp_path)
    api = FakeApi()

    def ask(_):
        raise AssertionError("prompt でないのに尋ねてはならない")

    assert not offer_after_test(
        "sess",
        device=bound_device(tmp_path),
        api_factory=lambda token: api,
        ask=ask,
        state_dir=tmp_path / "attempts",
        interactive=True,
        auto_submit=mode,
    )
    assert api.submissions == []
    assert capsys.readouterr().out == ""


def test_the_mode_comes_from_the_runner_through_the_environment(tmp_path, monkeypatch):
    """runner は pytest の前に取った実効値を環境変数に置く。省略時はそれを読む。"""
    monkeypatch.setenv("LPP_AUTO_SUBMIT", "prompt")
    passing_state(tmp_path)
    api = FakeApi()

    assert offer_after_test(
        "sess",
        device=bound_device(tmp_path),
        api_factory=lambda token: api,
        ask=lambda q: "y",
        state_dir=tmp_path / "attempts",
        interactive=True,
    )
    assert api.submissions == [("a1", True)]


# ---------------------------------------------------------------------------
# 手動の提出
# ---------------------------------------------------------------------------
def test_the_manual_command_submits_the_last_attempt(tmp_path):
    passing_state(tmp_path)
    api = FakeApi()

    assert (
        manual_submit(
            device=bound_device(tmp_path),
            api_factory=lambda token: api,
            ask=lambda q: "y",
            state_dir=tmp_path / "attempts",
        )
        == 0
    )
    # 手で出したものは自動提出ではない
    assert api.submissions == [("a1", False)]


def test_the_manual_command_can_submit_a_failing_attempt(tmp_path, capsys):
    """締め切り前に手元で動くところまで出せる道を塞がない。"""
    passing_state(tmp_path, all_passed=False, failed=2, total=5)
    api = FakeApi()

    assert (
        manual_submit(
            "01test",
            device=bound_device(tmp_path),
            api_factory=lambda token: api,
            ask=lambda q: "y",
            state_dir=tmp_path / "attempts",
        )
        == 0
    )
    assert api.submissions == [("a1", False)]
    # 何を出そうとしているかは見せる
    assert "2/5 件が通っていません" in capsys.readouterr().out


def test_the_manual_command_asks_before_sending(tmp_path):
    passing_state(tmp_path)
    api = FakeApi()

    assert (
        manual_submit(
            device=bound_device(tmp_path),
            api_factory=lambda token: api,
            ask=lambda q: "n",
            state_dir=tmp_path / "attempts",
        )
        == 1
    )
    assert api.submissions == []


def test_the_manual_command_names_the_assignments_it_knows(tmp_path, capsys):
    passing_state(tmp_path)

    assert (
        manual_submit(
            "04test",
            device=bound_device(tmp_path),
            ask=lambda q: "y",
            state_dir=tmp_path / "attempts",
        )
        == 1
    )
    assert "01test" in capsys.readouterr().out


def test_the_manual_command_sends_a_stuck_attempt_first(tmp_path):
    """テストのときに届かなかった試行も、回線が戻れば出せる。"""
    state_dir = tmp_path / "attempts"
    passing_state(tmp_path, attempt_id=None)
    device = bound_device(tmp_path)
    api = FakeApi()
    uploader = Uploader(
        device,
        queue_dir=tmp_path / "queue",
        failed_dir=tmp_path / "failed",
        api_factory=lambda token: api,
    )
    uploader.enqueue(
        {
            "idempotencyKey": "k1",
            "deviceId": "dev",
            "assignment": "01test",
            "deviceTime": "2026-04-01T09:00:00+09:00",
            "result": [],
        },
        b"tar",
    )

    assert (
        manual_submit(
            device=device,
            api_factory=lambda token: api,
            ask=lambda q: "y",
            uploader=uploader,
            state_dir=state_dir,
        )
        == 0
    )
    assert api.submissions == [("a1", False)]
    assert load_attempt("01test", state_dir)["attempt_id"] == "a1"


def test_a_second_submission_says_it_was_already_submitted(tmp_path, capsys):
    state_dir = tmp_path / "attempts"
    passing_state(
        tmp_path,
        submission={"submissionId": "s0", "submittedAt": "2026-04-01T09:30:00.000Z", "auto": True},
    )
    api = FakeApi()

    manual_submit(
        device=bound_device(tmp_path),
        api_factory=lambda token: api,
        ask=lambda q: "y",
        state_dir=state_dir,
    )
    assert "提出済み" in capsys.readouterr().out
    assert api.submissions == [("a1", False)]


# ---------------------------------------------------------------------------
# 失敗
# ---------------------------------------------------------------------------
def test_an_invalid_token_is_dropped(tmp_path, capsys):
    """持ち続けても毎回 401 になる。セットアップし直す道を示す。"""
    state = passing_state(tmp_path)
    device = bound_device(tmp_path)
    api = FakeApi(error=ApiError(401, "unauthenticated"))

    assert submit(state, device, auto=False, api_factory=lambda token: api,
                  state_dir=tmp_path / "attempts") is None
    assert device.device_token is None
    assert "lppsetup" in capsys.readouterr().out


def test_an_unknown_attempt_is_reported(tmp_path, capsys):
    state = passing_state(tmp_path)
    api = FakeApi(error=ApiError(404, "unknown_attempt"))

    assert submit(state, bound_device(tmp_path), auto=False,
                  api_factory=lambda token: api, state_dir=tmp_path / "attempts") is None
    out = capsys.readouterr().out
    assert "lpptest" in out
    # 失敗を握り潰さない
    assert load_attempt("01test", tmp_path / "attempts")["submission"] is None


def test_a_server_error_is_shown(tmp_path, capsys):
    state = passing_state(tmp_path)
    api = FakeApi(error=ApiError(503, "down"))

    assert submit(state, bound_device(tmp_path), auto=False,
                  api_factory=lambda token: api, state_dir=tmp_path / "attempts") is None
    assert "提出できませんでした" in capsys.readouterr().out
