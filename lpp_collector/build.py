"""課題のビルドと、その診断の記録。

判定は **終了コード** で行う。従来は `assert not serr` だったので、警告が
stderr に出ただけで「コンパイル失敗」と判定されていた。

診断は `-fdiagnostics-format=json` で受け取る。プラグインは
`take_build_record()` で 1 回だけ取り出し、試行と一緒に送る。
"""

import json
import os
import subprocess
from typing import Any, Dict, List, Optional, Tuple

# gcc は翻訳単位ごとに JSON の配列を 1 行ずつ stderr へ出す
DIAGNOSTICS_FLAG = "-fdiagnostics-format=json"

_record: Optional[Dict[str, Any]] = None


class BuildResult:
    def __init__(self, exit_code: int, stdout: str, stderr: str, command: str):
        self.exit_code = exit_code
        self.stdout = stdout
        self.stderr = stderr
        self.command = command

    @property
    def ok(self) -> bool:
        return self.exit_code == 0

    @property
    def message(self) -> str:
        return f"$ {self.command}\n(exit {self.exit_code})\n{self.stderr}".strip()


def parse_diagnostics(stderr: str) -> Tuple[List[Any], str]:
    """JSON の診断と、それ以外の行に分ける。

    `make` の出力や、JSON にならないリンカのエラーが混ざるので、
    読めた分だけ構造として持ち、残りは文字列で残す。
    """
    diagnostics: List[Any] = []
    rest: List[str] = []
    for line in stderr.splitlines():
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            try:
                parsed = json.loads(stripped)
            except ValueError:
                rest.append(line)
                continue
            if isinstance(parsed, list):
                diagnostics.extend(parsed)
                continue
        rest.append(line)
    return diagnostics, "\n".join(rest)


def compile_target(target: str, source_dir: str) -> BuildResult:
    """課題をビルドし、結果を記録する。

    Makefile がある場合も `CC` に診断つきの gcc を渡す。`$(CC)` を使っていない
    Makefile では何も変わらず、使っていれば診断が構造で取れる。学生が `CC` を
    自分で設定している場合はこちらが優先されるが、この課題では gcc 固定で困らない。
    """
    if os.path.isfile(os.path.join(source_dir, "Makefile")) or os.path.isfile(
        os.path.join(source_dir, "makefile")
    ):
        command = f'make CC="gcc {DIAGNOSTICS_FLAG}"'
    else:
        # -w は付けない。警告は失敗ではないが、消すと診断が何も残らない
        command = f"gcc {DIAGNOSTICS_FLAG} -o {target} *.c"

    proc = subprocess.run(
        command,
        shell=True,
        check=False,
        cwd=source_dir,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        universal_newlines=True,
    )
    diagnostics, text = parse_diagnostics(proc.stderr or "")

    global _record
    _record = {
        "exit": proc.returncode,
        "diagnostics": {
            "command": command,
            "gcc": diagnostics,
            "stderr": text or None,
            "stdout": (proc.stdout or "")[:10000] or None,
        },
    }

    # 学生の画面には人が読める側だけ出す
    if text:
        print(text)

    return BuildResult(proc.returncode, proc.stdout or "", text, command)


def take_build_record() -> Optional[Dict[str, Any]]:
    """記録を 1 回だけ取り出す。同じセッションで二重に送らない。"""
    global _record
    record, _record = _record, None
    return record
