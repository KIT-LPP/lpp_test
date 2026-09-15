"""サーバの API を叩く。

契約の単一の情報源はサーバの `src/api.ts` で、その写しが `openapi.json` である。
ここは手書きだが、送るフィールド名が契約と一致することを
`tests/test_contract.py` が `openapi.json` と突き合わせて検査する。

前学期の不具合は、クライアントと契約のずれが `except Exception` に握り潰されて
1 学期見えなかったものだった。ここでは失敗を必ず `ApiError` にし、状態コードと
本文を持たせる。呼び出し側は握り潰さずに学生へ見せる。
"""

import json
from typing import Any, Dict, List, Optional

import httpx

from .config import LPP_BASE_URL

# 60KB から 1MB の tar を送る。読み書きに 3 秒は短すぎて、本番では
# 47 端末のうち 42 が再送キューに滞留していた。接続だけ短く、転送は長く待つ
TIMEOUT = httpx.Timeout(connect=5.0, read=60.0, write=60.0, pool=5.0)


class ApiError(Exception):
    """サーバが期待どおりに応じなかった。

    `status_code` が 0 なら、そもそも要求が届いていない。
    """

    def __init__(self, status_code: int, message: str, body: bytes = b""):
        super().__init__(message)
        self.status_code = status_code
        self.body = body

    @property
    def is_retryable(self) -> bool:
        """後で送り直せば通る見込みがあるか。

        4xx は要求そのものが契約に合っていないので、送り直しても通らない。
        滞留させず、失敗として分けて残す。
        """
        return (
            self.status_code == 0
            or self.status_code in (408, 429)
            or self.status_code >= 500
        )


def attempt_fields(record: Dict[str, Any]) -> Dict[str, str]:
    """試行の multipart のテキスト側を組み立てる。

    `result` と `buildDiagnostics` は JSON を載せた 1 つの文字列フィールドである。
    配列を同名フィールドの繰り返しにすると、サーバ側は最後の 1 つしか見ない。
    """
    fields: Dict[str, str] = {
        "idempotencyKey": record["idempotencyKey"],
        "deviceId": record["deviceId"],
        "assignment": record["assignment"],
        "deviceTime": record["deviceTime"],
        "result": json.dumps(record.get("result") or [], ensure_ascii=False),
    }
    for key in ("deviceSentAt", "runnerVersion", "imageDigest"):
        if record.get(key) is not None:
            fields[key] = str(record[key])
    if record.get("buildExit") is not None:
        fields["buildExit"] = str(int(record["buildExit"]))
    if record.get("buildDiagnostics") is not None:
        fields["buildDiagnostics"] = json.dumps(
            record["buildDiagnostics"], ensure_ascii=False
        )
    return fields


def run_fields(record: Dict[str, Any]) -> Dict[str, str]:
    """lpprun の実行の multipart のテキスト側を組み立てる。

    測定条件 (buildFlags, runtimeEnv) は毎回送る。サニタイザを既定で
    有効にしているので、条件が分からない行は後から使えない。
    """
    fields: Dict[str, str] = {
        "idempotencyKey": record["idempotencyKey"],
        "deviceId": record["deviceId"],
        "deviceTime": record["deviceTime"],
    }
    for key in ("assignment", "deviceSentAt", "runnerVersion", "imageDigest", "stdout", "stderr"):
        if record.get(key) is not None:
            fields[key] = str(record[key])
    for key in ("buildFlags", "runtimeEnv", "argv", "buildDiagnostics"):
        if record.get(key) is not None:
            fields[key] = json.dumps(record[key], ensure_ascii=False)
    for key in ("buildExit", "runExit", "runSignal", "durationMs"):
        if record.get(key) is not None:
            fields[key] = str(int(record[key]))
    if record.get("timedOut") is not None:
        fields["timedOut"] = "true" if record["timedOut"] else "false"
    return fields


class LppApi:
    def __init__(
        self,
        base_url: str = LPP_BASE_URL,
        token: Optional[str] = None,
        transport: Optional[httpx.BaseTransport] = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self._client = httpx.Client(timeout=TIMEOUT, transport=transport)

    def close(self):
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # -----------------------------------------------------------------
    def _call(self, method: str, path: str, ok: int, **kwargs) -> Any:
        headers = dict(kwargs.pop("headers", {}))
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        try:
            response = self._client.request(
                method, self.base_url + path, headers=headers, **kwargs
            )
        except httpx.HTTPError as e:
            raise ApiError(0, f"サーバに接続できませんでした: {e}") from e

        if response.status_code == ok:
            if not response.content:
                return None
            try:
                return response.json()
            except ValueError as e:
                raise ApiError(
                    response.status_code,
                    f"応答が JSON ではありません: {e}",
                    response.content,
                ) from e

        raise ApiError(
            response.status_code,
            f"HTTP {response.status_code} {_error_message(response)}",
            response.content,
        )

    # -----------------------------------------------------------------
    def setup(self, token: str, device_id: str) -> Dict[str, Any]:
        return self._call(
            "POST", "/api/setup", 200, json={"token": token, "deviceId": device_id}
        )

    def get_consent(self) -> Optional[Dict[str, Any]]:
        return self._call("GET", "/api/consent", 200)

    def post_consent(
        self, research_ok: bool, publication_ok: bool, include_prior: bool
    ) -> Dict[str, Any]:
        return self._call(
            "POST",
            "/api/consent",
            200,
            json={
                "researchOk": research_ok,
                "publicationOk": publication_ok,
                "includePrior": include_prior,
            },
        )

    def revoke_consent(self) -> Dict[str, Any]:
        return self._call("DELETE", "/api/consent", 200)

    def post_attempt(self, record: Dict[str, Any], source_tar: bytes) -> Dict[str, Any]:
        return self._call(
            "POST",
            "/api/attempt",
            201,
            data=attempt_fields(record),
            files={"sourceCode": ("source.tar", source_tar, "application/x-tar")},
        )

    def post_run(self, record: Dict[str, Any], source_tar: bytes) -> Dict[str, Any]:
        return self._call(
            "POST",
            "/api/run",
            201,
            data=run_fields(record),
            files={"sourceCode": ("source.tar", source_tar, "application/x-tar")},
        )

    def post_submission(self, attempt_id: str, auto: bool = False) -> Dict[str, Any]:
        return self._call(
            "POST",
            "/api/submission",
            201,
            json={"attemptId": attempt_id, "auto": auto},
        )

    def list_devices(self) -> List[Dict[str, Any]]:
        return self._call("GET", "/api/devices", 200)["devices"]

    def unbind_device(self, binding_id: str) -> Dict[str, Any]:
        return self._call("DELETE", f"/api/devices/{binding_id}", 200)

    def list_assignments(self) -> List[Dict[str, Any]]:
        return self._call("GET", "/api/assignments", 200)["assignments"]


def _error_message(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return response.content.decode("utf-8", "replace")[:500]
    if isinstance(body, dict) and "error" in body:
        detail = body.get("detail")
        return f"{body['error']}: {detail}" if detail else str(body["error"])
    return json.dumps(body, ensure_ascii=False)[:500]
