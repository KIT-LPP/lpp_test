"""提出。

試行の送信は毎回行うが、提出はそれとは別の行為である。どの試行を提出と
するかは学生が決める。ここはその決定を `POST /api/submission` に送る。

入口は 2 つある。

- `lpptest` でテストがすべて通ったときの確認。「はい」と答えたときだけ送る。
  尋ねるのはサーバのフラグ `auto_submit` が `prompt` のときだけである (flags.py)
- `lppsubmit` による手動の提出。通らなかった試行でも出せる

**提出は身元を伴う。** サーバは端末トークンなしの提出を受け付けない
(未束縛でも受け入れるアップロードとはここが違う)。登録していない端末では
提出を持ちかけず、`lppsetup` を案内する。

どの試行を提出するかは、直前の試行をこちらで控えて決める。控えは課題ごとに
持つ。`lpptest 01test` の後に `lpptest 02test` を走らせてから `lppsubmit` を
打つことがあり、1 つしか持たないとどちらの課題を出すのか決められない。
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .api import ApiError, LppApi
from .config import IS_DOCKER_ENV, LPP_DATA_DIR
from .device import LppDevice
from .flags import AUTO_SUBMIT_ENV, PROMPT
from .uploader import FOREGROUND_DEADLINE, Uploader

STATE_DIR = Path(LPP_DATA_DIR) / "attempts"

# 「はい」とみなす答え。既定は「いいえ」にする。提出は取り消せないので、
# Enter を押しただけ・EOF で終わっただけで送ってはならない
YES_ANSWERS = ("y", "yes", "はい")


def _api_factory(factory=None) -> Callable[[Optional[str]], LppApi]:
    return factory or (lambda token: LppApi(token=token))


# ---------------------------------------------------------------------------
# 直前の試行の控え
# ---------------------------------------------------------------------------
def state_path(assignment: str, state_dir: Optional[Path] = None) -> Path:
    directory = Path(state_dir) if state_dir is not None else STATE_DIR
    # 課題名はサーバ側の識別子で、区切り文字は入らない。念のため潰しておく
    return directory / (assignment.replace("/", "_").replace(os.sep, "_") + ".json")


def save_attempt(state: Dict[str, Any], state_dir: Optional[Path] = None) -> Path:
    """直近の試行を控える。

    サーバへ送れなかったとき (`attempt_id` が無いとき) も書く。書かずに
    済ませると前回の控えがそのまま残り、`lppsubmit` が今日の試行のつもりで
    前回の試行を提出する。
    """
    path = state_path(state["assignment"], state_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)
    return path


def load_attempt(
    assignment: str, state_dir: Optional[Path] = None
) -> Optional[Dict[str, Any]]:
    path = state_path(assignment, state_dir)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def known_assignments(state_dir: Optional[Path] = None) -> List[str]:
    directory = Path(state_dir) if state_dir is not None else STATE_DIR
    if not directory.exists():
        return []
    return sorted(path.stem for path in directory.glob("*.json"))


def latest_attempt(state_dir: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    """最後に走らせた課題の控え。"""
    directory = Path(state_dir) if state_dir is not None else STATE_DIR
    if not directory.exists():
        return None
    files = sorted(directory.glob("*.json"), key=lambda p: p.stat().st_mtime)
    for path in reversed(files):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
    return None


def attempt_state(
    assignment: str,
    idempotency_key: str,
    device_time: str,
    result: List[Dict[str, Any]],
    attempt_id: Optional[str] = None,
    session_id: Optional[str] = None,
) -> Dict[str, Any]:
    """pytest の結果から控えの中身を作る。

    1 件も実行されていないものは「すべて通った」とは扱わない。収集の段で
    落ちると結果が空配列になるので、空を通過とみなすと、何も動いていない
    ものを提出しますかと尋ねることになる。
    """
    failed = [row for row in result if row.get("outcome") != "passed"]
    return {
        "assignment": assignment,
        "attempt_id": attempt_id,
        "idempotency_key": idempotency_key,
        "device_time": device_time,
        "session_id": session_id,
        "total": len(result),
        "failed": len(failed),
        "all_passed": bool(result) and not failed,
        "submission": None,
    }


# ---------------------------------------------------------------------------
# 提出
# ---------------------------------------------------------------------------
def submit(
    state: Dict[str, Any],
    device: LppDevice,
    auto: bool,
    api_factory=None,
    state_dir: Optional[Path] = None,
) -> Optional[Dict[str, Any]]:
    """1 件の試行を提出する。送れたら応答を返す。

    `auto` はサーバ側の印で、採点の一覧と Redmine のコメントに
    「自動提出」と出るかどうかだけを決める。テストを通した流れから出した
    ものを `True`、`lppsubmit` で明示的に出したものを `False` にしている。
    """
    api = _api_factory(api_factory)(device.device_token)
    try:
        result = api.post_submission(state["attempt_id"], auto=auto)
    except ApiError as e:
        if e.status_code == 401:
            # 送り直しても同じ 401 になる。手元のトークンを手放して
            # セットアップからやり直せるようにする
            device.forget_binding()
            print(
                "[lpp] 端末の登録が無効になっていました。"
                "`lppsetup` でセットアップし直してから提出してください"
            )
        elif e.status_code == 403:
            print(
                "[lpp] この試行は別の端末の登録に属しています。"
                "同じ端末で `lpptest` を実行し直してから提出してください"
            )
        elif e.status_code == 404:
            print(
                "[lpp] サーバにこの試行がありません。"
                "`lpptest` を実行し直してから提出してください"
            )
        else:
            print(f"[lpp] 提出できませんでした: {e}")
        return None
    except Exception as e:  # noqa: BLE001 - 想定外も握り潰さず見せる
        print(f"[lpp] 提出できませんでした: {e}")
        return None
    finally:
        api.close()

    state["submission"] = {
        "submissionId": result.get("submissionId"),
        "submittedAt": result.get("submittedAt"),
        "auto": auto,
    }
    save_attempt(state, state_dir)
    print(f"[lpp] {state['assignment']} を提出しました ({result.get('submittedAt')})")
    return result


def _confirm(question: str, ask=input) -> bool:
    try:
        answer = ask(question)
    except (EOFError, KeyboardInterrupt):
        print()
        return False
    return (answer or "").strip().lower() in YES_ANSWERS


def _summary(state: Dict[str, Any]) -> str:
    if state.get("all_passed"):
        outcome = f"すべて通りました ({state.get('total', 0)} 件)"
    else:
        outcome = f"{state.get('failed', 0)}/{state.get('total', 0)} 件が通っていません"
    return f"課題 {state['assignment']} / 実行 {state.get('device_time')} / {outcome}"


# ---------------------------------------------------------------------------
# テストの直後に持ちかける
# ---------------------------------------------------------------------------
# 一部だけを走らせたときに合否を丸めないための印。`-k` や `--lf` は
# pytest がテストを選ぶので、集めた結果はスイート全体のものではない
PARTIAL_PYTEST_ARGS = ("-k", "--lf", "--last-failed", "--deselect", "--sw", "--stepwise")


def is_full_suite_run(testcases: Optional[str], pytest_args: Optional[List[str]]) -> bool:
    """スイート全体を走らせたか。

    一部だけを走らせたときに提出を持ちかけてはならない。集めた結果は
    走らせた分のものなので、コンパイルのテストだけを通しても
    「すべてのテストが通りました」になってしまう。
    """
    if testcases not in (None, "all"):
        return False
    for arg in pytest_args or []:
        if arg.split("=")[0] in PARTIAL_PYTEST_ARGS:
            return False
    return True


def offer_after_test(
    session_id: Optional[str] = None,
    device: Optional[LppDevice] = None,
    api_factory=None,
    ask=input,
    state_dir: Optional[Path] = None,
    interactive: Optional[bool] = None,
    auto_submit: Optional[str] = None,
) -> bool:
    """テストがすべて通っていたら提出するか尋ねる。送ったら True。

    `session_id` は今回の実行のものと控えが同じ実行のものかを見るために使う。
    pytest が途中で殺されると控えは前回のまま残るので、これを見ないと
    前回の試行を今回のものとして提出しかねない。

    `auto_submit` はサーバが配るフラグの実効値 (flags.py)。`prompt` のときだけ
    尋ねる。それ以外では提出に関する案内も出さない。授業の開始前に
    `lppsubmit` を案内すると、尋ねないようにした意味が薄れる。
    省略すると runner が渡した環境変数を読み、それも無ければ尋ねない。
    """
    if auto_submit is None:
        auto_submit = os.environ.get(AUTO_SUBMIT_ENV)
    if auto_submit != PROMPT:
        return False

    state = latest_attempt(state_dir)
    if state is None:
        return False
    if session_id is not None and state.get("session_id") != session_id:
        return False
    if not state.get("all_passed"):
        return False

    assignment = state["assignment"]
    device = device or LppDevice()
    if not device.is_bound():
        print(
            "[lpp] すべてのテストが通りました。提出するにはこの端末の登録が要ります。"
            "`lppsetup` を実行してください"
        )
        return False
    if not state.get("attempt_id"):
        # 送信待ちのまま。提出には試行の id が要るので、届いてからになる
        print(
            "[lpp] すべてのテストが通りましたが、今回の試行はまだサーバに届いて"
            f"いません。届いた後に `lppsubmit {assignment}` で提出できます"
        )
        return False

    if interactive is None:
        interactive = sys.stdin.isatty()
    if not interactive:
        # 端末が無いところ (パイプ、CI、エージェント) で尋ねると、答えの
        # 無いまま既定の「いいえ」になる。黙って流さず出し方を書く
        print(
            f"[lpp] すべてのテストが通りました。`lppsubmit {assignment}` で提出できます"
        )
        return False

    if not _confirm(f"[lpp] すべてのテストが通りました。{assignment} を提出しますか? [y/N]: ", ask):
        print(f"[lpp] 提出していません。`lppsubmit {assignment}` でいつでも提出できます")
        return False

    return submit(state, device, auto=True, api_factory=api_factory, state_dir=state_dir) is not None


# ---------------------------------------------------------------------------
# 手動の提出 (lppsubmit)
# ---------------------------------------------------------------------------
def _resolve_attempt_id(
    state: Dict[str, Any],
    device: LppDevice,
    api_factory=None,
    uploader: Optional[Uploader] = None,
    state_dir: Optional[Path] = None,
) -> Optional[str]:
    """送信待ちのまま残っている試行を送って、その id を得る。

    テストを走らせたときにサーバへ届かなかった場合、控えには id が無い。
    ここで送り直せば、回線が戻った後に `lppsubmit` だけで提出できる。
    """
    if uploader is None:
        uploader = Uploader(device, api_factory=_api_factory(api_factory))
    print("[lpp] 送信待ちの試行を送っています...")
    uploader.flush(newest_first=True, deadline=time.monotonic() + FOREGROUND_DEADLINE)
    for message in uploader.errors:
        print(f"[lpp] {message}")
    attempt_id = uploader.attempt_ids.get(state.get("idempotency_key"))
    if attempt_id:
        state["attempt_id"] = attempt_id
        save_attempt(state, state_dir)
    return attempt_id


def manual_submit(
    assignment: Optional[str] = None,
    assume_yes: bool = False,
    device: Optional[LppDevice] = None,
    api_factory=None,
    ask=input,
    uploader: Optional[Uploader] = None,
    state_dir: Optional[Path] = None,
) -> int:
    """`lppsubmit` の中身。終了コードを返す。

    通らなかった試行でも出せる。締め切りの前に手元で動くところまでを
    出しておきたいことがあり、合否で止めると出す手段が無くなる。
    合否は画面に出して、何を出そうとしているか分かるようにする。
    """
    if assignment is None:
        state = latest_attempt(state_dir)
    else:
        state = load_attempt(assignment, state_dir)

    if state is None:
        known = known_assignments(state_dir)
        if assignment is not None and known:
            print(
                f"[lpp] {assignment} の試行がありません。"
                f"控えがあるのは {', '.join(known)} です"
            )
        else:
            print("[lpp] 提出できる試行がありません。先に `lpptest <課題>` を実行してください")
        return 1

    device = device or LppDevice()
    if not device.is_bound():
        print(
            "[lpp] 提出にはこの端末の登録が要ります。`lppsetup` を実行してください"
        )
        return 1

    if not state.get("attempt_id"):
        if not _resolve_attempt_id(
            state, device, api_factory=api_factory, uploader=uploader, state_dir=state_dir
        ):
            print(
                "[lpp] この試行はまだサーバに届いていません。"
                "接続を確かめて、もう一度 `lppsubmit` を実行してください"
            )
            return 1

    print(f"[lpp] 提出するもの: {_summary(state)}")
    previous = state.get("submission")
    if previous:
        print(f"[lpp] この試行は {previous.get('submittedAt')} に提出済みです")

    if not assume_yes:
        question = "[lpp] 提出しますか? [y/N]: "
        if previous:
            question = "[lpp] もう一度提出しますか? [y/N]: "
        if not _confirm(question, ask):
            print("[lpp] 提出していません")
            return 1

    return 0 if submit(
        state, device, auto=False, api_factory=api_factory, state_dir=state_dir
    ) else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="lppsubmit", description="直前のテストの試行を提出する"
    )
    parser.add_argument(
        "assignment",
        nargs="?",
        help="提出する課題 (省略すると最後に実行した課題)",
    )
    parser.add_argument(
        "-y", "--yes", action="store_true", help="確認を省いて提出する"
    )
    return parser


def main():
    args = build_parser().parse_args()

    if not IS_DOCKER_ENV:
        # 学生が触るコマンドはどれもホストでは docker を起動するだけで、
        # API を叩くのはコンテナの中である
        from .docker import run_test_container, update

        update()
        run_test_container(["lppsubmit", *sys.argv[1:]])
        return

    from .docker import fix_permission
    from .version import warn_on_version_skew

    warn_on_version_skew()
    try:
        code = manual_submit(args.assignment, assume_yes=args.yes)
    finally:
        # 控えはコンテナの中で root が書く。ホスト側から読めるようにする
        fix_permission()
    sys.exit(code)
