"""学生に見せるまとめ。

見ているのは「1426 行を読ませない」ための条件である。分類ごとにまとめ、
先に直すとよいものを上に置き、前回との差を出す。
"""

import json

import pytest

from lpp_collector import student_report


def row(nodeid, outcome="failed", longrepr=None):
    return {"nodeid": nodeid, "outcome": outcome, "when": "call", "longrepr": longrepr}


def detail(kind, input=None, text=None):
    return {
        "kind": kind,
        "title": student_report.KIND_TITLES[kind],
        "input": input,
        "text": text or student_report.KIND_TITLES[kind],
        "not_found": kind == "not_found",
    }


def text_of(lines):
    return "\n".join(line for line, _markup in lines)


# ---------------------------------------------------------------------------
# まとめ
# ---------------------------------------------------------------------------
def test_failures_are_grouped_by_kind_with_counts():
    rows = [
        row("t.py::test_run[sample11.mpl]"),
        row("t.py::test_run[sample12.mpl]"),
        row("t.py::test_run[sample13.mpl]"),
        row("t.py::test_ok", outcome="passed"),
    ]
    details = {
        "t.py::test_run[sample11.mpl]": detail("crash", "input01/sample11.mpl"),
        "t.py::test_run[sample12.mpl]": detail("crash", "input01/sample12.mpl"),
        "t.py::test_run[sample13.mpl]": detail(
            "output_mismatch", "input01/sample13.mpl"
        ),
    }

    out = text_of(student_report.render("03test", rows, details))
    assert "1 / 4 通過" in out
    assert "異常終了しました" in out and "  2 件" in out
    assert "sample11.mpl, sample12.mpl" in out


def test_the_kind_to_fix_first_is_shown_first():
    """コンパイルを直せば下流はまとめて動く。出力の違いより先に見せる。"""
    rows = [row("t.py::test_run[sample11.mpl]"), row("t.py::test_compile")]
    details = {
        "t.py::test_run[sample11.mpl]": detail(
            "output_mismatch", "input01/sample11.mpl"
        ),
        "t.py::test_compile": detail("compile"),
    }

    out = text_of(student_report.render("03test", rows, details))
    assert out.index("コンパイルが通りません") < out.index("出力が期待と違います")
    assert "[1] test_compile" in out


def test_failures_caused_by_a_broken_build_are_collapsed_into_one_line():
    rows = [row("t.py::test_compile")] + [
        row(f"t.py::test_run[sample1{i}.mpl]") for i in range(3)
    ]
    details = {"t.py::test_compile": detail("compile")}
    for i in range(3):
        details[f"t.py::test_run[sample1{i}.mpl]"] = detail(
            "not_found", f"input01/sample1{i}.mpl"
        )

    out = text_of(student_report.render("03test", rows, details))
    assert "コンパイルが通っていないため、他の 3 件も同じ理由で失敗しています。" in out
    # 直すところは 1 つなので、巻き添えの分を代表として出さない
    assert "[1] test_compile" in out
    assert "[2]" not in out


def test_only_a_few_failures_are_shown_in_detail():
    rows = [row(f"t.py::test_run[sample{i}.mpl]") for i in range(20)]
    details = {
        f"t.py::test_run[sample{i}.mpl]": detail(
            "output_mismatch", f"input01/sample{i}.mpl"
        )
        for i in range(20)
    }

    out = text_of(student_report.render("03test", rows, details))
    assert "[1] sample0.mpl" in out
    assert "[2]" not in out  # 同じ分類なので代表は 1 件
    assert "残りの失敗も詳しく見る" in out
    assert "lpptest 03test --full" in out


def test_a_clean_run_says_so_and_stops():
    rows = [row("t.py::test_a", outcome="passed")]
    out = text_of(student_report.render("03test", rows, {}))
    assert "すべて通りました。" in out
    assert "まず見るとよいもの" not in out


def test_an_empty_run_is_not_silently_green():
    """収集の段で落ちると結果が空になる。通ったように見せてはならない。"""
    out = text_of(student_report.render("03test", [], {}))
    assert "テストが 1 件も実行されていません" in out


def test_a_failure_without_a_detail_still_appears():
    """testkit を通らない素の assert や仕組み側の異常も落とさない。"""
    rows = [row("t.py::test_a", outcome="error", longrepr="ImportError: no module")]
    out = text_of(student_report.render("03test", rows, {}))
    assert "テストの用意が足りません" in out
    assert "ImportError: no module" in out


# ---------------------------------------------------------------------------
# 前回との差
# ---------------------------------------------------------------------------
def test_the_difference_from_the_previous_run_is_shown():
    rows = [
        row("t.py::a", outcome="passed"),
        row("t.py::b"),
    ]
    previous = {"t.py::a": "failed", "t.py::b": "passed"}
    out = text_of(student_report.render("03test", rows, {}, previous=previous))
    assert "前回から +1 / -1" in out


def test_tests_that_were_not_run_last_time_are_left_out_of_the_difference():
    """`-k` で絞った実行の後に全体を走らせても「30 件が落ちた」と言わない。"""
    rows = [row("t.py::a"), row("t.py::b")]
    newly_passed, newly_failed = student_report.compare_with_previous(
        rows, {"t.py::a": "passed"}
    )
    assert newly_failed == ["t.py::a"]
    assert newly_passed == []


def test_the_outcome_of_this_run_is_kept_for_the_next_one(tmp_path, monkeypatch):
    monkeypatch.setattr(student_report, "LPP_DATA_DIR", str(tmp_path))
    rows = [row("t.py::a", outcome="passed"), row("t.py::b")]

    student_report.save_current("03test", rows)

    assert student_report.load_previous("03test") == {
        "t.py::a": "passed",
        "t.py::b": "failed",
    }


def test_a_broken_history_file_is_ignored(tmp_path, monkeypatch):
    monkeypatch.setattr(student_report, "LPP_DATA_DIR", str(tmp_path))
    path = tmp_path / "last_results" / "03test.json"
    path.parent.mkdir(parents=True)
    path.write_text("{ではない", encoding="utf-8")

    assert student_report.load_previous("03test") == {}


def test_nothing_is_kept_when_the_assignment_is_unknown(tmp_path, monkeypatch):
    monkeypatch.setattr(student_report, "LPP_DATA_DIR", str(tmp_path))
    student_report.save_current(None, [row("t.py::a")])
    assert not (tmp_path / "last_results").exists()
