"""手書きのクライアントが openapi.json と一致していることを見る。

生成をやめた代わりに、ここが契約との一致を機械的に確かめる。
前学期の不具合は、クライアントが送る形と契約のずれが誰にも見えないまま
1 学期続いたものだった。
"""

import json
import os

import httpx
import pytest

from lpp_collector.api import LppApi, attempt_fields, run_fields

CONTRACT = json.load(
    open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "openapi.json"))
)

RESPONSES = {
    ("POST", "/api/setup"): (200, {"studentId": "s1", "deviceToken": "t", "bindingId": "b", "priorAttempts": {"count": 0, "from": None, "to": None}}),
    ("GET", "/api/consent"): (200, None),
    ("POST", "/api/consent"): (200, {"consentId": "c1"}),
    ("DELETE", "/api/consent"): (200, {"revoked": True}),
    ("POST", "/api/attempt"): (201, {"attemptId": "a1", "duplicate": False, "fileCount": 1, "bound": True}),
    ("POST", "/api/run"): (201, {"runId": "r1", "duplicate": False, "fileCount": 1, "bound": True}),
    ("POST", "/api/submission"): (201, {"submissionId": "s", "submittedAt": "2026-04-01T00:00:00.000Z"}),
    ("GET", "/api/devices"): (200, {"devices": []}),
    ("DELETE", "/api/devices/b1"): (200, {"unbound": True}),
    ("GET", "/api/assignments"): (200, {"assignments": []}),
}


@pytest.fixture
def recorded():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        status, body = RESPONSES[(request.method, request.url.path)]
        return httpx.Response(status, json=body)

    return calls, httpx.MockTransport(handler)


def _spec_path(path: str) -> str:
    """実際に叩いた経路を OpenAPI の書き方に戻す。"""
    if path.startswith("/api/devices/"):
        return "/api/devices/{bindingId}"
    return path


def test_every_call_matches_a_documented_route(recorded):
    calls, transport = recorded
    api = LppApi(base_url="http://example.test", token="tok", transport=transport)

    api.setup("token", "dev")
    api.get_consent()
    api.post_consent(True, False, True)
    api.revoke_consent()
    api.post_attempt(
        {
            "idempotencyKey": "k",
            "deviceId": "dev",
            "assignment": "01test",
            "deviceTime": "2026-04-01T00:00:00+09:00",
            "result": [],
        },
        b"tar",
    )
    api.post_run(
        {
            "idempotencyKey": "k",
            "deviceId": "dev",
            "deviceTime": "2026-04-01T00:00:00+09:00",
        },
        b"tar",
    )
    api.post_submission("a1")
    api.list_devices()
    api.unbind_device("b1")
    api.list_assignments()

    assert len(calls) == 10
    for call in calls:
        spec = CONTRACT["paths"].get(_spec_path(call.url.path))
        assert spec is not None, f"契約にない経路: {call.url.path}"
        assert call.method.lower() in spec, f"契約にない method: {call.method} {call.url.path}"


def test_json_bodies_match_the_schemas(recorded):
    calls, transport = recorded
    api = LppApi(base_url="http://example.test", token="tok", transport=transport)
    api.setup("token", "dev")
    api.post_consent(True, False, True)
    api.post_submission("a1", auto=True)

    for call, schema_name in zip(calls, ["SetupRequest", "ConsentRequest", "SubmissionRequest"]):
        schema = CONTRACT["components"]["schemas"][schema_name]
        body = json.loads(call.content)
        assert set(schema.get("required", [])) <= set(body), schema_name
        assert set(body) <= set(schema["properties"]), schema_name


def test_attempt_fields_match_the_schema():
    schema = CONTRACT["components"]["schemas"]["AttemptRequest"]
    fields = attempt_fields(
        {
            "idempotencyKey": "k",
            "deviceId": "dev",
            "assignment": "01test",
            "deviceTime": "2026-04-01T00:00:00+09:00",
            "deviceSentAt": "2026-04-01T00:00:01+09:00",
            "runnerVersion": "0.2.0",
            "imageDigest": "sha256:x",
            "buildExit": 1,
            "buildDiagnostics": {"gcc": []},
            "envLabels": {"AGENT_NAME": "claude_code", "MANAGED_BY_GIT": "true"},
            "result": [{"nodeid": "a::b", "outcome": "passed"}],
        }
    )
    # sourceCode はファイルとして別に送る
    required = set(schema["required"]) - {"sourceCode"}
    assert required <= set(fields)
    assert set(fields) <= set(schema["properties"])

    # JSON を載せるフィールドは 1 つの文字列で、配列を繰り返さない
    assert json.loads(fields["result"])[0]["nodeid"] == "a::b"
    assert json.loads(fields["buildDiagnostics"]) == {"gcc": []}
    assert json.loads(fields["envLabels"]) == {
        "AGENT_NAME": "claude_code",
        "MANAGED_BY_GIT": "true",
    }
    for name in ("result", "buildDiagnostics", "envLabels"):
        assert schema["properties"][name]["type"] == "string"


def test_optional_fields_are_left_out_when_unknown():
    fields = attempt_fields(
        {
            "idempotencyKey": "k",
            "deviceId": "dev",
            "assignment": "01test",
            "deviceTime": "2026-04-01T00:00:00+09:00",
            "result": [],
            "buildExit": None,
            "buildDiagnostics": None,
            "imageDigest": None,
            "envLabels": {},
        }
    )
    assert "buildExit" not in fields
    assert "buildDiagnostics" not in fields
    assert "imageDigest" not in fields
    # 空の申告は送らない。サーバは空文字を 400 で拒む
    assert "envLabels" not in fields


def test_attempt_sends_the_snapshot_as_a_file(recorded):
    calls, transport = recorded
    api = LppApi(base_url="http://example.test", token="tok", transport=transport)
    api.post_attempt(
        {
            "idempotencyKey": "k",
            "deviceId": "dev",
            "assignment": "01test",
            "deviceTime": "2026-04-01T00:00:00+09:00",
            "result": [],
        },
        b"tarbytes",
    )
    body = calls[0].content
    assert b'name="sourceCode"; filename="source.tar"' in body
    assert b"tarbytes" in body
    assert calls[0].headers["authorization"] == "Bearer tok"


def test_no_token_means_no_authorization_header(recorded):
    calls, transport = recorded
    api = LppApi(base_url="http://example.test", transport=transport)
    api.post_attempt(
        {
            "idempotencyKey": "k",
            "deviceId": "dev",
            "assignment": "01test",
            "deviceTime": "2026-04-01T00:00:00+09:00",
            "result": [],
        },
        b"t",
    )
    # 未束縛でも収集は続く。トークンがないことは異常ではない
    assert "authorization" not in calls[0].headers


def test_run_fields_match_the_schema():
    schema = CONTRACT["components"]["schemas"]["RunRequest"]
    fields = run_fields(
        {
            "kind": "run",
            "idempotencyKey": "k",
            "deviceId": "dev",
            "deviceTime": "2026-04-01T00:00:00+09:00",
            "deviceSentAt": "2026-04-01T00:00:01+09:00",
            "runnerVersion": "0.3.0",
            "imageDigest": "sha256:x",
            "buildFlags": ["-g", "-fsanitize=address,undefined"],
            "runtimeEnv": {"ASAN_OPTIONS": "detect_leaks=1"},
            "argv": ["./a.out", "input.mpl"],
            "buildExit": 0,
            "buildDiagnostics": {"gcc": []},
            "runExit": None,
            "runSignal": 6,
            "durationMs": 12,
            "timedOut": False,
            "stdout": "hi\n",
            "stderr": "boom\n",
            "envLabels": {"AGENT_NAME": "none", "MANAGED_BY_GIT": "false"},
        }
    )
    required = set(schema["required"]) - {"sourceCode"}
    assert required <= set(fields)
    # kind はキューの中だけのもので、契約には無い
    assert set(fields) <= set(schema["properties"])
    assert "kind" not in fields

    # 測定条件が構造のまま届くこと
    assert json.loads(fields["buildFlags"]) == ["-g", "-fsanitize=address,undefined"]
    assert json.loads(fields["runtimeEnv"]) == {"ASAN_OPTIONS": "detect_leaks=1"}
    assert json.loads(fields["argv"]) == ["./a.out", "input.mpl"]

    # 測定条件 (runtimeEnv) と収集時の文脈 (envLabels) は別のフィールドで届く
    assert json.loads(fields["envLabels"]) == {
        "AGENT_NAME": "none",
        "MANAGED_BY_GIT": "false",
    }
    assert schema["properties"]["envLabels"]["type"] == "string"

    # シグナルで落ちた実行は runExit を送らない。負の終了コードにしない
    assert "runExit" not in fields
    assert fields["runSignal"] == "6"
    assert fields["timedOut"] == "false"
