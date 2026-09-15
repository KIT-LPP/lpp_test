"""ディレクトリにある C ファイルをビルドして実行する。

`lpptest` は公式スイートを走らせるもので、合否を判定する。`lpprun` は
学生が自分の入力で動かすためのもので、合否の概念がない。記録もサーバ側で
別に持つ (program_run)。

**サニタイザを既定で有効にする。** これは観測している問いを変える決定である。
「学生は未定義動作に気付くか」ではなく「サニタイザの指摘に対処するか」に
なる。条件を後から復元できるよう、ビルドのフラグと実行時の環境を記録に
必ず載せる。`--no-sanitize` で切れる。

合否の判定は `lpptest` の側で行い、そちらは計測を入れないままにする。
サニタイザはメモリ配置を変えるので、合否そのものが動きうる。
"""

import argparse
import os
import shutil
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime
from glob import glob
from typing import Any, Dict, List, Optional, Tuple

from .build import parse_diagnostics, render_diagnostics
from .config import IS_DOCKER_ENV, LPP_DATA_DIR, TARGETPATH
from .device import LppDevice
from .snapshot import snapshot
from .uploader import FOREGROUND_DEADLINE, Uploader

UNBUFFER_HEADER = os.path.join(os.path.dirname(__file__), "runtime", "lpp_unbuffer.h")

SANITIZERS = "address,undefined"
BASE_FLAGS = ["-g", "-O0", "-fno-omit-frame-pointer", "-Wall", "-Wextra"]

# サニタイザの条件も記録に残す。既定を変えたら学期をまたいで混ぜない
RUNTIME_ENV = {
    # UBSan は既定だと 1 行しか出さず、どこで起きたか分からない
    "UBSAN_OPTIONS": "print_stacktrace=1",
    # 継承ではなく明示する。記録した条件と実際の条件をずらさない
    "ASAN_OPTIONS": "detect_leaks=1:abort_on_error=0",
}

# 出力の上限。サーバ側でも切るが、無限ループの出力で端末のメモリを
# 埋めないようこちらでも止める
MAX_OUTPUT_BYTES = 64 * 1024
DEFAULT_TIMEOUT = float(os.environ.get("LPP_RUN_TIMEOUT", 300))


class BuildOutcome:
    def __init__(self, exit_code: int, flags: List[str], diagnostics: Dict[str, Any], shown: str):
        self.exit_code = exit_code
        self.flags = flags
        self.diagnostics = diagnostics
        self.shown = shown

    @property
    def ok(self) -> bool:
        return self.exit_code == 0


def build_flags(sanitize: bool) -> List[str]:
    flags = list(BASE_FLAGS)
    if sanitize:
        flags.append(f"-fsanitize={SANITIZERS}")
    return flags


def build(source_dir: str, out_path: str, sanitize: bool = True) -> BuildOutcome:
    """ディレクトリ直下の *.c をまとめてビルドする。

    Makefile は見ない。サニタイザはリンクにもフラグが要るので、`make CC=...`
    では入らない。学生の Makefile の成果物を上書きしないよう、出力は
    データディレクトリに置く。
    """
    sources = sorted(glob(os.path.join(source_dir, "*.c")))
    if not sources:
        return BuildOutcome(
            127, build_flags(sanitize), {}, f"{source_dir} に .c ファイルがありません"
        )

    flags = build_flags(sanitize)
    command = [
        "gcc",
        *flags,
        "-fdiagnostics-format=json",
        "-include",
        UNBUFFER_HEADER,
        "-o",
        out_path,
        *sources,
    ]
    proc = subprocess.run(
        command,
        check=False,
        cwd=source_dir,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        universal_newlines=True,
    )
    diagnostics, text = parse_diagnostics(proc.stderr or "")
    shown = "\n".join(part for part in (render_diagnostics(diagnostics), text) if part)

    return BuildOutcome(
        proc.returncode,
        flags,
        {
            "command": " ".join(command),
            "gcc": diagnostics,
            "stderr": text or None,
            "sources": [os.path.relpath(s, source_dir) for s in sources],
        },
        shown,
    )


class RunOutcome:
    def __init__(self):
        self.exit_code: Optional[int] = None
        self.signal: Optional[int] = None
        self.duration_ms = 0
        self.timed_out = False
        self.stdout = ""
        self.stderr = ""


def _pump(stream, sink, buffer: List[str], limit: List[int]):
    """子の出力を画面へ流しながら手元にも溜める。"""
    for chunk in iter(lambda: stream.readline(), b""):
        text = chunk.decode("utf-8", "replace")
        sink.write(text)
        sink.flush()
        if limit[0] > 0:
            buffer.append(text[: limit[0]])
            limit[0] -= len(text)
    stream.close()


def run_program(
    binary: str, args: List[str], cwd: str, timeout: float = DEFAULT_TIMEOUT
) -> RunOutcome:
    """ビルドした実行ファイルを走らせ、出力を捕まえる。

    標準入力は素通しにする。捕まえると対話的なプログラムが動かない。
    画面への表示は捕まえながら流す。バッファリングはビルド時に切ってある
    (lpp_unbuffer.h)。
    """
    outcome = RunOutcome()
    env = dict(os.environ)
    env.update(RUNTIME_ENV)

    started = time.monotonic()
    proc = subprocess.Popen(
        [binary, *args],
        cwd=cwd,
        stdin=None,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )

    out_buf: List[str] = []
    err_buf: List[str] = []
    threads = [
        threading.Thread(
            target=_pump, args=(proc.stdout, sys.stdout, out_buf, [MAX_OUTPUT_BYTES])
        ),
        threading.Thread(
            target=_pump, args=(proc.stderr, sys.stderr, err_buf, [MAX_OUTPUT_BYTES])
        ),
    ]
    for t in threads:
        t.daemon = True
        t.start()

    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        outcome.timed_out = True
        proc.kill()
        proc.wait()
    except KeyboardInterrupt:
        proc.kill()
        proc.wait()

    for t in threads:
        t.join(timeout=1.0)

    outcome.duration_ms = int((time.monotonic() - started) * 1000)
    if proc.returncode is not None and proc.returncode < 0:
        # シグナルで落ちた。負の終了コードとして記録しない
        outcome.signal = -proc.returncode
    else:
        outcome.exit_code = proc.returncode
    outcome.stdout = "".join(out_buf)
    outcome.stderr = "".join(err_buf)
    return outcome


# ---------------------------------------------------------------------------
def make_record(
    device: LppDevice,
    device_time: datetime,
    build_outcome: BuildOutcome,
    run_outcome: Optional[RunOutcome],
    argv: List[str],
    assignment: Optional[str],
) -> Dict[str, Any]:
    from . import runner_version

    return {
        "kind": "run",
        "idempotencyKey": str(uuid.uuid4()),
        "deviceId": device.device_id,
        "assignment": assignment,
        "deviceTime": device_time.isoformat(),
        "runnerVersion": runner_version(),
        "imageDigest": os.environ.get("LPP_IMAGE_DIGEST"),
        "buildFlags": build_outcome.flags,
        "runtimeEnv": RUNTIME_ENV,
        "argv": argv,
        "buildExit": build_outcome.exit_code,
        "buildDiagnostics": build_outcome.diagnostics,
        "runExit": run_outcome.exit_code if run_outcome else None,
        "runSignal": run_outcome.signal if run_outcome else None,
        "durationMs": run_outcome.duration_ms if run_outcome else None,
        "timedOut": run_outcome.timed_out if run_outcome else False,
        "stdout": run_outcome.stdout if run_outcome else None,
        "stderr": run_outcome.stderr if run_outcome else None,
    }


def collect(record: Dict[str, Any], device: LppDevice, source_dir: str):
    uploader = Uploader(device)
    try:
        uploader.enqueue(record, snapshot(source_dir))
    except Exception as e:  # noqa: BLE001
        print(f"[lpp] 実行を記録できませんでした: {e}")
        return
    uploader.flush(newest_first=True, deadline=time.monotonic() + FOREGROUND_DEADLINE)
    for message in uploader.errors:
        print(f"[lpp] {message}")


# ---------------------------------------------------------------------------
def build_parser(prog: str, build_only: bool) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=prog,
        description=(
            "ディレクトリの *.c をビルドします"
            if build_only
            else "ディレクトリの *.c をビルドして実行します"
        ),
    )
    parser.add_argument(
        "--no-sanitize",
        action="store_true",
        help="サニタイザを付けずにビルドする",
    )
    parser.add_argument(
        "--assignment",
        help="課題名 (01test など)。分かる場合だけ",
    )
    if not build_only:
        parser.add_argument(
            "--timeout",
            type=float,
            default=DEFAULT_TIMEOUT,
            help=f"実行の打ち切り (既定 {DEFAULT_TIMEOUT:.0f} 秒)",
        )
        parser.add_argument(
            "args", nargs=argparse.REMAINDER, help="プログラムに渡す引数"
        )
    return parser


def execute(argv: List[str], build_only: bool) -> int:
    parser = build_parser("lppc" if build_only else "lpprun", build_only)
    opts = parser.parse_args(argv)

    device = LppDevice()
    device_time = datetime.now().astimezone()
    source_dir = TARGETPATH

    work = os.path.join(LPP_DATA_DIR, "build")
    shutil.rmtree(work, ignore_errors=True)
    os.makedirs(work, exist_ok=True)
    binary = os.path.join(work, "a.out")

    outcome = build(source_dir, binary, sanitize=not opts.no_sanitize)
    if outcome.shown:
        print(outcome.shown)
    # 子プロセスは fd へ直接書くので、先にこちらを吐き出しておかないと
    # 画面での前後が入れ替わる
    sys.stdout.flush()

    run_outcome = None
    if outcome.ok and not build_only:
        args = [a for a in getattr(opts, "args", []) if a != "--"]
        run_outcome = run_program(binary, args, source_dir, timeout=opts.timeout)
        if run_outcome.timed_out:
            print(f"[lpp] {opts.timeout:.0f} 秒で打ち切りました")
        elif run_outcome.signal:
            print(f"[lpp] シグナル {run_outcome.signal} で終了しました")
    elif not outcome.ok:
        print("[lpp] ビルドに失敗しました")

    argv_recorded = ["./a.out", *[a for a in getattr(opts, "args", []) if a != "--"]]
    collect(
        make_record(device, device_time, outcome, run_outcome, argv_recorded, opts.assignment),
        device,
        source_dir,
    )

    if not outcome.ok:
        return 1
    if run_outcome is None:
        return 0
    if run_outcome.timed_out:
        return 124
    if run_outcome.signal:
        return 128 + run_outcome.signal
    return run_outcome.exit_code or 0


def _main(build_only: bool) -> int:
    if IS_DOCKER_ENV:
        from .docker import fix_permission
        from .version import warn_on_version_skew

        warn_on_version_skew()
        try:
            return execute(sys.argv[1:], build_only)
        finally:
            fix_permission()

    from .docker import run_test_container, update

    update()
    run_test_container([("lppc" if build_only else "lpprun"), *sys.argv[1:]])
    return 0


def main():
    sys.exit(_main(build_only=False))


def compile_main():
    """`lppc`。ビルドだけして走らせない。"""
    sys.exit(_main(build_only=True))
