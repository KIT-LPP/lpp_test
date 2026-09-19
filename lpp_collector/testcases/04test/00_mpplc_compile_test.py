"""課題4用コンパイルテスト"""

import os
import shutil

from lpp_collector import testkit
from lpp_collector.config import TEST_BASE_DIR

TARGET = "mpplc"


def test_compile():
    """指定ディレクトリでコンパイルができるかをテスト"""
    testkit.compile_or_fail(TARGET)


def test_no_param():
    """引数を付けずに実行するテスト"""
    executed = testkit.run_target(TARGET)
    testkit.reject_abnormal_exit(executed)
    if not executed.stderr:
        testkit.fail(
            "cli",
            fields=[
                ("実行", f"./{TARGET}  (引数なし)"),
                ("あなた", "何も知らせずに終わりました"),
                ("期待", "使い方などのエラーを標準エラー出力に出す"),
            ],
            executed=executed,
        )


def test_not_valid_file():
    """存在しないファイルを引数にした場合のテスト"""
    executed = testkit.run_target(TARGET, "hogehoge")
    testkit.reject_abnormal_exit(executed)
    if not executed.stderr:
        testkit.fail(
            "cli",
            fields=[
                ("実行", f"./{TARGET} hogehoge  (無いファイル)"),
                ("あなた", "何も知らせずに終わりました"),
                ("期待", "開けないことを標準エラー出力に出す"),
            ],
            executed=executed,
        )


def test_absolute_path_file():
    """絶対パスでファイルを指定した場合のテスト"""
    shutil.copy(f"{TEST_BASE_DIR}/input01/sample12.mpl", "/tmp/sample12.mpl")
    executed = testkit.run_target(TARGET, "/tmp/sample12.mpl")
    if os.path.isfile("./sample12.csl") or os.path.isfile("/tmp/sample12.csl"):
        return
    testkit.fail(
        "cli",
        fields=[
            ("実行", f"./{TARGET} /tmp/sample12.mpl"),
            ("あなた", "sample12.csl がどこにも作られませんでした"),
            ("期待", "絶対パスで渡されたファイルも読めて、.csl を作る"),
        ],
        executed=executed,
    )


def test_dotted_path_file():
    """ドットを含むパスでファイルを指定した場合のテスト

    従来この判定は、直前に自分でコピーした `test.success.mpl` があるかを
    見ており、しかも mpplc には別のファイル (`/tmp/sample12.mpl`) を
    渡していた。いつでも通る判定になっていたので、`test.success.mpl` を
    渡して `test.success.csl` ができるかを見るように直した。
    """
    source = "/tmp/test.success.mpl"
    shutil.copy(f"{TEST_BASE_DIR}/input01/sample12.mpl", source)
    # 前の実行の名残と取り違えない
    made = ["./test.success.csl", "/tmp/test.success.csl", "./test.csl", "/tmp/test.csl"]
    for path in made:
        if os.path.isfile(path):
            os.remove(path)

    executed = testkit.run_target(TARGET, source)

    if os.path.isfile("./test.success.csl") or os.path.isfile("/tmp/test.success.csl"):
        return
    if os.path.isfile("./test.csl") or os.path.isfile("/tmp/test.csl"):
        testkit.fail(
            "cli",
            fields=[
                ("実行", f"./{TARGET} {source}"),
                ("あなた", "test.csl が作られました"),
                ("期待", "test.success.csl (最後のドットから後ろだけが拡張子)"),
            ],
            notes=["ファイル名の最初のドットで切っていないか確かめてください"],
            executed=executed,
        )
    testkit.fail(
        "cli",
        fields=[
            ("実行", f"./{TARGET} {source}"),
            ("あなた", ".csl がどこにも作られませんでした"),
            ("期待", "ドットを含む名前でも読めて、test.success.csl を作る"),
        ],
        executed=executed,
    )
