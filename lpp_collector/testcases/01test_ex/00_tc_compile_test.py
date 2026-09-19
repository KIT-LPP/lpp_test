"""課題1拡張用コンパイルテスト"""

from lpp_collector import testkit

TARGET = "tc"


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
