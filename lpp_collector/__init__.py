"""pytest プラグイン。テストの実行を 1 件の試行として記録し、送る。

収集は同意に依らない。提出とフィードバックのためにソースを送ることは課題の
遂行に必要な処理なので全員に対して行い、研究に使うかどうかはサーバ側の
同意で決まる。

学生に見せるまとめもここで出す (`student_report`)。pytest の既定の出力は
失敗ごとにテストの仕組みの traceback を並べるので、実物では 1426 行になり、
助言はその上に埋もれていた。まとめを最後に置き、既定では pytest 自身の
FAILURES / ERRORS を出さない。全部見たいときは `--full` で戻る。
"""

import os
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

import pytest
from _pytest.config import Config
from _pytest.reports import CollectReport, TestReport

from . import student_report, testkit
from .build import take_build_record
from .config import TARGETPATH
from .device import LppDevice
from .envlabels import env_labels
from .reports import ReportAggregator
from .snapshot import snapshot
from .submit import attempt_state, save_attempt
from .uploader import FOREGROUND_DEADLINE, Uploader


def runner_version() -> str:
    try:
        from importlib.metadata import version

        return version("lpp-collector")
    except Exception:  # noqa: BLE001 - 版が引けないこと自体は止める理由にならない
        return "unknown"


def pytest_addoption(parser):
    parser.addoption(
        "--lpp-full",
        action="store_true",
        default=False,
        help="失敗したテストを pytest の既定の形でも全部出す (lpptest --full)",
    )


class LppCollector:
    def __init__(self, config: Config):
        self.config = config
        self.device = LppDevice()
        self.uploader = Uploader(self.device)
        # 試行の時刻はセッションの開始時刻。オフセット付きで持つ。
        # コンテナの TZ は UTC だが、--run-pytest でホスト実行するとずれる
        self.device_time = datetime.now().astimezone()
        self.aggregator = ReportAggregator()
        self.uploader.start_background_retry()
        self.assignment = os.environ.get("LPP_TESTSUITE")
        # 失敗の中身 (testkit が組み立てたもの)。まとめの材料にする
        self.details: Dict[str, Dict[str, Any]] = {}
        # 前回の合否。今回の分で上書きする前に読む
        self.previous = student_report.load_previous(self.assignment)
        self.full: bool = bool(config.getoption("--lpp-full", False))
        self.record: Optional[Dict[str, Any]] = None
        self.notices: List[str] = []
        self.finished = False

    # -----------------------------------------------------------------
    def pytest_runtest_logreport(self, report: TestReport):
        # testkit が置いた失敗の中身を引き取る。テストは 1 つずつ順に走るので、
        # 段ごとに取り出せば取り違えは起きない
        detail = testkit.take_last_failure()
        if detail and report.failed:
            self.details[report.nodeid] = detail
        self.aggregator.add_test_report(report)

    def pytest_collectreport(self, report: CollectReport):
        self.aggregator.add_collect_report(report)

    # -----------------------------------------------------------------
    def result(self) -> List[Dict[str, Any]]:
        return self.aggregator.result()

    def _failed(self) -> bool:
        return any(row["outcome"] != "passed" for row in self.aggregator.result())

    def pytest_sessionfinish(self, session, exitstatus):
        """記録を作ってキューに積む。送信と表示は後 (terminal_summary) で行う。

        ここで送ってしまうと、学生は結果を見る前に通信の完了を待たされる。
        """
        if not self.assignment:
            # 課題名が分からないものはサーバが 422 で拒む。キューに積んでも
            # 通らないので、ここで止めて理由を見せる
            self.notices.append(
                "課題名が分からないため今回の結果は送りませんでした "
                "(lpptest 経由で実行してください)"
            )
            self.uploader.stop_background_retry()
            self._quiet_pytest_sections()
            return

        build = take_build_record()
        record: Dict[str, Any] = {
            "idempotencyKey": _new_key(),
            "deviceId": self.device.device_id,
            "assignment": self.assignment,
            "deviceTime": self.device_time.isoformat(),
            "runnerVersion": runner_version(),
            "imageDigest": os.environ.get("LPP_IMAGE_DIGEST"),
            "buildExit": build.get("exit") if build else None,
            "buildDiagnostics": build.get("diagnostics") if build else None,
            "envLabels": env_labels(),
            "result": self.result(),
        }
        self.record = record

        try:
            self.uploader.enqueue(record, snapshot(TARGETPATH))
        except Exception as e:  # noqa: BLE001
            self.notices.append(f"試行を記録できませんでした: {e}")

        self.uploader.stop_background_retry()
        student_report.save_current(self.assignment, record["result"])
        self._quiet_pytest_sections()

    def _quiet_pytest_sections(self):
        """pytest 自身の FAILURES / ERRORS / 警告のまとめを出さない。

        `longrepr` は各テストの実行時に作り終えているので、ここで表示の
        設定を変えてもサーバへ送る中身は痩せない。`--full` では触らない。
        """
        if self.full:
            return
        self.config.option.tbstyle = "no"
        # FAILED の羅列と警告のまとめ。どちらもこちらで出し直す
        reporter = self.config.pluginmanager.getplugin("terminalreporter")
        if reporter is not None:
            reporter.reportchars = ""

    # -----------------------------------------------------------------
    @pytest.hookimpl(trylast=True)
    def pytest_terminal_summary(self, terminalreporter, exitstatus, config):
        for line, markup in student_report.render(
            self.assignment,
            self.result(),
            self.details,
            previous=self.previous,
            full=self.full,
        ):
            terminalreporter.write_line(line, **markup)

        # 表示を先に出してから送る。学生を通信の完了待ちにしない
        self._finish()
        for message in self.notices:
            terminalreporter.write_line(f"[lpp] {message}")
        self.notices = []

    def pytest_unconfigure(self, config):
        """まとめが出なかったとき (--no-summary など) の取りこぼしを拾う。"""
        self._finish()
        for message in self.notices:
            print(f"[lpp] {message}")
        self.notices = []

    def _finish(self):
        if self.finished:
            return
        self.finished = True
        if self.record is None:
            return

        # 新しい順に送る。過去の滞留に今回の試行を巻き込ませない
        self.uploader.flush(
            newest_first=True, deadline=time.monotonic() + FOREGROUND_DEADLINE
        )

        # 提出はこの控えを見て行う。送れなかったときも書く。書かずに
        # 済ませると前回の控えが残り、今回のつもりで前回の試行が出る。
        # 送った後に書くのは、試行の id が応答で決まるためである
        try:
            save_attempt(
                attempt_state(
                    assignment=self.record["assignment"],
                    idempotency_key=self.record["idempotencyKey"],
                    device_time=self.record["deviceTime"],
                    result=self.record["result"],
                    attempt_id=self.uploader.attempt_ids.get(
                        self.record["idempotencyKey"]
                    ),
                    session_id=os.environ.get("LPP_SESSION_ID"),
                )
            )
        except OSError as e:
            self.notices.append(f"今回の試行を控えられませんでした: {e}")

        self.notices.extend(self.uploader.errors)
        remaining = len(self.uploader.pending())
        if remaining:
            self.notices.append(
                f"送信待ちが {remaining} 件あります。次回の実行で送り直します"
            )


def _new_key() -> str:
    import uuid

    return str(uuid.uuid4())


def pytest_configure(config: Config):  # pragma: no cover
    config.pluginmanager.register(LppCollector(config))
