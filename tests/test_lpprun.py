"""lpprun のビルドと実行。

実際に gcc を呼び、実際にプログラムを走らせる。サニタイザの報告や
シグナルでの終了は、作り物の入力では確かめられない。
"""

import os
import shutil

import pytest

from lpp_collector import lpprun

pytestmark = pytest.mark.skipif(
    shutil.which("gcc") is None, reason="gcc が要る"
)


def write(tmp_path, body, name="main.c"):
    (tmp_path / name).write_text(body)
    return str(tmp_path)


def build_and_run(tmp_path, body, args=(), timeout=30.0, sanitize=True):
    source_dir = write(tmp_path, body)
    binary = str(tmp_path / "out" / "a.out")
    os.makedirs(os.path.dirname(binary), exist_ok=True)
    outcome = lpprun.build(source_dir, binary, sanitize=sanitize)
    if not outcome.ok:
        return outcome, None
    return outcome, lpprun.run_program(binary, list(args), source_dir, timeout=timeout)


def test_a_clean_program_runs_and_its_output_is_captured(tmp_path):
    build, run = build_and_run(
        tmp_path, '#include <stdio.h>\nint main(void){ puts("hello"); return 0; }\n'
    )
    assert build.ok
    assert "-fsanitize=address,undefined" in build.flags
    assert run.exit_code == 0
    assert run.signal is None
    assert "hello" in run.stdout


def test_arguments_reach_the_program_and_the_cwd_is_the_source_dir(tmp_path):
    (tmp_path / "input.txt").write_text("from the file\n")
    build, run = build_and_run(
        tmp_path,
        """#include <stdio.h>
int main(int argc, char **argv){
  if (argc < 2) return 2;
  FILE *f = fopen(argv[1], "r");
  if (!f) return 3;
  char buf[64];
  if (fgets(buf, 64, f)) printf("%s", buf);
  return 0;
}
""",
        args=["input.txt"],
    )
    assert build.ok
    # 相対パスの引数が学生のディレクトリで解決されること
    assert run.exit_code == 0, run.stderr
    assert "from the file" in run.stdout


def test_a_heap_overflow_is_reported_by_the_sanitizer(tmp_path):
    build, run = build_and_run(
        tmp_path,
        """#include <stdlib.h>
int main(void){ char *p = malloc(4); p[7] = 'x'; free(p); return 0; }
""",
    )
    assert build.ok
    assert "AddressSanitizer" in run.stderr
    # ASan は既定でシグナルではなく終了コードで落ちる
    assert run.exit_code != 0


def test_signed_overflow_is_reported_with_a_location(tmp_path):
    build, run = build_and_run(
        tmp_path,
        """#include <stdio.h>
int main(void){ int x = 2147483647; x = x + 1; printf("%d\\n", x); return 0; }
""",
    )
    assert build.ok
    assert "runtime error" in run.stderr


def test_a_program_that_aborts_is_recorded_as_a_signal(tmp_path):
    build, run = build_and_run(
        tmp_path, "#include <stdlib.h>\nint main(void){ abort(); }\n", sanitize=False
    )
    assert build.ok
    # 負の終了コードとしてではなく、シグナルとして残す
    assert run.exit_code is None
    assert run.signal == 6


def test_a_runaway_program_is_cut_off(tmp_path):
    """改行を出さずに回り続けるプログラムでも流れ続けること。

    行で読むと 1 行も返らないので、打ち切りまで何も画面に出ず、
    そのあと溜まった全部が一度に出る。
    """
    build, run = build_and_run(
        tmp_path,
        "#include <stdio.h>\nint main(void){ for(;;) putchar('x'); }\n",
        timeout=1.5,
        sanitize=False,
    )
    assert build.ok
    assert run.timed_out
    assert len(run.stdout) > 0
    # 端末のメモリを埋めない
    assert len(run.stdout) <= lpprun.MAX_OUTPUT_BYTES + 4096


def test_a_leak_report_is_captured_whole(tmp_path):
    """LeakSanitizer の報告は終了の直後に出る。

    読み切る前に諦めると、切れた報告が完全なものとして記録される。
    """
    build, run = build_and_run(
        tmp_path,
        """#include <stdlib.h>
#include <stdio.h>
static void leak(int n){ void *p = malloc(32 + n); if (!p) return; }
int main(void){
  for (int i = 0; i < 40; i++) leak(i);
  printf("done\\n");
  return 0;
}
""",
    )
    assert build.ok
    assert "LeakSanitizer" in run.stderr
    # 末尾まで読めていること
    assert "SUMMARY: AddressSanitizer:" in run.stderr
    # 報告の最後の行まで届いていること
    last = [line for line in run.stderr.splitlines() if line.strip()][-1]
    assert last.startswith("SUMMARY: AddressSanitizer:"), last
    assert "40 allocation(s)" in last


def test_a_prompt_appears_before_the_program_reads_stdin(tmp_path):
    """パイプ越しでも入力の促しが先に出ること。

    stdbuf は使えない。LD_PRELOAD で割り込むのでサニタイザと衝突する。
    ビルド時に setvbuf を差し込んでいる。
    """
    import select
    import subprocess

    source_dir = write(
        tmp_path,
        '#include <stdio.h>\nint main(void){ printf("name? "); char b[32];'
        ' if(!fgets(b,32,stdin)) return 2; printf("hi %s", b); return 0; }\n',
    )
    binary = str(tmp_path / "a.out")
    assert lpprun.build(source_dir, binary).ok

    proc = subprocess.Popen(
        [binary],
        cwd=source_dir,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        ready, _, _ = select.select([proc.stdout], [], [], 5.0)
        assert ready, "入力を待つ前に促しが出ていない"
    finally:
        out, _ = proc.communicate(b"bob\n")
    assert b"hi bob" in out


def test_a_broken_source_fails_the_build_and_keeps_the_diagnostics(tmp_path):
    source_dir = write(tmp_path, "int main(void){ return }\n")
    outcome = lpprun.build(source_dir, str(tmp_path / "a.out"))

    assert not outcome.ok
    assert outcome.diagnostics["gcc"], "JSON の診断が取れていない"
    # 学生の画面にも残ること
    assert "error" in outcome.shown


def test_a_directory_without_sources_is_not_a_crash(tmp_path):
    outcome = lpprun.build(str(tmp_path), str(tmp_path / "a.out"))
    assert not outcome.ok
    assert ".c ファイルがありません" in outcome.shown


def test_the_record_carries_the_measurement_condition(tmp_path):
    from datetime import datetime

    from lpp_collector.device import LppDevice

    build, run = build_and_run(
        tmp_path, "#include <stdio.h>\nint main(void){ puts(\"x\"); return 0; }\n"
    )
    device = LppDevice(str(tmp_path / "device.json"))
    record = lpprun.make_record(
        device, datetime.now().astimezone(), build, run, ["./a.out"]
    )

    assert record["kind"] == "run"
    assert "-fsanitize=address,undefined" in record["buildFlags"]
    assert record["runtimeEnv"]["UBSAN_OPTIONS"] == "print_stacktrace=1"
    assert record["runExit"] == 0
    assert record["buildExit"] == 0
