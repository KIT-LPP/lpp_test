"""収集時の文脈の申告 (`envLabels`) を組み立てる。

サーバは中身を検証しない。何を送るかはここが単一の情報源で、**送るキーは
この固定の一覧だけ**である。環境を丸ごと写すと、API キーのような秘密情報が
そのまま研究データに残り、サーバ側では止められない。

判定は Python の中で行う。エージェントの判定は `detect_agent` (Vercel の
`detect-agent` の Python 移植) に任せ、返ってきた名前が既知の一覧にあるときだけ
`AGENT_NAME` に載せる。印となる環境変数の**値**は送らない
(`CLAUDE_CODE_MESSAGING_TOKEN` の値は送らない、という意味である)。

値をそのまま送るのは `AI_AGENT` だけである。これは道具が自分の名前を名乗るための
変数で、版まで入ることがあり (`claude-code_2-1-278_agent`)、その版が分かること自体
に意味がある。層別に使う `AGENT_NAME` を版で割らせないために、名乗りは
`AGENT_DECLARED` という別のキーに分けて載せる。

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

from detect_agent import KNOWN_AGENTS, determine_agent

from .config import TARGETPATH
from .flags import AUTO_SUBMIT_ENV, AUTO_SUBMIT_MODES

# ホスト側で判定するキーの一覧。ここに無いキーはホストから受け取っても捨てる。
# 送るのはこれに AUTO_SUBMIT (下) を足したものだけである
AGENT_NAME = "AGENT_NAME"
AGENT_DECLARED = "AGENT_DECLARED"
MANAGED_BY_GIT = "MANAGED_BY_GIT"
KNOWN_KEYS = (AGENT_NAME, AGENT_DECLARED, MANAGED_BY_GIT)

# ホストからコンテナへ渡す伝達路
RELAY_ENV = "LPP_HOST_ENV_LABELS"

# 自動提出のフラグの実効値 (flags.py)。`prompt` / `off` / `unavailable`。
# 分析で「この試行のあとに提出を尋ねたか」を区別するために載せる。
# ホストではなくコンテナの runner が決める値なので、RELAY_ENV からは受け取らない
AUTO_SUBMIT = "AUTO_SUBMIT"

# 既知の印が無かった。「エージェントを使っていない」と言い切れるわけではなく、
# 「この版の detect_agent が知っている印は無かった」である。知らないエージェントは
# none になる
NO_AGENT = "none"

# エージェントは居たが、名前が既知の一覧に無かった。`AGENT_NAME` は層別に使うので、
# 知らない名前をここで増やさない。名乗りそのものは `AGENT_DECLARED` に残る
OTHER_AGENT = "other"

# 道具が自分の名前を名乗る決まりの環境変数。実際 Claude Code は版まで入れて
# `claude-code_2-1-278_agent` と名乗る。detect_agent はこれを最優先で読み、
# 名乗られた文字列をそのまま名前として返すので、印からの判定より先に使わせない
# (版ごとに `AGENT_NAME` が割れて層別できなくなる)。名乗りは `AGENT_DECLARED` へ回す
AI_AGENT_ENV = "AI_AGENT"

# サーバ側の上限 (api.ts の envLabelLimits)
MAX_VALUE_LENGTH = 256

# git が動かせなかった。「管理していない」と「確かめられなかった」を混ぜない
UNKNOWN = "unknown"

# git が固まっても試行の記録を止めない。ネットワーク越しのファイルシステムで
# 遅くなることがある
GIT_TIMEOUT = 5.0

# 送ってよい名前の一覧。detect_agent が印から決めた名前だけがここに入る。
# 一覧そのものを detect_agent から借りるので、パッケージを上げれば新しい
# エージェントもこのファイルを触らずに通る
KNOWN_AGENT_NAMES = frozenset(KNOWN_AGENTS.values())


def _environ(environ: Optional[Mapping[str, str]]) -> Mapping[str, str]:
    return os.environ if environ is None else environ


def _determine_by_markers(env: Mapping[str, str]):
    """印だけで `detect_agent` に訊く。名乗り (`AI_AGENT`) は外す。

    `determine_agent()` は引数を取らず `os.environ` を直接読むので、その間だけ
    `os.environ` を差し替える (`detect_agent` は呼び出しの度に引き直す)。
    差し替える先は名乗りを抜いた写しで、これは検査のためだけの仕掛けではない。
    本番でも、名乗りより印を先に見るために通る道である。
    """
    masked = {key: value for key, value in env.items() if key != AI_AGENT_ENV}
    saved = os.environ
    os.environ = masked  # type: ignore[assignment]
    try:
        return determine_agent()
    finally:
        os.environ = saved  # type: ignore[assignment]


def detect_agent_name(environ: Optional[Mapping[str, str]] = None) -> str:
    """どの AI エージェントの下で動いているか。

    どの印をどの名前に結び付けるかは `detect_agent` が決める。送るのは既知の
    一覧にある名前だけで、印の**値**は送らない。

    先に印を見て、名乗り (`AI_AGENT`) は印で分からなかったときだけ見る。名乗りは
    学生の環境次第の文字列なので、既知の名前とそのまま一致するときに限って使い、
    それ以外は「既知ではない何かが居た」として `other` に畳む。畳んだ中身は
    `AGENT_DECLARED` (`detect_declared_agent`) に残るので、失われるわけではない。
    """
    env = _environ(environ)
    try:
        result = _determine_by_markers(env)
    except Exception:
        # 申告のために試行の記録を落とさない。分からなかったことは隠さない
        return UNKNOWN

    if result.get("is_agent"):
        name = (result.get("agent") or {}).get("name")
        if isinstance(name, str) and name in KNOWN_AGENT_NAMES:
            return name
        return OTHER_AGENT

    declared = detect_declared_agent(env)
    if declared == NO_AGENT:
        return NO_AGENT
    return declared if declared in KNOWN_AGENT_NAMES else OTHER_AGENT


def detect_declared_agent(environ: Optional[Mapping[str, str]] = None) -> str:
    """道具が `AI_AGENT` で名乗った文字列そのもの。名乗りが無ければ `none`。

    `AGENT_NAME` と分けるのは、名乗りに版が入るためである
    (`claude-code_2-1-278_agent`)。同じ道具でも版ごとに違う文字列になるので、
    これを `AGENT_NAME` に入れると層別が版で割れる。分けておけば、層別は
    `AGENT_NAME` で、版の違いを見たいときは `AGENT_DECLARED` で、と使い分けられる。

    ここだけは環境変数の値をそのまま送る。`AI_AGENT` は道具が自分を名乗るための
    変数で、秘密を入れる場所ではないためである。それでも上限で切る。上限を破った
    申告はサーバが 400 で拒み、その試行が丸ごと落ちる。
    """
    raw = _environ(environ).get(AI_AGENT_ENV)
    if not isinstance(raw, str) or not raw.strip():
        return NO_AGENT
    return raw.strip()[:MAX_VALUE_LENGTH]


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
        AGENT_DECLARED: detect_declared_agent(environ),
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
    auto_submit = env.get(AUTO_SUBMIT_ENV)
    if auto_submit in AUTO_SUBMIT_MODES:
        labels[AUTO_SUBMIT] = auto_submit
    return labels
