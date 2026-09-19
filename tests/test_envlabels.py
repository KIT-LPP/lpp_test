"""収集時の文脈の申告 (envLabels) を組み立てる側の検査。

環境変数は必ず明示的に渡す。`os.environ` を読ませると、このテスト自体を
エージェントの端末から走らせたときだけ落ちるテストになる。
"""

import json
import os
import subprocess
from pathlib import Path

from lpp_collector import envlabels
from lpp_collector.envlabels import (
    AGENT_DECLARED,
    AGENT_NAME,
    MANAGED_BY_GIT,
    MAX_VALUE_LENGTH,
    NO_AGENT,
    OTHER_AGENT,
    RELAY_ENV,
    UNKNOWN,
    detect,
    detect_agent_name,
    detect_declared_agent,
    detect_managed_by_git,
    env_labels,
    relay_value,
)


def git_init(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "init", "-q"],
        cwd=str(path),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=True,
    )
    return path


def test_no_marker_means_no_known_agent():
    assert detect_agent_name({}) == NO_AGENT
    assert detect_agent_name({"EDITOR": "vim"}) == NO_AGENT
    # 空の印は立っていないのと同じ
    assert detect_agent_name({"CLAUDECODE": ""}) == NO_AGENT


def test_known_markers_map_to_fixed_names():
    """名前は detect_agent のものをそのまま使う。"""
    assert detect_agent_name({"CLAUDECODE": "1"}) == "claude_code"
    assert detect_agent_name({"GEMINI_CLI": "1"}) == "gemini_cli"
    assert detect_agent_name({"CODEX_SANDBOX": "seatbelt"}) == "codex_cli"
    assert detect_agent_name({"CURSOR_TRACE_ID": "x"}) == "cursor"


def test_the_marker_value_never_leaves_the_terminal():
    """印の値は送らない。秘密情報が研究データに残るのを防ぐ。"""
    name = detect_agent_name(
        {"CLAUDECODE": "1", "CLAUDE_CODE_MESSAGING_TOKEN": "secret-token"}
    )
    assert name == "claude_code"
    assert "secret" not in name


def test_the_marker_is_read_before_the_self_declaration(tmp_path: Path):
    """実際の Claude Code は版まで入れて名乗る。名乗りを先に読ませない。

    detect_agent は `AI_AGENT` を最優先で読み、名乗られた文字列をそのまま名前と
    して返す。それを `AGENT_NAME` にすると、同じ道具でも版ごとに名前が割れて
    層別できなくなる。名乗りは捨てずに `AGENT_DECLARED` へ回す。

    名乗りを外すのは `os.environ` の差し替えで、detect_agent が `os.environ` を
    呼び出しの度に引くことに乗っている。上流が `from os import environ` に
    変えるとこの仕掛けは黙って効かなくなる。パッケージを上げた後にそれを
    捕まえるのはこの検査だけである。
    """
    env = {"AI_AGENT": "claude-code_2-1-278_agent", "CLAUDECODE": "1"}
    assert detect_agent_name(env) == "claude_code"

    labels = detect(str(tmp_path), env)
    assert labels[AGENT_NAME] == "claude_code"
    assert labels[AGENT_DECLARED] == "claude-code_2-1-278_agent"


def test_a_self_declared_name_does_not_widen_the_stratifier(tmp_path: Path):
    """名乗りは印で分からなかったときだけ見る。`AGENT_NAME` は既知の名前だけ。"""
    # 既知の名前そのものなら、こちらの一覧にある定数を送るのと変わらない
    assert detect_agent_name({"AI_AGENT": "cursor"}) == "cursor"

    # 印が無く、名乗りも知らない形。`AGENT_NAME` は other に畳む
    for declared in ("claude-code_2-1-278_agent", "some-internal-tool"):
        assert detect_agent_name({"AI_AGENT": declared}) == OTHER_AGENT
        # 畳んだ中身は失われない
        assert detect(str(tmp_path), {"AI_AGENT": declared})[AGENT_DECLARED] == declared

    # 空の名乗りは名乗っていないのと同じ
    assert detect_agent_name({"AI_AGENT": "  "}) == NO_AGENT


def test_the_declaration_is_kept_as_it_is():
    """`AI_AGENT` は道具が自分を名乗るための変数。版が分かること自体に意味がある。"""
    assert detect_declared_agent({"AI_AGENT": "claude-code_2-1-278_agent"}) == (
        "claude-code_2-1-278_agent"
    )
    assert detect_declared_agent({}) == NO_AGENT
    assert detect_declared_agent({"AI_AGENT": "  "}) == NO_AGENT
    assert detect_declared_agent({"AI_AGENT": " cursor \n"}) == "cursor"

    # 印があっても名乗りは名乗りで、こちらが名前を作って埋めることはしない
    assert detect_declared_agent({"CLAUDECODE": "1"}) == NO_AGENT


def test_an_overlong_declaration_is_cut_at_the_source():
    """上限を破った申告はサーバが 400 で拒み、その試行が丸ごと落ちる。"""
    declared = detect_declared_agent({"AI_AGENT": "x" * (MAX_VALUE_LENGTH + 10)})
    assert len(declared) == MAX_VALUE_LENGTH


def test_the_order_is_the_packages():
    """印が複数立ったときの優先順位は detect_agent が決める。

    Claude Code を Cursor の端末から動かすと両方立つ。どちらを取るかを
    こちらで決め直さない (決め直すと、パッケージを上げる度にずれる)。
    """
    assert detect_agent_name({"CLAUDECODE": "1", "CURSOR_TRACE_ID": "x"}) == "cursor"


def test_a_detector_that_breaks_does_not_cost_the_attempt(monkeypatch):
    """パッケージが落ちても試行は記録する。分からなかったことは隠さない。"""

    def broken():
        raise RuntimeError("agents.json")

    before = os.environ
    monkeypatch.setattr(envlabels, "determine_agent", broken)
    assert detect_agent_name({"CLAUDECODE": "1"}) == UNKNOWN
    # 落ちた後でも差し替えた環境を戻す
    assert os.environ is before


def test_the_ambient_environment_is_left_as_it_was():
    """検査のために `os.environ` を差し替えるので、必ず戻す。"""
    before = os.environ
    detect_agent_name({"CLAUDECODE": "1"})
    assert os.environ is before
    assert os.environ.get("CLAUDECODE") == before.get("CLAUDECODE")


def test_git_itself_answers_whether_the_directory_is_managed(tmp_path: Path):
    plain = tmp_path / "plain"
    plain.mkdir()
    assert detect_managed_by_git(str(plain)) == "false"

    repo = git_init(tmp_path / "repo")
    assert detect_managed_by_git(str(repo)) == "true"


def test_a_subdirectory_of_a_repository_counts_as_managed(tmp_path: Path):
    """`.git` の有無では分からない。課題を下の階層に置く学生がいる。"""
    repo = git_init(tmp_path / "repo")
    work = repo / "kadai1"
    work.mkdir()
    assert not (work / ".git").exists()
    assert detect_managed_by_git(str(work)) == "true"


def test_an_empty_dot_git_is_not_a_repository(tmp_path: Path):
    """存在だけを見ていたときは、これを管理下と答えていた。"""
    fake = tmp_path / "fake"
    (fake / ".git").mkdir(parents=True)
    assert detect_managed_by_git(str(fake)) == "false"


def test_git_that_cannot_be_run_is_not_reported_as_unmanaged(tmp_path: Path, monkeypatch):
    """git が入っていない端末で「使っていない」と言わない。"""

    def no_git(*args, **kwargs):
        raise FileNotFoundError("git")

    monkeypatch.setattr(envlabels.subprocess, "run", no_git)
    assert detect_managed_by_git(str(tmp_path)) == UNKNOWN


def test_git_that_hangs_does_not_hang_the_attempt(tmp_path: Path, monkeypatch):
    def too_slow(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="git", timeout=envlabels.GIT_TIMEOUT)

    monkeypatch.setattr(envlabels.subprocess, "run", too_slow)
    assert detect_managed_by_git(str(tmp_path)) == UNKNOWN


def test_values_are_strings(tmp_path: Path):
    """true と "true" が混ざると、層別が型のゆらぎで割れる。"""
    labels = detect(str(tmp_path), {})
    assert labels == {
        AGENT_NAME: NO_AGENT,
        AGENT_DECLARED: NO_AGENT,
        MANAGED_BY_GIT: "false",
    }
    assert all(isinstance(value, str) for value in labels.values())


def test_only_the_fixed_keys_are_sent(tmp_path: Path):
    labels = detect(str(tmp_path), {"CLAUDECODE": "1", "AWS_SECRET_ACCESS_KEY": "s"})
    assert set(labels) == {AGENT_NAME, AGENT_DECLARED, MANAGED_BY_GIT}


def test_the_host_decides_what_the_container_cannot_see(tmp_path: Path):
    """コンテナの中にエージェントの印は無い。ホストの判定を使う。"""
    host = {"CLAUDECODE": "1", "AI_AGENT": "claude-code_2-1-278_agent"}
    relayed = relay_value(str(tmp_path), host)
    assert json.loads(relayed)[AGENT_NAME] == "claude_code"

    # コンテナ側: 印は見えないが、渡された申告を使う
    labels = env_labels(str(tmp_path), {RELAY_ENV: relayed})
    assert labels[AGENT_NAME] == "claude_code"
    assert labels[AGENT_DECLARED] == "claude-code_2-1-278_agent"


def test_an_old_host_still_gets_what_the_container_can_see(tmp_path: Path):
    """古いホスト側 wrapper は何も渡してこない。分かる範囲で埋める。"""
    repo = git_init(tmp_path / "repo")
    labels = env_labels(str(repo), {})
    assert labels == {
        AGENT_NAME: NO_AGENT,
        AGENT_DECLARED: NO_AGENT,
        MANAGED_BY_GIT: "true",
    }


def test_a_broken_relay_does_not_cost_the_attempt(tmp_path: Path):
    """申告が壊れていても試行は記録する。申告は試行より軽い。"""
    for broken in ("", "{", "[]", "null", json.dumps({AGENT_NAME: 1})):
        labels = env_labels(str(tmp_path), {RELAY_ENV: broken})
        assert labels == {
            AGENT_NAME: NO_AGENT,
            AGENT_DECLARED: NO_AGENT,
            MANAGED_BY_GIT: "false",
        }


def test_unknown_keys_in_the_relay_are_dropped(tmp_path: Path):
    relayed = json.dumps({AGENT_NAME: "codex_cli", "AWS_SECRET_ACCESS_KEY": "s"})
    labels = env_labels(str(tmp_path), {RELAY_ENV: relayed})
    assert set(labels) == {AGENT_NAME, AGENT_DECLARED, MANAGED_BY_GIT}
    assert labels[AGENT_NAME] == "codex_cli"


def test_an_overlong_value_is_cut_instead_of_failing_the_upload(tmp_path: Path):
    """上限を破った申告はサーバが 400 で拒み、その試行は再送されない。"""
    relayed = json.dumps({AGENT_NAME: "x" * (MAX_VALUE_LENGTH + 10)})
    labels = env_labels(str(tmp_path), {RELAY_ENV: relayed})
    assert len(labels[AGENT_NAME]) == MAX_VALUE_LENGTH
