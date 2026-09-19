"""収集時の文脈の申告 (`envLabels`) を組み立てる。

サーバは中身を検証しない。何を送るかはここが単一の情報源で、**送るキーは
この固定の一覧だけ**である。環境を丸ごと写すと、API キーのような秘密情報が
そのまま研究データに残り、サーバ側では止められない。

判定は Python の中で行う。学生が設定した環境変数を値として送ることはしない。
下の表に載っている印は「その印があったかどうか」だけを見て、こちらで決めた
定数を送る (`CLAUDE_CODE_MESSAGING_TOKEN` の値は送らない、という意味である)。

**判定はホスト側で行う。** `lpptest` と `lpprun` はホスト側で docker を起動し、
pytest はコンテナの中で走る。エージェントの印はホスト側の環境変数にしか無く、
git もコンテナの中には入っているとは限らない。見えているのはどちらもホスト側
なので、ホストで判定した結果を `LPP_HOST_ENV_LABELS` でコンテナへ渡す。この
環境変数は道具の内部の伝達路で、学生が設定するものではない
(`LPP_IMAGE_DIGEST` と同じ扱い)。
"""

import json
import os
import subprocess
from typing import Dict, Mapping, Optional

from .config import TARGETPATH

# サーバが受けるキーの一覧。ここに無いキーは送らないし、受け取っても捨てる
AGENT_NAME = "AGENT_NAME"
MANAGED_BY_GIT = "MANAGED_BY_GIT"
KNOWN_KEYS = (AGENT_NAME, MANAGED_BY_GIT)

# ホストからコンテナへ渡す伝達路
RELAY_ENV = "LPP_HOST_ENV_LABELS"

# 既知の印が無かった。「エージェントを使っていない」と言い切れるわけではなく、
# 「この版が知っている印は無かった」である。知らないエージェントは none になる
NO_AGENT = "none"

# サーバ側の上限 (api.ts の envLabelLimits)
MAX_VALUE_LENGTH = 256

# git が動かせなかった。「管理していない」と「確かめられなかった」を混ぜない
UNKNOWN = "unknown"

# git が固まっても試行の記録を止めない。ネットワーク越しのファイルシステムで
# 遅くなることがある
GIT_TIMEOUT = 5.0

# 印 → 送る名前。値は読まない。印があったかどうかだけを見る。
#
# 上から順に見て、最初に当たったものを送る。Claude Code を別のエージェントの
# 端末から動かすと印が両方立つので、順序がそのまま優先順位になる。
#
# 載せるのは、その道具の文書か実際の環境で確かめた印だけにする。推測で足すと、
# 「エージェントを使っていない学生」が使っていることにされる。Cursor のように
# 印が文書化されていないものは、分かるまで載せない (none になる)。
AGENT_MARKERS = (
    # 実際の Claude Code の環境で確認
    ("CLAUDECODE", "claude_code"),
    ("CLAUDE_CODE_ENTRYPOINT", "claude_code"),
    # docs/tools/shell.md: run_shell_command は GEMINI_CLI=1 を立てる
    ("GEMINI_CLI", "gemini_cli"),
    # shell ツールは CODEX_SANDBOX_NETWORK_DISABLED=1 を、macOS の
    # sandbox-exec 経由では CODEX_SANDBOX=seatbelt を立てる。
    # 砂場を切って動かしていると印が立たないので取りこぼしうる
    ("CODEX_SANDBOX", "codex"),
    ("CODEX_SANDBOX_NETWORK_DISABLED", "codex"),
)


def _environ(environ: Optional[Mapping[str, str]]) -> Mapping[str, str]:
    return os.environ if environ is None else environ


def detect_agent_name(environ: Optional[Mapping[str, str]] = None) -> str:
    """どの AI エージェントの下で動いているか。"""
    env = _environ(environ)
    for marker, name in AGENT_MARKERS:
        if env.get(marker):
            return name
    return NO_AGENT


def detect_managed_by_git(target_path: str) -> str:
    """作業しているディレクトリが git の管理下か。`true` / `false` / `unknown`。

    `.git` の有無ではなく git 自身に訊く。判定したいのは「学生が git を使って
    いるか」であって、ディレクトリの中身ではない。git に訊けば、課題の
    ディレクトリがリポジトリの下の階層にある場合も、worktree や submodule で
    `.git` がファイルになっている場合も、そのまま正しい答えが返る。git が
    入っていない端末では、そもそも使いようがないことも分かる。

    `git status` ではなく `rev-parse` を使うのは、status が index を
    書き戻しにいく (学生のリポジトリに触る) ためである。知りたいのは
    作業ツリーの中かどうかだけなので、読むだけで済ませる。

    `safe.directory` を緩めるのは、コンテナの中で `/workspaces` の持ち主が
    ホストの UID になり、そのままだと「dubious ownership」で落ちるためである。
    読むだけなので緩めても副作用はない。
    """
    argv = [
        "git",
        "-c",
        "safe.directory=*",
        "-C",
        target_path,
        "rev-parse",
        "--is-inside-work-tree",
    ]
    try:
        proc = subprocess.run(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=GIT_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError):
        # git が入っていない、または時間内に返らなかった
        return UNKNOWN
    if proc.returncode != 0:
        # リポジトリの外。git は入っているので、使っていないと言える
        return "false"
    return "true" if proc.stdout.decode("utf-8", "replace").strip() == "true" else "false"


def detect(
    target_path: Optional[str] = None,
    environ: Optional[Mapping[str, str]] = None,
) -> Dict[str, str]:
    """今いる環境から申告を組み立てる。

    値は文字列で揃える。`true` と `"true"` が混ざると、層別が型のゆらぎで割れる。
    """
    path = TARGETPATH if target_path is None else target_path
    return {
        AGENT_NAME: detect_agent_name(environ),
        MANAGED_BY_GIT: detect_managed_by_git(path),
    }


def relay_value(
    target_path: Optional[str] = None,
    environ: Optional[Mapping[str, str]] = None,
) -> str:
    """ホスト側で判定した申告を、コンテナへ渡せる 1 つの文字列にする。"""
    return json.dumps(detect(target_path, environ), ensure_ascii=False)


def _relayed(environ: Mapping[str, str]) -> Dict[str, str]:
    """ホストから渡された申告を読む。

    壊れていたら黙って捨てる。ここで例外を上げると、申告のために試行そのものが
    記録されなくなる。申告は試行より軽い。
    """
    raw = environ.get(RELAY_ENV)
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except ValueError:
        return {}
    if not isinstance(parsed, dict):
        return {}

    labels: Dict[str, str] = {}
    for key in KNOWN_KEYS:
        value = parsed.get(key)
        if isinstance(value, str) and value:
            # 上限を破った申告はサーバが 400 で拒み、その試行は再送されずに
            # upload_failed へ落ちる。申告のために試行を捨てない
            labels[key] = value[:MAX_VALUE_LENGTH]
    return labels


def env_labels(
    target_path: Optional[str] = None,
    environ: Optional[Mapping[str, str]] = None,
) -> Dict[str, str]:
    """記録に載せる申告。

    ホストから渡された分を優先する。学生が実際に作業しているのはホスト側で、
    コンテナの中の環境はこちらが用意したものだからである。ホスト側の wrapper が
    古くて何も渡してこないときは、今いる場所で分かる範囲を埋める。コンテナの
    中なら、エージェントの印は見えず (`none`)、git も入っていなければ
    `unknown` になる。`--run-pytest` でホスト実行したときはここが本番になる。
    """
    env = _environ(environ)
    labels = detect(target_path, env)
    labels.update(_relayed(env))
    return labels
