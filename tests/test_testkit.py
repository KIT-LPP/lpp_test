"""課題のテストが共通で使う道具。

ここで見ているのは「落ち方の分類」である。従来は終了コードを捨てていたので、
異常終了と「正しい入力なのにエラーを出した」が同じ顔をしていた。
"""

import pytest

from lpp_collector import testkit

Failed = pytest.fail.Exception


@pytest.fixture(autouse=True)
def _clear_stash():
    testkit.take_last_failure()
    yield
    testkit.take_last_failure()


# ---------------------------------------------------------------------------
# 実行
# ---------------------------------------------------------------------------
def test_a_normal_run_keeps_the_exit_code():
    executed = testkit.run("printf out; printf err >&2; exit 3")
    assert executed.returncode == 3
    assert executed.stdout == "out"
    assert executed.stderr == "err"
    assert executed.kind is None


def test_a_signal_death_is_a_crash_in_both_shapes():
    """シェルが exec したときは負、フォークしたときは 128+N が返る。"""
    assert testkit.Run("c", -11, "", "").kind == "crash"
    assert testkit.Run("c", 139, "", "").kind == "crash"
    assert testkit.Run("c", 139, "", "").signal_name == "SIGSEGV"


def test_a_large_exit_code_is_not_mistaken_for_a_signal():
    """`exit(-1)` は 255 を返す。「シグナル 127 で停止」にしてはならない。"""
    executed = testkit.Run("./mpplc x", 255, "", "")
    assert executed.crashed is False
    assert executed.kind is None


def test_a_missing_executable_is_told_apart_from_a_crash():
    executed = testkit.run("/nonexistent/cr input.mpl")
    assert executed.returncode == 127
    assert executed.kind == "not_found"


def test_a_hanging_program_is_killed_with_its_children():
    """孫が残るとパイプが閉じず、communicate() が返らない。"""
    executed = testkit.run("sleep 30 | cat", timeout=0.5)
    assert executed.timed_out
    assert executed.kind == "timeout"


# ---------------------------------------------------------------------------
# 失敗
# ---------------------------------------------------------------------------
def test_a_failure_carries_a_readable_text_and_a_detail():
    with pytest.raises(Failed) as excinfo:
        testkit.fail(
            "output_mismatch",
            fields=[("あなた", "abc"), ("期待", "xyz")],
            notes=["空白は除いています"],
        )

    text = str(excinfo.value)
    assert "出力が期待と違います" in text
    assert "あなた" in text and "abc" in text
    assert "※ 空白は除いています" in text

    detail = testkit.take_last_failure()
    assert detail["kind"] == "output_mismatch"
    assert detail["text"] == text


def test_the_detail_is_handed_over_only_once():
    with pytest.raises(Failed):
        testkit.fail("other")
    assert testkit.take_last_failure() is not None
    assert testkit.take_last_failure() is None


def test_how_it_ended_wins_over_the_given_kind():
    """segfault が「出力が違います」に紛れないようにする。"""
    with pytest.raises(Failed):
        testkit.fail(
            "output_mismatch", executed=testkit.Run("./cr x", 139, "", "")
        )
    detail = testkit.take_last_failure()
    assert detail["kind"] == "crash"
    assert detail["not_found"] is False


def test_a_failure_after_a_missing_executable_is_marked_as_blocked():
    with pytest.raises(Failed) as excinfo:
        testkit.fail(
            "unexpected_error",
            fields=[("期待", "この入力は正しいので、表を出力する")],
            executed=testkit.Run("/w/cr input.mpl", 127, "", ""),
            hint="lpprun input.mpl",
        )

    detail = testkit.take_last_failure()
    assert detail["not_found"] is True
    # 実行ファイルが無いのだから、入力の話をしても始まらない
    text = str(excinfo.value)
    assert "cr という名前の実行ファイル" in text
    assert "lpprun" not in text


def test_output_that_is_not_utf8_is_its_own_kind():
    """従来は subprocess の中の UnicodeDecodeError が学生に見えていた。"""
    with pytest.raises(Failed):
        # POSIX の printf は 8 進のみ。\377 は UTF-8 としては読めない
        testkit.run(r"printf '\377\376'")
    assert testkit.take_last_failure()["kind"] == "encoding"


# ---------------------------------------------------------------------------
# 終わり方そのものが異常なもの
# ---------------------------------------------------------------------------
def test_an_error_report_from_a_normal_exit_is_allowed():
    testkit.reject_abnormal_exit(testkit.Run("./cr x", 1, "", "Line: 3 ERROR"))


def test_a_crash_is_not_accepted_as_an_error_report():
    """シェルの "Segmentation fault" がエラーの報告として通っていた。"""
    executed = testkit.Run("./tc x", 139, "", "Segmentation fault (core dumped)")
    with pytest.raises(Failed):
        testkit.reject_abnormal_exit(executed)
    assert testkit.take_last_failure()["kind"] == "crash"


def test_a_missing_executable_is_not_accepted_as_an_error_report():
    executed = testkit.Run("./tc x", 127, "", "/bin/sh: 1: ./tc: not found")
    with pytest.raises(Failed):
        testkit.reject_abnormal_exit(executed)
    assert testkit.take_last_failure()["kind"] == "not_found"


# ---------------------------------------------------------------------------
# 比較
# ---------------------------------------------------------------------------
def test_the_first_differing_line_is_found_with_its_position():
    mismatch = testkit.first_mismatch(["a", "b", "c"], ["a", "x"])
    assert mismatch.line_no == 2
    assert mismatch.actual == "b" and mismatch.expected == "x"
    assert "あなた 3 行 / 期待 2 行" in mismatch.where


def test_the_filler_for_missing_lines_follows_the_caller():
    """課題1拡張だけ埋め草が None で、末尾の空行の扱いが他と違う。"""
    assert testkit.first_mismatch(["a", ""], ["a"], fill="") is None
    assert testkit.first_mismatch(["a", ""], ["a"], fill=None) is not None


def test_matching_output_does_not_fail(tmp_path, monkeypatch):
    monkeypatch.setattr(testkit, "TARGETPATH", str(tmp_path))
    testkit.compare_or_fail(["a", "b"], ["a", "b"])


def test_a_mismatch_says_where_and_what(tmp_path, monkeypatch):
    monkeypatch.setattr(testkit, "TARGETPATH", str(tmp_path))
    with pytest.raises(Failed) as excinfo:
        testkit.compare_or_fail(["a", "b"], ["a", "x"], stem="sample11")

    text = str(excinfo.value)
    assert "2 行目" in text
    # 見比べる先は、比較に使ったものと並べて置いたもの
    assert "sample11.out" in text and "sample11.expected" in text


# ---------------------------------------------------------------------------
# エラーの行番号
# ---------------------------------------------------------------------------
def test_the_error_line_is_allowed_to_be_off_by_one():
    testkit.compare_error_line_or_fail("LINE 12 ERROR", "11")
    testkit.compare_error_line_or_fail("LINE 12 ERROR", "13")


def test_the_line_number_is_not_taken_from_a_path():
    """テスト環境のパスには python3 が入る。どの入力でも「3 行目」になる。"""
    text = "--- /usr/lib/python3/dist-packages/lpp_collector/testcases/input01/x.mpl\nERROR: bad"
    assert testkit.error_line_number(text) is None


def test_a_line_number_attached_to_a_file_name_is_read():
    """gcc のように `file.c:12:` と書くのは行番号の報告である。"""
    assert testkit.error_line_number("/lpp_test/input01/sample013.mpl:4") == 4
    assert testkit.error_line_number("sample013.mpl:4: ERROR") == 4


def test_the_line_number_comes_from_the_message():
    assert testkit.error_line_number("Line:    3 ERROR: number is too large") == 3
    assert testkit.error_line_number("LINE\t12\nERROR: type is not same") == 12
    assert testkit.error_line_number("Line 3: too big number") == 3
    # ファイル名の後ろにコロンだけが付いていても、行番号は文言から拾う
    assert testkit.error_line_number("scan.c: line 12") == 12


def test_a_far_off_error_line_fails():
    with pytest.raises(Failed):
        testkit.compare_error_line_or_fail("LINE 12 ERROR", "20")
    assert testkit.take_last_failure()["kind"] == "error_line"


def test_an_error_without_a_line_number_is_its_own_kind():
    """従来は AttributeError で落ちていて、理由が読めなかった。"""
    with pytest.raises(Failed):
        testkit.compare_error_line_or_fail("something went wrong", "12")
    assert testkit.take_last_failure()["kind"] == "no_line_number"


# ---------------------------------------------------------------------------
# 保存
# ---------------------------------------------------------------------------
def test_the_raw_output_is_kept_next_to_the_compared_one(tmp_path, monkeypatch):
    monkeypatch.setattr(testkit, "TARGETPATH", str(tmp_path))
    testkit.save_raw("sample11", testkit.Run("./cr x", 0, "生の出力\n", "警告\n"))

    directory = tmp_path / "test_results"
    assert (directory / "sample11.raw.stdout").read_text(encoding="utf-8") == "生の出力\n"
    assert (directory / "sample11.raw.stderr").read_text(encoding="utf-8") == "警告\n"


def test_the_oracle_is_copied_out_of_the_test_environment(tmp_path, monkeypatch):
    monkeypatch.setattr(testkit, "TARGETPATH", str(tmp_path))
    oracle = tmp_path / "expects" / "sample11.stdout"
    oracle.parent.mkdir()
    oracle.write_text("data|integer|2|8,9\n", encoding="utf-8")

    testkit.save_expected("sample11", oracle)

    copied = tmp_path / "test_results" / "sample11.expected"
    assert copied.read_text(encoding="utf-8") == "data|integer|2|8,9\n"
