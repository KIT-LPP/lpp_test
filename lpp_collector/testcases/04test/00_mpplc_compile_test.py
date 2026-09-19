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
    """ドットを含むパスでファイルを指定した場合のテスト"""
    shutil.copy(f"{TEST_BASE_DIR}/input01/sample12.mpl", "/tmp/test.success.mpl")
    executed = testkit.run_target(TARGET, "/tmp/sample12.mpl")
    if os.path.isfile("./test.success.mpl") or os.path.isfile("/tmp/test.success.mpl"):
        return
    if os.path.isfile("/tmp/test.csl") or os.path.isfile("./test.csl"):
        testkit.fail(
            "cli",
            fields=[
                ("あなた", "test.csl が作られました"),
                ("期待", "test.success.csl (最後のドットだけを拡張子とみなす)"),
            ],
            executed=executed,
        )
    testkit.fail(
        "cli",
        fields=[
            ("あなた", "ファイル名を指定した出力がありません"),
            ("期待", "ドットを含む名前でも読めて、.csl を作る"),
        ],
        executed=executed,
    )
