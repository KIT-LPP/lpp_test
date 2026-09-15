"""pytest の report を、送る形の行にまとめる。

従来は `when == "call"` の report だけを数えていたので、setup の段で失敗
または skip したテストは 1 件も残らなかった。ここでは nodeid ごとに 1 行へ
まとめ、合否を決めた段を `when` に残す。段ごとに 1 行にすると、合否の
件数が 3 倍になる。
"""

from typing import Any, Dict, List


class ReportAggregator:
    def __init__(self):
        self.rows: Dict[str, Dict[str, Any]] = {}
        self.collect_errors: List[Dict[str, Any]] = []

    def add_test_report(self, report):
        row = self.rows.setdefault(
            report.nodeid,
            {
                "nodeid": report.nodeid,
                "outcome": "passed",
                "when": report.when,
                "duration": 0.0,
                "longrepr": None,
            },
        )
        row["duration"] += float(getattr(report, "duration", 0.0) or 0.0)

        if report.failed:
            # call の失敗はテストの失敗、それ以外の段の失敗は環境の異常
            outcome = "failed" if report.when == "call" else "error"
        elif report.skipped:
            outcome = "skipped"
        else:
            return  # passed は既に付いた結果を覆さない

        row["outcome"] = outcome
        row["when"] = report.when
        row["longrepr"] = _text(report) or None

    def add_collect_report(self, report):
        """収集の段で落ちたものも残す。

        ここで落ちるとテストは 1 件も実行されず、結果が空配列になる。
        本番の 63% が空だった原因は特定できていないが、空のまま届いて
        何も分からない状態にはしない。
        """
        if not report.failed:
            return
        self.collect_errors.append(
            {
                "nodeid": getattr(report, "nodeid", None) or "<collect>",
                "outcome": "error",
                "when": "collect",
                "longrepr": str(report.longrepr)[:10000] if report.longrepr else None,
            }
        )

    def result(self) -> List[Dict[str, Any]]:
        return self.collect_errors + list(self.rows.values())


def _text(report) -> str:
    text = getattr(report, "longreprtext", None)
    if text:
        return str(text)[:10000]
    longrepr = getattr(report, "longrepr", None)
    return str(longrepr)[:10000] if longrepr else ""
