"""pytest プラグイン。テストの実行を 1 件の試行として記録し、送る。

収集は同意に依らない。提出とフィードバックのためにソースを送ることは課題の
遂行に必要な処理なので全員に対して行い、研究に使うかどうかはサーバ側の
同意で決まる。
"""

import os
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

from _pytest.config import Config
from _pytest.reports import CollectReport, TestReport

from .build import take_build_record
from .config import TARGETPATH
from .device import LppDevice
from .reports import ReportAggregator
from .snapshot import snapshot
from .uploader import FOREGROUND_DEADLINE, Uploader


def runner_version() -> str:
    try:
        from importlib.metadata import version

        return version("lpp-collector")
    except Exception:  # noqa: BLE001 - 版が引けないこと自体は止める理由にならない
        return "unknown"


class LppCollector:
    def __init__(self, config: Config):
        self.device = LppDevice()
        self.uploader = Uploader(self.device)
        # 試行の時刻はセッションの開始時刻。オフセット付きで持つ。
        # コンテナの TZ は UTC だが、--run-pytest でホスト実行するとずれる
        self.device_time = datetime.now().astimezone()
        self.aggregator = ReportAggregator()
        self.uploader.start_background_retry()

    # -----------------------------------------------------------------
    def pytest_runtest_logreport(self, report: TestReport):
        self.aggregator.add_test_report(report)

    def pytest_collectreport(self, report: CollectReport):
        self.aggregator.add_collect_report(report)

    # -----------------------------------------------------------------
    def result(self) -> List[Dict[str, Any]]:
        return self.aggregator.result()

    def pytest_sessionfinish(self, session, exitstatus):
        assignment = os.environ.get("LPP_TESTSUITE")
        if not assignment:
            # 課題名が分からないものはサーバが 422 で拒む。キューに積んでも
            # 通らないので、ここで止めて理由を見せる
            print(
                "[lpp] 課題名が分からないため今回の結果は送りませんでした "
                "(lpptest 経由で実行してください)"
            )
            self.uploader.stop_background_retry()
            return

        build = take_build_record()
        record: Dict[str, Any] = {
            "idempotencyKey": _new_key(),
            "deviceId": self.device.device_id,
            "assignment": assignment,
            "deviceTime": self.device_time.isoformat(),
            "runnerVersion": runner_version(),
            "imageDigest": os.environ.get("LPP_IMAGE_DIGEST"),
            "buildExit": build.get("exit") if build else None,
            "buildDiagnostics": build.get("diagnostics") if build else None,
            "result": self.result(),
        }

        try:
            self.uploader.enqueue(record, snapshot(TARGETPATH))
        except Exception as e:  # noqa: BLE001
            print(f"[lpp] 試行を記録できませんでした: {e}")

        self.uploader.stop_background_retry()
        # 新しい順に送る。過去の滞留に今回の試行を巻き込ませない
        self.uploader.flush(
            newest_first=True, deadline=time.monotonic() + FOREGROUND_DEADLINE
        )

        for message in self.uploader.errors:
            print(f"[lpp] {message}")
        remaining = len(self.uploader.pending())
        if remaining:
            print(f"[lpp] 送信待ちが {remaining} 件あります。次回の実行で送り直します")


def _new_key() -> str:
    import uuid

    return str(uuid.uuid4())


def pytest_configure(config: Config):  # pragma: no cover
    config.pluginmanager.register(LppCollector(config))
