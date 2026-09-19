"""課題1用テスト

合否の条件は従来のままである。変えたのは、落ちたときに何を見せるか。
"""

import re
from pathlib import Path
import glob
import pytest

from lpp_collector import testkit
from lpp_collector.config import TEST_BASE_DIR

TARGET = "tc"

TEST_EXPECT_DIR = Path(__file__).parent / Path("test_expects")

test_data = sorted(glob.glob(f"{TEST_BASE_DIR}/input01/*.mpl", recursive=True))
paramed_test_data = [
    pytest.param(mpl_file, id=Path(mpl_file).name) for mpl_file in test_data
]

NOTE = "キーワードと符号の行だけを取り出し、辞書順に並べ替えてから比較しています"


def expects_error(mpl_file, stem) -> bool:
    """エラーが出ることを期待している入力か。

    名前が sample0* で、かつ期待するエラーが用意されているものに限る
    (従来どおり、中身が 3 バイト以下の .stderr は用意が無いものとみなす)。
    """
    expect_err_file = TEST_EXPECT_DIR / (stem + ".stderr")
    is_error_expected = expect_err_file.exists() and expect_err_file.stat().st_size > 3
    return bool(re.search(r"sample0", str(mpl_file))) and is_error_expected


def normalize(stdout: str):
    """比較に使う形にそろえる。"""
    out = []
    for line in stdout.splitlines():
        if re.search(r"Identifier", line):
            continue
        if re.search(r'\s*"\s*\S*\s*"\s*\d+\s*', line):
            formatted = re.sub(r'\s*"\s*(\S*)\s*"\s*(\d+)\s*', r'"\1"\t\2\n', line)
            out.append(formatted)
    out.sort()
    return out


@pytest.mark.timeout(10)
@pytest.mark.parametrize(("mpl_file"), paramed_test_data)
def test_run(mpl_file):
    """準備したテストケースを全て実行する．"""
    stem = Path(mpl_file).stem
    out_file = testkit.result_dir() / (stem + ".out")

    executed = testkit.run_target(TARGET, mpl_file)
    testkit.save_raw(stem, executed)

    if executed.stderr:
        testkit.save_lines(out_file, executed.stderr.splitlines())
        testkit.reject_abnormal_exit(
            executed, input=mpl_file, hint=testkit.rerun_hint(mpl_file)
        )
        if not expects_error(mpl_file, stem):
            head = executed.stderr.strip().splitlines()
            testkit.fail(
                "unexpected_error",
                input=mpl_file,
                fields=[
                    ("あなた", head[0] if head else ""),
                    ("期待", "この入力は正しいので、字句の表を出力する"),
                ],
                executed=executed,
                hint=testkit.rerun_hint(mpl_file),
            )
        if out_file.read_text(encoding="utf-8") == "":
            testkit.fail(
                "missing_error",
                input=mpl_file,
                fields=[("期待", "誤りのある行を知らせるエラーを出す")],
                executed=executed,
                hint=testkit.rerun_hint(mpl_file),
            )
        return

    # 従来と同じ中身を書き、書いたものを読み直して比べる
    testkit.save_text(out_file, "".join(normalize(executed.stdout)))
    expect_file = TEST_EXPECT_DIR / (stem + ".stdout")
    expected = testkit.read_text_or_fail(expect_file, input=mpl_file)
    testkit.save_expected(stem, expect_file)
    testkit.compare_or_fail(
        out_file.read_text(encoding="utf-8").splitlines(),
        expected.splitlines(),
        input=mpl_file,
        stem=stem,
        executed=executed,
        notes=[NOTE],
        hint=testkit.rerun_hint(mpl_file),
    )
