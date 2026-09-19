"""実物のサーバに対して、セットアップから提出までを通す。

前学期の不具合は、クライアントとサーバを一度も突き合わせていなかったために
1 学期見えなかった。契約の突き合わせ (test_contract.py) は形しか見ないので、
記録が本当に成立することはここで確かめる。

    cd ../lpp_collector_v2 && DB_NAME=lpp_dev PORT=13459 bun run src/index.ts
    LPP_TEST_SERVER=http://127.0.0.1:13459 LPP_TEST_TOKEN=... pytest tests

`LPP_TEST_TOKEN` は `bun run scripts/issue-tokens.ts roster.csv --commit --out`
で発行したセットアップトークンである。
"""

import os
import tarfile
import uuid
from datetime import datetime, timezone
from io import BytesIO

import pytest

from lpp_collector.api import LppApi
from lpp_collector.device import LppDevice
from lpp_collector.submit import attempt_state, submit
from lpp_collector.uploader import Uploader

BASE_URL = os.environ.get("LPP_TEST_SERVER")
TOKEN = os.environ.get("LPP_TEST_TOKEN")

pytestmark = pytest.mark.skipif(
    not BASE_URL or not TOKEN,
    reason="LPP_TEST_SERVER と LPP_TEST_TOKEN が要る",
)


def a_tar(name="main.c", body=b"int main(void){return 0;}"):
    buf = BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tf:
        info = tarfile.TarInfo(name)
        info.size = len(body)
        tf.addfile(info, BytesIO(body))
    return buf.getvalue()


def a_record(device_id, key=None):
    return {
        "idempotencyKey": key or str(uuid.uuid4()),
        "deviceId": device_id,
        "assignment": "01test",
        "deviceTime": datetime.now(timezone.utc).astimezone().isoformat(),
        "runnerVersion": "test",
        "buildExit": 0,
        "buildDiagnostics": {"command": "gcc", "gcc": []},
        "result": [{"nodeid": "t.py::test_compile", "outcome": "passed", "when": "call"}],
    }


def test_setup_consent_and_submission(tmp_path):
    device = LppDevice(str(tmp_path / "device.json"))

    # 1. 未確認のまま収集される
    with LppApi(base_url=BASE_URL) as api:
        first = api.post_attempt(a_record(device.device_id), a_tar())
    assert first["bound"] is False

    # 2. セットアップで端末と学籍番号が結びつき、溜まっていた分が見える
    with LppApi(base_url=BASE_URL) as api:
        result = api.setup(TOKEN, device.device_id)
    assert result["priorAttempts"]["count"] == 1
    device.bind(result["studentId"], result["deviceToken"], result["bindingId"])

    # 3. 同意が記録される。前学期はここが 1 件も成立していなかった
    with LppApi(base_url=BASE_URL, token=device.device_token) as api:
        api.post_consent(True, False, True)
        state = api.get_consent()
    assert state["researchOk"] is True
    assert state["includePrior"] is True

    # 4. 束縛された状態でのアップロードと提出
    uploader = Uploader(
        device, queue_dir=tmp_path / "queue", failed_dir=tmp_path / "failed",
        api_factory=lambda token: LppApi(base_url=BASE_URL, token=token),
    )
    queued = a_record(device.device_id)
    uploader.enqueue(queued, a_tar("main.c", b"int main(void){return 1;}"))
    assert uploader.flush() == 1, uploader.errors
    assert uploader.pending() == []

    # 5. キューから送った試行も、その場で提出できる形で id が残る。
    # 提出は試行の id を鍵にするので、応答を捨てると出せなくなる
    attempt_id = uploader.attempt_ids[queued["idempotencyKey"]]
    state = attempt_state(
        assignment=queued["assignment"],
        idempotency_key=queued["idempotencyKey"],
        device_time=queued["deviceTime"],
        result=queued["result"],
        attempt_id=attempt_id,
    )
    assert state["all_passed"] is True
    assert submit(
        state,
        device,
        auto=True,
        api_factory=lambda token: LppApi(base_url=BASE_URL, token=token),
        state_dir=tmp_path / "attempts",
    )
    assert state["submission"]["submissionId"]

    with LppApi(base_url=BASE_URL, token=device.device_token) as api:
        second = api.post_attempt(a_record(device.device_id), a_tar())
        assert second["bound"] is True
        submission = api.post_submission(second["attemptId"])
        assert submission["submissionId"]
        devices = api.list_devices()
        assert any(d["id"] == device.binding_id for d in devices)
        assert [a["id"] for a in api.list_assignments()].count("01test") == 1


def test_the_same_idempotency_key_is_not_stored_twice(tmp_path):
    device = LppDevice(str(tmp_path / "device.json"))
    record = a_record(device.device_id)
    with LppApi(base_url=BASE_URL) as api:
        first = api.post_attempt(record, a_tar())
        again = api.post_attempt(record, a_tar())
    assert again["duplicate"] is True
    assert again["attemptId"] == first["attemptId"]
