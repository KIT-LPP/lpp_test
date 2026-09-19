# PYTHON_ARGCOMPLETE_OK

import subprocess
import sys
from typing import List
from lpp_collector.config import (
    LPP_DATA_DIR,
    TEST_BASE_DIR,
    IS_DOCKER_ENV,
)
import argcomplete, argparse
import glob
from pathlib import Path
from .docker import fix_permission, run_test_container, run_debug_build, update
from .submit import is_full_suite_run, offer_after_test
from .version import warn_on_version_skew
import os
import uuid


all_testcases = [
    Path(testcase)
    for testcase in glob.glob(f"{TEST_BASE_DIR}/**/*_test.py", recursive=True)
]
all_testsuite_list = sorted(set([testcase.parent.name for testcase in all_testcases]))


base_parser = argparse.ArgumentParser(add_help=False)
base_parser.add_argument(
    "--run-pytest",
    action="store_true",
    help="Enforce running pytest even though not in Docker environment",
)
base_parser.add_argument(
    "--update",
    action="store_true",
    help="Update Docker image and exit",
)
base_parser.add_argument(
    "testsuite", choices=all_testsuite_list, help="Specify testsuite"
)

full_parser = argparse.ArgumentParser(parents=[base_parser])
specified_testsuite = base_parser.parse_known_args()[0].testsuite

if specified_testsuite in all_testsuite_list:
    all_testcases = [
        testcase
        for testcase in all_testcases
        if testcase.parent.name == specified_testsuite
    ]
    full_parser.add_argument(
        "testcases",
        choices=[testcase.name for testcase in all_testcases] + ["all"],
        help="Specify testcase to run",
        default="all",
        nargs="?",
    )
else:
    full_parser.add_argument(
        "testcases",
        help="Specify testcase to run",
        choices=["all"],
        default="all",
        nargs="?",
    )

full_parser.add_argument("pytest_args", nargs=argparse.REMAINDER)

argcomplete.autocomplete(full_parser)


def run_pytest(args):
    testsuite: str = args.testsuite
    testcases = [
        testcase for testcase in all_testcases if testcase.parent.name == testsuite
    ]

    if len(testcases) == 0:
        print(f"No testcases found in {testsuite}")
        return

    specified_testcases: List[str] = [args.testcases]

    if "all" not in specified_testcases:
        testcases = [
            testcase for testcase in testcases if testcase.name in specified_testcases
        ]

    # Sort testcases by name
    testcase_paths = sorted([str(testcase.absolute()) for testcase in testcases])

    # print(f"Running pytest with {testcase_paths}")

    pwd = os.getcwd()
    os.environ["LPP_TARGET_PATH"] = pwd
    # 課題名はここでしか分からない。プラグインは環境変数で受け取る。
    # 従来はプラグインが "a" という固定値を申告しており、どの課題の試行か
    # サーバ側で区別できなかった
    os.environ["LPP_TESTSUITE"] = testsuite
    # 今回の実行の印。提出を持ちかけるかどうかは、プラグインが残した控えが
    # 今回のものかで決める。pytest が途中で殺されると控えは前回のまま残る
    session_id = str(uuid.uuid4())
    os.environ["LPP_SESSION_ID"] = session_id
    subprocess.call(
        [
            "pytest",
            *args.pytest_args,
            *testcase_paths,
        ],
        cwd=TEST_BASE_DIR,
    )
    os.chdir(pwd)

    # 合否はプラグインが集めた結果で見る。pytest の終了コードではなく
    # 控えの all_passed を唯一の根拠にする。ただし一部だけを走らせたときは
    # 尋ねない。集めた結果は走らせた分のものなので、コンパイルのテストだけを
    # 通しても「すべてのテストが通りました」になってしまう
    if is_full_suite_run(args.testcases, args.pytest_args):
        try:
            offer_after_test(session_id)
        except Exception as e:  # noqa: BLE001 - テストの結果は出したまま理由を見せる
            print(f"[lpp] 提出の確認に失敗しました: {e}")


def main():
    args = full_parser.parse_args()

    if not os.path.exists(LPP_DATA_DIR):
        os.mkdir(LPP_DATA_DIR)
    # print(args)
    if args.update:
        update(True)
        return

    if args.run_pytest or IS_DOCKER_ENV:
        if IS_DOCKER_ENV:
            # 学生が普段使うのは lpptest なので、版のずれはここで知らせる
            warn_on_version_skew()
        run_pytest(args)
    else:
        if "LPP_DOCKER_BASE" in os.environ:
            run_debug_build(os.environ["LPP_DOCKER_BASE"])
        else:
            update()
        run_test_container(["lpptest", *sys.argv[1:]])

    if IS_DOCKER_ENV:
        # Fix permissions
        fix_permission()


def main_PYTHON_ARGCOMPLETE_OK():
    main()
