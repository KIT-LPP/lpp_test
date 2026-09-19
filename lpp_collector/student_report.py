"""テストの結果を、学生が読む形にまとめる。

pytest の既定の出力は、失敗 1 件ごとにテストの仕組み側の traceback
(`common_task`、`zip_longest`) を出す。課題3 のある実物では 1426 行になり、
学生のコードは 1 行も現れなかった。助言 (`[lpp] ...`) はその 1380 行の上に
埋もれていた。

ここでは、集めた結果を分類ごとにまとめ、代表を数件だけ詳しく見せる。
全部を見たい人のための逃げ道は `lpptest <課題> --full` に置く。
"""

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .config import LPP_DATA_DIR
from .testkit import KIND_TITLES, display_width, pad

# 先に直すと他がまとめて動くものから並べる。表示の順序であり、
# 「まず見るとよいもの」を選ぶ順序でもある
KIND_ORDER = [
    "compile",
    "not_found",
    "cli",
    "timeout",
    "crash",
    "encoding",
    "assemble",
    "comet2",
    "missing_csl",
    "unexpected_error",
    "missing_error",
    "no_output",
    "idempotency",
    "output_mismatch",
    "error_line",
    "no_line_number",
    "harness",
    "other",
]

WIDTH = 74
# 詳しく見せる件数。多いと結局読まれない
DETAIL_LIMIT = 3
# 失敗の分類ごとに名前を並べる上限
NAME_LIMIT = 4

Line = Tuple[str, Dict[str, Any]]


# ---------------------------------------------------------------------------
# 前回の結果
# ---------------------------------------------------------------------------
def _history_path(assignment: str) -> Path:
    safe = assignment.replace("/", "_").replace(os.sep, "_")
    return Path(LPP_DATA_DIR) / "last_results" / f"{safe}.json"


def load_previous(assignment: Optional[str]) -> Dict[str, str]:
    """前回の同じ課題の合否。無ければ空。"""
    if not assignment:
        return {}
    try:
        data = json.loads(_history_path(assignment).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    outcomes = data.get("outcomes")
    return outcomes if isinstance(outcomes, dict) else {}


def save_current(assignment: Optional[str], rows: List[Dict[str, Any]]) -> None:
    if not assignment:
        return
    path = _history_path(assignment)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "assignment": assignment,
            "outcomes": {row["nodeid"]: row.get("outcome") for row in rows},
        }
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        # 履歴が残らないだけで、結果の表示は成り立つ
        pass


def compare_with_previous(
    rows: List[Dict[str, Any]], previous: Dict[str, str]
) -> Tuple[List[str], List[str]]:
    """前回から通るようになったもの、落ちるようになったもの。

    両方に出てくるものだけを見る。`-k` で絞った実行の後に全体を走らせると、
    走らせていなかった分が「新たに落ちた」ことになってしまう。
    """
    newly_passed: List[str] = []
    newly_failed: List[str] = []
    for row in rows:
        nodeid = row["nodeid"]
        if nodeid not in previous:
            continue
        was = previous[nodeid] == "passed"
        now = row.get("outcome") == "passed"
        if now and not was:
            newly_passed.append(nodeid)
        elif was and not now:
            newly_failed.append(nodeid)
    return newly_passed, newly_failed


# ---------------------------------------------------------------------------
# 材料
# ---------------------------------------------------------------------------
class Item:
    def __init__(self, row: Dict[str, Any], detail: Optional[Dict[str, Any]]):
        self.nodeid: str = row["nodeid"]
        self.outcome: str = row.get("outcome", "passed")
        self.detail = detail or {}
        self.longrepr: str = row.get("longrepr") or ""

    @property
    def kind(self) -> str:
        if self.detail.get("kind"):
            return self.detail["kind"]
        if self.outcome == "error":
            return "harness"
        return "other"

    @property
    def title(self) -> str:
        return self.detail.get("title") or KIND_TITLES.get(self.kind, KIND_TITLES["other"])

    @property
    def name(self) -> str:
        """学生が探すときの名前。入力ファイル名を優先する。"""
        if self.detail.get("input"):
            return Path(str(self.detail["input"])).name
        if self.nodeid.endswith("]") and "[" in self.nodeid:
            return self.nodeid[self.nodeid.rindex("[") + 1 : -1]
        return self.nodeid.split("::")[-1]

    @property
    def text(self) -> str:
        return self.detail.get("text") or self.longrepr


def collect(
    rows: List[Dict[str, Any]], details: Dict[str, Dict[str, Any]]
) -> List[Item]:
    return [Item(row, details.get(row["nodeid"])) for row in rows]


def _group(items: List[Item]) -> "List[Tuple[str, List[Item]]]":
    groups: Dict[str, List[Item]] = {}
    for item in items:
        groups.setdefault(item.kind, []).append(item)
    order = {kind: index for index, kind in enumerate(KIND_ORDER)}
    return sorted(groups.items(), key=lambda pair: order.get(pair[0], len(KIND_ORDER)))


def _representatives(grouped: "List[Tuple[str, List[Item]]]") -> List[Item]:
    """分類ごとに 1 件ずつ、先に直すとよい順に選ぶ。

    コンパイルが通っていないときは、実行ファイルが無くて落ちた分を出さない。
    直すところは 1 つで、それは既に上に出ている。
    """
    blocked = any(kind == "compile" for kind, _members in grouped)
    chosen: List[Item] = []
    for kind, members in grouped:
        if blocked and kind == "not_found":
            continue
        chosen.append(members[0])
        if len(chosen) >= DETAIL_LIMIT:
            break
    return chosen


# ---------------------------------------------------------------------------
# 表示
# ---------------------------------------------------------------------------
def _sep(char: str = "━") -> Line:
    return (char * (WIDTH // display_width(char)), {"cyan": True})


def _names(members: List[Item]) -> str:
    names = [item.name for item in members]
    shown = ", ".join(names[:NAME_LIMIT])
    if len(names) > NAME_LIMIT:
        shown += f" ほか {len(names) - NAME_LIMIT} 件"
    return shown


def render(
    assignment: Optional[str],
    rows: List[Dict[str, Any]],
    details: Dict[str, Dict[str, Any]],
    previous: Optional[Dict[str, str]] = None,
    full: bool = False,
) -> List[Line]:
    items = collect(rows, details)
    passed = [item for item in items if item.outcome == "passed"]
    skipped = [item for item in items if item.outcome == "skipped"]
    failed = [item for item in items if item.outcome not in ("passed", "skipped")]

    lines: List[Line] = [("", {}), _sep()]

    total = len(items)
    head = f" {assignment or 'テスト'} の結果    {len(passed)} / {total} 通過"
    if previous:
        newly_passed, newly_failed = compare_with_previous(rows, previous)
        if newly_passed or newly_failed:
            head += f"    前回から +{len(newly_passed)} / -{len(newly_failed)}"
    lines.append((head, {"bold": True, "green": not failed, "red": bool(failed)}))
    lines.append(_sep())

    if not items:
        lines.append(("  テストが 1 件も実行されていません。", {"red": True}))
        lines.append(("  テスト環境の不具合の可能性があります。担当教員に知らせてください。", {}))
        return lines

    if passed:
        lines.append((f"  ✓ {pad('通過', 30)}{len(passed):>3} 件", {"green": True}))
    grouped = _group(failed)
    for kind, members in grouped:
        title = KIND_TITLES.get(kind, KIND_TITLES["other"])
        lines.append(
            (
                f"  ✗ {pad(title, 30)}{len(members):>3} 件  {_names(members)}",
                {"red": True},
            )
        )
    if skipped:
        lines.append((f"  - {pad('省略', 30)}{len(skipped):>3} 件", {}))

    if not failed:
        lines.append(("", {}))
        lines.append(("  すべて通りました。", {"green": True, "bold": True}))
        return lines

    # コンパイルが通っていないと、実行のテストは全部落ちる。同じ原因なので
    # 1 行にまとめ、先にコンパイルを直せばよいことが分かるようにする
    blocked = [item for item in failed if item.detail.get("not_found")]
    if blocked and any(item.kind == "compile" for item in failed):
        lines.append(("", {}))
        lines.append(
            (
                f"  コンパイルが通っていないため、実行のテスト {len(blocked)} 件も"
                "失敗しています。",
                {},
            )
        )

    shown = _representatives(grouped)
    lines.append(("", {}))
    lines.append((_heading("まず見るとよいもの"), {"cyan": True}))
    for number, item in enumerate(shown, 1):
        lines.append(("", {}))
        lines.append((f" [{number}] {item.name}  {item.title}", {"bold": True, "red": True}))
        body = item.text.splitlines()
        # 分類の見出しは上の行に出したので、本文の 1 行目は落とす
        if body and body[0].strip() == item.title.strip():
            body = body[1:]
        limit = 400 if full else 40
        for line in body[:limit]:
            lines.append(("    " + line, {}))
        if len(body) > limit:
            lines.append((f"    ... (残り {len(body) - limit} 行は --full で)", {}))

    remaining = len(failed) - len(shown)
    lines.append(("", {}))
    lines.append((_heading("次の一手"), {"cyan": True}))
    suite = assignment or "<課題>"
    if remaining > 0 and not full:
        lines.append((f"  {pad('残りの失敗も詳しく見る', 28)}lpptest {suite} --full", {}))
    lines.append(
        (
            f"  {pad('1 件だけ走らせる', 28)}lpptest {suite} all -k {_keyword(shown[0])}",
            {},
        )
    )
    lines.append((f"  {pad('自分の入力で動かす', 28)}lpprun <入力ファイル>  (サニタイザ付き)", {}))
    lines.append(
        (f"  {pad('出力を見比べる', 28)}test_results/ の .out と .expected", {})
    )
    return lines


def _heading(title: str) -> str:
    text = f" ── {title} "
    return text + "─" * max(4, WIDTH - display_width(text))


def _keyword(item: Item) -> str:
    """`-k` に渡せる形。入力ファイル名は拡張子を落とす。"""
    name = item.name
    return name[: name.rindex(".")] if "." in name else name
