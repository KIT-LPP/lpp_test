"""同意の対話そのものを見る。

前学期に壊れていたのはまさにこの経路で、しかも失敗が表示されないまま
1 学期続いた。画面を差し替えて、何がサーバへ送られるかを確かめる。
"""

import json
from collections import namedtuple

import httpx
import pytest

from lpp_collector import consent as flow
from lpp_collector.api import LppApi
from lpp_collector.device import LppDevice

Response = namedtuple("Response", "returncode value")

YES, NO, ESC = 0, 1, 255


class FakeWhiptail:
    """答えを台本で渡す whiptail。"""

    def __init__(self, answers, inputs=None):
        self.answers = list(answers)
        self.inputs = list(inputs or [])
        self.shown = []

    def run(self, kind, msg, extra_args=None):
        self.shown.append(msg)
        return Response(self.answers.pop(0), "")

    def msgbox(self, msg):
        self.shown.append(msg)

    def inputbox(self, msg, default="", password=False):
        self.shown.append(msg)
        return self.inputs.pop(0)

    def menu(self, msg, items):
        self.shown.append(msg)
        return self.answers.pop(0)


class Server:
    """要求を覚えるだけの最小のサーバ。"""

    def __init__(self, consent=None, setup_status=200):
        self.calls = []
        self.consent = consent
        self.setup_status = setup_status

    def transport(self):
        return httpx.MockTransport(self._handle)

    def _handle(self, request):
        self.calls.append(request)
        path = request.url.path
        if path == "/api/setup":
            if self.setup_status != 200:
                return httpx.Response(self.setup_status, json={"error": "nope"})
            return httpx.Response(
                200,
                json={
                    "studentId": "s1",
                    "deviceToken": "tok",
                    "bindingId": "b1",
                    "priorAttempts": {"count": 3, "from": "2026-04-01T00:00:00Z", "to": "2026-04-02T00:00:00Z"},
                },
            )
        if path == "/api/consent" and request.method == "GET":
            return httpx.Response(200, json=self.consent)
        if path == "/api/consent" and request.method == "POST":
            return httpx.Response(200, json={"consentId": "c1"})
        if path == "/api/consent" and request.method == "DELETE":
            return httpx.Response(200, json={"revoked": True})
        raise AssertionError(path)

    def posted_consent(self):
        posts = [c for c in self.calls if c.url.path == "/api/consent" and c.method == "POST"]
        return [json.loads(c.content) for c in posts]


def api_for(server, token=None):
    return LppApi(base_url="http://example.test", token=token, transport=server.transport())


def test_consenting_records_all_three_answers():
    server = Server()
    whiptail = FakeWhiptail([YES, YES, YES])

    assert flow.ask_consent(api_for(server, "tok"), whiptail, prior={"count": 3, "from": "a", "to": "b"})
    assert server.posted_consent() == [
        {"researchOk": True, "publicationOk": True, "includePrior": True}
    ]


def test_declining_is_recorded_too():
    """「尋ねたが断られた」と「まだ尋ねていない」は別の状態である。"""
    server = Server()
    assert flow.ask_consent(api_for(server, "tok"), FakeWhiptail([NO]))
    assert server.posted_consent() == [
        {"researchOk": False, "publicationOk": False, "includePrior": False}
    ]


@pytest.mark.parametrize("answers", [[ESC], [YES, ESC], [YES, YES, ESC]])
def test_escaping_changes_nothing(answers):
    """ESC を「いいえ」にしてはならない。

    POST は開いている同意を取り消して新しい行を積むので、画面から抜けた
    だけで同意が取り消され、研究に入っていた試行が黙って外れる。
    """
    server = Server(consent={"researchOk": True, "publicationOk": True, "includePrior": True, "decidedAt": "2026-04-01T00:00:00Z"})
    assert not flow.ask_consent(api_for(server, "tok"), FakeWhiptail(answers))
    assert server.posted_consent() == []


def test_escaping_the_revoke_screen_changes_nothing():
    server = Server()
    assert not flow.revoke_consent(api_for(server, "tok"), FakeWhiptail([ESC]))
    assert [c for c in server.calls if c.method == "DELETE"] == []


def test_setup_binds_the_device_and_then_asks(tmp_path, monkeypatch):
    server = Server()
    device = LppDevice(str(tmp_path / "device.json"))
    whiptail = FakeWhiptail([YES, NO, YES], inputs=[("a-token", 0)])

    # run_setup は接続先を自分で作るので、差し替えて奪う
    monkeypatch.setattr(flow, "LppApi", lambda *a, **k: api_for(server, k.get("token")))
    monkeypatch.setattr(
        flow, "api_with_token", lambda base, dev: api_for(server, dev.device_token)
    )
    assert flow.run_setup(None, device, whiptail)

    assert device.student_id == "s1"
    assert device.device_token == "tok"
    # 未束縛で溜まっていた件数を示してから include_prior を尋ねている
    assert any("3 件" in text for text in whiptail.shown)
    assert server.posted_consent() == [
        {"researchOk": True, "publicationOk": False, "includePrior": True}
    ]


def test_a_wrong_token_is_explained(tmp_path, monkeypatch):
    server = Server(setup_status=401)
    device = LppDevice(str(tmp_path / "device.json"))
    whiptail = FakeWhiptail([], inputs=[("bad", 0)])

    monkeypatch.setattr(flow, "LppApi", lambda *a, **k: api_for(server, k.get("token")))
    assert not flow.run_setup(None, device, whiptail)

    # 握り潰さず、何が起きたか見せる
    assert any("トークンが正しくない" in text for text in whiptail.shown)
    assert device.device_token is None
