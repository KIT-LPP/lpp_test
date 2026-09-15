import json
from pathlib import Path

import pytest

from lpp_collector.api import ApiError
from lpp_collector.device import LppDevice
from lpp_collector.uploader import Uploader


class FakeApi:
    def __init__(self, error=None):
        self.error = error
        self.sent = []

    def post_attempt(self, record, source_tar):
        self.sent.append((record, source_tar))
        if self.error is not None:
            raise self.error
        return {"attemptId": "a1", "duplicate": False}

    def close(self):
        pass


def make_uploader(tmp_path: Path, api: FakeApi) -> Uploader:
    device = LppDevice(str(tmp_path / "device.json"))
    device.bind("s1", "tok", "b1")
    return Uploader(
        device,
        queue_dir=tmp_path / "queue",
        failed_dir=tmp_path / "failed",
        api_factory=lambda token: api,
    )


def record(key="k1"):
    return {
        "idempotencyKey": key,
        "deviceId": "dev",
        "assignment": "01test",
        "deviceTime": "2026-04-01T00:00:00+09:00",
        "result": [],
    }


def test_the_attempt_is_on_disk_before_it_is_sent(tmp_path):
    api = FakeApi(error=ApiError(0, "unreachable"))
    uploader = make_uploader(tmp_path, api)
    uploader.enqueue(record(), b"tar")

    # 送れなくても記録は残る
    assert uploader.flush() == 0
    assert len(uploader.pending()) == 1
    stored = json.loads((uploader.pending()[0] / "attempt.json").read_text())
    assert stored["idempotencyKey"] == "k1"


def test_a_sent_attempt_leaves_the_queue(tmp_path):
    api = FakeApi()
    uploader = make_uploader(tmp_path, api)
    uploader.enqueue(record(), b"tar")

    assert uploader.flush() == 1
    assert uploader.pending() == []
    assert api.sent[0][1] == b"tar"


def test_the_send_time_is_stamped_at_each_attempt(tmp_path):
    """滞留していても時間軸が壊れないようにする。

    試行の時刻はキューに書いた時点のものを保つ。送信の時刻は送るたびに
    付け直すので、received_at との差が時計のずれ、device_time との差が
    滞留として分かれる。
    """
    api = FakeApi()
    uploader = make_uploader(tmp_path, api)
    uploader.enqueue(record(), b"tar")
    uploader.flush()

    sent = api.sent[0][0]
    assert sent["deviceTime"] == "2026-04-01T00:00:00+09:00"
    assert sent["deviceSentAt"] != sent["deviceTime"]


def test_an_old_failure_does_not_hold_back_the_new_attempt(tmp_path):
    """一度の失敗で以降すべてを滞留させない。

    本番では 47 端末のうち 42 がこれで滞留していた。
    """
    api = FakeApi()
    uploader = make_uploader(tmp_path, api)

    old = uploader.enqueue(record("old"), b"old")
    # 古い方を読めなくして、送信が必ず失敗する状態にする
    (old / "attempt.json").write_text("{ broken")
    uploader.enqueue(record("new"), b"new")

    assert uploader.flush(newest_first=True) == 1
    assert [key for _, body in api.sent for key in [body]] == [b"new"]
    assert uploader.pending() == []


def test_a_rejected_attempt_is_kept_aside_instead_of_retried(tmp_path):
    api = FakeApi(error=ApiError(422, "unknown_assignment"))
    uploader = make_uploader(tmp_path, api)
    uploader.enqueue(record(), b"tar")

    assert uploader.flush() == 0
    assert uploader.pending() == []
    # 消さない。契約の取り違えで拒まれた場合に何を送ろうとしたか残す
    assert (tmp_path / "failed" / "k1" / "attempt.json").exists()


def test_a_server_error_stays_in_the_queue(tmp_path):
    api = FakeApi(error=ApiError(503, "down"))
    uploader = make_uploader(tmp_path, api)
    uploader.enqueue(record(), b"tar")

    assert uploader.flush() == 0
    assert len(uploader.pending()) == 1


def test_an_invalid_token_is_dropped_and_the_attempt_kept(tmp_path):
    api = FakeApi(error=ApiError(401, "not bound"))
    uploader = make_uploader(tmp_path, api)
    uploader.enqueue(record(), b"tar")
    uploader.flush()

    # 持ち続けても毎回 401 になる。未束縛としてなら受け入れられるので記録は残す
    assert uploader.device.device_token is None
    assert len(uploader.pending()) == 1
    assert any("lppsetup" in m for m in uploader.errors)


def test_the_old_pickle_queue_is_moved_aside(tmp_path):
    """本番の端末には旧 API 宛の *.dat が溜まっている。

    読もうとして毎回落ちると、新しい試行まで送れなくなる。
    """
    api = FakeApi()
    uploader = make_uploader(tmp_path, api)
    uploader.queue_dir.mkdir(parents=True, exist_ok=True)
    (uploader.queue_dir / "1699999999.0.dat").write_bytes(b"\x80\x04 legacy pickle")
    uploader.enqueue(record(), b"tar")

    assert uploader.flush() == 1
    assert (tmp_path / "failed" / "legacy" / "1699999999.0.dat").exists()
    assert uploader.pending() == []


def test_an_interrupted_send_comes_back(tmp_path, monkeypatch):
    """送っている途中でプロセスが終わったものを取り残さない。"""
    import os
    import time

    from lpp_collector import uploader as module

    api = FakeApi()
    up = make_uploader(tmp_path, api)
    entry = up.enqueue(record(), b"tar")
    stuck = entry.with_name(entry.name + ".sending")
    os.replace(entry, stuck)

    assert up.pending() == []
    # 走っている送信は横取りしない
    assert up.flush() == 0

    old = time.time() - module.STALE_SENDING - 1
    os.utime(stuck, (old, old))
    assert up.flush() == 1


def test_an_unreachable_server_stops_the_round(tmp_path):
    """届かないと分かった後も 1 件ずつ接続を待たない。"""
    api = FakeApi(error=ApiError(0, "unreachable"))
    up = make_uploader(tmp_path, api)
    for key in ("a", "b", "c"):
        up.enqueue(record(key), b"tar")

    assert up.flush() == 0
    assert len(api.sent) == 1
    assert len(up.pending()) == 3
