"""同意の対話そのものを見る。

前学期に壊れていたのはまさにこの経路で、しかも失敗が表示されないまま
1 学期続いた。画面を差し替えて、何がサーバへ送られるかを確かめる。
"""

import io
import json

import httpx
import pytest

from lpp_collector import consent as flow
from lpp_collector.api import LppApi
from lpp_collector.device import LppDevice

YES, NO, ESC = True, False, None


class FakePrompts:
    """答えを台本で渡す対話。

    中断 (ESC や ctrl-c) は None である。「いいえ」と同じ値にしてはならない。
    """

    def __init__(self, answers, inputs=None):
        self.answers = list(answers)
        self.inputs = list(inputs or [])
        self.shown = []

    def ask(self, text, yes="はい", no="いいえ"):
        self.shown.append(text)
        return self.answers.pop(0)

    def notice(self, text):
        self.shown.append(text)

    def secret(self, label):
        self.shown.append(label)
        return self.inputs.pop(0)

    def menu(self, text, items, question="操作を選んでください"):
        self.shown.append(text)
        self.offered = [value for value, _ in items]
        return self.answers.pop(0)


class Server:
    """要求を覚えるだけの最小のサーバ。"""

    def __init__(self, consent=None, setup_status=200, priors=True):
        self.calls = []
        self.consent = consent
        self.setup_status = setup_status
        self.priors = priors

    def transport(self):
        return httpx.MockTransport(self._handle)

    def _handle(self, request):
        self.calls.append(request)
        path = request.url.path
        if path == "/api/setup":
            if self.setup_status != 200:
                return httpx.Response(self.setup_status, json={"error": "nope"})
            body = {
                "studentId": "s1",
                "deviceToken": "tok",
                "bindingId": "b1",
            }
            if self.priors:
                body["priorAttempts"] = {"count": 3, "from": "2026-04-01T00:00:00Z", "to": "2026-04-02T00:00:00Z"}
                body["priorRuns"] = {"count": 2, "from": "2026-04-01T00:00:00Z", "to": "2026-04-02T00:00:00Z"}
            return httpx.Response(200, json=body)
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
    ui = FakePrompts([YES, YES, YES])

    assert flow.ask_consent(api_for(server, "tok"), ui, prior_summary="・テストの実行 3 件")
    assert server.posted_consent() == [
        {"researchOk": True, "publicationOk": True, "includePrior": True}
    ]


def test_declining_is_recorded_too():
    """「尋ねたが断られた」と「まだ尋ねていない」は別の状態である。"""
    server = Server()
    assert flow.ask_consent(api_for(server, "tok"), FakePrompts([NO]))
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
    assert not flow.ask_consent(api_for(server, "tok"), FakePrompts(answers))
    assert server.posted_consent() == []


def test_escaping_the_revoke_screen_changes_nothing():
    server = Server()
    assert not flow.revoke_consent(api_for(server, "tok"), FakePrompts([ESC]))
    assert [c for c in server.calls if c.method == "DELETE"] == []


def test_setup_binds_the_device_and_then_asks(tmp_path, monkeypatch):
    server = Server()
    device = LppDevice(str(tmp_path / "device.json"))
    ui = FakePrompts([YES, NO, YES], inputs=["a-token"])

    # run_setup は接続先を自分で作るので、差し替えて奪う
    monkeypatch.setattr(flow, "LppApi", lambda *a, **k: api_for(server, k.get("token")))
    monkeypatch.setattr(
        flow, "api_with_token", lambda base, dev: api_for(server, dev.device_token)
    )
    assert flow.run_setup(None, device, ui)

    assert device.student_id == "s1"
    assert device.device_token == "tok"
    # 未束縛で溜まっていた件数を、試行と lpprun で分けて示している
    assert any("テストの実行 3 件" in text for text in ui.shown)
    assert any("lpprun の実行 2 件" in text for text in ui.shown)
    assert server.posted_consent() == [
        {"researchOk": True, "publicationOk": False, "includePrior": True}
    ]


def test_a_wrong_token_is_explained(tmp_path, monkeypatch):
    server = Server(setup_status=401)
    device = LppDevice(str(tmp_path / "device.json"))
    ui = FakePrompts([], inputs=["bad"])

    monkeypatch.setattr(flow, "LppApi", lambda *a, **k: api_for(server, k.get("token")))
    assert not flow.run_setup(None, device, ui)

    # 握り潰さず、何が起きたか見せる
    assert any("トークンが正しくない" in text for text in ui.shown)
    assert device.device_token is None


def test_the_screen_is_not_opened_without_a_terminal(monkeypatch, capsys):
    """端末が無いと prompt_toolkit は入力を読めない。壊れた画面より出し方を書く。

    ホスト側で `-t` を付けなくなったので、エージェントや CI から
    `lppsetup` を実行するとここまで届くようになった。
    """
    monkeypatch.setattr(flow.sys, "stdin", io.StringIO())
    monkeypatch.setattr(
        flow, "Prompts", lambda *a, **k: pytest.fail("端末が無いのに画面を開いた")
    )

    assert flow.interactive() == 1
    assert "端末が要ります" in capsys.readouterr().out


def test_a_first_setup_without_prior_records_still_asks(tmp_path, monkeypatch):
    """未束縛の記録が 1 件も無い端末でも、最後まで通る。

    この経路だけが別の文面を使う。前は定数を取り込み忘れていて
    NameError で落ちており、初めて触る学生ほど踏みやすい穴だった。
    """
    server = Server(priors=False)
    device = LppDevice(str(tmp_path / "device.json"))
    ui = FakePrompts([YES, YES, NO], inputs=["a-token"])

    monkeypatch.setattr(flow, "LppApi", lambda *a, **k: api_for(server, k.get("token")))
    monkeypatch.setattr(
        flow, "api_with_token", lambda base, dev: api_for(server, dev.device_token)
    )
    assert flow.run_setup(None, device, ui)

    # 別の端末で先に走らせていた分がありうる、という断り
    assert any("別の端末" in text for text in ui.shown)
    assert server.posted_consent() == [
        {"researchOk": True, "publicationOk": True, "includePrior": False}
    ]


# ---------------------------------------------------------------------------
# lpptest の初回に持ちかける
# ---------------------------------------------------------------------------
def first_run(device, ui, monkeypatch, server=None, interactive=True):
    if server is not None:
        monkeypatch.setattr(flow, "LppApi", lambda *a, **k: api_for(server, k.get("token")))
        monkeypatch.setattr(
            flow, "api_with_token", lambda base, dev: api_for(server, dev.device_token)
        )
    return flow.offer_setup_on_first_run(
        None, device=device, ui=ui, interactive=interactive
    )


def test_the_first_run_offers_setup_and_goes_through(tmp_path, monkeypatch):
    server = Server()
    device = LppDevice(str(tmp_path / "device.json"))
    ui = FakePrompts(["setup", YES, NO, YES], inputs=["a-token"])

    assert first_run(device, ui, monkeypatch, server)

    assert device.device_token == "tok"
    # 断る道が必ず出ている。登録しないと進めない画面で止めてはならない
    assert ui.offered == ["setup", "later", "never"]


@pytest.mark.parametrize("answer", ["later", ESC])
def test_postponing_keeps_the_question_open(tmp_path, monkeypatch, answer):
    """「今はしない」と ESC は保留である。断りとして残さない。"""
    path = str(tmp_path / "device.json")
    device = LppDevice(path)

    assert not first_run(device, FakePrompts([answer]), monkeypatch)
    assert not device.setup_declined
    # 次に lpptest を走らせたときは、また尋ねる
    assert not LppDevice(path).setup_declined


def test_declining_for_good_is_remembered(tmp_path, monkeypatch):
    path = str(tmp_path / "device.json")

    assert not first_run(LppDevice(path), FakePrompts(["never"]), monkeypatch)

    device = LppDevice(path)
    assert device.setup_declined
    # 一度断った端末には、もう画面を出さない
    ui = FakePrompts([])
    assert not first_run(device, ui, monkeypatch)
    assert ui.shown == []


def test_a_registered_device_is_not_asked(tmp_path, monkeypatch):
    device = LppDevice(str(tmp_path / "device.json"))
    device.bind("s1", "tok", "b1")

    ui = FakePrompts([])
    assert not first_run(device, ui, monkeypatch)
    assert ui.shown == []


def test_nothing_is_asked_without_a_terminal(tmp_path, monkeypatch, capsys):
    """端末が無いところ (CI、エージェント、パイプ) では尋ねない。

    毎回の案内も出さない。提出が要る場面になれば submit.py が書く。
    """
    path = str(tmp_path / "device.json")
    ui = FakePrompts([])

    assert not first_run(LppDevice(path), ui, monkeypatch, interactive=False)
    assert ui.shown == []
    assert capsys.readouterr().out == ""
    # 尋ねていないのだから、断ったことにもしない
    assert not LppDevice(path).setup_declined
