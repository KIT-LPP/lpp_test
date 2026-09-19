"""課題のテストが共通で使う道具。

従来は `command()` と `common_task()` が課題ごとに複製されていて (11 ファイル)、
どれも `returncode` を捨てていた。異常終了に気付けていたのは、シェルが
"Segmentation fault" を stderr に書いていたからで、「正しい入力なのにエラーを
出した」と区別が付かなかった。ここに 1 つだけ置いて、終了コードを残す。

**合否の条件はここでは変えない。** 終了コードは失敗の**分類**にだけ使う。
例えば課題1では、エラーが出ることを期待する入力で異常終了すると今は通る。
それを落とすのは採点の変更であって、結果の見やすさの改修ではない。

失敗は `fail()` から `pytest.fail(..., pytrace=False)` で送る。テストの
仕組みの traceback (`common_task` や `zip_longest`) は学生の助けにならず、
1 回の実行で 1400 行になっていた。ここで組み立てた日本語の説明がそのまま
画面にも出て、サーバにも `longrepr` として残る。
"""

import os
import re
import signal
import subprocess
import unicodedata
from pathlib import Path
from typing import Any, Dict, List, NoReturn, Optional, Sequence, Tuple

import pytest

from .config import TARGETPATH, TEST_BASE_DIR

# 実行の上限 (秒)。テストに付いている `@pytest.mark.timeout(10)` より短くする。
# pytest-timeout の SIGALRM に先を越されると、こちらの「時間内に終わりません」
# ではなく pytest の traceback になる
DEFAULT_TIMEOUT = 9.0

# 失敗の分類。表示のまとめ方と、どれを先に見せるかがこれで決まる
KIND_TITLES = {
    "compile": "コンパイルが通りません",
    "cli": "引数の扱いが仕様と違います",
    "not_found": "実行ファイルがありません",
    "timeout": "時間内に終わりません",
    "encoding": "出力が文字として読めません",
    "crash": "異常終了しました",
    "assemble": "アセンブルが通りません",
    "comet2": "COMET II で実行できません",
    "missing_csl": ".csl が作られていません",
    "unexpected_error": "正しい入力でエラーが出ます",
    "missing_error": "エラーが出るべき所で出ません",
    "no_output": "何も出力されません",
    "idempotency": "2 回通すと結果が変わります",
    "output_mismatch": "出力が期待と違います",
    "error_line": "エラーの行番号が違います",
    "no_line_number": "エラーに行番号がありません",
    "harness": "テストの用意が足りません",
    "other": "失敗しました",
}


# ---------------------------------------------------------------------------
# 実行
# ---------------------------------------------------------------------------
class Run:
    """1 回の実行の記録。"""

    def __init__(
        self,
        command: str,
        returncode: int,
        stdout: str,
        stderr: str,
        timed_out: bool = False,
        timeout: Optional[float] = None,
    ):
        self.command = command
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.timed_out = timed_out
        self.timeout = timeout

    @property
    def crashed(self) -> bool:
        """シグナルで死んだか。

        `shell=True` なので、シェルが最後のコマンドを exec したときは負の値、
        フォークしたときは 128+N が返る。どちらの形も見る。

        128 より大きければ全部シグナル、とはしない。`exit(-1)` を書いた
        プログラムは 255 を返すので、「シグナル 127 で停止」になってしまう。
        実在するシグナルの範囲 (SIGRTMAX は 64) に収まるものだけを見る。
        """
        return self.returncode < 0 or 128 < self.returncode <= 128 + 64

    @property
    def signal_number(self) -> Optional[int]:
        if not self.crashed:
            return None
        if self.returncode < 0:
            return -self.returncode
        return self.returncode - 128

    @property
    def signal_name(self) -> str:
        number = self.signal_number
        if number is None:
            return ""
        try:
            return signal.Signals(number).name
        except ValueError:
            return f"signal {number}"

    @property
    def not_found(self) -> bool:
        """実行ファイルが無い。コンパイルが通っていないときに出る。"""
        return self.returncode == 127

    @property
    def kind(self) -> Optional[str]:
        """終了の仕方から分かる分類。分からなければ None。"""
        if self.timed_out:
            return "timeout"
        if self.not_found:
            return "not_found"
        if self.crashed:
            return "crash"
        return None


def run(command: str, timeout: float = DEFAULT_TIMEOUT) -> Run:
    """コマンドを実行して、終了コードごと持ち帰る。

    `start_new_session` を付けるのは、時間切れのときに子孫ごと殺すためである。
    `shell=True` では `subprocess` が殺せるのはシェルだけで、シェルが
    フォークしていると、生き残った孫がパイプを掴んだまま communicate() が
    返らない。
    """
    proc = subprocess.Popen(
        command,
        shell=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        universal_newlines=True,
        start_new_session=True,
    )
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
        return Run(command, proc.returncode, stdout, stderr, timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill(proc)
        stdout, stderr = proc.communicate()
        return Run(
            command,
            proc.returncode,
            stdout or "",
            stderr or "",
            timed_out=True,
            timeout=timeout,
        )
    except UnicodeDecodeError:
        # 出力の取り込みは UTF-8 の決め打ちで、読めないバイトがあると
        # ここで落ちる。従来もテストは落ちていたが、学生に見えるのは
        # subprocess の中の traceback だった
        _kill(proc)
        proc.wait()
        fail(
            "encoding",
            fields=[("実行", command)],
            notes=[
                "UTF-8 として読めないバイトが出力に含まれています",
                "初期化していない変数や、配列の範囲外を書いていないか確かめてください",
            ],
        )
    except BaseException:
        # Ctrl-C もここを通る。`start_new_session` で別のセッションにいる
        # ので、端末の SIGINT は子には届かない。置き去りにしない
        _kill(proc)
        proc.wait()
        raise


def _kill(proc) -> None:
    """子孫ごと殺す。`shell=True` ではシェルだけ殺しても孫が残る。"""
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except OSError:
        proc.kill()


def target_path(target: str) -> Path:
    return Path(TARGETPATH) / Path(target)


def run_target(target: str, *args: Any, timeout: float = DEFAULT_TIMEOUT) -> Run:
    """課題の実行ファイルを動かす。"""
    parts = [str(target_path(target))] + [str(arg) for arg in args]
    return run(" ".join(parts), timeout=timeout)


# ---------------------------------------------------------------------------
# 出力の保存
# ---------------------------------------------------------------------------
def result_dir() -> Path:
    directory = Path(TARGETPATH) / "test_results"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def save_text(path, text: str) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def save_lines(path, lines: Sequence[str], newline: bool = True) -> Path:
    body = "".join(line + ("\n" if newline else "") for line in lines)
    return save_text(path, body)


def save_raw(stem: str, executed: Run) -> None:
    """プログラムが実際に吐いたものを、整形前のまま残す。

    比較に使う `.out` は空白を潰したり並べ替えたりした後の姿で、学生が
    自分の出力を探しても見つからない。生のものを隣に置く。
    """
    directory = result_dir()
    save_text(directory / f"{stem}.raw.stdout", executed.stdout)
    save_text(directory / f"{stem}.raw.stderr", executed.stderr)


def save_expected(stem: str, expect_file) -> Optional[Path]:
    """期待値をテスト環境の中から持ち出して、出力の隣に置く。

    オラクルはコンテナの中にしかないので、そのままでは学生が `diff` を
    取れない。
    """
    try:
        text = Path(expect_file).read_text(encoding="utf-8")
    except OSError:
        return None
    return save_text(result_dir() / f"{stem}.expected", text)


def rerun_hint(input: Any) -> str:
    """同じ入力を手元で動かす案内。サニタイザ付きなので原因が出ることがある。"""
    return f"lpprun {short_path(input)}"


def short_path(path) -> str:
    """テスト環境の中の長い絶対パスを、読める形に縮める。"""
    text = str(path)
    base = str(TEST_BASE_DIR)
    if text.startswith(base):
        rest = text[len(base) :].lstrip("/")
        # mklink が張る symlink。学生が lppshell で入ったときに見える名前
        return f"/lpp_test/{rest}" if os.path.exists("/lpp_test") else rest
    if text.startswith(str(TARGETPATH)):
        rest = text[len(str(TARGETPATH)) :].lstrip("/")
        return rest or "."
    return text


# ---------------------------------------------------------------------------
# 失敗
# ---------------------------------------------------------------------------
# 直前の失敗の中身。プラグイン (lpp_collector/__init__.py) が
# `pytest_runtest_logreport` で 1 回だけ取り出し、まとめの材料にする。
# テストは 1 つずつ順に走るので、受け渡しはこれで足りる
_last_failure: Optional[Dict[str, Any]] = None


def take_last_failure() -> Optional[Dict[str, Any]]:
    global _last_failure
    detail, _last_failure = _last_failure, None
    return detail


def display_width(text: str) -> int:
    """端末での幅。日本語は 1 文字で 2 桁を占める。"""
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in text)


def pad(text: str, width: int) -> str:
    return text + " " * max(0, width - display_width(text))


# ラベルの桁。「次の一手」(8 桁) が一番長い
LABEL_WIDTH = 10


def _render(detail: Dict[str, Any]) -> str:
    lines = [detail["title"]]
    for label, value in detail.get("fields", []):
        if value is None or value == "":
            continue
        text = str(value)
        if "\n" in text:
            lines.append(f"  {label}:")
            lines.extend("    " + part for part in text.splitlines())
        else:
            lines.append(f"  {pad(label, LABEL_WIDTH)}: {text}")
    for note in detail.get("notes", []):
        lines.append(f"  ※ {note}")
    return "\n".join(lines)


def fail(
    kind: str,
    *,
    input: Any = None,
    fields: Optional[List[Tuple[str, Any]]] = None,
    notes: Optional[List[str]] = None,
    executed: Optional[Run] = None,
    hint: Optional[str] = None,
    title: Optional[str] = None,
) -> NoReturn:
    """テストを落とす。学生が読む形の説明を付けて。

    `executed` を渡すと、終了の仕方 (異常終了・時間切れ・実行ファイルが無い)
    が分類より優先される。合否は変わらない。何が起きたかの名前が変わるだけで、
    ここを飛ばすと「出力が期待と違います」の中に segfault が紛れる。
    """
    global _last_failure

    if executed is not None and executed.kind is not None:
        kind = executed.kind

    fields = list(fields or [])
    notes = list(notes or [])

    if kind == "not_found" and executed is not None:
        # 呼び出し側は「正しい入力なのにエラーが出た」のつもりで説明を
        # 組み立てているが、起きているのは実行ファイルが無いことである。
        # `lpprun` を勧めても始まらないので、ここで差し替える
        wanted = Path(executed.command.split()[0]).name
        fields = [("必要なもの", f"課題のディレクトリに {wanted} という名前の実行ファイル")]
        notes = [
            "Makefile を使っているときは、作る実行ファイルの名前を確かめてください",
        ]
        hint = None

    if input is not None:
        fields.insert(0, ("入力", short_path(input)))
    if executed is not None:
        if executed.timed_out:
            fields.append(
                (
                    "状態",
                    f"{executed.timeout or DEFAULT_TIMEOUT:.0f} 秒で終わらないので"
                    "打ち切りました",
                )
            )
        elif executed.crashed:
            fields.append(("終了", f"シグナル {executed.signal_name} で停止"))
        elif executed.returncode != 0:
            fields.append(("終了", f"終了コード {executed.returncode}"))

    if hint:
        fields.append(("次の一手", hint))

    detail: Dict[str, Any] = {
        "kind": kind,
        "title": title or KIND_TITLES.get(kind, KIND_TITLES["other"]),
        "input": short_path(input) if input is not None else None,
        "fields": fields,
        "notes": notes,
        "returncode": executed.returncode if executed is not None else None,
        "not_found": executed.not_found if executed is not None else False,
    }
    text = _render(detail)
    detail["text"] = text
    _last_failure = detail
    pytest.fail(text, pytrace=False)


# ---------------------------------------------------------------------------
# 比較
# ---------------------------------------------------------------------------
class Mismatch:
    def __init__(self, line_no: int, actual, expected, actual_len: int, expected_len: int):
        self.line_no = line_no
        self.actual = actual
        self.expected = expected
        self.actual_len = actual_len
        self.expected_len = expected_len

    @property
    def where(self) -> str:
        if self.actual_len == self.expected_len:
            return f"{self.line_no} 行目"
        return (
            f"{self.line_no} 行目 "
            f"(あなた {self.actual_len} 行 / 期待 {self.expected_len} 行)"
        )


def first_mismatch(
    actual: Sequence[str], expected: Sequence[str], fill: Optional[str] = ""
) -> Optional[Mismatch]:
    """最初に食い違う行を探す。

    `fill` は行数が違うときの埋め草で、従来の `zip_longest` の引数をそのまま
    引き継ぐ。課題1拡張だけ埋め草が None で、末尾の空行の扱いが変わるため、
    既定値に寄せずに呼び出し側から渡す。
    """
    import itertools

    for index, (a, e) in enumerate(itertools.zip_longest(actual, expected, fillvalue=fill), 1):
        if a != e:
            return Mismatch(index, a, e, len(actual), len(expected))
    return None


def reject_abnormal_exit(
    executed: Run, *, input: Any = None, hint: Optional[str] = None
) -> None:
    """終わり方そのものが異常なものは、エラーが出ていても通さない。

    エラーを期待する入力 (`sample0*`) の判定は「標準エラー出力に何か出たか」
    だけを見ていた。シェルが書いた "Segmentation fault" もエラーの報告と
    数えられるので、**落ちた**プログラムが通っていた。実行ファイルが 1 つも
    無くても、`not found` がエラーの報告として通っていた。
    """
    if executed.kind is None:
        return

    head = executed.stderr.strip().splitlines()
    fields: List[Tuple[str, Any]] = []
    if executed.kind != "not_found" and head:
        fields.append(("あなた", head[0]))
    fail(
        executed.kind,
        input=input,
        fields=fields,
        notes=["誤りを報告するはずの入力でも、プログラム自体が落ちてはいけません"]
        if executed.kind == "crash"
        else None,
        executed=executed,
        hint=hint,
    )


def _shown(value) -> str:
    if value is None:
        return "(行がありません)"
    if value == "":
        return "(空行)"
    return value


def compare_or_fail(
    actual_lines: Sequence[str],
    expected_lines: Sequence[str],
    *,
    input: Any = None,
    stem: Optional[str] = None,
    fill: Optional[str] = "",
    notes: Optional[List[str]] = None,
    executed: Optional[Run] = None,
    hint: Optional[str] = None,
    kind: str = "output_mismatch",
    actual_label: str = "あなた",
    expected_label: str = "期待",
) -> None:
    """期待と突き合わせ、違っていれば落とす。条件は従来のままである。"""
    mismatch = first_mismatch(actual_lines, expected_lines, fill=fill)
    if mismatch is None:
        return

    fields: List[Tuple[str, Any]] = [
        ("位置", mismatch.where),
        (actual_label, _shown(mismatch.actual)),
        (expected_label, _shown(mismatch.expected)),
    ]
    if stem:
        directory = result_dir()
        fields.append(
            (
                "見比べ",
                "diff {} {}".format(
                    short_path(directory / f"{stem}.out"),
                    short_path(directory / f"{stem}.expected"),
                ),
            )
        )
    fail(
        kind,
        input=input,
        fields=fields,
        notes=notes,
        executed=executed,
        hint=hint,
    )


# ファイル名やパスらしい語。この中の数字を行番号と読んではならない
_PATHISH = re.compile(r"/|\.(?:mpl|csl|c|h)(?::|$)")
# gcc のように `file.c:12:` と付けているときの行番号
_LINE_SUFFIX = re.compile(r":(\d+)(?::|$)")


def error_line_number(text: str) -> Optional[int]:
    """エラーの文言から行番号を拾う。

    最初の数字をそのまま行番号とみなしてはならない。入力のパスを表示する
    プログラムでは、パスの中の数字が拾われる。テスト環境のパスには
    `python3` が入るので、**どんな入力でも 3 行目と報告したこと**になり、
    3 行目にエラーのある課題だけ通っていた。

    語ごとに見て、パスらしい語は飛ばす。ただし `file.c:12:` の形で
    行番号を付けているものは、その数字を拾う。
    """
    for token in (text or "").split():
        if _PATHISH.search(token):
            suffix = _LINE_SUFFIX.search(token)
            if suffix:
                return int(suffix.group(1))
            continue
        found = re.search(r"\d+", token)
        if found:
            return int(found.group())
    return None


def compare_error_line_or_fail(
    actual_text: str,
    expected_text: str,
    *,
    input: Any = None,
    executed: Optional[Run] = None,
    hint: Optional[str] = None,
) -> None:
    """エラーの行番号を突き合わせる。前後 1 行までは許す (従来どおり)。"""
    actual = error_line_number(actual_text)
    expected = error_line_number(expected_text)

    if actual is None:
        head = (actual_text or "").strip().splitlines()
        fail(
            "no_line_number",
            input=input,
            fields=[("あなた", head[0] if head else "(出力なし)")],
            notes=["エラーには、誤りのある行の番号を入れてください"],
            executed=executed,
            hint=hint,
        )
    if expected is None:
        fail(
            "harness",
            input=input,
            fields=[("期待値", "行番号が読み取れません")],
            executed=executed,
        )
    if not actual - 1 <= expected <= actual + 1:
        fail(
            "error_line",
            input=input,
            fields=[
                ("あなた", f"{actual} 行目"),
                ("期待", f"{expected} 行目 (前後 1 行までは許容)"),
                ("文言", (actual_text or "").strip().splitlines()[0] if actual_text.strip() else ""),
            ],
            executed=executed,
            hint=hint,
        )


def read_text_or_fail(path, *, input: Any = None) -> str:
    try:
        return Path(path).read_text(encoding="utf-8")
    except OSError:
        fail(
            "harness",
            input=input,
            fields=[("見つからないファイル", short_path(path))],
            notes=["テスト環境の不具合の可能性があります。担当教員に知らせてください"],
        )


# ---------------------------------------------------------------------------
# コンパイル
# ---------------------------------------------------------------------------
def compile_or_fail(target: str, source_dir: Optional[str] = None):
    """課題をビルドする。失敗したらコンパイラの出力をそのまま見せる。"""
    from .build import compile_target

    result = compile_target(target, source_dir or TARGETPATH)
    if result.ok:
        return result
    fail(
        "compile",
        fields=[
            ("コマンド", result.command),
            ("終了コード", result.exit_code),
            ("コンパイラの出力", result.stderr or "(出力なし)"),
        ],
        hint="上の行番号の箇所を直してから、もう一度 lpptest を実行してください",
    )
