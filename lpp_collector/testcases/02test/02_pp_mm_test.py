"""課題2用メタモーフィックテスト

自分自身が生成したソースコードを読み込ませると、同じものが出てくるはず。
合否の条件は従来のままである。変えたのは、落ちたときに何を見せるか。
"""

from pathlib import Path
import glob
import pytest

from lpp_collector import testkit
from lpp_collector.config import TEST_BASE_DIR

TARGET = "pp"

# エラーが出ないことが期待されるデータのみ
test_valid_data = sorted(
    glob.glob(f"{TEST_BASE_DIR}/input0[12]/sample[!0]*.mpl", recursive=True)
)

paramed_test_data = [
    pytest.param(mpl_file, id=Path(mpl_file).name) for mpl_file in test_valid_data
]


def analyze(source):
    """pp を 1 回動かす。返り値は (実行の記録, 出力の行, エラーか)。"""
    executed = testkit.run_target(TARGET, source)
    if executed.stderr:
        return executed, executed.stderr.splitlines(), True
    return executed, executed.stdout.splitlines(), False


def _broken(mpl_file, executed, stage):
    head = executed.stderr.strip().splitlines()
    testkit.fail(
        "idempotency",
        input=mpl_file,
        fields=[
            ("どこで", stage),
            ("あなた", head[0] if head else "(何も出力されませんでした)"),
            ("期待", "整形した結果をもう一度 pp に通しても、同じものが出る"),
        ],
        executed=executed,
        hint=testkit.rerun_hint(mpl_file),
    )


@pytest.mark.timeout(10)
@pytest.mark.parametrize(("mpl_file"), paramed_test_data)
def test_idempotency(mpl_file):
    """メタモーフィックテストによって，冪等性を確認"""
    stem = Path(mpl_file).stem
    out_file = testkit.result_dir() / (stem + ".out")
    out2_file = testkit.result_dir() / (stem + ".out2")

    # 1回目の実行
    executed, lines, errored = analyze(mpl_file)
    testkit.save_lines(out_file, lines)
    if errored:
        # エラーになるわけがないテストデータのみを与えるので、ここはダメ
        _broken(mpl_file, executed, "1 回目 (元のファイル)")

    # 2回目の実行。1 回目の出力をそのまま読ませる
    executed2, lines2, errored2 = analyze(out_file)
    testkit.save_lines(out2_file, lines2)
    if errored2:
        # 自分が出したものを自分で読めない
        _broken(mpl_file, executed2, "2 回目 (自分が整形したファイル)")

    testkit.compare_or_fail(
        lines2,
        lines,
        input=mpl_file,
        executed=executed2,
        kind="idempotency",
        actual_label="2 回目",
        expected_label="1 回目",
        notes=[
            "整形した結果をもう一度 pp に通すと、同じものが出てこなければ"
            "なりません",
            f"比べたもの: {testkit.short_path(out2_file)} と "
            f"{testkit.short_path(out_file)}",
        ],
        hint=testkit.rerun_hint(mpl_file),
    )
