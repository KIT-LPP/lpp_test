"""課題4用コンパイル・実行テスト

合否の条件は従来のままである。変えたのは、落ちたときに何を見せるか。
"""

import json
import os
import re
from pathlib import Path
import glob
import pytest

from lpp_collector import testkit
from lpp_collector.config import TEST_BASE_DIR

TARGET = "mpplc"

TEST_EXPECT_DIR = Path(__file__).parent / Path("test_expects")
CASL2_FILE_DIR = "casl2"
# テスト環境では /casljs に入っている。手元で確かめるときだけ差し替える
C2C2 = Path(os.environ.get("LPP_CASLJS_DIR", "/casljs")) / Path("c2c2.js")

# @pytest.mark.timeout(15) の下で mpplc と node を 2 回動かす。
# それぞれの上限は、合わせてもマークを超えない値にする
STEP_TIMEOUT = 6.0

test_data = sorted(glob.glob(f"{TEST_BASE_DIR}/input*/*.mpl", recursive=True))
paramed_test_data = [
    pytest.param(mpl_file, id=Path(mpl_file).name) for mpl_file in test_data
]


def expects_error(mpl_file) -> bool:
    """エラーが出ることを期待している入力か (sample0* がそれ)。"""
    return bool(re.search(r"sample0", str(mpl_file)))


def find_csl(mpl_file):
    """mpplc が吐いた .csl を探す。置き場所は課題の実装次第で 2 通りある。"""
    csl_filename = Path(mpl_file).stem + ".csl"
    candidates = [
        Path(TEST_BASE_DIR) / Path(csl_filename),
        Path(mpl_file).parent / Path(csl_filename),
    ]
    return next((c for c in candidates if c.exists()), None)


def compile_task(mpl_file, out_file):
    """mpplc を動かして .csl を取り出す。エラーを期待する入力なら 1 を返す。"""
    executed = testkit.run_target(TARGET, mpl_file, timeout=STEP_TIMEOUT)

    if executed.stderr:
        if not expects_error(mpl_file):
            head = executed.stderr.strip().splitlines()
            testkit.fail(
                "unexpected_error",
                input=mpl_file,
                fields=[
                    ("あなた", head[0] if head else ""),
                    ("期待", "この入力は正しいので、CASL2 を出力する"),
                ],
                executed=executed,
                hint=testkit.rerun_hint(mpl_file),
            )
        testkit.save_lines(out_file, executed.stderr.splitlines())
        return 1, executed

    cslfile = find_csl(mpl_file)
    if cslfile is None:
        testkit.fail(
            "missing_csl",
            input=mpl_file,
            fields=[
                ("探した場所", f"{TEST_BASE_DIR} と {Path(mpl_file).parent}"),
                ("期待", f"{Path(mpl_file).stem}.csl を作る"),
            ],
            executed=executed,
            hint=testkit.rerun_hint(mpl_file),
        )

    casl2dir = Path(__file__).parent / Path(CASL2_FILE_DIR)
    casl2dir.mkdir(exist_ok=True)
    cslfile.rename(casl2dir / cslfile.name)
    return 0, executed


def node_lines(command, kind, mpl_file, note=None):
    """c2c2 を動かして出力の行を返す。0 以外で終わったらそこで落とす。"""
    executed = testkit.run(command, timeout=STEP_TIMEOUT)
    if executed.returncode != 0:
        head = (executed.stderr or executed.stdout).strip().splitlines()
        testkit.fail(
            kind,
            input=mpl_file,
            fields=[
                ("実行", command),
                ("あなた", head[0] if head else "(出力なし)"),
            ],
            notes=[note] if note else None,
            executed=executed,
        )
    return executed.stdout.splitlines(), executed


def execution_task(casl2_file, out_file, mpl_file):
    """c2c2 でアセンブルして実行する。"""
    assembler_text, _ = node_lines(
        f"node {C2C2} -n -c -a {casl2_file}",
        "assemble",
        mpl_file,
    )
    if "DEFINED SYMBOLS" not in assembler_text:
        testkit.save_lines(
            out_file, ["============ASSEMBLE ERROR=============="] + assembler_text
        )
        testkit.fail(
            "assemble",
            input=mpl_file,
            fields=[
                ("あなた", assembler_text[-1] if assembler_text else "(出力なし)"),
                ("期待", "アセンブルが通り、シンボルの表が出る"),
                ("出力", testkit.short_path(out_file)),
            ],
            notes=["出力した CASL2 が文法として通っていません"],
        )

    input_path = Path(__file__).parent / Path("input.json")
    with open(input_path, encoding="utf-8") as fp:
        inp = json.load(fp)
    inputparams = ""
    if Path(casl2_file).name in inp.keys():
        inputparams = " ".join(list(inp[Path(casl2_file).name]))

    terminal_text, _ = node_lines(
        f"node {C2C2} -n -q -r {casl2_file} {inputparams}",
        "comet2",
        mpl_file,
        note="アセンブルは通りましたが、実行が最後まで進みませんでした",
    )
    lines = [
        line
        for line in terminal_text
        if line.startswith("IN>") or line.startswith("OUT>")
    ]
    testkit.save_lines(out_file, lines)
    return lines


@pytest.mark.timeout(15)
@pytest.mark.parametrize(("mpl_file"), paramed_test_data)
def test_mpplc_run(mpl_file):
    """mpplcを実行する"""
    name = Path(mpl_file).name
    out_file = testkit.result_dir() / (name + ".out")

    res, executed = compile_task(mpl_file, out_file)

    if res == 0:
        casl2file = (
            Path(__file__).parent
            / Path(CASL2_FILE_DIR)
            / Path(Path(mpl_file).stem + ".csl")
        )
        if os.path.getsize(casl2file) == 0:
            testkit.fail(
                "missing_csl",
                input=mpl_file,
                fields=[
                    ("あなた", f"{casl2file.name} が空です"),
                    ("期待", "CASL2 のコードを書き出す"),
                ],
                executed=executed,
                hint=testkit.rerun_hint(mpl_file),
            )
        stem = casl2file.name
        out_file = testkit.result_dir() / (stem + ".out")
        lines = execution_task(casl2file, out_file, mpl_file)

        expect_file = TEST_EXPECT_DIR / (stem + ".out")
        expected = testkit.read_text_or_fail(expect_file, input=mpl_file)
        testkit.save_expected(stem, expect_file)
        testkit.compare_or_fail(
            lines,
            expected.splitlines(),
            input=mpl_file,
            stem=stem,
            notes=["COMET II で動かしたときの入出力 (IN>/OUT>) を比べています"],
            hint=testkit.rerun_hint(mpl_file),
        )
        return

    expect_file = TEST_EXPECT_DIR / (name + ".stderr")
    expected = testkit.read_text_or_fail(expect_file, input=mpl_file)
    testkit.save_expected(name, expect_file)
    testkit.compare_error_line_or_fail(
        out_file.read_text(encoding="utf-8"),
        expected,
        input=mpl_file,
        executed=executed,
        hint=testkit.rerun_hint(mpl_file),
    )
