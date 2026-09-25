"""サーバが配るフィーチャーフラグの受け取り。

届かなければ既定値で動くこと、その失敗でテストを止めないことを重点的に見る。
"""

import threading
import time
from pathlib import Path

import httpx
import pytest

from lpp_collector.api import ApiError, LppApi
from lpp_collector.device import LppDevice
from lpp_collector.flags import (
    OFF,
    PROMPT,
    UNAVAILABLE,
    auto_submit_mode,
    fetch_flags,
)


def bound_device(tmp_path: Path) -> LppDevice:
    device = LppDevice(str(tmp_path / "device.json"))
    device.bind("s1", "tok", "b1")
    return device


def api_answering(handler):
    """`handler(request)` で応じる LppApi を作る factory。"""
    requests = []

    def record(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return handler(request)

    def factory(token):
        return LppApi(
            base_url="http://example.test",
            token=token,
            transport=httpx.MockTransport(record),
        )

    return factory, requests


def test_the_resolved_flags_come_back(tmp_path):
    factory, requests = api_answering(
        lambda r: httpx.Response(200, json={"flags": {"auto_submit": "prompt"}})
    )

    flags = fetch_flags("01test", device=bound_device(tmp_path), api_factory=factory)

    assert flags == {"auto_submit": "prompt"}
    # 学生単位の値はトークンを添えたときだけ効く
    assert requests[0].headers["authorization"] == "Bearer tok"
    assert requests[0].url.params["assignment"] == "01test"


def test_an_unbound_device_asks_without_a_token(tmp_path):
    """セットアップ前でも全体と課題の値は要る。"""
    factory, requests = api_answering(
        lambda r: httpx.Response(200, json={"flags": {}})
    )

    fetch_flags("01test", device=LppDevice(str(tmp_path / "device.json")), api_factory=factory)

    assert "authorization" not in requests[0].headers


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(401, json={"error": "unauthenticated"}),
        httpx.Response(500, text="boom"),
        httpx.Response(200, text="not json"),
    ],
)
def test_any_failure_means_no_flags(tmp_path, response):
    factory, _ = api_answering(lambda r: response)

    assert fetch_flags("01test", device=bound_device(tmp_path), api_factory=factory) is None


def test_a_401_does_not_drop_the_binding(tmp_path):
    """束縛を手放すのはアップロードの側の仕事。フラグが取れないだけで提出を止めない。"""
    device = bound_device(tmp_path)
    factory, _ = api_answering(
        lambda r: httpx.Response(401, json={"error": "unauthenticated"})
    )

    fetch_flags("01test", device=device, api_factory=factory)

    assert device.is_bound()


def test_an_unreachable_server_means_no_flags(tmp_path):
    def unreachable(request):
        raise httpx.ConnectError("no route", request=request)

    factory, _ = api_answering(unreachable)

    assert fetch_flags("01test", device=bound_device(tmp_path), api_factory=factory) is None


def test_a_hung_server_is_given_up_at_the_deadline(tmp_path):
    """黙って捨てるネットワークでも、テストの開始を期限より長く待たせない。"""
    release = threading.Event()

    class HungApi:
        def get_flags(self, assignment):
            release.wait(10)
            return {"auto_submit": "prompt"}

        def close(self):
            pass

    started = time.monotonic()
    try:
        flags = fetch_flags(
            "01test",
            device=bound_device(tmp_path),
            api_factory=lambda token: HungApi(),
            deadline=0.2,
        )
    finally:
        release.set()

    assert flags is None
    assert time.monotonic() - started < 2


def test_non_string_values_are_dropped(tmp_path):
    factory, _ = api_answering(
        lambda r: httpx.Response(200, json={"flags": {"auto_submit": "prompt", "x": 1}})
    )

    assert fetch_flags("01test", device=bound_device(tmp_path), api_factory=factory) == {
        "auto_submit": "prompt"
    }


@pytest.mark.parametrize(
    "flags, mode",
    [
        ({"auto_submit": "prompt"}, PROMPT),
        ({"auto_submit": "off"}, OFF),
        # 知らない値は off。確認なしで提出する値は置いていない
        ({"auto_submit": "on"}, OFF),
        # 行が無ければ既定値
        ({}, OFF),
        # 届かなかった。挙動は off と同じだが、試行には区別して残す
        (None, UNAVAILABLE),
    ],
)
def test_auto_submit_mode(flags, mode):
    assert auto_submit_mode(flags) == mode
