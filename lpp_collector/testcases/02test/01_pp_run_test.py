"""課題2用テスト

合否の条件は従来のままである。変えたのは、落ちたときに何を見せるか。
"""

import re
from pathlib import Path
import glob
import pytest

from lpp_collector import testkit
from lpp_collector.config import TEST_BASE_DIR

TARGET = "pp"

TEST_EXPECT_DIR = Path(__file__).parent / Path("test_expects")

# 全てのテストデータ
test_data = sorted(glob.glob(f"{TEST_BASE_DIR}/input0[12]/*.mpl", recursive=True))

paramed_test_data = [
    pytest.param(mpl_file, id=Path(mpl_file).name) for mpl_file in test_data
]

NOTE = "整形した結果を 1 行ずつそのまま比べています (空白も数えます)"


def expects_error(mpl_file) -> bool:
    """エラーが出ることを期待している入力か (sample0* がそれ)。"""
    return bool(re.search(r"sample0", str(mpl_file)))


def analyze(mpl_file):
    """pp を 1 回動かす。返り値は (実行の記録, 出力の行, エラーか)。"""
    executed = testkit.run_target(TARGET, mpl_file)
    if executed.stderr:
        return executed, executed.stderr.splitlines(), True
    return executed, executed.stdout.splitlines(), False


@pytest.mark.timeout(10)
@pytest.mark.parametrize(("mpl_file"), paramed_test_data)
def test_run(mpl_file):
    """準備したテストケースを全て実行する．

    期待された出力が得られるかを確認．ただし，厳密すぎるため，テストに
    通らないからといってダメというわけではない．
    """
    stem = Path(mpl_file).stem
    out_file = testkit.result_dir() / (stem + ".out")

    executed, lines, errored = analyze(mpl_file)
    testkit.save_raw(stem, executed)
    testkit.save_lines(out_file, lines)

    # 正常終了した場合
    if not errored:
        expect_file = TEST_EXPECT_DIR / (stem + ".stdout")
        expected = testkit.read_text_or_fail(expect_file, input=mpl_file)
        testkit.save_expected(stem, expect_file)
        testkit.compare_or_fail(
            lines,
            expected.splitlines(),
            input=mpl_file,
            stem=stem,
            executed=executed,
            notes=[NOTE],
            hint=testkit.rerun_hint(mpl_file),
        )
        return

    testkit.reject_abnormal_exit(
        executed, input=mpl_file, hint=testkit.rerun_hint(mpl_file)
    )

    if not expects_error(mpl_file):
        head = executed.stderr.strip().splitlines()
        testkit.fail(
            "unexpected_error",
            input=mpl_file,
            fields=[
                ("あなた", head[0] if head else ""),
                ("期待", "この入力は正しいので、整形した結果を出力する"),
            ],
            executed=executed,
            hint=testkit.rerun_hint(mpl_file),
        )

    # 異常終了した場合。エラーの行番号が正しいかを確認
    # (正解データの前後1行にあるものまで許容)
    expect_file = TEST_EXPECT_DIR / (stem + ".stderr")
    expected = testkit.read_text_or_fail(expect_file, input=mpl_file)
    testkit.save_expected(stem, expect_file)
    testkit.compare_error_line_or_fail(
        "\n".join(lines),
        expected,
        input=mpl_file,
        executed=executed,
        hint=testkit.rerun_hint(mpl_file),
    )
