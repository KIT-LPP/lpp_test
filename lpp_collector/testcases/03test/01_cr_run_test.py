"""課題3用テスト

合否の条件は従来のままである。変えたのは、落ちたときに何を見せるか。
"""

import re
from pathlib import Path
import glob
import pytest

from lpp_collector import testkit
from lpp_collector.config import TEST_BASE_DIR

TARGET = "cr"

TEST_EXPECT_DIR = Path(__file__).parent / Path("test_expects")

test_data = sorted(glob.glob(f"{TEST_BASE_DIR}/input0[123]/*.mpl", recursive=True))

paramed_test_data = [
    pytest.param(mpl_file, id=Path(mpl_file).name) for mpl_file in test_data
]

NOTE = "空白をすべて取り除き、1 行目を捨てて辞書順に並べ替えてから比較しています"


def expects_error(mpl_file) -> bool:
    """エラーが出ることを期待している入力か (sample0* がそれ)。"""
    return bool(re.search(r"sample0", str(mpl_file)))


def analyze(mpl_file):
    """cr を 1 回動かして、比較に使う形にそろえる。

    返り値は (実行の記録, 比較に使う行, エラーとして扱うか)。
    """
    executed = testkit.run_target(TARGET, mpl_file)
    if executed.stderr:
        return executed, executed.stderr.splitlines(), True
    lines = [re.sub(r"\s", r"", line) for line in executed.stdout.splitlines()]
    if not lines:
        # 標準出力も標準エラー出力も空。従来もここはエラー扱いだった
        return executed, [], True
    lines.pop(0)  # 1行目を捨てる
    lines.sort()
    return executed, lines, False


@pytest.mark.timeout(10)
@pytest.mark.parametrize(("mpl_file"), paramed_test_data)
def test_cr_run(mpl_file):
    """準備したテストケースを全て実行する．"""
    stem = Path(mpl_file).stem
    out_file = testkit.result_dir() / (stem + ".out")

    executed, lines, errored = analyze(mpl_file)
    testkit.save_raw(stem, executed)
    testkit.save_lines(out_file, lines)

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

    if not expects_error(mpl_file):
        head = executed.stderr.strip().splitlines()
        testkit.fail(
            "unexpected_error",
            input=mpl_file,
            fields=[
                ("あなた", head[0] if head else "(標準出力も標準エラー出力も空です)"),
                ("期待", "この入力は正しいので、表を出力する"),
            ],
            executed=executed,
            hint=testkit.rerun_hint(mpl_file),
        )

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
